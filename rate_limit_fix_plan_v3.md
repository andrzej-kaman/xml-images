# Ostateczny Plan Naprawczy dla Aplikacji (Wersja 3)

## Cel

Ten ostateczny plan implementuje architektonicznie poprawne, wydajne i odporne na błędy rozwiązanie problemu limitów API (`429`) poprzez wprowadzenie mechanizmu kolejki z opóźnieniem (`Delayed Queue`) w Redis. Plan ten jest wynikiem iteracyjnego dopracowywania w oparciu o kluczowe uwagi użytkownika.

## Proponowana Architektura: "Delayed Queue" z Użyciem Sorted Set

Wprowadzimy dwupoziomowy system kolejkowy w Redis:

1.  **`JOB_QUEUE_KEY` (List):** Główna kolejka FIFO dla zadań gotowych do natychmiastowego przetworzenia.
2.  **`DELAYED_JOB_QUEUE_KEY` (Sorted Set):** "Poczekalnia" dla zadań, które napotkały błąd `429`. Zadania będą tu przechowywane razem z docelowym czasem ponownego wykonania (timestamp) jako ich "score".

## Szczegółowe Zmiany w Kodzie `app.py`

### Krok 1: Modyfikacja Logiki Workera (`generation_worker`)

Główna pętla workera zostanie rozbudowana, aby zarządzać obiema kolejkami.

1.  **Importy:** Dodamy `from google.api_core import exceptions`.
2.  **Nowa Pętla Główna Workera:**
    -   Pętla `while True` w `generation_worker` zostanie zmodyfikowana.
    -   **Krok A: Przenoszenie Zadań z Poczekalni:** Na początku każdej iteracji, worker sprawdzi `DELAYED_JOB_QUEUE_KEY`.
        -   Pobierze wszystkie zadania, których `score` (timestamp) jest mniejszy lub równy aktualnemu czasowi.
        -   Atomowo przeniesie te zadania z `DELAYED_JOB_QUEUE_KEY` do `JOB_QUEUE_KEY`.
        -   To zapewni, że zadania gotowe do ponowienia będą traktowane priorytetowo.
    -   **Krok B: Przetwarzanie Zadań z Głównej Kolejki:** Worker użyje `redis_client.blpop(JOB_QUEUE_KEY, timeout=1)`, aby czekać na zadanie. `timeout` jest ważny, aby pętla nie blokowała się na stałe i mogła regularnie sprawdzać kolejkę opóźnioną.
    -   Pobrane zadanie zostanie przetworzone przez nową funkcję `process_product_job`.

### Krok 2: Logika Przetwarzania i Obsługi Błędów (`process_product_job`)

1.  **Stworzenie funkcji `process_product_job(job_details, status_path_base)`:**
    -   Funkcja ta będzie zawierać całą logikę dla jednego produktu (pobieranie obrazów, analiza, generowanie, zapis).
    -   Całość zostanie opakowana w blok `try...except`.
2.  **Obsługa Błędu `429`:**
    -   `except exceptions.ResourceExhausted as e:`
    -   Wewnątrz tego bloku:
        1.  Obliczymy czas ponowienia, np. `retry_timestamp = time.time() + 30`.
        2.  Dodamy zadanie do "poczekalni": `redis_client.zadd(DELAYED_JOB_QUEUE_KEY, {json.dumps(job_details): retry_timestamp})`.
        3.  Zalogujemy informację o opóźnionym ponowieniu.
3.  **Obsługa Innych Błędów:** Inne wyjątki będą logowane do pliku statusu sesji jako błędy krytyczne dla danego produktu.
4.  **Sukces:** Po pomyślnym przetworzeniu, licznik `processed_products` w pliku statusu sesji zostanie zaktualizowany.

### Krok 3: Refaktoryzacja Tworzenia Zadań i Poprawa Jakości (Bez Zmian z v2)

Te kroki pozostają takie same jak w poprzednim, odrzuconym planie, ponieważ są poprawne:

1.  **Jeden Produkt = Jedno Zadanie:** Endpoint `/api/xml/generate` zostanie zmodyfikowany, aby tworzyć osobne zadanie w `JOB_QUEUE_KEY` dla każdego produktu.
2.  **Poprawa Jakości:** Model `TEXT_ANALYSIS_MODEL` zostanie zmieniony na `gemini-2.5-pro`.

## Oczekiwane Rezultaty

-   **Brak "Burzy Ponowień":** Opóźnianie zadań w "poczekalni" skutecznie rozłoży obciążenie API w czasie, minimalizując liczbę błędów `429`.
-   **Brak Blokowania:** Workery pozostają cały czas aktywne, nie używając `time.sleep`, co maksymalizuje ich wydajność.
-   **Pełna Realizacja Zadań:** Architektura gwarantuje, że (prawie) wszystkie zadania zostaną w końcu wykonane, nawet przy okresowych problemach z API.
-   **Wysoka Jakość Obrazów:** Dzięki użyciu lepszego modelu AI.

---

## Checklista Implementacji

### Krok 0: Przygotowanie Środowiska
- [x] Dodać import `from google.api_core import exceptions`.
- [x] Zdefiniować nowy klucz Redis `DELAYED_JOB_QUEUE_KEY = "delayed_job_queue"` w sekcji konfiguracyjnej.

### Krok 1: Refaktoryzacja Tworzenia Zadań
- [x] Zmodyfikować endpoint `/api/xml/generate`, aby w pętli dodawał do `JOB_QUEUE_KEY` osobne zadanie dla każdego produktu.

### Krok 2: Refaktoryzacja Głównej Pętli Workera
- [x] Zmodyfikować pętlę `while True` w `generation_worker`.
- [x] Dodać logikę sprawdzającą `DELAYED_JOB_QUEUE_KEY` i przenoszącą gotowe zadania do `JOB_QUEUE_KEY`.
- [x] Zmodyfikować `blpop`, aby używał `timeout=1`, umożliwiając regularne sprawdzanie obu kolejek.
- [x] Zastąpić wywołanie `run_generation_for_session` wywołaniem nowej funkcji `process_product_job`.

### Krok 3: Implementacja Logiki Przetwarzania Produktu
- [x] Stworzyć nową funkcję `process_product_job(job_details)`.
- [x] Przenieść odpowiednią logikę przetwarzania jednego produktu z `run_generation_for_session` do `process_product_job`.
- [x] Opakować logikę w `process_product_job` w blok `try...except`.
- [x] Dodać obsługę `exceptions.ResourceExhausted` (błąd 429), która dodaje zadanie do `DELAYED_JOB_QUEUE_KEY` z opóźnieniem.
- [x] Dodać logikę aktualizacji licznika `processed_products` w pliku `status.json` po pomyślnym wykonaniu zadania.
- [x] Usunąć starą funkcję `run_generation_for_session`.

### Krok 4: Poprawa Jakości
- [x] Zmienić wartość `TEXT_ANALYSIS_MODEL` na `"gemini-2.5-pro"`.

### Krok 5: Weryfikacja i Sprzątanie
- [x] Przejrzeć cały przepływ i upewnić się, że stany (`pending`, `processing`, `complete`) są poprawnie zarządzane na poziomie sesji.
- [x] Zweryfikować, czy błędy są poprawnie zapisywane w pliku `status.json`.
