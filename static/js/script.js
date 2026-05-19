// ==================== GLOBAL STATE ====================
let currentMode = 'xml'; // 'xml' | 'upload'

// State for the XML multi-step workflow
let xmlState = {
    sessionId: null, imageUrls: [], totalImages: 0, totalProducts: 0,
    settings: { resolution: '1K', aspect_ratio: '1:1', styles: ['', ''] },
    status: 'idle', progress: 0, pollInterval: null
};

// State for the custom upload mode
let personSets = [];
let personCounter = 0;

// ==================== INIT ====================
document.addEventListener('DOMContentLoaded', () => {
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
    if (mode === 'upload' && personSets.length === 0) {
        addPersonSet();
    }
}

// ==================== XML WORKFLOW ====================

function showXmlStep(step) {
    ['upload', 'settings', 'styles', 'progress', 'results'].forEach(s => {
        const el = document.getElementById(`xml-step-${s}`);
        if (el) el.style.display = 'none';
    });
    const currentStepEl = document.getElementById(`xml-step-${step}`);
    if (currentStepEl) currentStepEl.style.display = 'block';
}

async function handleXmlUpload() {
    hideError();
    const fileInput = document.getElementById('xmlFileInput');
    const file = fileInput.files[0];
    if (!file) { return showError('Proszę wybrać plik XML.'); }

    const formData = new FormData();
    formData.append('xml_file', file);

    try {
        const response = await fetch('/api/xml/start', { method: 'POST', body: formData });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Nieznany błąd serwera.');

        xmlState.sessionId = data.session_id;
        xmlState.totalImages = data.image_count;
        xmlState.totalProducts = data.product_count;
        xmlState.status = 'settings';
        document.getElementById('settings-image-count').textContent = data.image_count;
        showXmlStep('settings');
    } catch (err) { showError(err.message); }
}

function handleSettingsSubmit() {
    xmlState.settings.resolution = document.getElementById('xmlResolution').value;
    xmlState.settings.aspect_ratio = document.getElementById('xmlAspectRatio').value;
    xmlState.status = 'styles';
    showXmlStep('styles');
}

async function handleStylesSubmit() {
    const style1 = document.getElementById('xmlStyle1').value.trim();
    const style2 = document.getElementById('xmlStyle2').value.trim();
    if (!style1 || !style2) { return showError('Proszę wpisać oba style.'); }

    xmlState.settings.styles = [style1, style2];
    xmlState.status = 'processing';
    showXmlStep('progress');

    try {
        const response = await fetch('/api/xml/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...xmlState.settings, session_id: xmlState.sessionId })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Błąd rozpoczęcia generowania.');
        xmlState.pollInterval = setInterval(pollForStatus, 2500);
    } catch (err) { 
        showError(err.message); 
        showXmlStep('styles'); // Go back to styles step on error
    }
}

async function pollForStatus() {
    if (!xmlState.sessionId) return;

    try {
        const response = await fetch(`/api/xml/status/${xmlState.sessionId}`);
        if (!response.ok) {
            // Spróbuj odczytać błąd JSON, nawet jeśli status nie jest 200 OK
            try {
                const errorData = await response.json();
                throw new Error(errorData.error || `Błąd serwera: ${response.status}`);
            } catch (jsonError) {
                throw new Error(`Błąd serwera: ${response.status}`);
            }
        }

        const data = await response.json();

        const progress = data.total_products > 0 ? (data.processed_products / data.total_products) * 100 : 0;
        const progressBar = document.getElementById('xmlProgressFill');
        const statusMessage = document.getElementById('xmlStatusMessage');
        if(progressBar) progressBar.style.width = `${progress.toFixed(0)}%`;
        if(statusMessage) statusMessage.textContent = `Przetworzono ${data.processed_products} z ${data.total_products}`;

        if (data.status === 'complete' || data.status === 'failed') {
            clearInterval(xmlState.pollInterval);
            xmlState.status = 'complete';
            displayResults(data);
        }
    } catch (err) {
        showError(`Błąd odpytywania o status: ${err.message}. Spróbuj odświeżyć stronę.`);
        clearInterval(xmlState.pollInterval);
    }
}

function displayResults(data) {
    showXmlStep('results');
    const downloadSection = document.getElementById('xmlDownloadSection');
    const errorsSection = document.getElementById('xmlErrorsSection');
    const errorsList = document.getElementById('xmlErrorsList');

    // Wyczyść poprzednie wyniki
    downloadSection.innerHTML = '';
    errorsList.innerHTML = '';
    errorsSection.style.display = 'none';

    // 1. Wyświetl link do pobrania, jeśli istnieje
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

    // 2. Wyświetl szczegółowe błędy, jeśli wystąpiły
    if (data.errors && data.errors.length > 0) {
        errorsSection.style.display = 'block';
        data.errors.forEach(error => {
            const li = document.createElement('li');
            const source = error.file ? `plik <strong>${error.file}</strong>` : (error.source_url ? `URL <strong>${error.source_url}</strong>` : 'przetwarzanie ogólne');
            li.innerHTML = `Błąd na etapie '${error.step}' dla ${source}: <em>${error.message}</em>`;
            errorsList.appendChild(li);
        });
    }
}

async function handleDownloadClick(event) {
    event.preventDefault();
    const link = event.currentTarget;
    const url = link.href;

    link.classList.add('disabled');
    link.querySelector('.btn-text').textContent = 'Pobieranie...';

    try {
        const response = await fetch(url);
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.error || `Nie udało się pobrać pliku. Status: ${response.status}`);
        }

        const blob = await response.blob();
        const tempUrl = window.URL.createObjectURL(blob);
        const tempLink = document.createElement('a');

        const filename = url.substring(url.lastIndexOf('/') + 1);
        tempLink.href = tempUrl;
        tempLink.setAttribute('download', filename);

        document.body.appendChild(tempLink);
        tempLink.click();
        document.body.removeChild(tempLink);
        window.URL.revokeObjectURL(tempUrl);

        // Zaktualizuj interfejs po pomyślnym pobraniu
        link.outerHTML = '<p class="success-text">✅ Plik pobrany! Dane sesji zostały usunięte z serwera.</p>';

    } catch (err) {
        showError(err.message);
        // Przywróć przycisk, jeśli pobieranie się nie powiodło
        link.classList.remove('disabled');
        link.querySelector('.btn-text').textContent = 'Pobierz ZIP';
    }
}

// ==================== CUSTOM UPLOAD MODE ====================

function addPersonSet() {
    const id = ++personCounter;
    personSets.push({ id, files: [null, null, null, null], description: '' });
    renderPersonSets();
}

function removePersonSet(id) {
    if (personSets.length === 1) return;
    personSets = personSets.filter(p => p.id !== id);
    renderPersonSets();
}

function renderPersonSets() {
    const container = document.getElementById('personsContainer');
    if (!container) return;
    container.innerHTML = '';
    personSets.forEach((person, idx) => {
        const set = document.createElement('div');
        set.className = 'person-set';
        set.innerHTML = `
            <div class="person-set-header"><div class="person-set-title"><span class="badge">${idx + 1}</span>Osoba ${idx + 1}</div>${personSets.length > 1 ? `<button class="btn-remove-person" onclick="removePersonSet(${person.id})" title="Usuń">✕</button>`: ''}</div>
            <div class="photo-grid">${[0,1,2,3].map(slotIdx => buildSlotHTML(person.id, slotIdx, person.files[slotIdx])).join('')}</div>
            <div class="description-group"><label class="description-label">💼 Zawód / opis</label><textarea class="neo-textarea" rows="2" placeholder="Podaj zawód lub krótki opis osoby..." id="desc-${person.id}" oninput="updateDescription(${person.id}, this.value)">${person.description}</textarea></div>
        `;
        container.appendChild(set);
        [0,1,2,3].forEach(slotIdx => {
            const slot = document.getElementById(`slot-${person.id}-${slotIdx}`);
            slot.addEventListener('dragover', e => { e.preventDefault(); slot.classList.add('drag-over'); });
            slot.addEventListener('dragleave', () => slot.classList.remove('drag-over'));
            slot.addEventListener('drop', e => {
                e.preventDefault();
                slot.classList.remove('drag-over');
                const file = e.dataTransfer.files[0];
                if (file && file.type.startsWith('image/')) handleFileSelect(person.id, slotIdx, file);
            });
            slot.querySelector('input[type="file"]').addEventListener('change', (e) => handleFileSelect(person.id, slotIdx, e.target.files[0]));
        });
    });
}

function buildSlotHTML(personId, slotIdx, file) {
    const hasImage = file !== null;
    return `
        <div class="photo-slot ${hasImage ? 'has-image' : ''}" id="slot-${personId}-${slotIdx}">
            <input type="file" id="file-${personId}-${slotIdx}" accept="image/*" style="display:none">
            ${hasImage ? `<img src="${file._dataUrl}" alt="Zdjęcie">` : '<div class="slot-empty"><span>📷</span></div>'}
            <div class="photo-slot-label">Zdjęcie ${slotIdx + 1}</div>
        </div>
    `;
}

function handleFileSelect(personId, slotIdx, file) {
    if (!file || !file.type.startsWith('image/')) return;
    const reader = new FileReader();
    reader.onload = (e) => {
        const person = personSets.find(p => p.id === personId);
        if (!person) return;
        file._dataUrl = e.target.result;
        person.files[slotIdx] = file;
        renderPersonSets(); // Re-render for simplicity
    };
    reader.readAsDataURL(file);
}

function updateDescription(personId, value) {
    const person = personSets.find(p => p.id === personId);
    if (person) person.description = value;
}

async function processUploadMode() {
    if (!personSets.every(p => p.files.some(f=>f) && p.description)) {
        return showError('Każda osoba musi mieć co najmniej jedno zdjęcie i opis.');
    }
    hideError();

    const formData = new FormData();
    formData.append('persons_count', personSets.length);
    personSets.forEach((person, idx) => {
        formData.append(`description_${idx}`, person.description.trim());
        person.files.forEach((file, slotIdx) => {
            if (file) formData.append(`image_${idx}_${slotIdx}`, file, file.name);
        });
    });

    // In the new version, we should use the same polling mechanism as the XML workflow
    // For now, it's just a simple fetch
    try {
        const response = await fetch('/api/process-upload', { method: 'POST', body: formData });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Błąd serwera');
        alert('Przetwarzanie w trybie custom zakończone! (Logika wyników do implementacji)');
    } catch (err) {
        showError(err.message);
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

