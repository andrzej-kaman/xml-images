from flask import Flask, render_template, jsonify, Response, stream_with_context, send_file, request
from google import genai
import os
import io
import time
import random
import json
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

# Nowa biblioteka google-genai automatycznie używa zmiennej środowiskowej GEMINI_API_KEY.
client = genai.Client()

# Zaktualizowane Modele
TEXT_ANALYSIS_MODEL    = "gemini-2.5-flash"        # Szybki model multimodalny do czytania obrazów i generowania tekstu
IMAGE_GENERATION_MODEL = "gemini-2.5-flash-image" # Dedykowany model graficzny (Imagen) do generowania obrazów

MAX_REFERENCE_IMAGES = 5

# Foldery
TEMP_FOLDER   = os.path.join('/tmp', 'product_processor')
os.makedirs(TEMP_FOLDER,   exist_ok=True)

# ============== HELPER FUNCTIONS ==============

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

# ============== GEMINI & IMAGEN FUNCTIONS ==============

class GeminiAnalysisError(Exception):
    pass

def analyze_product_for_two_prompts_xml(images_pil, product_name):
    analysis_prompt = f"""Jesteś ekspertem od fotografii produktowej... 
    (Tutaj wstaw pełną treść swojego promptu, instrukcji analizy, 
    wymogów kompozycji itp.) 
    ...[Prompt 2: Lifestylowy]"""
    try:
        contents = [analysis_prompt] + images_pil
        response = client.models.generate_content(
            model=TEXT_ANALYSIS_MODEL,
            contents=contents
        )
        
        if not response or not response.text:
            raise GeminiAnalysisError(f"Gemini zwrócił pustą odpowiedź dla {product_name}")
            
        prompts = [p.strip() for p in response.text.splitlines() if p.strip()]
        return prompts[:2]
    except Exception as e:
        raise GeminiAnalysisError(f"Błąd analizy Gemini dla '{product_name}': {e}")

def generate_gemini_image_sync(prompt, index, product_name):
    """
    Uwaga: Standardowe API Imagen (imagen-3.0-generate-001) generuje obraz z tekstu. 
    Bezpośrednie przekazanie zdjęcia referencyjnego wymaga specjalnych metod edycji obrazów.
    W tej funkcji skupiamy się na wygenerowaniu obrazu na podstawie wygenerowanego promptu.
    """
    safe_product_name = re.sub(r'[^\w\-_\.]', '', product_name)
    filename = f"{safe_product_name}_creative_{index}_{int(time.time())}.jpeg"
    
    try:
        result = client.models.generate_images(
            model=IMAGE_GENERATION_MODEL,
            prompt=prompt,
            config=dict(
                number_of_images=1,
                output_mime_type="image/jpeg",
                aspect_ratio="1:1"
            )
        )
        
        for generated_image in result.generated_images:
            img_bytes = generated_image.image.image_bytes
            pil_image = Image.open(io.BytesIO(img_bytes))
            return pil_image, filename
        
        print(f"Error: Nie znaleziono danych obrazu w odpowiedzi dla promptu: {prompt}")
        return None, None

    except Exception as e:
        print(f"Error during image generation: {e}")
        return None, None

# ============== API ENDPOINTS & WORKFLOW ==============

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
                    # Analiza obrazu w celu uzyskania promptów
                    base_prompts = analyze_product_for_two_prompts_xml([pil_img], product_name)
                    
                    final_prompts = []
                    if styles and len(styles) >= 2:
                        final_prompts.extend([f"{base_prompts[0]}, {styles[0]}", f"{base_prompts[1]}, {styles[1]}"])
                    else:
                        final_prompts = base_prompts

                    # Generowanie obrazów na podstawie promptów
                    for i, prompt in enumerate(final_prompts):
                        # Zauważ zmianę argumentów - Imagen domyślnie korzysta z samego promptu tekstowego
                        pil_image, filename = generate_gemini_image_sync(prompt, i, product_name)
                        if pil_image and filename:
                            pil_image.save(os.path.join(output_folder, filename))
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
    app.run(host='0.0.0.0', port=8003, debug=True)