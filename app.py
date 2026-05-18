from flask import Flask, render_template, jsonify, Response, stream_with_context, send_file, request

# ✅ Jeden nowy SDK do wszystkiego (tekst + obrazy)
import google.generativeai as genai
from google.generativeai import types as genai_types

import os
import io
import time
import random
import json
import queue
import threading
import zipfile
import shutil
from PIL import Image
from datetime import datetime
import re
import httpx
import xml.etree.ElementTree as ET

app = Flask(__name__)

# ============== CONFIGURATION ==============

# API Keys
GEMINI_KEY = os.environ.get('GEMINI_API_KEY')
if not GEMINI_KEY:
    raise ValueError("Nie znaleziono klucza API. Ustaw zmienną środowiskową GEMINI_API_KEY.")

# ── Modele ──────────────────────────────────────────────────────────────────
TEXT_ANALYSIS_MODEL    = "gemini-2.5-flash"          # do analizy zdjęć i generowania promptów
IMAGE_GENERATION_MODEL = "gemini-2.5-flash-image"

# ── Obrazy referencyjne ──────────────────────────────────────────────────────
MAX_REFERENCE_IMAGES = 5

# ── Rozmiar i proporcje generowanych obrazów ──────────────────────────────────
IMAGE_ASPECT_RATIO = "1:1"
IMAGE_SIZE = "None"

# ── Retry ─────────────────────────────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_DELAY_BASE = 2
RETRY_DELAY_MAX = 120

# Foldery robocze
TEMP_FOLDER   = os.path.join('/tmp', 'product_processor')
OUTPUT_FOLDER = os.path.join('/tmp', 'product_output')

os.makedirs(TEMP_FOLDER,   exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

gemini_client = genai.Client(api_key=GEMINI_KEY)

# ============== HELPER FUNCTIONS ==============

def exponential_backoff_delay(attempt):
    """Calculate exponential backoff delay with jitter"""
    delay = min(RETRY_DELAY_BASE * (2 ** attempt), RETRY_DELAY_MAX)
    jitter = random.uniform(0, delay * 0.1)
    return delay + jitter

def download_image_from_url(url, folder):
    """Pobiera obraz z URL i zapisuje go w podanym folderze.""" 
    try:
        response = httpx.get(url, follow_redirects=True, timeout=15)
        response.raise_for_status()  # Rzuć wyjątek dla kodów 4xx/5xx

        filename = os.path.basename(url.split('?')[0])
        if not filename:
            filename = f"image_{int(time.time())}.jpg"
        
        filepath = os.path.join(folder, filename)
        
        with open(filepath, 'wb') as f:
            f.write(response.content)
            
        print(f"✅ Pobrano obraz: {url} -> {filepath}")
        return filepath
    except httpx.HTTPStatusError as e:
        print(f"❌ Błąd HTTP podczas pobierania {url}: {e}")
        return None
    except Exception as e:
        print(f"❌ Nieoczekiwany błąd podczas pobierania {url}: {e}")
        return None

def parse_xml_for_image_urls(xml_path):
    """
    Parsuje plik XML w poszukiwaniu adresów URL obrazów.
    """
    urls = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        # Bardziej generyczne wyszukiwanie, aby obsłużyć różne formaty XML
        for elem in root.iter():
            if 'image' in elem.tag.lower() and elem.text and elem.text.strip().startswith('http'):
                 urls.append(elem.text.strip())
            elif elem.get('url'): # Dla tagów typu <enclosure url="..." />
                 if elem.get('url').strip().startswith('http'):
                    urls.append(elem.get('url').strip())
        return list(set(urls)) # Zwróć unikalne URL-e
    except ET.ParseError as e:
        print(f"❌ Błąd parsowania XML: {e}")
        return []
    except Exception as e:
        print(f"❌ Nieoczekiwany błąd podczas parsowania XML: {e}")
        return []


# ============== ANALIZA GEMINI (tekst) ==============

class GeminiAnalysisError(Exception):
    """Custom exception for Gemini analysis failures"""
    pass

def analyze_product_with_gemini(images_pil, product_name):
    """
    Analiza produktu przez Gemini - zwraca 4 prompty.
    """
    analysis_prompt = f"""Działaj jako ekspert fotografii produktowej i inżynier promptów AI. Twoim zadaniem jest stworzenie profesjonalnych promptów do wygenerowania scen z produktem dla produktu widocznego na załączonym zdjęciu. Nazwa produktu (czasami też dodatkowe informacje): {product_name}.

KROK 1: ANALIZA OBRAZU
Dokładnie przeanalizuj załączone zdjęcie. Zidentyfikuj:
1. Wygląd produktu: kształt opakowania, kolor, materiał (szkło, plastik, metal?), kolor etykiety i tekstu.
2. Sugestie z etykiety: czy są tam nazwy owoców, roślin, minerałów lub słowa kluczowe (np. "Gold", "Eco", "Aqua")? To posłuży do doboru motywu tła.

KROK 2: OPRACOWANIE KONCEPCJI (Przykłdowe opracowanie koncepcji jeżeli produkt nie będzie do niej pasował opracuj swoje koncepcje)
Na podstawie analizy opracuj 4 różne koncepcje tła, które najlepiej sprzedadzą ten konkretny produkt.

KROK 3: GENEROWANIE PROMPTÓW (OUTPUT)
Wygeneruj 4 gotowe prompty w języku angielskim (standard dla generatorów obrazów). Każdy prompt musi być skonstruowany wg schematu:
"[Szczegółowy opis wizualny produktu z analizy], placed on [opis tła i podłoża], surrounded by [elementy dodatkowe/rekwizyty], [rodzaj oświetlenia], [styl i technikalne parametry: 8k, photorealistic, product photography, sharp focus, depth of field]".

WAŻNE: Zwróć TYLKO 4 prompty, każdy w nowej linii, bez numeracji, bez dodatkowych komentarzy.

FORMAT ODPOWIEDZI:
[Prompt 1]
[Prompt 2]
[Prompt 3]
[Prompt 4]"""

    print(f"   🤖 Analizuję {len(images_pil)} zdjęć produktu: {product_name}")

    try:
        contents = [analysis_prompt] + images_pil
        response = text_model.generate_content(
            contents=contents,
        )

        if not response or not response.text:
            raise GeminiAnalysisError(f"Gemini zwrócił pustą odpowiedź dla produktu {product_name}")

        prompts_text = response.text.strip()
        prompts = [p.strip() for p in prompts_text.splitlines() if p.strip() and len(p.strip()) > 20]
        prompts = [p for p in prompts if not p.startswith('#') and not p.startswith('**')]

        if len(prompts) < 4:
            print("⚠️ Gemini zwrócił za mało promptów, dodaję fallback")
            base_prompts = [" "," "," "," "]
            while len(prompts) < 4:
                prompts.append(base_prompts[len(prompts)])

        print(f"✅ Wygenerowano {len(prompts[:4])} promptów")
        return prompts[:4]

    except Exception as e:
        error_msg = str(e)
        if "400" in error_msg or "Bad Request" in error_msg or "safety" in error_msg.lower():
            raise GeminiAnalysisError(f"❌ BŁĄD: Gemini odrzucił obraz produktu '{product_name}' (naruszenie polityki bezpieczeństwa). Błąd: {error_msg}")
        raise GeminiAnalysisError(f"❌ BŁĄD: Błąd analizy Gemini dla produktu '{product_name}': {error_msg}")

def analyze_product_for_two_prompts_xml(images_pil, product_name):
    """
    Analiza produktu przez Gemini w celu wygenerowania 2 promptów (studyjnego i lifestylowego).
    """
    analysis_prompt = f"""Jesteś ekspertem od fotografii produktowej i inżynierii promptów AI. Twoim celem jest stworzenie dwóch, precyzyjnych promptów w języku angielskim na podstawie załączonego zdjęcia produktu i jego nazwy: '{product_name}'.

KROK 1: DOKŁADNA ANALIZA PRODUKTU
Na podstawie zdjęcia, zidentyfikuj kluczowe cechy produktu:
-   Wygląd: Jaki ma kształt, kolorystykę, materiał (szkło, metal, plastik)?
-   Etykieta: Jakie słowa, ikony, składniki (owoce, zioła, minerały) się na niej znajdują? To klucz do inspiracji.

KROK 2: STWORZENIE 2 PROMPTÓW
Wygeneruj DOKŁADNIE DWA prompty, każdy w nowej linii. Trzymaj się formatu i nie dodawaj żadnych dodatkowych opisów, tytułów ani numeracji.

PROMPT 1: WIZUALIZACJA STUDYJNA
Schemat: `[Szczegółowy opis produktu], on a minimalist studio pedestal, surrounded by [elementy z etykiety], clean studio lighting, 8k, photorealistic, product photography, sharp focus.`

PROMPT 2: WIZUALIZACJA LIFESTYLE
Schemat: `[Szczegółowy opis produktu] in a real-life setting, [opis otoczenia i kontekstu użycia], with [naturalne rekwizyty], natural morning light, depth of field, 8k, photorealistic.`

FORMAT WYJŚCIOWY (TYLKO TO):
[Prompt 1: Studyjny]
[Prompt 2: Lifestylowy]"""

    print(f"   🤖 Analizuję {len(images_pil)} zdjęć produktu (XML workflow): {product_name}")
    try:
        contents = [analysis_prompt] + images_pil
        response = text_model.generate_content(
            contents=contents,
        )

        if not response or not response.text:
            raise GeminiAnalysisError(f"Gemini zwrócił pustą odpowiedź (XML workflow) dla {product_name}")

        prompts_text = response.text.strip()
        prompts = [p.strip() for p in prompts_text.splitlines() if p.strip() and len(p.strip()) > 20]
        
        if len(prompts) < 2:
            print("⚠️ Gemini zwrócił mniej niż 2 prompty, dodaję fallbacki")
            fallback_prompts = [
                f"{product_name}, on a minimalist studio pedestal, clean studio lighting, 8k, photorealistic, product photography, sharp focus.",
                f"{product_name} in a real-life setting, natural morning light, depth of field, 8k, photorealistic."
            ]
            while len(prompts) < 2:
                prompts.append(fallback_prompts[len(prompts)])
        
        print(f"✅ Wygenerowano 2 prompty (XML workflow)")
        return prompts[:2]

    except Exception as e:
        error_msg = str(e)
        if "400" in error_msg or "Bad Request" in error_msg or "safety" in error_msg.lower():
            raise GeminiAnalysisError(f"❌ BŁĄD BEZPIECZEŃSTWA: Gemini odrzucił obraz produktu '{product_name}' (XML workflow). Błąd: {error_msg}")
        raise GeminiAnalysisError(f"❌ BŁĄD ANALIZY: Błąd Gemini dla produktu '{product_name}' (XML workflow): {error_msg}")

# ============== GENEROWANIE OBRAZÓW GEMINI ==============

def generate_gemini_image_sync(prompt, index, product_name, reference_images_pil, progress_callback=None):
    """
    Generowanie obrazu przez Gemini Image API.
    """
    filename = f"creative_{index}_{int(time.time())}.png"
    filepath = os.path.join(OUTPUT_FOLDER, filename)

    contents = [prompt]
    if reference_images_pil:
        refs_to_use = reference_images_pil[:MAX_REFERENCE_IMAGES]
        contents.extend(refs_to_use)

    image_config_kwargs = {"aspect_ratio": IMAGE_ASPECT_RATIO}
    if IMAGE_SIZE and "pro" in IMAGE_GENERATION_MODEL:
        image_config_kwargs["image_size"] = IMAGE_SIZE

    image_config = genai_types.ImageConfig(**image_config_kwargs)
    gen_config = genai_types.GenerateContentConfig(response_modalities=['TEXT', 'IMAGE'], image_config=image_config)

    for attempt in range(MAX_RETRIES):
        try:
            if progress_callback:
                progress_callback(f"Generowanie kreacji #{index} dla {product_name}...")

            response = gemini_client.models.generate_content(
                model=IMAGE_GENERATION_MODEL,
                contents=contents,
                config=gen_config,
            )

            image_saved = False
            for part in response.parts:
                if part.inline_data is not None:
                    raw = part.inline_data.data
                    if isinstance(raw, str):
                        import base64 as _b64
                        raw = _b64.b64decode(raw)
                    pil_image = Image.open(io.BytesIO(raw))
                    pil_image.save(filepath)
                    image_saved = True
                    break

            if image_saved:
                if progress_callback:
                    progress_callback(f"Kreacja #{index} gotowa!")
                return {'index': index, 'filename': filename, 'filepath': filepath, 'prompt': prompt}

            time.sleep(exponential_backoff_delay(attempt))

        except Exception as e:
            # ... (error handling) ...
            time.sleep(exponential_backoff_delay(attempt))
    return None

def generate_batch_images(prompts, product_name, reference_images_pil, progress_callback=None):
    results = []
    for i, prompt in enumerate(prompts, 1):
        result = generate_gemini_image_sync(
            prompt=prompt, index=i, product_name=product_name,
            reference_images_pil=reference_images_pil, progress_callback=progress_callback
        )
        if result:
            results.append(result)
        if i < len(prompts):
            time.sleep(1)
    return results

# ============== API ENDPOINTS ==============

@app.route('/')
def index():
    return render_template('index_saas.html')

@app.route('/api/xml/start', methods=['POST'])
def xml_start_processing():
    """Krok 1: Rozpoczyna nową sesję przetwarzania XML i tworzy plik statusu."""
    if 'xml_file' not in request.files:
        return jsonify({'error': 'Brak pliku XML w zapytaniu'}), 400
    
    file = request.files['xml_file']
    if file.filename == '':
        return jsonify({'error': 'Nie wybrano pliku'}), 400

    if not file or not file.filename.endswith('.xml'):
        return jsonify({'error': 'Nieprawidłowy format pliku. Wymagany jest plik .xml'}), 400

    session_id = f"session_{int(time.time())}_{random.randint(1000, 9999)}"
    session_folder = os.path.join(TEMP_FOLDER, session_id)
    os.makedirs(session_folder, exist_ok=True)

    xml_path = os.path.join(session_folder, 'original.xml')
    file.save(xml_path)

    image_urls = parse_xml_for_image_urls(xml_path)
    if not image_urls:
        return jsonify({'error': 'Nie znaleziono linków do obrazów w pliku XML'}), 400

    # Utwórz plik status.json
    status_data = {
        'status': 'pending',
        'total_images': len(image_urls),
        'processed_images': 0,
        'image_urls': image_urls,
        'errors': []
    }
    with open(os.path.join(session_folder, 'status.json'), 'w') as f:
        json.dump(status_data, f)

    return jsonify({
        'status': 'Sesja rozpoczęta',
        'session_id': session_id,
        'image_urls_found': image_urls,
        'image_count': len(image_urls)
    })

def run_generation_thread(session_id, resolution, aspect_ratio, styles):
    """Logika generowania uruchamiana w osobnym wątku, z aktualizacją status.json."""
    print(f"[{session_id}] 🔥 Rozpoczynam wątek generowania...")
    session_folder = os.path.join(TEMP_FOLDER, session_id)
    status_path = os.path.join(session_folder, 'status.json')

    def update_status(status=None, processed_increment=0, error=None):
        with open(status_path, 'r+') as f:
            data = json.load(f)
            if status:
                data['status'] = status
            if processed_increment:
                data['processed_images'] += processed_increment
            if error:
                data['errors'].append(error)
            f.seek(0)
            json.dump(data, f)
            f.truncate()

    try:
        update_status(status='processing')
        feed_folder = os.path.join(session_folder, 'feed')
        output_folder = os.path.join(session_folder, 'output')
        os.makedirs(output_folder, exist_ok=True)

        global IMAGE_ASPECT_RATIO, IMAGE_SIZE
        IMAGE_ASPECT_RATIO = aspect_ratio
        IMAGE_SIZE = resolution

        image_files = [f for f in os.listdir(feed_folder) if os.path.isfile(os.path.join(feed_folder, f))]

        for image_file in image_files:
            try:
                print(f"[{session_id}] processing {image_file}...")
                image_path = os.path.join(feed_folder, image_file)
                product_name = os.path.splitext(image_file)[0]
                
                with Image.open(image_path) as img:
                    pil_img = img.convert('RGB')
                    base_prompts = analyze_product_for_two_prompts_xml([pil_img], product_name)

                    final_prompts = []
                    if styles and len(styles) >= 2:
                        final_prompts.append(f"{base_prompts[0]}, {styles[0]}")
                        final_prompts.append(f"{base_prompts[1]}, {styles[1]}")
                    else:
                        final_prompts = base_prompts

                    creatives = generate_batch_images(
                        prompts=final_prompts, product_name=product_name, reference_images_pil=[pil_img]
                    )
                    
                    for creative in creatives:
                        new_path = os.path.join(output_folder, creative['filename'])
                        shutil.move(creative['filepath'], new_path)
                    
                    update_status(processed_increment=1)
                    print(f"[{session_id}] ✅ Zakończono: {image_file}")

            except Exception as e:
                error_msg = f"Błąd podczas przetwarzania {image_file}: {e}"
                print(f"[{session_id}] ❌ {error_msg}")
                update_status(error=error_msg)
        
        update_status(status='complete')
        print(f"[{session_id}] ✅ Wątek generowania zakończony.")

    except Exception as e:
        error_msg = f"Krytyczny błąd wątku generowania: {e}"
        print(f"[{session_id}] ❌ {error_msg}")
        update_status(status='failed', error=error_msg)

@app.route('/api/xml/generate', methods=['POST'])
def xml_generate_creations():
    """Krok 2: Pobiera obrazy i rozpoczyna proces generowania w tle."""
    data = request.json
    session_id = data.get('session_id')
    resolution = data.get('resolution', '1K')
    aspect_ratio = data.get('aspect_ratio', '1:1')
    styles = data.get('styles', [])

    if not session_id or not os.path.exists(os.path.join(TEMP_FOLDER, session_id)):
        return jsonify({'error': 'Nieprawidłowe ID sesji'}), 404

    session_folder = os.path.join(TEMP_FOLDER, session_id)
    status_path = os.path.join(session_folder, 'status.json')

    with open(status_path, 'r') as f:
        status_data = json.load(f)
    
    feed_folder = os.path.join(session_folder, 'feed')
    os.makedirs(feed_folder, exist_ok=True)

    for url in status_data['image_urls']:
        download_image_from_url(url, feed_folder)
    
    thread = threading.Thread(target=run_generation_thread, args=(session_id, resolution, aspect_ratio, styles))
    thread.start()

    return jsonify({
        'status': 'Przetwarzanie rozpoczęte w tle',
        'session_id': session_id
    })

@app.route('/api/xml/status/<session_id>', methods=['GET'])
def get_xml_status(session_id):
    """Krok 3: Zwraca status przetwarzania sesji."""
    session_folder = os.path.join(TEMP_FOLDER, session_id)
    status_path = os.path.join(session_folder, 'status.json')

    if not os.path.exists(status_path):
        return jsonify({'error': 'Nieprawidłowe ID sesji'}), 404

    with open(status_path, 'r') as f:
        status_data = json.load(f)

    if status_data['status'] == 'complete' and 'download_url' not in status_data:
        output_folder = os.path.join(session_folder, 'output')
        if os.path.exists(output_folder) and os.listdir(output_folder):
            # Logika pakowania do ZIP, którą mamy z poprzednich wersji
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_filename = f"kreacje_{session_id}_{timestamp}.zip"
            zip_path = os.path.join(TEMP_FOLDER, zip_filename)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(output_folder):
                    for file in files:
                        zipf.write(os.path.join(root, file), file)
            status_data['download_url'] = f'/api/download/{zip_filename}'
            with open(status_path, 'w') as f:
                json.dump(status_data, f)

    return jsonify(status_data)


@app.route('/api/process-upload', methods=['POST'])
def process_upload():
    # Logika dla trybu "custom" (pozostaje bez zmian na razie)
    return jsonify({'status': 'Endpoint dla uploadu customowego - do implementacji'})

@app.route('/api/download/<filename>')
def download_zip(filename):
    try:
        filepath = os.path.join(TEMP_FOLDER, filename)
        if os.path.exists(filepath):
            return send_file(filepath, as_attachment=True, download_name=filename, mimetype='application/zip')
        return jsonify({'error': 'Plik nie istnieje'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8003, debug=True)
:
    app.run(host='0.0.0.0', port=8003, debug=True)
