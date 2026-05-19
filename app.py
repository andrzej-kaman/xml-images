from flask import Flask, render_template, jsonify, Response, send_file, request, after_this_request
from google import genai
from google.genai import types
import os
import io
import time
import json
import threading
import zipfile
from PIL import Image
import re
import httpx
import xml.etree.ElementTree as ET
import shutil
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import pathlib
import atexit


app = Flask(__name__)

# ============== KONFIGURACJA ==============

# W chmurze (Render.com) ścieżkę do klucza podajesz w zmiennej GOOGLE_APPLICATION_CREDENTIALS
client = genai.Client(
    vertexai=True,
    project="eco-league-496710-c0",
    location="us-central1"
)

# Modele AI
TEXT_ANALYSIS_MODEL = "gemini-2.5-flash"
IMAGE_GENERATION_MODEL = "gemini-2.5-flash-image" # Nano Banana

# Foldery tymczasowe
TEMP_FOLDER = 'temp_files'
os.makedirs(TEMP_FOLDER, exist_ok=True)

# Definiujemy globalne ustawienia wyłączające filtry bezpieczeństwa (dla obu modeli)
GLOBAL_SAFETY_SETTINGS = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
]


# ============== FUNKCJE POMOCNICZE ==============

def cleanup_old_sessions():
    """Skanuje folder tymczasowy i usuwa sesje starsze niż 45 minut."""
    print("🧹 Uruchamiam zadanie czyszczenia starych sesji (kryterium: 45 minut)...")
    temp_path = pathlib.Path(TEMP_FOLDER)
    cutoff = datetime.now() - timedelta(minutes=45)

    for path in temp_path.iterdir():
        if path.is_dir() and path.name.startswith('session_'):
            try:
                dir_time = datetime.fromtimestamp(path.stat().st_mtime)
                if dir_time < cutoff:
                    shutil.rmtree(path)
                    print(f"🗑️ Usunięto starą sesję (starsza niż 45 min): {path.name}")
            except Exception as e:
                print(f"❌ Błąd podczas usuwania folderu {path.name}: {e}")
        elif path.is_file() and path.name.startswith('kreacje_') and path.name.endswith('.zip'):
             try:
                file_time = datetime.fromtimestamp(path.stat().st_mtime)
                if file_time < cutoff:
                    os.remove(path)
                    print(f"🗑️ Usunięto stary plik ZIP (starszy niż 45 min): {path.name}")
             except Exception as e:
                print(f"❌ Błąd podczas usuwania pliku {path.name}: {e}")

def download_image_from_url(url, folder):
    try:
        response = httpx.get(url, follow_redirects=True, timeout=15)
        response.raise_for_status()
        filename = os.path.basename(url.split('?')[0]) or f"image_{int(time.time())}.jpg"
        filepath = os.path.join(folder, filename)
        with open(filepath, 'wb') as f:
            f.write(response.content)
        print(f"✅ Pobrano obraz: {url} -> {filepath}")
        return filepath
    except Exception as e:
        print(f"❌ Błąd podczas pobierania {url}: {e}")
        return None

def parse_xml_for_products_with_images(xml_path):
    """Parsuje XML w poszukiwaniu produktów i ich obrazów, obsługując przestrzeń nazw 'g'."""
    products = []
    try:
        namespaces = {'g': 'http://base.google.com/ns/1.0'}
        ET.register_namespace('g', namespaces['g'])

        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Bardziej precyzyjne wyszukiwanie <item> wewnątrz <channel>
        channel = root.find('channel')
        if channel is not None:
            product_nodes = channel.findall('item')
        else:
            # Fallback for simpler XML structures
            product_nodes = root.findall('.//item') + root.findall('.//product')

        if not product_nodes:
            product_nodes = [root]

        for i, prod_node in enumerate(product_nodes):
            urls = []
            for elem in prod_node.iter():
                tag = elem.tag.split('}')[-1]
                if 'image_link' in tag or 'additional_image_link' in tag:
                    if elem.text and elem.text.strip().startswith('http'):
                        urls.append(elem.text.strip())
                elif 'image' in tag and elem.text and elem.text.strip().startswith('http'):
                     urls.append(elem.text.strip())
                elif elem.get('url') and elem.get('url').strip().startswith('http'):
                     urls.append(elem.get('url').strip())

            if urls:
                unique_urls = list(dict.fromkeys(urls))

                name_node = prod_node.find('g:title', namespaces) or prod_node.find('title')
                if name_node is None:
                     name_node = prod_node.find('.//title')

                product_name = name_node.text.strip() if name_node is not None and name_node.text else f"Produkt {i+1}"

                products.append({
                    "name": product_name,
                    "image_urls": unique_urls[:4]
                })

        print(f"INFO: Znaleziono {len(products)} produktów. Zwracam maksymalnie 10.")
        return products[:10]

    except ET.ParseError as e:
        print(f"❌ Błąd parsowania XML: {e}")
        return []
    except Exception as e:
        print(f"❌ Nieoczekiwany błąd w parse_xml_for_products_with_images: {e}")
        return []


# ============== LOGIKA AI ==============

def analyze_product_for_two_prompts_xml(images_pil, product_name, styles=None):
    if styles and any(styles):
        style_1 = styles[0] if len(styles) > 0 else ""
        style_2 = styles[1] if len(styles) > 1 else style_1
        
        analysis_prompt = f"""Jesteś ekspertem od fotografii produktowej.
Przeanalizuj załączone zdjęcie produktu. Następnie stwórz 2 precyzyjne prompty w języku angielskim dla generatora obrazów.

ZASADA: Najpierw dokładnie opisz fizyczny wygląd produktu ze zdjęcia (materiał, kształt, kolory, widoczne etykiety), a następnie umieść ten produkt DOKŁADNIE w takim otoczeniu/stylu:
- Prompt 1 ma mieć otoczenie: "{style_1}"
- Prompt 2 ma mieć otoczenie: "{style_2}"

Nie dodawaj żadnych wstępów. Zwróć tylko 2 linijki tekstu, każda z nich to osobny, spójny prompt gotowy do wygenerowania obrazu.
"""
    else:
        analysis_prompt = """Jesteś ekspertem od fotografii produktowej.
Przeanalizuj załączone zdjęcie produktu i wygeneruj dokładnie 2 precyzyjne prompty w języku angielskim, służące do wygenerowania nowego tła (lifestyle / aranżacja) dla tego produktu.

Wymagania:
1. Pierwszy prompt ma być klasyczny, czysty i minimalistyczny.
2. Drugi prompt ma być lifestylowy, pasujący do zastosowania produktu.
Nie dodawaj żadnych wstępów. Zwróć tylko 2 linijki tekstu, każda z nich to osobny prompt.
"""

    try:
        contents = [analysis_prompt] + images_pil
        response = client.models.generate_content(
            model=TEXT_ANALYSIS_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                safety_settings=GLOBAL_SAFETY_SETTINGS
            )
        )
        
        if not response or not response.text:
            raise Exception(f"Gemini zwrócił pustą odpowiedź dla {product_name}")
            
        prompts = [p.strip() for p in response.text.splitlines() if p.strip()]
        return prompts[:2]
    except Exception as e:
        raise Exception(f"Błąd analizy Gemini dla '{product_name}': {e}")


def generate_gemini_image_sync(prompt, index, product_name, reference_images, resolution=None, aspect_ratio=None):
    # Nazwa produktu jest już oczyszczona w wątku, ale dla pewności zostawiamy
    safe_product_name = re.sub(r'[^\w\-_\.]', '', product_name)
    filename = f"{safe_product_name}_creative_{index}_{int(time.time())}.jpeg"
    
    img_params = {}
    if aspect_ratio:
        img_params['aspect_ratio'] = aspect_ratio
    if resolution:
        img_params['image_size'] = resolution

    gen_config = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        safety_settings=GLOBAL_SAFETY_SETTINGS
    )
    
    if img_params:
        gen_config.image_config = types.ImageConfig(**img_params)
    
    try:
        # Tworzymy zawartość, dodając prompt i wszystkie obrazy referencyjne
        content_for_generation = [prompt] + reference_images

        response = client.models.generate_content(
            model=IMAGE_GENERATION_MODEL,
            contents=content_for_generation,
            config=gen_config
        )
        
        img_bytes = None
        
        if hasattr(response, 'generated_images') and response.generated_images:
            img_bytes = response.generated_images[0].image.image_bytes
        elif response.candidates:
            candidate = response.candidates[0]
            if not candidate.content:
                print(f"❌ Nano Banana odrzucił generowanie dla: {product_name}")
                print(f"Powód odrzucenia (finish_reason): {getattr(candidate, 'finish_reason', 'Brak informacji')}")
            elif candidate.content.parts:
                for part in candidate.content.parts:
                    if hasattr(part, 'inline_data') and part.inline_data:
                        img_bytes = part.inline_data.data
                        break
        
        if img_bytes:
            generated_pil = Image.open(io.BytesIO(img_bytes))
            return generated_pil, filename
            
        print(f"Błąd: Nie znaleziono danych obrazu w odpowiedzi Nano Banana dla promptu: {prompt}")
        return None, None

    except Exception as e:
        print(f"Błąd podczas generowania obrazu: {e}")
        return None, None


# ============== ENDPOINTY FLASK ==============

@app.route('/')
def index():
    return render_template('index_saas.html')

@app.route('/api/xml/start', methods=['POST'])
def xml_start_processing():
    session_id = f"session_{int(time.time())}"
    session_folder = os.path.join(TEMP_FOLDER, session_id)
    os.makedirs(session_folder, exist_ok=True)
    xml_path = os.path.join(session_folder, 'original.xml')

    try:
        if request.is_json and 'xml_url' in request.json:
            xml_url = request.json['xml_url']
            if not xml_url: return jsonify({'error': 'Podano pusty link (URL)'}), 400
            response = httpx.get(xml_url, follow_redirects=True, timeout=20)
            response.raise_for_status()
            with open(xml_path, 'wb') as f: f.write(response.content)
        elif 'xml_file' in request.files:
            file = request.files['xml_file']
            if not file or not file.filename or not file.filename.endswith('.xml'):
                return jsonify({'error': 'Wymagany jest plik .xml'}), 400
            file.save(xml_path)
        else:
            return jsonify({'error': 'Nie dostarczono pliku XML ani linku URL.'}), 400

        products = parse_xml_for_products_with_images(xml_path)
        if not products:
            return jsonify({'error': 'Nie znaleziono produktów z obrazami w pliku XML.'}), 400

        status_data = {
            'status': 'pending', 'total_products': len(products),
            'processed_products': 0, 'products': products, 'errors': []
        }
        with open(os.path.join(session_folder, 'status.json'), 'w') as f: json.dump(status_data, f)

        return jsonify({'session_id': session_id, 'product_count': len(products)})
    except Exception as e:
        if os.path.exists(session_folder): shutil.rmtree(session_folder)
        return jsonify({'error': f'Błąd inicjowania sesji XML: {e}'}), 500

@app.route('/api/manual/start', methods=['POST'])
def manual_start_processing():
    session_id = f"session_{int(time.time())}"
    session_folder = os.path.join(TEMP_FOLDER, session_id)
    feed_folder = os.path.join(session_folder, 'feed')
    os.makedirs(feed_folder, exist_ok=True)

    try:
        products = []
        # Używamy request.form.get() do bezpiecznego odczytu, aby uniknąć błędów
        i = 0
        while True:
            product_name_key = f'product_{i}_name'
            if product_name_key not in request.form:
                break
            
            product_name = request.form.get(product_name_key)
            if not product_name:
                i += 1
                continue # Pomiń produkty bez nazwy

            # Zbieranie plików dla danego produktu
            image_paths = []
            j = 0
            while True:
                file_key = f'product_{i}_file_{j}'
                if file_key not in request.files:
                    break
                
                file = request.files.get(file_key)
                if file and file.filename:
                    # Zabezpieczenie nazwy pliku i zapis
                    safe_filename = re.sub(r'[^a-zA-Z0-9_.-]', '', os.path.basename(file.filename))
                    file_path = os.path.join(feed_folder, f"{product_name.replace(' ', '_')}_{j}_{safe_filename}")
                    file.save(file_path)
                    image_paths.append(file_path)
                j += 1
            
            if image_paths:
                products.append({'name': product_name, 'image_paths': image_paths})
            i += 1
        
        if not products:
            return jsonify({'error': 'Nie dodano żadnych prawidłowych produktów z obrazami.'}), 400

        status_data = {
            'status': 'pending', 'total_products': len(products),
            'processed_products': 0, 'products': products, 'errors': []
        }
        with open(os.path.join(session_folder, 'status.json'), 'w') as f: json.dump(status_data, f)

        return jsonify({'session_id': session_id, 'product_count': len(products)})
    except Exception as e:
        if os.path.exists(session_folder): shutil.rmtree(session_folder)
        print(f"Błąd w manual_start_processing: {e}")
        return jsonify({'error': f'Błąd inicjowania sesji ręcznej: {e}'}), 500

def run_generation_thread(session_id, resolution, aspect_ratio, styles):
    session_folder = os.path.join(TEMP_FOLDER, session_id)
    status_path = os.path.join(session_folder, 'status.json')

    def update_status(status=None, processed_increment=0, error_details=None):
        with open(status_path, 'r+') as f:
            data = json.load(f)
            if status: data['status'] = status
            if processed_increment: data['processed_products'] += processed_increment
            if error_details: data['errors'].append(error_details)
            f.seek(0); json.dump(data, f); f.truncate()

    try:
        update_status(status='processing')
        feed_folder = os.path.join(session_folder, 'feed') # Upewniamy się, że istnieje
        output_folder = os.path.join(session_folder, 'output')
        os.makedirs(feed_folder, exist_ok=True); os.makedirs(output_folder, exist_ok=True)

        with open(status_path, 'r') as f: status_data = json.load(f)
        for product in status_data['products']:
            product_name = product['name']
            
            # === ADAPTACJA DLA DWÓCH TRYBÓW ===
            reference_image_paths = []
            # Tryb Ręczny: ścieżki już istnieją
            if 'image_paths' in product and product['image_paths']:
                reference_image_paths = product['image_paths']
            # Tryb XML: pobieramy obrazy z URL
            elif 'image_urls' in product and product['image_urls']:
                for url in product['image_urls']:
                    img_path = download_image_from_url(url, feed_folder)
                    if img_path:
                        reference_image_paths.append(img_path)
                    else:
                        update_status(error_details={'product_name': product_name, 'source_url': url, 'message': 'Nie udało się pobrać obrazu.', 'step': 'download'})

            if not reference_image_paths:
                update_status(error_details={'product_name': product_name, 'message': 'Brak obrazów referencyjnych dla produktu.', 'step': 'ai_processing'})
                update_status(processed_increment=1)
                continue

            try:
                pil_images = []
                for img_path in reference_image_paths:
                    with Image.open(img_path) as img:
                        img.thumbnail((1024, 1024))
                        pil_images.append(img.convert('RGB'))

                if not pil_images:
                    raise Exception("Nie udało się załadować żadnych obrazów PIL.")

                safe_product_name = re.sub(r'[^\w\-_\.]', '', product_name)
                final_prompts = analyze_product_for_two_prompts_xml(pil_images, product_name, styles)

                print(f"⏳ Czekam 10 sekund po analizie tekstu dla '{product_name}'...")
                time.sleep(10)

                for i, prompt in enumerate(final_prompts):
                    generated_image, filename = generate_gemini_image_sync(prompt, i, safe_product_name, pil_images, resolution, aspect_ratio)
                    if generated_image and filename:
                        generated_image.save(os.path.join(output_folder, filename))
                    
                    print(f"⏳ Czekam 15 sekund po wygenerowaniu obrazu dla '{product_name}'...")
                    time.sleep(15)

                update_status(processed_increment=1)
            except Exception as e:
                update_status(error_details={'product_name': product_name, 'message': str(e), 'step': 'ai_processing'})
                update_status(processed_increment=1) # Kontynuuj nawet po błędzie

        update_status(status='complete')
    except Exception as e:
        update_status(status='failed', error_details={'message': str(e), 'step': 'general'})

@app.route('/api/xml/generate', methods=['POST'])
def xml_generate_creations():
    data = request.json
    session_id = data.get('session_id')
    if not session_id or not os.path.exists(os.path.join(TEMP_FOLDER, session_id)):
        return jsonify({'error': 'Nieprawidłowe ID sesji'}), 404

    args = (session_id, data.get('resolution'), data.get('aspect_ratio'), data.get('styles'))
    thread = threading.Thread(target=run_generation_thread, args=args)
    thread.start()

    return jsonify({'status': 'Przetwarzanie rozpoczęte w tle', 'session_id': session_id})

@app.route('/api/xml/status/<session_id>', methods=['GET'])
def get_xml_status(session_id):
    status_path = os.path.join(TEMP_FOLDER, session_id, 'status.json')
    if not os.path.exists(status_path): return jsonify({'error': 'Nieprawidłowe ID sesji'}), 404

    with open(status_path, 'r') as f: data = json.load(f)

    if data['status'] == 'complete' and 'download_url' not in data:
        output_folder = os.path.join(TEMP_FOLDER, session_id, 'output')
        if os.path.exists(output_folder) and os.listdir(output_folder):
            zip_filename = f"kreacje_{session_id}.zip"
            zip_path = os.path.join(TEMP_FOLDER, zip_filename)
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for root, _, files in os.walk(output_folder):
                    for file in files:
                        zipf.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), output_folder))
            data['download_url'] = f'/api/download/{zip_filename}'
            with open(status_path, 'w') as f: json.dump(data, f)

    return jsonify(data)

@app.route('/api/download/<filename>')
def download_zip(filename):
    filepath = os.path.join(TEMP_FOLDER, filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Plik nie istnieje lub został już usunięty'}), 404

    match = re.search(r'kreacje_(session_\d+)\.zip', filename)
    if not match:
        @after_this_request
        def cleanup_zip(response):
            try:
                os.remove(filepath)
            except OSError as e:
                print(f"Błąd podczas usuwania pliku zip {filepath}: {e}")
        return response
    return send_file(filepath, as_attachment=True)

    session_id = match.group(1)
    session_folder = os.path.join(TEMP_FOLDER, session_id)

    @after_this_request
    def cleanup_session_data(response):
        try:
            if os.path.exists(session_folder):
                shutil.rmtree(session_folder)
                print(f"✅ Usunięto folder sesji: {session_folder}")
            if os.path.exists(filepath):
                os.remove(filepath)
                print(f"✅ Usunięto plik ZIP: {filepath}")
        except Exception as e:
            print(f"❌ Wystąpił błąd podczas czyszczenia plików dla sesji {session_id}: {e}")
        return response

    return send_file(filepath, as_attachment=True)

if __name__ == '__main__':
    # Konfiguracja i uruchomienie harmonogramu czyszczenia
    scheduler = BackgroundScheduler()
    # Uruchamiaj zadanie co 45 minut
    scheduler.add_job(func=cleanup_old_sessions, trigger="interval", minutes=45)
    scheduler.start()

    # Zapewnienie, że harmonogram zostanie poprawnie zamknięty przy wyjściu z aplikacji
    atexit.register(lambda: scheduler.shutdown())

    port = int(os.environ.get('PORT', 8003))
    app.run(host='0.0.0.0', port=port, debug=False)
