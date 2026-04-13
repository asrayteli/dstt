let currentFilters = null;
const managerContractCache = new Map();

const UPLOAD_INPUT_IDS = ['subject-file', 'prev-year-subject-file', 'site-file'];
const FILE_NAME_LABEL_IDS = {
    'subject-file': 'subject-file-name',
    'prev-year-subject-file': 'prev-year-subject-file-name',
    'site-file': 'site-file-name'
};

document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    showWelcomeScreen();
});

function setupEventListeners() {
    const uploadBtn = document.getElementById('upload-btn');
    if (uploadBtn) {
        uploadBtn.addEventListener('click', () => {
            showWelcomeScreen();
            openFileDialog('subject-file');
        });
    }

    const resetBtn = document.getElementById('reset-btn');
    if (resetBtn) {
        resetBtn.addEventListener('click', resetAll);
    }

    const uploadForm = document.getElementById('upload-form');
    if (uploadForm) {
        uploadForm.addEventListener('submit', (e) => {
            e.preventDefault();
            handleFileUpload();
        });
    }

    const siteSource = document.getElementById('site-source');
    if (siteSource) {
        siteSource.addEventListener('change', syncSiteSourceMode);
    }

    setupUploadInteractions();

    const resultExpandToggle = document.getElementById('result-expand-toggle');
    if (resultExpandToggle) {
        resultExpandToggle.addEventListener('click', () => toggleResultFocus());
    }

    const resultFocusBackdrop = document.getElementById('result-focus-backdrop');
    if (resultFocusBackdrop) {
        resultFocusBackdrop.addEventListener('click', () => toggleResultFocus(false));
    }

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isResultFocusEnabled()) {
            toggleResultFocus(false);
        }
    });

    document.querySelectorAll('.tab-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            if (!btn.dataset.tab) return;
            switchTab(btn.dataset.tab);
        });
    });

    const comparisonMode = document.getElementById('comparison-mode');
    if (comparisonMode) {
        comparisonMode.addEventListener('change', () => {
            applyComparisonModeUI(comparisonMode.value);
        });
    }

    const detailSearch = document.getElementById('detail-search');
    if (detailSearch) {
        detailSearch.addEventListener('input', (e) => {
            tableRenderer.searchQuery = e.target.value;
            if (currentFilters) {
                const results = comparisonEngine.executeComparison(currentFilters);
                tableRenderer.renderDetailTable(results, currentFilters);
            }
        });
    }

    applyComparisonModeUI(comparisonMode ? comparisonMode.value : 'prev_year');
    syncSiteSourceMode();
}

function syncSiteSourceMode() {
    const siteSource = document.getElementById('site-source');
    const sitePanel = document.getElementById('site-source-file-panel');
    const dbPanel = document.getElementById('site-source-db-panel');
    const siteInput = document.getElementById('site-file');
    const useDb = !siteSource || siteSource.value === 'db';

    if (sitePanel) {
        sitePanel.style.display = useDb ? 'none' : 'block';
    }
    if (dbPanel) {
        dbPanel.style.display = useDb ? 'block' : 'none';
    }
    if (siteInput) {
        siteInput.required = !useDb;
        if (useDb) {
            siteInput.value = '';
            updateFileNameLabel('site-file');
        }
    }
}

function setupUploadInteractions() {
    document.querySelectorAll('[data-pick-for]').forEach((btn) => {
        btn.addEventListener('click', (e) => {
            const inputId = e.currentTarget.dataset.pickFor;
            openFileDialog(inputId);
        });
    });

    document.querySelectorAll('.dropzone[data-input-id]').forEach((zone) => {
        const inputId = zone.dataset.inputId;
        zone.addEventListener('click', (e) => {
            if (e.target.closest('button')) return;
            openFileDialog(inputId);
        });

        zone.addEventListener('dragover', (e) => {
            e.preventDefault();
            zone.classList.add('dragover');
        });

        zone.addEventListener('dragenter', (e) => {
            e.preventDefault();
            zone.classList.add('dragover');
        });

        zone.addEventListener('dragleave', () => {
            zone.classList.remove('dragover');
        });

        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            zone.classList.remove('dragover');
            const droppedFiles = e.dataTransfer && e.dataTransfer.files;
            if (!droppedFiles || droppedFiles.length === 0) return;
            setInputFile(inputId, droppedFiles[0]);
        });
    });

    UPLOAD_INPUT_IDS.forEach((inputId) => {
        const input = document.getElementById(inputId);
        if (!input) return;
        input.addEventListener('change', () => updateFileNameLabel(inputId));
    });
}

function openFileDialog(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    input.click();
}

function setInputFile(inputId, file) {
    if (!file) return;
    if (!String(file.name || '').toLowerCase().endsWith('.csv')) {
        alert('CSVファイルを選択してください。');
        return;
    }

    const input = document.getElementById(inputId);
    if (!input) return;

    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    updateFileNameLabel(inputId);
}

function updateFileNameLabel(inputId) {
    const input = document.getElementById(inputId);
    const label = document.getElementById(FILE_NAME_LABEL_IDS[inputId]);
    if (!input || !label) return;
    label.textContent = input.files && input.files[0] ? input.files[0].name : '未選択';
}

function resetUploadLabels() {
    Object.values(FILE_NAME_LABEL_IDS).forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.textContent = '未選択';
    });
}

function showWelcomeScreen() {
    const welcome = document.getElementById('welcome-screen');
    const main = document.getElementById('main-content');
    if (welcome) welcome.classList.remove('hidden');
    if (main) main.classList.add('hidden');
}

function showMainContent() {
    const welcome = document.getElementById('welcome-screen');
    const main = document.getElementById('main-content');
    const resetBtn = document.getElementById('reset-btn');
    if (welcome) welcome.classList.add('hidden');
    if (main) main.classList.remove('hidden');
    if (resetBtn) resetBtn.style.display = 'inline-block';
    applyComparisonModeUI();
}

async function handleFileUpload() {
    const subjectInput = document.getElementById('subject-file');
    const prevYearInput = document.getElementById('prev-year-subject-file');
    const siteInput = document.getElementById('site-file');
    const siteSource = document.getElementById('site-source')?.value || 'db';

    const subjectFile = subjectInput && subjectInput.files ? subjectInput.files[0] : null;
    const prevYearSubjectFile = prevYearInput && prevYearInput.files ? prevYearInput.files[0] : null;
    const siteFile = siteInput && siteInput.files ? siteInput.files[0] : null;

    if (!subjectFile) {
        alert('科目別分析表（CSV）を選択してください。');
        return;
    }
    if (siteSource === 'file' && !siteFile) {
        alert('現場表読み込みモードでは現行現場表 CSV を選択してください。');
        return;
    }

    const formData = new FormData();
    formData.append('subject_file', subjectFile);
    if (prevYearSubjectFile) formData.append('prev_year_subject_file', prevYearSubjectFile);
    if (siteFile) formData.append('site_file', siteFile);
    formData.append('site_source', siteSource);

    showLoading();

    try {
        const response = await fetch('/tools/subject_analysis_tool/api/upload', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();
        if (!response.ok || result.error) {
            throw new Error(result.error || 'データの読み込みに失敗しました。');
        }

        dataManager.setData(result.data);
        filterController.initUI();
        showMainContent();
        hideLoading();

        alert(
            `読み込みに成功しました。\n` +
            `- 当期データ: ${result.metadata.current_year_count}件\n` +
            `- 前年データ: ${result.metadata.prev_year_count}件\n` +
            `- 現場マッピング: ${result.metadata.site_count}件`
        );
    } catch (error) {
        hideLoading();
        alert(`エラー: ${error.message}`);
        console.error('Upload error:', error);
    }
}

async function resolveManagerScopedContracts(managerId) {
    const key = String(managerId || '').trim();
    if (!key) return null;
    if (managerContractCache.has(key)) {
        return managerContractCache.get(key);
    }

    const response = await fetch(`/tools/subject_analysis_tool/api/manager_contracts?manager_id=${encodeURIComponent(key)}`);
    const payload = await response.json();
    if (!response.ok || payload.error) {
        throw new Error(payload.error || '担当者番号による現場絞り込みに失敗しました。');
    }

    const contractCodes = Array.isArray(payload.contract_codes) ? payload.contract_codes : [];
    managerContractCache.set(key, contractCodes);
    return contractCodes;
}

async function applyFilters() {
    const filters = normalizeFiltersForSimpleView(filterController.getFilters());

    if (filters.months.length === 0) {
        alert('対象月を選択してください。');
        return;
    }

    if (filters.subjects.length === 0) {
        alert('科目を選択してください。');
        return;
    }

    if (filters.comparisonMode === 'prev_year' && !dataManager.hasPrevYearData()) {
        alert('前年比較には前年データが必要です。');
        return;
    }

    if (filters.comparisonMode === 'same_year' && !filters.baseMonth) {
        alert('基準月を選択してください。');
        return;
    }

    showLoading();

    try {
        if (filters.managerId) {
            const managerContracts = await resolveManagerScopedContracts(filters.managerId);
            filters.allowedContractCodes = managerContracts;
        }

        currentFilters = filters;
        const results = comparisonEngine.executeComparison(filters);

        if (results.length === 0) {
            hideLoading();
            alert('条件に一致するデータがありません。フィルター条件を見直してください。');
            return;
        }

        updateAllTabs(results, filters);
        hideLoading();
    } catch (error) {
        hideLoading();
        alert(`エラー: ${error.message}`);
        console.error('Analysis error:', error);
    }
}

function updateAllTabs(results, filters) {
    tableRenderer.renderDetailTable(results, filters);

    const siteSummary = comparisonEngine.getSiteSummary();
    tableRenderer.renderSiteSummary(siteSummary, filters);

    const subjectSummary = comparisonEngine.getSubjectSummary();
    tableRenderer.renderSubjectSummary(subjectSummary, filters);

    const profitData = comparisonEngine.getProfitAnalysis();
    tableRenderer.renderProfitAnalysis(profitData, filters);

    chartRenderer.renderChart(filters);
    chartRenderer.renderHeatmap(results, filters);

    const stats = comparisonEngine.getStatistics(filters);
    tableRenderer.renderStatistics(stats);
}

function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    document.querySelectorAll('.tab-pane').forEach((pane) => {
        pane.classList.remove('active');
    });

    const targetPane = document.getElementById(`tab-${tabName}`);
    if (targetPane) {
        targetPane.classList.add('active');
        if (tabName === 'chart' && currentFilters) {
            chartRenderer.renderChart(currentFilters);
        }
    }
}

function resetAll() {
    if (!confirm('読み込み済みデータと選択状態をリセットします。よろしいですか？')) {
        return;
    }

    dataManager.reset();
    filterController.reset();
    chartRenderer.destroy();

    const uploadForm = document.getElementById('upload-form');
    if (uploadForm) uploadForm.reset();
    resetUploadLabels();
    syncSiteSourceMode();

    currentFilters = null;
    managerContractCache.clear();
    toggleResultFocus(false);
    showWelcomeScreen();

    const resetBtn = document.getElementById('reset-btn');
    if (resetBtn) resetBtn.style.display = 'none';
}

function showLoading() {
    const loading = document.getElementById('loading');
    if (loading) loading.classList.remove('hidden');
}

function hideLoading() {
    const loading = document.getElementById('loading');
    if (loading) loading.classList.add('hidden');
}

function isResultFocusEnabled() {
    const workbench = document.querySelector('.sat-workbench');
    return !!(workbench && workbench.classList.contains('focus-results'));
}

function toggleResultFocus(forceState = null) {
    const workbench = document.querySelector('.sat-workbench');
    const backdrop = document.getElementById('result-focus-backdrop');
    const toggle = document.getElementById('result-expand-toggle');
    if (!workbench || !backdrop || !toggle) return;

    const nextState = forceState === null ? !workbench.classList.contains('focus-results') : !!forceState;
    workbench.classList.toggle('focus-results', nextState);
    backdrop.classList.toggle('hidden', !nextState);
    toggle.textContent = nextState ? '縮小' : '拡大';
    toggle.classList.toggle('active', nextState);
}

function normalizeFiltersForSimpleView(filters) {
    if (!filters || filters.comparisonMode !== 'simple_view') {
        return filters;
    }

    const normalized = { ...filters };
    if (normalized.months.length === 0) {
        normalized.months = [...dataManager.metadata.months];
    }
    if (normalized.subjects.length === 0) {
        normalized.subjects = getAllSubjects();
    }
    normalized.displayMode = 'value_only';
    normalized.highlightEnabled = false;
    return normalized;
}

function getAllSubjects() {
    const groups = dataManager.getSubjects();
    return [...new Set(Object.values(groups).flat())];
}

function applyComparisonModeUI(mode = null) {
    const comparisonModeSelect = document.getElementById('comparison-mode');
    if (mode && comparisonModeSelect && comparisonModeSelect.value !== mode) {
        comparisonModeSelect.value = mode;
    }

    if (filterController && typeof filterController.updateComparisonModeUI === 'function') {
        filterController.updateComparisonModeUI();
    }

    const selectedMode = comparisonModeSelect?.value || mode || 'prev_year';
    const activeTab = document.querySelector('.tab-btn.active');
    if (selectedMode === 'simple_view' && activeTab && activeTab.classList.contains('compare-only')) {
        switchTab('detail');
    }
}

window.applyFilters = applyFilters;
window.switchTab = switchTab;
window.resetAll = resetAll;
