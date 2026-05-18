from flask import Flask, render_template, jsonify, Response, send_file, request
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

app = Flask(__name__)

# ============== KONFIGURACJA ==============

# Automatycznie pobiera klucz z os.environ["GEMINI_API_KEY"]
client = genai.Client()

# Modele z rodziny Gemini 2.5
TEXT_ANALYSIS_MODEL = "gemini-2.5-flash"
IMAGE_GENERATION_MODEL = "gemini-2.5-flash-image"

# Foldery tymczasowe
TEMP_FOLDER = os.path.join('/tmp', 'product_processor')
os.makedirs(TEMP_FOLDER, exist_ok=True)


# ============== FUNKCJE POMOCNICZE ==============

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

def parse_xml_for_image_urls(xml_path):
    urls = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for elem in root.iter():
            if 'image' in elem.tag.lower() and elem.text and elem.text.strip().startswith('http'):
                 urls.append(elem.text.strip())
            elif elem.get('url') and elem.get('url').strip().startswith('http'):
                 urls.append(elem.get('url').strip())
        return list(set(urls))
    except Exception as e:
        print(f"❌ Błąd parsowania XML: {e}")
        return []


# ============== LOGIKA AI ==============

def analyze_product_for_two_prompts_xml(images_pil, product_name):
    """
    Analiza zdjęcia wejściowego przez model Gemini w celu uzyskania 2 promptów.
    WAŻNE: Podmień ten tekst na swój prawdziwy, długi prompt. 
    """
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
            contents=contents
        )
        
        if not response or not response.text:
            raise Exception(f"Gemini zwrócił pustą odpowiedź dla {product_name}")
            
        prompts = [p.strip() for p in response.text.splitlines() if p.strip()]
        return prompts[:2]
    except Exception as e:
        raise Exception(f"Błąd analizy Gemini dla '{product_name}': {e}")


def generate_gemini_image_sync(prompt, index, product_name, reference_image):
    """
    Wywołanie modelu Nano Banana do WYGENEROWANIA obrazu.
    Przekazujemy tu zarówno prompt określający tło/styl, jak i samo zdjęcie produktu z XML.
    """
    safe_product_name = re.sub(r'[^\w\-_\.]', '', product_name)
    filename = f"{safe_product_name}_creative_{index}_{int(time.time())}.jpeg"
    
    try:
        # PAKIET DANYCH: Wysyłamy tekst (prompt) ORAZ obraz referencyjny produktu w jednej liście `contents`
        response = client.models.generate_content(
            model=IMAGE_GENERATION_MODEL,
            contents=[prompt, reference_image],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"]
            )
        )
        
        img_bytes = None
        
        if hasattr(response, 'generated_images') and response.generated_images:
            img_bytes = response.generated_images[0].image.image_bytes
        elif response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
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
    if 'xml_file' not in request.files: return jsonify({'error': 'Brak pliku XML'}), 400
    file = request.files['xml_file']
    if not file or not file.filename.endswith('.xml'): return jsonify({'error': 'Wymagany plik .xml'}), 400

    session_id = f"session_{int(time.time())}"
    session_folder = os.path.join(TEMP_FOLDER, session_id)
    os.makedirs(session_folder, exist_ok=True)

    xml_path = os.path.join(session_folder, 'original.xml')
    file.save(xml_path)

    image_urls = parse_xml_for_image_urls(xml_path)
    if not image_urls: return jsonify({'error': 'Nie znaleziono URLi obrazów w XML'}), 400

    status_data = {
        'status': 'pending', 'total_images': len(image_urls),
        'processed_images': 0, 'image_urls': image_urls, 'errors': []
    }
    with open(os.path.join(session_folder, 'status.json'), 'w') as f:
        json.dump(status_data, f)

    return jsonify({'status': 'Sesja rozpoczęta', 'session_id': session_id, 'image_count': len(image_urls)})

def run_generation_thread(session_id, resolution, aspect_ratio, styles):
    session_folder = os.path.join(TEMP_FOLDER, session_id)
    status_path = os.path.join(session_folder, 'status.json')

    def update_status(status=None, processed_increment=0, error=None):
        with open(status_path, 'r+') as f:
            data = json.load(f)
            if status: data['status'] = status
            if processed_increment: data['processed_images'] += processed_increment
            if error: data['errors'].append(error)
            f.seek(0); json.dump(data, f); f.truncate()

    try:
        update_status(status='processing')
        feed_folder = os.path.join(session_folder, 'feed')
        output_folder = os.path.join(session_folder, 'output')
        os.makedirs(feed_folder, exist_ok=True); os.makedirs(output_folder, exist_ok=True)

        with open(status_path, 'r') as f: status_data = json.load(f)
        for url in status_data['image_urls']:
            download_image_from_url(url, feed_folder)
        
        image_files = [f for f in os.listdir(feed_folder) if os.path.isfile(os.path.join(feed_folder, f))]

        for image_file in image_files:
            try:
                image_path = os.path.join(feed_folder, image_file)
                product_name = os.path.splitext(image_file)[0]
                with Image.open(image_path) as img:
                    pil_img = img.convert('RGB')
                    
                    # 1. Krok: Gemini odczytuje obrazek i tworzy prompty
                    base_prompts = analyze_product_for_two_prompts_xml([pil_img], product_name)
                    
                    final_prompts = []
                    if styles and len(styles) >= 2:
                        final_prompts.extend([f"{base_prompts[0]}, {styles[0]}", f"{base_prompts[1]}, {styles[1]}"])
                    else:
                        final_prompts = base_prompts

                    # 2. Krok: Nano Banana tworzy obrazki na podstawie wygenerowanego promptu ORAZ oryginalnego zdjęcia (pil_img)
                    for i, prompt in enumerate(final_prompts):
                        generated_image, filename = generate_gemini_image_sync(prompt, i, product_name, pil_img)
                        if generated_image and filename:
                            generated_image.save(os.path.join(output_folder, filename))
                            
                update_status(processed_increment=1)
            except Exception as e:
                update_status(error=str(e))
        
        update_status(status='complete')

    except Exception as e:
        update_status(status='failed', error=str(e))

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
    if os.path.exists(filepath): return send_file(filepath, as_attachment=True)
    return jsonify({'error': 'Plik nie istnieje'}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8003)), debug=False)
