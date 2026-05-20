// ==================== GLOBAL STATE & CONSTANTS ====================
let currentMode = 'xml';
let activeSessionId = null;
let pollInterval = null;
let productCardCounter = 0;
// New robust way to handle files for manual upload
let manualProductsData = new Map();

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
    addProductCard(); // Start with one card
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
    document.getElementById(viewId).style.display = 'block';
}

function showError(message) {
    const el = document.getElementById('errorMessage');
    el.innerHTML = `<strong>Błąd:</strong> ${message}`;
    el.style.display = 'block';
}

function hideError() {
    document.getElementById('errorMessage').style.display = 'none';
}

// ==================== POLLING & STATUS UPDATE (Shared) ====================
// ... (This section remains unchanged from the previous correct version)
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
        updateProgress(data.processed_products, data.total_products, data.queue_size);
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
function updateProgress(processed, total, queueSize) {
    const progress = total > 0 ? (processed / total) * 100 : 0;
    document.getElementById('progressFill').style.width = `${progress.toFixed(0)}%`;
    let message = `Przetworzono ${processed} z ${total} produktów...`;
    if (queueSize > 0) {
        message += ` (Zadań w kolejce: ${queueSize})`;
    }
    document.getElementById('statusMessage').textContent = message;
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
// ... (This section remains unchanged from the previous correct version)
async function handleXmlUpload() {
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

// ==================== REWRITTEN MANUAL WORKFLOW ====================
function addProductCard() {
    const container = document.getElementById('productsContainer');
    if (manualProductsData.size >= 10) return showError("Można dodać maksymalnie 10 produktów.");

    const template = document.getElementById('productCardTemplate');
    if (!template) return showError("Błąd krytyczny: Nie znaleziono szablonu produktu!");

    const card = template.content.cloneNode(true).firstElementChild;
    const cardId = `product_${++productCardCounter}`;
    card.dataset.id = cardId;
    manualProductsData.set(cardId, { name: '', files: [] });

    card.querySelector('.product-number').textContent = manualProductsData.size;
    card.querySelector('.product-name-input').addEventListener('input', (e) => {
        manualProductsData.get(cardId).name = e.target.value;
    });
    card.querySelector('.btn-remove-product').addEventListener('click', () => {
        manualProductsData.delete(cardId);
        card.remove();
        // Re-number remaining cards
        document.querySelectorAll('#productsContainer .product-card').forEach((c, index) => {
            c.querySelector('.product-number').textContent = index + 1;
        });
    });

    const dropZone = card.querySelector('.drop-zone');
    const fileInput = card.querySelector('.drop-zone-input');
    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (e) => handleFiles(cardId, e.target.files));
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragleave', (e) => { e.preventDefault(); dropZone.classList.remove('drag-over'); });
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        handleFiles(cardId, e.dataTransfer.files);
    });

    container.appendChild(card);
}

function handleFiles(cardId, newFiles) {
    const productData = manualProductsData.get(cardId);
    if (!productData) return;

    const filesToAdd = Array.from(newFiles).filter(file => file.type.startsWith('image/'));
    if (productData.files.length + filesToAdd.length > 4) {
        showError("Można dodać maksymalnie 4 zdjęcia na produkt.");
        return;
    }

    productData.files.push(...filesToAdd);
    renderPreviews(cardId);
}

function renderPreviews(cardId) {
    const productData = manualProductsData.get(cardId);
    const card = document.querySelector(`.product-card[data-id="${cardId}"]`);
    if (!productData || !card) return;

    const previewContainer = card.querySelector('.image-previews');
    previewContainer.innerHTML = '';

    productData.files.forEach((file, index) => {
        const reader = new FileReader();
        reader.onload = (e) => {
            const div = document.createElement('div');
            div.className = 'preview-image-container';
            const removeBtn = document.createElement('button');
            removeBtn.className = 'remove-image-btn';
            removeBtn.textContent = '×';
            removeBtn.addEventListener('click', () => {
                productData.files.splice(index, 1);
                renderPreviews(cardId);
            });
            const img = document.createElement('img');
            img.className = 'preview-image';
            img.src = e.target.result;
            div.appendChild(img);
            div.appendChild(removeBtn);
            previewContainer.appendChild(div);
        };
        reader.readAsDataURL(file);
    });
}

function showManualSettings() {
    hideError();
    if (manualProductsData.size === 0) return showError('Proszę dodać przynajmniej jeden produkt.');
    let allOk = true;
    manualProductsData.forEach(prod => {
        if (prod.files.length === 0) allOk = false;
    });
    if (!allOk) return showError('Każdy dodany produkt musi mieć co najmniej jedno zdjęcie.');
    showView('manual-step-settings');
}

async function handleManualGeneration() {
    hideError();
    const formData = new FormData();
    let productIndex = 0;
    for (const [cardId, productData] of manualProductsData.entries()) {
        const name = productData.name || `Produkt ${productIndex + 1}`;
        formData.append(`product_${productIndex}_name`, name);
        productData.files.forEach((file, fileIndex) => {
            formData.append(`product_${productIndex}_file_${fileIndex}`, file);
        });
        productIndex++;
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
