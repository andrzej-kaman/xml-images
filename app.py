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
    Uwaga: Standardowe API Imagen (imagen-3.0-generate