let currentFilters = null;
const managerContractCache = new Map();
let pendingUploadResult = null;
let lastUploadMetadata = null;

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

    const startAnalysisBtn = document.getElementById('start-analysis-btn');
    if (startAnalysisBtn) {
        startAnalysisBtn.addEventListener('click', confirmUploadAndShowMain);
    }

    const reviewBackBtn = document.getElementById('review-back-btn');
    if (reviewBackBtn) {
        reviewBackBtn.addEventListener('click', () => setUploadReviewMode(false));
    }

    const siteSource = document.getElementById('site-source');
    if (siteSource) {
        siteSource.addEventListener('change', syncSiteSourceMode);
    }

    const managerFilterEnabled = document.getElementById('manager-filter-enabled');
    if (managerFilterEnabled) {
        managerFilterEnabled.addEventListener('change', syncManagerFilterMode);
    }

    setupUploadInteractions();
    setupPresetInteractions();
    setupTemplateInteractions();
    setupQuickViewInteractions();
    loadFilterPresetList();

    const resultExpandToggle = document.getElementById('result-expand-toggle');
    if (resultExpandToggle) {
        resultExpandToggle.addEventListener('click', () => toggleResultFocus());
    }

    const satSidebarToggle = document.getElementById('sat-sidebar-toggle');
    if (satSidebarToggle) {
        satSidebarToggle.addEventListener('click', () => toggleSatSidebar());
    }

    const satConditionsToggle = document.getElementById('sat-conditions-toggle');
    if (satConditionsToggle) {
        satConditionsToggle.addEventListener('click', () => toggleSatConditions());
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
                tableRenderer.currentPage = 1;
                tableRenderer.renderDetailTable(comparisonEngine.getResults(), currentFilters);
            }
        });
    }

    applyComparisonModeUI(comparisonMode ? comparisonMode.value : 'prev_year');
    syncResultLayoutToggles();
    syncSiteSourceMode();
    syncManagerFilterMode();
}

function syncManagerFilterMode() {
    const enabled = document.getElementById('manager-filter-enabled');
    const managerInput = document.getElementById('upload-manager-id');
    if (!managerInput) return;

    const isEnabled = !!(enabled && enabled.checked);
    managerInput.disabled = !isEnabled;
    if (!isEnabled) {
        managerInput.value = '';
    } else {
        managerInput.focus();
    }
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

function setupPresetInteractions() {
    const presetSelect = document.getElementById('filter-preset-select');
    if (!presetSelect) return;
    presetSelect.addEventListener('change', () => {
        if (presetSelect.value) {
            applyFilterPreset(presetSelect.value);
        }
    });
}

function setupTemplateInteractions() {
    document.querySelectorAll('.template-chip').forEach((btn) => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.template-chip').forEach((chip) => chip.classList.remove('active'));
            btn.classList.add('active');
            applyAnalysisTemplate(btn.dataset.template);
        });
    });
}

function setupQuickViewInteractions() {
    document.querySelectorAll('.quick-view-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.quick-view-btn').forEach((item) => item.classList.remove('active'));
            btn.classList.add('active');
            applyQuickView(btn.dataset.view);
        });
    });
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
        showNotice('CSVファイルを選択してください。', 'error');
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
    syncResultLayoutToggles();
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
        showNotice('科目別分析表（CSV）を選択してください。', 'error');
        return;
    }
    if (siteSource === 'file' && !siteFile) {
        showNotice('現場表読み込みモードでは現行現場表 CSV を選択してください。', 'error');
        return;
    }

    const managerFilterEnabled = document.getElementById('manager-filter-enabled')?.checked || false;
    const uploadManagerId = (document.getElementById('upload-manager-id')?.value || '').trim();
    if (managerFilterEnabled && !uploadManagerId) {
        showNotice('担当者絞り込みが有効です。担当者番号を入力してください。', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('subject_file', subjectFile);
    if (prevYearSubjectFile) formData.append('prev_year_subject_file', prevYearSubjectFile);
    if (siteFile) formData.append('site_file', siteFile);
    formData.append('site_source', siteSource);
    if (managerFilterEnabled && uploadManagerId) {
        formData.append('manager_id', uploadManagerId);
    }

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
        pendingUploadResult = result;
        lastUploadMetadata = result.metadata || {};
        renderUploadReview(lastUploadMetadata);
        setUploadReviewMode(true);
        hideLoading();
        showNotice('読み込みが完了しました。件数と警告を確認してから分析へ進んでください。', 'success');
    } catch (error) {
        hideLoading();
        showNotice(`エラー: ${error.message}`, 'error');
        console.error('Upload error:', error);
    }
}

function confirmUploadAndShowMain() {
    if (!pendingUploadResult) {
        showNotice('先にCSVを読み込んでください。', 'error');
        return;
    }
    updateUploadStep('analysis');
    showMainContent();
    applyAnalysisTemplate(document.querySelector('.template-chip.active')?.dataset.template || 'diff_top');
}

function setUploadReviewMode(isReviewing) {
    const reviewPanel = document.getElementById('upload-review-panel');
    const uploadSubmitBtn = document.getElementById('upload-submit-btn');
    const startAnalysisBtn = document.getElementById('start-analysis-btn');
    if (reviewPanel) reviewPanel.classList.toggle('hidden', !isReviewing);
    if (uploadSubmitBtn) uploadSubmitBtn.classList.toggle('hidden', isReviewing);
    if (startAnalysisBtn) startAnalysisBtn.classList.toggle('hidden', !isReviewing);
    updateUploadStep(isReviewing ? 'review' : 'select');
}

function updateUploadStep(activeStep) {
    document.querySelectorAll('.sat-step').forEach((step) => {
        step.classList.toggle('active', step.dataset.step === activeStep);
    });
}

function renderUploadReview(metadata) {
    const validation = metadata.validation || {};
    setText('review-current-count', metadata.current_year_count || validation.row_count || 0);
    setText('review-prev-count', metadata.prev_year_count || validation.prev_year_row_count || 0);
    setText('review-site-count', metadata.site_count || validation.site_mapping_count || 0);
    setText('review-unclassified-count', metadata.unclassified_count || validation.unmatched_site_mapping_count || 0);

    const managerFilterEl = document.getElementById('review-manager-filter');
    if (managerFilterEl) {
        const managerFilter = metadata.manager_filter;
        managerFilterEl.textContent = managerFilter
            ? `${managerFilter.manager_id}（現場${Number(managerFilter.matched_contract_count || 0).toLocaleString()}件）`
            : 'なし';
    }

    const warnings = Array.isArray(metadata.warnings) ? metadata.warnings : [];
    const warningEl = document.getElementById('review-warnings');
    if (!warningEl) return;
    if (warnings.length === 0) {
        warningEl.innerHTML = '<div class="review-warning empty">警告はありません。すぐに分析を開始できます。</div>';
        return;
    }
    warningEl.innerHTML = warnings
        .slice(0, 8)
        .map((warning) => `<div class="review-warning">${escapeHtml(warning)}</div>`)
        .join('');
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = Number(value || 0).toLocaleString();
}

function showNotice(message, type = '') {
    const notice = document.getElementById('sat-notice');
    if (!notice) return;
    notice.textContent = message;
    notice.className = `sat-notice ${type}`.trim();
    notice.classList.remove('hidden');
}

function escapeHtml(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
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

    if ((filters.comparisonMode === 'prev_year' || filters.comparisonMode === 'cumulative') && !dataManager.hasPrevYearData()) {
        alert('前年比較・累計比較には前年データが必要です。');
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
            if (!Array.isArray(managerContracts) || managerContracts.length === 0) {
                hideLoading();
                alert(`担当者番号 ${filters.managerId} に紐づく現場が現場リストPLUSに見つかりません。`);
                return;
            }
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
    saveRecentFilters(filters);
}

function applyQuickView(view) {
    const results = comparisonEngine.getResults();
    if (!results.length || !currentFilters) return;

    if (view === 'clear') {
        tableRenderer.currentPage = 1;
        tableRenderer.sortColumn = null;
        tableRenderer.renderDetailTable(results, currentFilters);
        switchTab('detail');
        return;
    }

    let sorted = [...results];
    if (view === 'diff_abs') {
        sorted.sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff));
    } else if (view === 'rate_abs') {
        sorted.sort((a, b) => Math.abs(b.diffRate) - Math.abs(a.diffRate));
    } else if (view === 'anomaly') {
        sorted.sort((a, b) => Number(b.isAnomaly) - Number(a.isAnomaly) || Math.abs(b.diff) - Math.abs(a.diff));
    } else if (view === 'profit_worse') {
        switchTab('profit');
        tableRenderer.profitSortColumn = 'profitRateDiff';
        tableRenderer.profitSortDirection = 'asc';
        tableRenderer.renderProfitAnalysis(comparisonEngine.getProfitAnalysis(), currentFilters);
        return;
    }

    tableRenderer.currentPage = 1;
    tableRenderer.sortColumn = null;
    tableRenderer.renderDetailTable(sorted, currentFilters);
    switchTab('detail');
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
    syncManagerFilterMode();

    currentFilters = null;
    pendingUploadResult = null;
    lastUploadMetadata = null;
    managerContractCache.clear();
    toggleResultFocus(false);
    toggleSatSidebar(false);
    toggleSatConditions(false);
    setUploadReviewMode(false);
    showWelcomeScreen();

    const resetBtn = document.getElementById('reset-btn');
    if (resetBtn) resetBtn.style.display = 'none';
}

function getFilterStorageKey() {
    return 'sat.filterPresets.v1';
}

function readFilterPresets() {
    try {
        return JSON.parse(localStorage.getItem(getFilterStorageKey()) || '{}');
    } catch (_error) {
        return {};
    }
}

function writeFilterPresets(presets) {
    localStorage.setItem(getFilterStorageKey(), JSON.stringify(presets));
}

function saveCurrentFilterPreset() {
    if (!dataManager.hasData()) return;
    const nameInput = document.getElementById('filter-preset-name');
    const name = (nameInput?.value || '').trim() || `条件 ${new Date().toLocaleString('ja-JP')}`;
    const presets = readFilterPresets();
    presets[name] = filterController.getFilters();
    writeFilterPresets(presets);
    if (nameInput) nameInput.value = '';
    loadFilterPresetList();
    const select = document.getElementById('filter-preset-select');
    if (select) select.value = name;
}

function saveRecentFilters(filters) {
    try {
        localStorage.setItem('sat.recentFilters.v1', JSON.stringify(filters));
    } catch (_error) {
        // localStorageが使えない環境では保存だけ諦める。
    }
}

function loadFilterPresetList() {
    const select = document.getElementById('filter-preset-select');
    if (!select) return;
    const presets = readFilterPresets();
    const options = ['<option value="">保存済み条件を選択</option>'];
    Object.keys(presets).sort((a, b) => a.localeCompare(b, 'ja')).forEach((name) => {
        options.push(`<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`);
    });
    select.innerHTML = options.join('');
}

function applyFilterPreset(name) {
    const presets = readFilterPresets();
    const filters = presets[name];
    if (!filters) return;
    applyFiltersToUI(filters);
}

function applyFiltersToUI(filters) {
    setInputValue('manager-id-filter', filters.managerId || '');
    setInputValue('site-group-mode', filters.siteGroupMode || '8digit');
    filterController.renderSiteList();
    setInputValue('comparison-mode', filters.comparisonMode || 'prev_year');
    setInputValue('base-month', filters.baseMonth || '4');
    setInputValue('display-mode', filters.displayMode || 'all');
    setInputValue('threshold-rate', filters.thresholdRate || 5);
    setInputValue('threshold-amount', filters.thresholdAmount || 10000);
    setInputValue('threshold-condition', filters.thresholdCondition || 'or');
    const highlight = document.getElementById('highlight-enabled');
    if (highlight) highlight.checked = filters.highlightEnabled !== false;

    checkValues('.month-checkbox', filters.months || []);
    checkValues('.site-checkbox', filters.sites || []);
    checkValues('.subject-checkbox', filters.subjects || []);
    filterController.updateSelectedMonths();
    filterController.updateSelectedSites();
    filterController.updateSelectedSubjects();
    applyComparisonModeUI(filters.comparisonMode || 'prev_year');
}

function setInputValue(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value;
}

function checkValues(selector, values) {
    const valueSet = new Set((values || []).map((value) => String(value)));
    document.querySelectorAll(selector).forEach((input) => {
        input.checked = valueSet.has(String(input.value));
    });
}

function applyAnalysisTemplate(templateName) {
    if (!dataManager.hasData()) return;
    const allMonths = [...dataManager.metadata.months];
    checkValues('.month-checkbox', allMonths);
    setInputValue('display-mode', 'all');
    setInputValue('threshold-condition', 'or');
    setInputValue('threshold-rate', 5);
    setInputValue('threshold-amount', 10000);

    if (templateName === 'sales_trend') {
        setInputValue('comparison-mode', 'simple_view');
        selectSubjects((subject) => subject.includes('売上') || subject.includes('請負料'));
    } else if (templateName === 'cost_increase') {
        setInputValue('comparison-mode', 'prev_year');
        selectSubjects((subject) => !(subject.includes('売上') || subject.includes('請負料')));
        setInputValue('threshold-amount', 30000);
    } else if (templateName === 'profit_down') {
        setInputValue('comparison-mode', 'prev_year');
        selectSubjects((subject) => subject === '基本請負料' || subject === 'その他請負料' || !(subject.includes('売上')));
        setInputValue('threshold-rate', 3);
    } else if (templateName === 'manager') {
        setInputValue('comparison-mode', 'prev_year');
        selectSubjects(() => true);
        const managerInput = document.getElementById('manager-id-filter');
        if (managerInput) managerInput.focus();
    } else {
        setInputValue('comparison-mode', 'prev_year');
        selectSubjects(() => true);
    }

    filterController.updateSelectedMonths();
    filterController.updateSelectedSubjects();
    applyComparisonModeUI();
}

function selectSubjects(predicate) {
    document.querySelectorAll('.subject-checkbox').forEach((input) => {
        input.checked = predicate(input.value);
    });
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

function toggleSatSidebar(forceCollapsed = null) {
    const workbench = document.querySelector('.sat-workbench');
    const toggle = document.getElementById('sat-sidebar-toggle');
    if (!workbench || !toggle) return;

    const nextState = forceCollapsed === null
        ? !workbench.classList.contains('sidebar-collapsed')
        : !!forceCollapsed;
    workbench.classList.toggle('sidebar-collapsed', nextState);
    toggle.textContent = nextState ? '対象選択を表示' : '対象選択を隠す';
    toggle.classList.toggle('active', nextState);
}

function toggleSatConditions(forceCollapsed = null) {
    const workbench = document.querySelector('.sat-workbench');
    const toggle = document.getElementById('sat-conditions-toggle');
    if (!workbench || !toggle) return;

    const nextState = forceCollapsed === null
        ? !workbench.classList.contains('conditions-collapsed')
        : !!forceCollapsed;
    workbench.classList.toggle('conditions-collapsed', nextState);
    toggle.textContent = nextState ? '分析条件を表示' : '分析条件を隠す';
    toggle.classList.toggle('active', nextState);
}

function syncResultLayoutToggles() {
    const workbench = document.querySelector('.sat-workbench');
    if (!workbench) return;
    toggleSatSidebar(workbench.classList.contains('sidebar-collapsed'));
    toggleSatConditions(workbench.classList.contains('conditions-collapsed'));
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
window.saveCurrentFilterPreset = saveCurrentFilterPreset;
