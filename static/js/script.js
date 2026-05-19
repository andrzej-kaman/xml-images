// ==================== GLOBAL STATE & CONSTANTS ====================
let currentMode = 'xml';
let activeSessionId = null;
let pollInterval = null;

// ==================== DOMContentLoaded - INITIALIZATION ====================
document.addEventListener('DOMContentLoaded', () => {
    // --- THEME TOGGLE LOGIC ---
    const themeToggleButton = document.getElementById('theme-toggle-btn');
    const body = document.body;
    const themeIcon = themeToggleButton.querySelector('i');

    const applyTheme = (theme) => {
        body.classList.toggle('dark-mode', theme === 'dark');
        themeIcon.classList.toggle('fa-moon', theme === 'light');
        themeIcon.classList.toggle('fa-sun', theme === 'dark');
    };

    themeToggleButton.addEventListener('click', () => {
        const newTheme = body.classList.contains('dark-mode') ? 'light' : 'dark';
        localStorage.setItem('theme', newTheme);
        applyTheme(newTheme);
    });

    const savedTheme = localStorage.getItem('theme') || 'light';
    applyTheme(savedTheme);

    // --- APP LOGIC ---
    document.getElementById('tab-xml').addEventListener('click', () => switchMode('xml'));
    document.getElementById('tab-upload').addEventListener('click', () => switchMode('upload'));

    document.getElementById('startXmlBtn').addEventListener('click', handleXmlUpload);
    document.getElementById('xmlGenerateBtn').addEventListener('click', handleXmlGeneration);

    document.getElementById('addProductBtn').addEventListener('click', addProductCard);
    document.getElementById('manualSubmitBtn').addEventListener('click', showManualSettings);
    document.getElementById('manualGenerateBtn').addEventListener('click', handleManualGeneration);

    switchMode('xml');
    if (document.getElementById('productsContainer').children.length === 0) {
        addProductCard();
    }
});

// ==================== UI & VIEW MANAGEMENT ====================
function switchMode(mode) {
    currentMode = mode;
    document.getElementById('tab-xml').classList.toggle('active', mode === 'xml');
    document.getElementById('tab-upload').classList.toggle('active', mode === 'upload');
    document.getElementById('mode-xml').style.display = mode === 'xml' ? 'block' : 'none';
    document.getElementById('mode-upload').style.display = mode === 'upload' ? 'block' : 'none';
    showView(mode === 'xml' ? 'xml-step-upload' : 'manual-step-products');
    hideError();
}

function showView(viewId) {
    document.querySelectorAll('.app-step').forEach(step => step.style.display = 'none');
    const view = document.getElementById(viewId);
    if (view) view.style.display = 'block';
}

function showError(message) {
    const el = document.getElementById('errorMessage');
    el.innerHTML = `<strong>Błąd:</strong> ${message}`;
    el.style.display = 'block';
}

function hideError() {
    document.getElementById('errorMessage').style.display = 'none';
}

// ==================== POLLING & STATUS UPDATE ====================
function startPolling(sessionId) {
    activeSessionId = sessionId;
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollForStatus, 3000);
    showView('progress-step');
}

async function pollForStatus() {
    if (!activeSessionId) return;
    try {
        const response = await fetch(`/api/xml/status/${activeSessionId}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `Błąd serwera: ${response.status}`);
        updateProgress(data.processed_products, data.total_products);
        if (data.status === 'complete' || data.status === 'failed') {
            clearInterval(pollInterval);
            pollInterval = null;
            displayResults(data);
        }
    } catch (err) {
        showError(`Błąd odpytywania o status: ${err.message}.`);
        clearInterval(pollInterval);
        pollInterval = null;
        switchMode(currentMode);
    }
}

function updateProgress(processed, total) {
    const progress = total > 0 ? (processed / total) * 100 : 0;
    document.getElementById('progressFill').style.width = `${progress.toFixed(0)}%`;
    document.getElementById('statusMessage').textContent = `Przetworzono ${processed} z ${total} produktów...`;
}

function displayResults(data) {
    showView('results-step');
    const downloadSection = document.getElementById('downloadSection');
    const errorsSection = document.getElementById('errorsSection');
    const errorsList = document.getElementById('errorsList');
    downloadSection.innerHTML = '';
    errorsList.innerHTML = '';
    errorsSection.style.display = 'none';
    if (data.download_url) {
        const downloadLink = document.createElement('a');
        downloadLink.href = data.download_url;
        downloadLink.className = 'btn btn-primary';
        downloadLink.innerHTML = '<i class="fa-solid fa-download"></i> Pobierz Wyniki (.zip)';
        downloadSection.appendChild(downloadLink);
    } else {
        downloadSection.innerHTML = '<p>Nie wygenerowano żadnych plików. Sprawdź logi błędów.</p>';
    }
    if (data.errors && data.errors.length > 0) {
        errorsSection.style.display = 'block';
        data.errors.forEach(error => {
            const li = document.createElement('li');
            li.textContent = `Produkt: ${error.product_name || 'N/A'} - ${error.message}`;
            errorsList.appendChild(li);
        });
    }
}

// ==================== XML WORKFLOW ====================
asnyc function handleXmlUpload() {
    hideError();
    const fileInput = document.getElementById('xmlFileInput');
    const urlInput = document.getElementById('xmlUrlInput');
    let body, headers = {};
    if (fileInput.files.length > 0) {
        const formData = new FormData();
        formData.append('xml_file', fileInput.files[0]);
        body = formData;
    } else if (urlInput.value.trim() !== '') {
        body = JSON.stringify({ xml_url: urlInput.value.trim() });
        headers['Content-Type'] = 'application/json';
    } else {
        return showError('Proszę wybrać plik XML lub podać link URL.');
    }
    try {
        const response = await fetch('/api/xml/start', { method: 'POST', body, headers });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Nieznany błąd serwera.');
        activeSessionId = data.session_id;
        document.getElementById('settings-product-count').textContent = data.product_count;
        showView('xml-step-settings');
    } catch (err) { showError(err.message); }
}

async function handleXmlGeneration() {
    const style1 = document.getElementById('xmlStyle1').value.trim();
    const style2 = document.getElementById('xmlStyle2').value.trim();
    if (!style1 || !style2) return showError('Proszę wpisać oba style.');
    const payload = {
        session_id: activeSessionId,
        resolution: document.getElementById('xmlResolution').value,
        aspect_ratio: document.getElementById('xmlAspectRatio').value,
        styles: [style1, style2]
    };
    await generateCreations(payload);
}

// ==================== MANUAL WORKFLOW ====================
let productCardCounter = 0;

function addProductCard() {
    const container = document.getElementById('productsContainer');
    if (container.children.length >= 10) {
        return showError("Można dodać maksymalnie 10 produktów.");
    }
    const template = document.getElementById('productCardTemplate');
    const card = template.content.cloneNode(true).firstElementChild;
    card.dataset.id = `product_${++productCardCounter}`;
    card.querySelector('.product-number').textContent = container.children.length + 1;
    card.querySelector('.btn-remove-product').addEventListener('click', () => card.remove());
    const dropZone = card.querySelector('.drop-zone');
    const fileInput = card.querySelector('.drop-zone-input');
    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (e) => handleFiles(card, e.target.files));
    dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragleave', e => { e.preventDefault(); dropZone.classList.remove('drag-over'); });
    dropZone.addEventListener('drop', e => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        handleFiles(card, e.dataTransfer.files);
    });
    container.appendChild(card);
}

function handleFiles(card, files) {
    const previewContainer = card.querySelector('.image-previews');
    const newFiles = Array.from(files).filter(file => file.type.startsWith('image/'));
    if (previewContainer.children.length + newFiles.length > 4) {
        return showError("Można dodać maksymalnie 4 zdjęcia na produkt.");
    }
    newFiles.forEach(file => {
        const reader = new FileReader();
        reader.onload = (e) => {
            const div = document.createElement('div');
            div.className = 'preview-image-container';
            div.innerHTML = `<img src="${e.target.result}" class="preview-image"><button class="remove-image-btn">×</button>`;
            div.querySelector('.remove-image-btn').addEventListener('click', () => div.remove());
            // A simple way to associate file with element for later retrieval
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(file);
            div.querySelector('img').files = dataTransfer.files;
            previewContainer.appendChild(div);
        };
        reader.readAsDataURL(file);
    });
}

function showManualSettings() {
    hideError();
    if (document.querySelectorAll('#productsContainer .product-card').length === 0) {
        return showError('Proszę dodać przynajmniej jeden produkt.');
    }
    showView('manual-step-settings');
}

async function handleManualGeneration() {
    hideError();
    const productCards = document.querySelectorAll('#productsContainer .product-card');
    const formData = new FormData();
    for (let i = 0; i < productCards.length; i++) {
        const card = productCards[i];
        const name = card.querySelector('.product-name-input').value || `Produkt ${i + 1}`;
        formData.append(`product_${i}_name`, name);
        const imagePreviews = card.querySelectorAll('.preview-image');
        if (imagePreviews.length === 0) return showError(`Produkt #${i + 1} nie ma żadnych zdjęć.`);
        for (let j = 0; j < imagePreviews.length; j++) {
            formData.append(`product_${i}_file_${j}`, imagePreviews[j].files[0]);
        }
    }

    try {
        const response = await fetch('/api/manual/start', { method: 'POST', body: formData });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Błąd serwera przy trybie ręcznym.');
        const style1 = document.getElementById('manualStyle1').value.trim();
        const style2 = document.getElementById('manualStyle2').value.trim();
        if (!style1 || !style2) return showError('Proszę wpisać oba style.');
        const payload = {
            session_id: data.session_id,
            resolution: document.getElementById('manualResolution').value,
            aspect_ratio: document.getElementById('manualAspectRatio').value,
            styles: [style1, style2]
        };
        await generateCreations(payload);
    } catch (err) { showError(err.message); }
}

// ==================== SHARED GENERATION CALL ====================
async function generateCreations(payload) {
    try {
        const response = await fetch('/api/xml/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!response.ok) throw new Error((await response.json()).error || 'Błąd rozpoczęcia generowania.');
        startPolling(payload.session_id);
    } catch (err) {
        showError(err.message);
        switchMode(currentMode);
    }
}
