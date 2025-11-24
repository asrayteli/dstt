// メイン制御スクリプト

// グローバル変数
let currentFilters = null;

// 初期化
document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    showWelcomeScreen();
});

/**
 * イベントリスナー設定
 */
function setupEventListeners() {
    // アップロードボタン
    document.getElementById('upload-btn').addEventListener('click', () => {
        openUploadModal();
    });

    // リセットボタン
    document.getElementById('reset-btn').addEventListener('click', () => {
        resetAll();
    });

    // アップロードフォーム
    document.getElementById('upload-form').addEventListener('submit', (e) => {
        e.preventDefault();
        handleFileUpload();
    });

    // タブ切り替え
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            switchTab(btn.dataset.tab);
        });
    });

    // 比較モード変更時のUI更新
    const comparisonMode = document.getElementById('comparison-mode');
    if (comparisonMode) {
        comparisonMode.addEventListener('change', () => {
            const baseMonthGroup = document.getElementById('base-month-group');
            if (comparisonMode.value === 'same_year') {
                baseMonthGroup.style.display = 'block';
            } else {
                baseMonthGroup.style.display = 'none';
            }
        });
    }
}

/**
 * ウェルカム画面表示
 */
function showWelcomeScreen() {
    document.getElementById('welcome-screen').classList.remove('hidden');
    document.getElementById('main-content').classList.add('hidden');
}

/**
 * メインコンテンツ表示
 */
function showMainContent() {
    document.getElementById('welcome-screen').classList.add('hidden');
    document.getElementById('main-content').classList.remove('hidden');
    document.getElementById('reset-btn').style.display = 'inline-block';
}

/**
 * アップロードモーダルを開く
 */
function openUploadModal() {
    document.getElementById('upload-modal').classList.remove('hidden');
}

/**
 * アップロードモーダルを閉じる
 */
function closeUploadModal() {
    document.getElementById('upload-modal').classList.add('hidden');
}

/**
 * ファイルアップロード処理
 */
async function handleFileUpload() {
    const subjectFile = document.getElementById('subject-file').files[0];
    const prevYearSubjectFile = document.getElementById('prev-year-subject-file').files[0];
    const siteFile = document.getElementById('site-file').files[0];

    if (!subjectFile) {
        alert('科目別推移表（CSV）は必須です');
        return;
    }

    // FormData作成
    const formData = new FormData();
    formData.append('subject_file', subjectFile);
    if (prevYearSubjectFile) {
        formData.append('prev_year_subject_file', prevYearSubjectFile);
    }
    if (siteFile) {
        formData.append('site_file', siteFile);
    }

    // ローディング表示
    showLoading();
    closeUploadModal();

    try {
        const response = await fetch('/tools/subject_analysis_tool/api/upload', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();

        if (!response.ok || result.error) {
            throw new Error(result.error || 'アップロードに失敗しました');
        }

        // データをDataManagerに設定
        dataManager.setData(result.data);

        // デバッグ情報
        console.log('Loaded data:', result.data);
        console.log('Sites:', dataManager.getSites());
        console.log('Subjects:', dataManager.getSubjects());

        // UI初期化
        filterController.initUI();

        // メインコンテンツ表示
        showMainContent();
        hideLoading();

        alert(`データの読み込みに成功しました\n- 当期データ: ${result.metadata.current_year_count}件\n- 前年データ: ${result.metadata.prev_year_count}件\n- 現場マッピング: ${result.metadata.site_count}件`);

    } catch (error) {
        hideLoading();
        alert(`エラー: ${error.message}`);
        console.error('Upload error:', error);
    }
}

/**
 * フィルターを適用して分析実行
 */
function applyFilters() {
    const filters = filterController.getFilters();

    // バリデーション
    if (filters.months.length === 0) {
        alert('対象月を選択してください');
        return;
    }

    if (filters.subjects.length === 0) {
        alert('科目を選択してください');
        return;
    }

    // 前年比較モードで前年データがない場合
    if (filters.comparisonMode === 'prev_year' && !dataManager.hasPrevYearData()) {
        alert('前年比較を行うには前年データが必要です');
        return;
    }

    // 同年度内比較モードで基準月が選択されていない場合
    if (filters.comparisonMode === 'same_year' && !filters.baseMonth) {
        alert('基準月を選択してください');
        return;
    }

    showLoading();
    currentFilters = filters;

    try {
        // 比較実行
        const results = comparisonEngine.executeComparison(filters);

        // デバッグ情報
        console.log('Filters:', filters);
        console.log('Results count:', results.length);

        // 結果が空の場合の警告
        if (results.length === 0) {
            hideLoading();
            alert('条件に一致するデータが見つかりませんでした。\n\n確認事項：\n- 対象月、現場、科目が選択されているか\n- 前年比較の場合、前年データがアップロードされているか\n- 同年度内比較の場合、基準月以外の月が選択されているか');
            return;
        }

        // 各タブを更新
        updateAllTabs(results, filters);

        hideLoading();
    } catch (error) {
        hideLoading();
        alert(`エラー: ${error.message}`);
        console.error('Analysis error:', error);
    }
}

/**
 * すべてのタブを更新
 */
function updateAllTabs(results, filters) {
    // 詳細一覧
    tableRenderer.renderDetailTable(results, filters);

    // 現場別サマリー
    const siteSummary = comparisonEngine.getSiteSummary();
    tableRenderer.renderSiteSummary(siteSummary, filters);

    // 科目別サマリー
    const subjectSummary = comparisonEngine.getSubjectSummary();
    tableRenderer.renderSubjectSummary(subjectSummary, filters);

    // グラフ
    chartRenderer.renderChart(filters);

    // ヒートマップ
    chartRenderer.renderHeatmap(results, filters);

    // 統計分析
    const stats = comparisonEngine.getStatistics();
    tableRenderer.renderStatistics(stats);
}

/**
 * タブ切り替え
 */
function switchTab(tabName) {
    // タブボタンの状態更新
    document.querySelectorAll('.tab-btn').forEach(btn => {
        if (btn.dataset.tab === tabName) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });

    // タブコンテンツの表示切り替え
    document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.remove('active');
    });

    const targetPane = document.getElementById(`tab-${tabName}`);
    if (targetPane) {
        targetPane.classList.add('active');

        // グラフタブの場合は再描画
        if (tabName === 'chart' && currentFilters) {
            chartRenderer.renderChart(currentFilters);
        }
    }
}

/**
 * リセット
 */
function resetAll() {
    if (!confirm('すべてのデータと設定をリセットしますか？')) {
        return;
    }

    // データをリセット
    dataManager.reset();
    filterController.reset();
    chartRenderer.destroy();

    // UIをリセット
    document.getElementById('upload-form').reset();
    currentFilters = null;

    // ウェルカム画面に戻る
    showWelcomeScreen();
    document.getElementById('reset-btn').style.display = 'none';
}

/**
 * ローディング表示
 */
function showLoading() {
    document.getElementById('loading').classList.remove('hidden');
}

/**
 * ローディング非表示
 */
function hideLoading() {
    document.getElementById('loading').classList.add('hidden');
}

/**
 * グローバル関数（HTMLから呼ばれる）
 */
window.openUploadModal = openUploadModal;
window.closeUploadModal = closeUploadModal;
window.applyFilters = applyFilters;
window.switchTab = switchTab;
window.resetAll = resetAll;
