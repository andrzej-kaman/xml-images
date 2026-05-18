# Checklista Przebudowy Aplikacji (XML-Centric)

Ta checklista definiuje wszystkie kroki niezbędne do przebudowy aplikacji i wdrożenia nowego, zorientowanego na XML przepływu pracy.

---

### Faza 1: Backend - Refaktoryzacja i Nowe Fundamenty

-   [ ] **Kod:** Całkowite usunięcie logiki i endpointów związanych z integracją Google Drive z pliku `produkty.py`.
-   [ ] **Struktura:** Stworzenie dedykowanego folderu `feed/` do tymczasowego przechowywania pobranych obrazów.
-   [ ] **API:** Zaprojektowanie nowego, wieloetapowego endpointu API (np. `/api/xml/start`, `/api/xml/generate`, `/api/xml/status`), który obsłuży nowy proces.
-   [ ] **Logika:** Implementacja mechanizmu, który najpierw pobiera *wszystkie* zdjęcia z XML do `feed/`, a dopiero potem rozpoczyna ich przetwarzanie.
-   [ ] **Zarządzanie Modelami:** Stworzenie klasy lub modułu `GeminiManager`, który będzie abstrakcją nad modelami AI. Będzie on odpowiedzialny za:
    -   Przechowywanie nazw modeli (`gemini-2.5-flash`, `gemini-3-pro-image-preview`).
    -   Implementację mechanizmu `rate limiting` (odpowiednich opóźnień/pauz) w zależności od używanego modelu, aby unikać błędów API.

---

### Faza 2: Backend - Logika Głównego Procesu

-   [ ] **API:** Rozbudowa endpointu API, aby przyjmował od klienta parametry: rozdzielczość, proporcje i dwa wybrane style (lub style niestandardowe).
-   [ ] **Prompty:** Modyfikacja funkcji generującej prompty, aby uwzględniała dane z XML (opis produktu) oraz analizę obrazu.
-   [ ] **Generowanie:** Implementacja głównej pętli, która dla każdego obrazu w folderze `feed/`:
    1.  Wywołuje generator promptów.
    2.  Wywołuje generator obrazów dwa razy – raz dla każdego z podanych stylów, łącząc go z podstawowym promptem i obrazem produktu.
    3.  Zapisuje wyniki w strukturze gotowej do spakowania.

---

### Faza 3: Frontend - Nowy Interfejs Użytkownika

-   [ ] **Layout:** Całkowite usunięcie interfejsu związanego z Google Drive.
-   [ ] **Przepływ:** Stworzenie wieloekranowego interfejsu użytkownika:
    1.  **Ekran 1 (Upload):** Prosty formularz do przesyłania pliku XML.
    2.  **Ekran 2 (Ustawienia):** Formularz z opcjami wyboru rozdzielczości i proporcji.
    3.  **Ekran 3 (Style):** Interfejs do wyboru dwóch stylów z listy lub wpisania własnych.
    4.  **Ekran 4 (Progres):** Widok postępu generowania z paskiem postępu lub logiem operacji.
    5.  **Ekran 5 (Wyniki):** Galeria wygenerowanych obrazów z przyciskiem do pobrania ZIP.
-   [ ] **Logika JS:** Stworzenie nowego skryptu `app.js` (lub refaktoryzacja istniejącego), który będzie zarządzał stanem aplikacji (przechodzenie między ekranami) i komunikował się z nowymi endpointami API.

---

### Faza 4: Testowanie i Finalizacja

-   [ ] **Testy E2E:** Przeprowadzenie pełnych testów nowego przepływu XML – od wgrania pliku do pobrania wyników.
-   [ ] **Testy Trybu Custom:** Weryfikacja, czy uproszczony tryb "custom" nadal działa poprawnie.
-   [ ] **Walidacja:** Dodanie obsługi błędów (np. niepoprawny XML, niedziałające linki do zdjęć).
-   [ ] **Optymalizacja:** Przegląd kodu pod kątem wydajności i czytelności.
