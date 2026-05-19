// ==================== GLOBAL STATE ====================
let currentMode = 'xml';

// State for workflows
let xmlState = { sessionId: null, status: 'idle', pollInterval: null };
let manualState = { sessionId: null, status: 'idle', pollInterval: null, productCounter: 0, products: [] };

// ==================== INIT ====================
document.addEventListener('DOMContentLoaded', () => {
    // Initialize the view with the default mode
    switchMode('xml');
});

// ==================== MODE SWITCHING ====================
function switchMode(mode) {
    currentMode = mode;
    document.getElementById('tab-xml').classList.toggle('active', mode === 'xml');
    document.getElementById('tab-upload').classList.toggle('active', mode === 'upload');
    document.getElementById('mode-xml').style.display = mode === 'xml' ? 'block' : 'none';
    document.getElementById('mode-upload').style.display = mode === 'upload' ? 'block' : 'none';
    hideError();

    if (mode === 'upload' && manualState.products.length === 0) {
        addProductCard(); // Use the new function
    }
}

// ==================== SHARED HELPER FUNCTIONS ====================
function startPolling(mode) {
    const state = mode === 'xml' ? xmlState : manualState;
    if (state.pollInterval) clearInterval(state.pollInterval);
    state.pollInterval = setInterval(() => pollForStatus(mode), 2500);
}

async function pollForStatus(mode) {
    const state = mode === 'xml' ? xmlState : manualState;
    if (!state.sessionId) return;

    try {
        const response = await fetch(`/api/xml/status/${state.sessionId}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || `Błąd serwera: ${response.status}`);
        }

        const prefix = mode === 'xml' ? 'xml' : 'manual';
        const progress = data.total_products > 0 ? (data.processed_products / data.total_products) * 100 : 0;
        document.getElementById(`${prefix}ProgressFill`).style.width = `${progress.toFixed(0)}%`;
        document.getElementById(`${prefix}StatusMessage`).textContent = `Przetworzono ${data.processed_products} z ${data.total_products}`;

        if (data.status === 'complete' || data.status === 'failed') {
            clearInterval(state.pollInterval);
            state.status = 'complete';
            displayResults(mode, data);
        }
    } catch (err) {
        showError(`Błąd odpytywania o status: ${err.message}.`);
        clearInterval(state.pollInterval);
    }
}

function displayResults(mode, data) {
    const prefix = mode === 'xml' ? 'xml' : 'manual';
    showStep(mode, 'results');

    const downloadSection = document.getElementById(`${prefix}DownloadSection`);
    const errorsSection = document.getElementById(`${prefix}ErrorsSection`);
    const errorsList = document.getElementById(`${prefix}ErrorsList`);

    downloadSection.innerHTML = '';
    errorsList.innerHTML = '';
    errorsSection.style.display = 'none';

    if (data.download_url) {
        const downloadLink = document.createElement('a');
        downloadLink.href = data.download_url;
        downloadLink.className = 'btn neo-button download';
        downloadLink.innerHTML = '<span class="btn-icon">📦</span><span class="btn-text">Pobierz ZIP</span>';
        downloadLink.addEventListener('click', handleDownloadClick);
        downloadSection.appendChild(downloadLink);
    } else {
        downloadSection.innerHTML = '<p class="error-text">Nie wygenerowano plików do pobrania. Sprawdź listę błędów.</p>';
    }

    if (data.errors && data.errors.length > 0) {
        errorsSection.style.display = 'block';
        data.errors.forEach(error => {
            const li = document.createElement('li');
            const source = error.product_name ? `produkt <strong>${error.product_name}</strong>` : (error.source_url ? `URL <strong>${error.source_url}</strong>` : 'przetwarzanie ogólne');
            li.innerHTML = `Błąd na etapie '${error.step}' dla ${source}: <em>${error.message}</em>`;
            errorsList.appendChild(li);
        });
    }
}

function showStep(mode, step) {
    const steps = mode === 'xml' ? ['upload', 'settings', 'styles', 'progress', 'results'] : ['products', 'settings', 'progress', 'results'];
    const prefix = mode === 'xml' ? 'xml' : 'manual';
    steps.forEach(s => {
        document.getElementById(`${prefix}-step-${s}`).style.display = 'none';
    });
    document.getElementById(`${prefix}-step-${step}`).style.display = 'block';
}

async function handleDownloadClick(event) {
    event.preventDefault();
    const link = event.currentTarget;
    try {
        const response = await fetch(link.href);
        if (!response.ok) throw new Error((await response.json()).error || 'Pobieranie nie powiodło się');
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = link.href.split('/').pop();
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        link.outerHTML = '<p class="success-text">✅ Plik pobrany! Dane sesji zostały usunięte.</p>';
    } catch (err) {
        showError(err.message);
    }
}

// ==================== XML WORKFLOW ====================

function toggleXmlSource() {
    const source = document.querySelector('input[name="xmlSource"]:checked').value;
    document.getElementById('xml-source-file').style.display = source === 'file' ? 'block' : 'none';
    document.getElementById('xml-source-url').style.display = source === 'url' ? 'block' : 'none';
}

async function handleXmlUpload() {
    hideError();
    const source = document.querySelector('input[name="xmlSource"]:checked').value;
    let body, headers = {};

    if (source === 'file') {
        const file = document.getElementById('xmlFileInput').files[0];
        if (!file) return showError('Proszę wybrać plik XML.');
        const formData = new FormData();
        formData.append('xml_file', file);
        body = formData;
    } else {
        const url = document.getElementById('xmlUrlInput').value.trim();
        if (!url) return showError('Proszę wkleić link do pliku XML.');
        body = JSON.stringify({ xml_url: url });
        headers['Content-Type'] = 'application/json';
    }

    try {
        const response = await fetch('/api/xml/start', { method: 'POST', body, headers });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Nieznany błąd serwera.');

        xmlState.sessionId = data.session_id;
        document.getElementById('settings-image-count').textContent = data.image_count;
        showStep('xml', 'settings');
    } catch (err) { showError(err.message); }
}

function handleSettingsSubmit() {
    showStep('xml', 'styles');
}

async function handleStylesSubmit() {
    const style1 = document.getElementById('xmlStyle1').value.trim();
    const style2 = document.getElementById('xmlStyle2').value.trim();
    if (!style1 || !style2) return showError('Proszę wpisać oba style.');

    const payload = {
        session_id: xmlState.sessionId,
        resolution: document.getElementById('xmlResolution').value,
        aspect_ratio: document.getElementById('xmlAspectRatio').value,
        styles: [style1, style2]
    };

    try {
        const response = await fetch('/api/xml/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!response.ok) throw new Error((await response.json()).error || 'Błąd rozpoczęcia generowania.');
        showStep('xml', 'progress');
        startPolling('xml');
    } catch (err) {
        showError(err.message);
        showStep('xml', 'styles');
    }
}

// ==================== MANUAL UPLOAD MODE (NEW) ====================

function addProductCard() {
    if (manualState.products.length >= 10) {
        showError("Osiągnięto maksymalną liczbę 10 produktów.");
        return;
    }

    const id = ++manualState.productCounter;
    const productData = { id, name: '', files: [] };
    manualState.products.push(productData);

    const template = document.getElementById('productCardTemplate');
    const card = template.content.cloneNode(true).firstElementChild;
    card.dataset.id = id;

    card.querySelector('.product-number').textContent = manualState.products.length;
    
    // --- Event Listeners ---
    const removeBtn = card.querySelector('.btn-remove-product');
    removeBtn.addEventListener('click', () => removeProductCard(id));

    const nameInput = card.querySelector('.product-name-input');
    nameInput.addEventListener('input', (e) => {
        const product = manualState.products.find(p => p.id === id);
        if(product) product.name = e.target.value;
    });

    const dropZone = card.querySelector('.drop-zone');
    const fileInput = card.querySelector('.drop-zone-input');

    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (e) => {
        handleFiles(id, e.target.files);
        fileInput.value = ''; // Reset for re-uploading same file
    });

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
        });
    });

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.add('drag-over'));
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.remove('drag-over'));
    });

    dropZone.addEventListener('drop', (e) => {
        handleFiles(id, e.dataTransfer.files);
    });

    document.getElementById('productsContainer').appendChild(card);
}

function removeProductCard(id) {
    manualState.products = manualState.products.filter(p => p.id !== id);
    document.querySelector(`.product-card[data-id='${id}']`).remove();

    // Re-number the remaining cards
    document.querySelectorAll('.product-card').forEach((card, index) => {
        card.querySelector('.product-number').textContent = index + 1;
    });
}

function handleFiles(productId, files) {
    const product = manualState.products.find(p => p.id === productId);
    if (!product) return;

    const newFiles = Array.from(files).filter(file => file.type.startsWith('image/'));
    const totalFiles = product.files.length + newFiles.length;

    if (totalFiles > 4) {
        showError("Można dodać maksymalnie 4 zdjęcia na produkt.");
        // Trim the excess files if user selects too many at once
        const needed = 4 - product.files.length;
        if (needed > 0) {
            product.files.push(...newFiles.slice(0, needed));
        }
    } else {
        product.files.push(...newFiles);
    }
    
    updatePreviews(productId);
}

function updatePreviews(productId) {
    const product = manualState.products.find(p => p.id === productId);
    if (!product) return;

    const card = document.querySelector(`.product-card[data-id='${productId}']`);
    const previewContainer = card.querySelector('.image-previews');
    previewContainer.innerHTML = '';

    product.files.forEach((file, index) => {
        const reader = new FileReader();
        reader.onload = (e) => {
            const div = document.createElement('div');
            div.className = 'preview-image-container';
            div.innerHTML = `
                <img src="${e.target.result}" class="preview-image" alt="${file.name}">
                <button class="remove-image-btn" data-index="${index}">×</button>
            `;
            div.querySelector('.remove-image-btn').addEventListener('click', () => {
                product.files.splice(index, 1);
                updatePreviews(productId);
            });
            previewContainer.appendChild(div);
        };
        reader.readAsDataURL(file);
    });
}

function showManualSettings() {
    hideError();
    if (manualState.products.length === 0) {
        return showError('Proszę dodać przynajmniej jeden produkt.');
    }
    let allOk = true;
    for (const [index, product] of manualState.products.entries()) {
        const card = document.querySelector(`.product-card[data-id='${product.id}']`);
        card.classList.remove('has-error');

        if (product.files.length === 0) {
            card.classList.add('has-error');
            showError(`Produkt #${index + 1} nie ma żadnych zdjęć.`);
            allOk = false;
        }
    }
    if (allOk) {
        showStep('upload', 'settings');
    }
}

async function handleManualSubmit() {
    hideError();
    const style1 = document.getElementById('manualStyle1').value.trim();
    const style2 = document.getElementById('manualStyle2').value.trim();
    if (!style1 || !style2) return showError('Proszę wpisać oba style.');

    const formData = new FormData();

    manualState.products.forEach((product, pIndex) => {
        // The backend doesn't use the name, but we have it in `product.name` if needed.
        product.files.forEach((file, fIndex) => {
            formData.append(`product_${pIndex}_file_${fIndex}`, file);
        });
    });

    try {
        const response = await fetch('/api/manual/start', { method: 'POST', body: formData });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Błąd serwera przy tworzeniu sesji.');
        
        manualState.sessionId = data.session_id;
        
        const generatePayload = {
            session_id: manualState.sessionId,
            resolution: document.getElementById('manualResolution').value,
            aspect_ratio: document.getElementById('manualAspectRatio').value,
            styles: [style1, style2]
        };

        const generateResponse = await fetch('/api/xml/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(generatePayload)
        });
        if (!generateResponse.ok) throw new Error((await generateResponse.json()).error || 'Błąd rozpoczęcia generowania.');

        showStep('upload', 'progress');
        startPolling('upload');

    } catch (err) {
        showError(err.message);
        showStep('upload', 'settings');
    }
}

// ==================== UI HELPERS ====================
function showError(message) {
    const el = document.getElementById('errorMessage');
    el.innerHTML = `<strong>❌ Błąd:</strong> ${message}`;
    el.style.display = 'block';
}

function hideError() {
    document.getElementById('errorMessage').style.display = 'none';
}
