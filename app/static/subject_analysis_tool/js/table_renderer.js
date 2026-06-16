// テーブルレンダラー - テーブル表示、ソート、ページネーション

class TableRenderer {
    constructor() {
        this.currentPage = 1;
        this.itemsPerPage = 50;
        this.sortColumn = null;
        this.sortDirection = 'asc';
        this.currentData = [];
        this.searchQuery = '';
        this.minItemsPerPage = 3;
        this.maxItemsPerPage = 500;
        this.resizeFrame = null;
        this.resizeObserver = null;

        // 各テーブル用の検索・ソート状態
        this.siteSummarySearch = '';
        this.subjectSummarySearch = '';
        this.subjectSummarySortColumn = null;
        this.subjectSummarySortDirection = 'asc';
        this.profitSearch = '';
        this.profitSortColumn = null;
        this.profitSortDirection = 'asc';

        this.contextMenuEl = null;
        this.detailPopupEl = null;
        this.headerHelpEl = null;
        this.headerHelpAnchor = null;
        this.initializeDetailContextMenu();
        this.initializeHeaderHelpTooltip();
        this.initializeAutoPageSizing();
    }

    /**
     * テーブルのヘッダーにカーソルを載せたとき、その項目が示す内容と
     * 計算方法を補足するツールチップを表示する。
     *
     * 詳細一覧のヘッダーは静的HTML、その他のタブはJSで再描画されるため、
     * document へのイベント委譲で一度だけ登録しておく。
     */
    initializeHeaderHelpTooltip() {
        document.addEventListener('mouseover', (e) => {
            const th = e.target.closest && e.target.closest('th[data-help]');
            if (!th || th === this.headerHelpAnchor) return;
            this.showHeaderHelp(th);
        });

        document.addEventListener('mouseout', (e) => {
            if (!this.headerHelpAnchor) return;
            const th = e.target.closest && e.target.closest('th[data-help]');
            if (!th) return;
            // ヘッダー内の子要素（ソート記号など）への移動では消さない
            if (e.relatedTarget && th.contains(e.relatedTarget)) return;
            this.hideHeaderHelp();
        });

        document.addEventListener('click', () => this.hideHeaderHelp());
        window.addEventListener('scroll', () => this.hideHeaderHelp(), true);
    }

    showHeaderHelp(th) {
        this.hideHeaderHelp();

        const help = th.getAttribute('data-help');
        if (!help) return;

        // ソート記号などを除いた純粋な見出しテキストをタイトルにする
        const titleClone = th.cloneNode(true);
        titleClone.querySelectorAll('.sort-indicator').forEach(el => el.remove());
        const title = titleClone.textContent.trim();

        const tip = document.createElement('div');
        tip.className = 'tooltip sat-header-help';
        tip.innerHTML = `
            <div class="tooltip-content">
                <div class="tooltip-label">${this.escapeText(title)}</div>
                <div class="sat-header-help-body">${this.escapeText(help)}</div>
            </div>
        `;
        document.body.appendChild(tip);

        // ヘッダーの直下を基準に、画面内へ収まる位置を計算する
        const rect = th.getBoundingClientRect();
        const pos = this.calculateTooltipPosition(rect.left + rect.width / 2, rect.bottom, tip);
        tip.style.left = `${pos.x}px`;
        tip.style.top = `${pos.y}px`;

        this.headerHelpEl = tip;
        this.headerHelpAnchor = th;
    }

    hideHeaderHelp() {
        if (this.headerHelpEl) {
            this.headerHelpEl.remove();
            this.headerHelpEl = null;
        }
        this.headerHelpAnchor = null;
    }

    initializeAutoPageSizing() {
        const setup = () => {
            const tableContainer = this.getDetailTableContainer();
            if (!tableContainer || typeof ResizeObserver === 'undefined') return;

            this.resizeObserver = new ResizeObserver(() => {
                if (this.resizeFrame) {
                    cancelAnimationFrame(this.resizeFrame);
                }
                this.resizeFrame = requestAnimationFrame(() => {
                    this.resizeFrame = null;
                    this.updatePageSizeFromTableHeight(true);
                });
            });
            this.resizeObserver.observe(tableContainer);
        };

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', setup, { once: true });
        } else {
            setup();
        }
    }

    getDetailTableContainer() {
        return document.querySelector('#tab-detail .table-container');
    }

    calculateItemsPerPageFromHeight() {
        const tableContainer = this.getDetailTableContainer();
        if (!tableContainer) return this.itemsPerPage;

        const table = document.getElementById('detail-table');
        const tbody = document.getElementById('detail-tbody');
        const headerHeight = table?.querySelector('thead')?.getBoundingClientRect().height || 42;
        const firstRowHeight = tbody?.querySelector('tr')?.getBoundingClientRect().height || 38;
        const availableHeight = tableContainer.clientHeight - headerHeight - 4;

        if (availableHeight <= firstRowHeight) {
            return this.minItemsPerPage;
        }

        const calculated = Math.floor(availableHeight / firstRowHeight);
        return Math.min(this.maxItemsPerPage, Math.max(this.minItemsPerPage, calculated));
    }

    updatePageSizeFromTableHeight(shouldRender = false) {
        const nextItemsPerPage = this.calculateItemsPerPageFromHeight();
        if (nextItemsPerPage === this.itemsPerPage) return false;

        this.itemsPerPage = nextItemsPerPage;

        const totalItems = this.applySearch(this.currentData, this.searchQuery).length;
        const totalPages = Math.max(1, Math.ceil(totalItems / this.itemsPerPage));
        if (this.currentPage > totalPages) {
            this.currentPage = totalPages;
        }

        if (shouldRender && this.currentData.length > 0) {
            this.renderDetailTable(this.currentData, this.getActiveFilters());
        }

        return true;
    }

    /**
     * 適用済みのフィルター（分析実行時のもの）を返す。
     * 未適用のUI変更を拾わないよう、画面上の値ではなく currentFilters を優先する。
     */
    getActiveFilters() {
        if (typeof currentFilters !== 'undefined' && currentFilters) {
            return currentFilters;
        }
        return filterController.getFilters();
    }

    /**
     * HTMLテキスト用エスケープ（現場名等にタグ・引用符が含まれても崩れないように）
     */
    escapeText(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    /**
     * HTML属性値用エスケープ（シングル/ダブルクォート両方を無害化）
     */
    escapeAttr(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    bindSearchInput(input, onCommit) {
        if (!input) return;
        let isComposing = false;

        input.addEventListener('compositionstart', () => {
            isComposing = true;
        });

        input.addEventListener('compositionend', (e) => {
            isComposing = false;
            onCommit(e.target.value);
        });

        input.addEventListener('input', (e) => {
            if (isComposing || e.isComposing) return;
            onCommit(e.target.value);
        });
    }

    /**
     * 右クリックメニューの初期化
     */
    initializeDetailContextMenu() {
        document.addEventListener('click', () => {
            this.hideContextMenu();
            this.hideDetailPopup();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.hideContextMenu();
                this.hideDetailPopup();
            }
        });
    }

    showContextMenu(x, y, onShowDetail) {
        this.hideContextMenu();

        const menu = document.createElement('div');
        menu.className = 'sat-context-menu';
        menu.innerHTML = `<button type="button" class="sat-context-menu-item">詳細を表示</button>`;
        menu.style.left = `${x}px`;
        menu.style.top = `${y}px`;

        const menuItem = menu.querySelector('.sat-context-menu-item');
        menuItem.addEventListener('click', (e) => {
            e.stopPropagation();
            this.hideContextMenu();
            onShowDetail();
        });

        document.body.appendChild(menu);
        this.contextMenuEl = menu;
    }

    hideContextMenu() {
        if (this.contextMenuEl) {
            this.contextMenuEl.remove();
            this.contextMenuEl = null;
        }
    }

    showDetailPopup(x, y, htmlContent) {
        this.hideDetailPopup();

        const popup = document.createElement('div');
        popup.className = 'sat-detail-popup tooltip';
        popup.innerHTML = htmlContent;
        popup.addEventListener('click', (e) => e.stopPropagation());

        document.body.appendChild(popup);

        const pos = this.calculateTooltipPosition(x, y, popup);
        popup.style.left = `${pos.x}px`;
        popup.style.top = `${pos.y}px`;

        this.detailPopupEl = popup;
    }

    hideDetailPopup() {
        if (this.detailPopupEl) {
            this.detailPopupEl.remove();
            this.detailPopupEl = null;
        }
    }

    bindContextMenuForTooltipData(selector, contentBuilder) {
        document.querySelectorAll(selector).forEach(element => {
            element.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                e.stopPropagation();

                const tooltipDataStr = element.getAttribute('data-tooltip');
                if (!tooltipDataStr) return;

                try {
                    // getAttribute はHTMLエンティティを復号済みの文字列を返す
                    const data = JSON.parse(tooltipDataStr);
                    this.showContextMenu(e.clientX, e.clientY, () => {
                        this.showDetailPopup(e.clientX, e.clientY, contentBuilder(data));
                    });
                } catch (error) {
                    console.error('Context menu detail error:', error);
                }
            });
        });
    }

    bindContextMenuForTooltipText(selector) {
        document.querySelectorAll(selector).forEach(element => {
            element.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                e.stopPropagation();

                const tooltipText = element.getAttribute('data-tooltip');
                if (!tooltipText) return;

                this.showContextMenu(e.clientX, e.clientY, () => {
                    this.showDetailPopup(
                        e.clientX,
                        e.clientY,
                        `<div class="tooltip-content"><div style="max-width: 320px;">${tooltipText}</div></div>`
                    );
                });
            });
        });
    }

    /**
     * 詳細一覧テーブルを描画
     */
    renderDetailTable(results, filters) {
        this.currentData = results;
        const tbody = document.getElementById('detail-tbody');
        const countEl = document.getElementById('detail-count');
        const isSimpleMode = filters.comparisonMode === 'simple_view';

        if (!tbody) return;

        if (results.length === 0) {
            const colSpan = isSimpleMode ? 6 : 9;
            tbody.innerHTML = `<tr><td colspan="${colSpan}" class="text-center text-gray-500">データがありません</td></tr>`;
            if (countEl) countEl.textContent = '0';
            this.renderPagination(0);
            return;
        }

        // 検索フィルタリング適用
        const filteredResults = this.applySearch(results, this.searchQuery);
        this.updatePageSizeFromTableHeight(false);

        countEl.textContent = filteredResults.length.toLocaleString();

        // ソート適用
        const sortedResults = this.applySorting([...filteredResults]);
        const totalPages = Math.max(1, Math.ceil(sortedResults.length / this.itemsPerPage));
        if (this.currentPage > totalPages) {
            this.currentPage = totalPages;
        }

        // ページネーション適用
        const paginatedResults = this.applyPagination(sortedResults);

        // テーブル描画
        let html = '';
        paginatedResults.forEach((result, index) => {
            const item = result.item;
            const segment = dataManager.getSegment(item.contract_code);

            // ハイライトクラス決定
            let highlightClass = '';
            if (!isSimpleMode && filters.highlightEnabled && result.isAnomaly) {
                if (result.diff > 0) {
                    highlightClass = 'highlight-increase';
                } else if (result.diff < 0) {
                    highlightClass = 'highlight-decrease';
                }
                // 極端な異常値
                if (result.diffRateAbs > filters.thresholdRate * 2 ||
                    result.diffAbs > filters.thresholdAmount * 2) {
                    highlightClass = 'highlight-severe';
                }
            }

            // 表示値の決定
            let currentValueDisplay = this.formatNumber(result.currentValue);
            let comparisonValueDisplay = this.formatNumber(result.comparisonValue);
            let diffDisplay = this.formatNumber(result.diff, true);
            let diffRateDisplay = this.formatNumber(result.diffRate, true) + '%';

            // 表示モードに応じて調整
            if (isSimpleMode || filters.displayMode === 'value_only') {
                comparisonValueDisplay = '-';
                diffDisplay = '-';
                diffRateDisplay = '-';
            } else if (filters.displayMode === 'value_diff') {
                diffRateDisplay = '-';
            } else if (filters.displayMode === 'value_rate') {
                diffDisplay = '-';
            } else if (filters.displayMode === 'diff_only') {
                currentValueDisplay = '-';
                comparisonValueDisplay = '-';
                diffRateDisplay = '-';
            } else if (filters.displayMode === 'rate_only') {
                currentValueDisplay = '-';
                comparisonValueDisplay = '-';
                diffDisplay = '-';
            }

            // ツールチップ用のデータ属性を追加
            const tooltipData = JSON.stringify({
                siteName: item.site_name,
                corpName: item.corp_name,
                segment: segment,
                subject: item.subject_name,
                month: result.month,
                currentValue: result.currentValue,
                comparisonValue: result.comparisonValue,
                comparisonLabel: result.comparisonLabel,
                diff: result.diff,
                diffRate: result.diffRate,
                hasComparison: !!result.hasComparison,
                isRevenue: item.is_revenue
            });

            html += `
                <tr class="${highlightClass}" data-tooltip="${this.escapeAttr(tooltipData)}" data-row-index="${index}">
                    <td>${this.escapeText(item.site_name)}</td>
                    <td>${this.escapeText(item.corp_name)}</td>
                    <td>${this.escapeText(segment)}</td>
                    <td>${this.escapeText(item.subject_name)}</td>
                    <td>${result.month}月</td>
                    <td class="text-right">${currentValueDisplay}</td>
                    <td class="text-right compare-only">${comparisonValueDisplay}</td>
                    <td class="text-right compare-only">${diffDisplay}</td>
                    <td class="text-right compare-only">${diffRateDisplay}</td>
                </tr>
            `;
        });

        tbody.innerHTML = html;

        // ページネーション更新
        this.renderPagination(sortedResults.length);

        // ソートイベント設定
        this.setupSortListeners();

        // ツールチップイベント設定
        this.setupTooltipListeners();
    }

    /**
     * 現場別サマリーを描画
     */
    renderSiteSummary(summary, filters) {
        const container = document.getElementById('site-summary-content');
        const isSimpleMode = filters.comparisonMode === 'simple_view';
        if (!container) return;

        if (summary.length === 0) {
            container.innerHTML = '<p class="text-center text-gray-500">データがありません</p>';
            return;
        }

        let html = '';
        summary.forEach((site, index) => {
            html += `
                <div class="summary-card">
                    <div class="summary-card-header" onclick="toggleSummaryCard(${index})">
                        <div>
                            <div class="summary-card-title">
                                ${this.escapeText(site.siteName)} (${this.escapeText(site.corpName)})
                            </div>
                            <div class="text-sm text-gray-600">
                                ${this.escapeText(site.segment)}
                                <span class="compare-only"> | 異常値: ${site.anomalyCount}件</span>
                                <span class="simple-only"> | 単純表示</span>
                            </div>
                        </div>
                        <div class="text-right">
                            <div class="text-lg font-bold ${isSimpleMode ? '' : (site.totalDiff >= 0 ? 'text-red-600' : 'text-blue-600')}">
                                ${isSimpleMode ? this.formatNumber(site.totalCurrent) : this.formatNumber(site.totalDiff, true)}
                            </div>
                            <div class="text-sm text-gray-600 compare-only">
                                (${this.formatNumber(site.totalDiffRate, true)}%)
                            </div>
                        </div>
                    </div>
                    <div class="summary-card-body" id="site-summary-${index}" style="display: none;">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th data-help="収支科目の名称です（基本請負料・人件費 など）。">科目</th>
                                    <th data-help="データの対象月です。">月</th>
                                    <th data-help="当期CSVの、その月・科目の金額です。原価科目は符号反転済みのマイナス値で表示されます。">当期値</th>
                                    <th class="compare-only" data-help="比較モードに応じた比較元の金額です（前年同月・基準月 など）。">比較値</th>
                                    <th class="compare-only" data-help="当期値 − 比較値。プラスは増加、マイナスは減少を表します。">差異</th>
                                    <th class="compare-only" data-help="差異 ÷ 比較値 × 100。比較値が0の場合は0%になります。">差異率</th>
                                </tr>
                            </thead>
                            <tbody>
            `;

            site.items.forEach(result => {
                const highlightClass = !isSimpleMode && filters.highlightEnabled && result.isAnomaly
                    ? (result.diff > 0 ? 'highlight-increase' : 'highlight-decrease')
                    : '';

                // ツールチップデータ
                const tooltipData = JSON.stringify({
                    siteName: site.siteName,
                    corpName: site.corpName,
                    segment: site.segment,
                    subject: result.item.subject_name,
                    month: result.month,
                    currentValue: result.currentValue,
                    comparisonValue: result.comparisonValue,
                    comparisonLabel: result.comparisonLabel,
                    diff: result.diff,
                    diffRate: result.diffRate,
                    hasComparison: !!result.hasComparison,
                    isRevenue: result.item.is_revenue
                });

                html += `
                    <tr class="${highlightClass}" data-tooltip="${this.escapeAttr(tooltipData)}">
                        <td>${this.escapeText(result.item.subject_name)}</td>
                        <td>${result.month}月</td>
                        <td class="text-right">${this.formatNumber(result.currentValue)}</td>
                        <td class="text-right compare-only">${this.formatNumber(result.comparisonValue)}</td>
                        <td class="text-right compare-only">${this.formatNumber(result.diff, true)}</td>
                        <td class="text-right compare-only">${this.formatNumber(result.diffRate, true)}%</td>
                    </tr>
                `;
            });

            html += `
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;

        // ツールチップ設定
        this.setupSiteSummaryTooltipListeners();
    }

    /**
     * 現場別サマリーのツールチップリスナー設定
     */
    setupSiteSummaryTooltipListeners() {
        this.bindContextMenuForTooltipData('#site-summary-content tbody tr[data-tooltip]', (data) => {
            const typeLabel = data.isRevenue ? '売上' : '原価';
            const signNote = data.isRevenue ? '' : '（符号反転済み）';
            const comparisonHtml = data.hasComparison
                ? `
                    <div class="tooltip-row"><span class="tooltip-key">比較元 (${this.escapeText(data.comparisonLabel)}):</span><span class="tooltip-value">${this.formatNumber(data.comparisonValue)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">差異:</span><span class="tooltip-value" style="color: ${data.diff >= 0 ? '#fca5a5' : '#93c5fd'}">${this.formatNumber(data.diff, true)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">差異率:</span><span class="tooltip-value" style="color: ${data.diffRate >= 0 ? '#fca5a5' : '#93c5fd'}">${this.formatNumber(data.diffRate, true)}%</span></div>
                `
                : '<div class="tooltip-row"><span class="tooltip-key">モード:</span><span class="tooltip-value">単純表示（比較なし）</span></div>';

            return `
                <div class="tooltip-content">
                    <div class="tooltip-label">📊 現場別比較情報 ${signNote}</div>
                    <div class="tooltip-row"><span class="tooltip-key">現場:</span><span class="tooltip-value">${this.escapeText(data.siteName)}</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">科目:</span><span class="tooltip-value">${this.escapeText(data.subject)} (${typeLabel})</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">対象月:</span><span class="tooltip-value">${data.month}月</span></div>
                    <hr style="border-color: rgba(255,255,255,0.2); margin: 0.5rem 0;">
                    <div class="tooltip-row"><span class="tooltip-key">当期値:</span><span class="tooltip-value">${this.formatNumber(data.currentValue)}円</span></div>
                    ${comparisonHtml}
                </div>
            `;
        });
    }

    /**
     * 科目別サマリーを描画
     */
    renderSubjectSummary(summary, filters) {
        const container = document.getElementById('subject-summary-content');
        const isSimpleMode = filters.comparisonMode === 'simple_view';
        if (!container) return;

        if (summary.length === 0) {
            container.innerHTML = '<p class="text-center text-gray-500">データがありません</p>';
            return;
        }

        // 検索フィルタリング
        let filteredSummary = summary;
        if (this.subjectSummarySearch && this.subjectSummarySearch.trim() !== '') {
            const query = this.subjectSummarySearch.toLowerCase();
            filteredSummary = summary.filter(subject =>
                subject.subjectName.toLowerCase().includes(query)
            );
        }

        // ソート適用
        if (this.subjectSummarySortColumn) {
            filteredSummary = this.sortSubjectSummary([...filteredSummary]);
        }

        let html = `
            <div class="mb-4">
                <input type="text" id="subject-summary-search" placeholder="科目名で検索..." class="form-input" style="max-width: 300px;" value="${this.escapeAttr(this.subjectSummarySearch)}">
            </div>
            <table class="data-table">
                <thead>
                    <tr>
                        <th class="sortable-subject" data-column="subjectName" data-help="収支科目の名称です。同じ科目を全現場・全選択月で集約した行です。">科目名 <span class="sort-indicator"></span></th>
                        <th class="sortable-subject" data-column="siteCount" data-help="その科目のデータを持つ現場（契約コード）の数です。">現場数 <span class="sort-indicator"></span></th>
                        <th class="sortable-subject compare-only" data-column="anomalyCount" data-help="差異が閾値（差異率・差異金額）を超えた件数です。判定条件（AND/OR）に従って数えます。">異常値件数 <span class="sort-indicator"></span></th>
                        <th class="sortable-subject" data-column="totalCurrent" data-help="その科目の当期値を、選択した全現場・全月で合計した金額です。原価科目は符号反転済みのマイナス値で集計されます。">当期合計 <span class="sort-indicator"></span></th>
                        <th class="sortable-subject compare-only" data-column="totalComparison" data-help="その科目の比較値（前年同月など）を、選択した全現場・全月で合計した金額です。">比較合計 <span class="sort-indicator"></span></th>
                        <th class="sortable-subject compare-only" data-column="totalDiff" data-help="当期合計 − 比較合計。プラスは増加、マイナスは減少を表します。">差異 <span class="sort-indicator"></span></th>
                        <th class="sortable-subject compare-only" data-column="totalDiffRate" data-help="差異 ÷ 比較合計 × 100。比較合計が0の場合は0%になります。">差異率 <span class="sort-indicator"></span></th>
                    </tr>
                </thead>
                <tbody>
        `;

        filteredSummary.forEach(subject => {
            const highlightClass = !isSimpleMode && filters.highlightEnabled &&
                (subject.anomalyCount > 0 ||
                    Math.abs(subject.totalDiffRate) > filters.thresholdRate)
                ? (subject.totalDiff > 0 ? 'highlight-increase' : 'highlight-decrease')
                : '';

            // ツールチップデータ
            const tooltipData = JSON.stringify({
                subjectName: subject.subjectName,
                siteCount: subject.siteCount,
                anomalyCount: subject.anomalyCount,
                totalCurrent: subject.totalCurrent,
                totalComparison: subject.totalComparison,
                totalDiff: subject.totalDiff,
                totalDiffRate: subject.totalDiffRate,
                avgCurrent: subject.totalCurrent / subject.siteCount,
                avgComparison: subject.totalComparison / subject.siteCount,
                avgDiff: subject.totalDiff / subject.siteCount,
                hasComparison: !isSimpleMode
            });

            html += `
                <tr class="${highlightClass}" data-tooltip="${this.escapeAttr(tooltipData)}">
                    <td>${this.escapeText(subject.subjectName)}</td>
                    <td class="text-right">${subject.siteCount}</td>
                    <td class="text-right compare-only">${subject.anomalyCount}</td>
                    <td class="text-right">${this.formatNumber(subject.totalCurrent)}</td>
                    <td class="text-right compare-only">${this.formatNumber(subject.totalComparison)}</td>
                    <td class="text-right compare-only">${this.formatNumber(subject.totalDiff, true)}</td>
                    <td class="text-right compare-only">${this.formatNumber(subject.totalDiffRate, true)}%</td>
                </tr>
            `;
        });

        html += `
                </tbody>
            </table>
        `;

        container.innerHTML = html;

        // 再描画後もソートインジケーターを維持
        if (this.subjectSummarySortColumn) {
            const indicator = container.querySelector(
                `.sortable-subject[data-column="${this.subjectSummarySortColumn}"] .sort-indicator`
            );
            if (indicator) {
                indicator.textContent = this.subjectSummarySortDirection === 'asc' ? ' ▲' : ' ▼';
            }
        }

        // ツールチップ設定
        this.setupSubjectSummaryTooltipListeners();

        // 検索とソートのリスナー設定
        this.setupSubjectSummaryListeners(summary, filters);
    }

    /**
     * 科目別サマリーのツールチップリスナー設定
     */
    setupSubjectSummaryTooltipListeners() {
        this.bindContextMenuForTooltipData('#subject-summary-content tbody tr[data-tooltip]', (data) => {
            const comparisonHtml = data.hasComparison
                ? `
                    <div class="tooltip-row"><span class="tooltip-key">比較合計:</span><span class="tooltip-value">${this.formatNumber(data.totalComparison)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">差異合計:</span><span class="tooltip-value" style="color: ${data.totalDiff >= 0 ? '#fca5a5' : '#93c5fd'}">${this.formatNumber(data.totalDiff, true)}円</span></div>
                    <hr style="border-color: rgba(255,255,255,0.2); margin: 0.5rem 0;">
                    <div class="tooltip-row"><span class="tooltip-key">現場平均（比較）:</span><span class="tooltip-value">${this.formatNumber(data.avgComparison)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">現場平均（差異）:</span><span class="tooltip-value" style="color: ${data.avgDiff >= 0 ? '#fca5a5' : '#93c5fd'}">${this.formatNumber(data.avgDiff, true)}円</span></div>
                    <hr style="border-color: rgba(255,255,255,0.2); margin: 0.5rem 0;">
                    <div class="tooltip-row"><span class="tooltip-key">異常値件数:</span><span class="tooltip-value text-red-400">${data.anomalyCount}件</span></div>
                `
                : '<div class="tooltip-row"><span class="tooltip-key">モード:</span><span class="tooltip-value">単純表示（比較なし）</span></div>';

            return `
                <div class="tooltip-content">
                    <div class="tooltip-label">📋 科目別サマリー詳細</div>
                    <div class="tooltip-row"><span class="tooltip-key">科目:</span><span class="tooltip-value">${this.escapeText(data.subjectName)}</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">対象現場数:</span><span class="tooltip-value">${data.siteCount}現場</span></div>
                    <hr style="border-color: rgba(255,255,255,0.2); margin: 0.5rem 0;">
                    <div class="tooltip-row"><span class="tooltip-key">当期合計:</span><span class="tooltip-value">${this.formatNumber(data.totalCurrent)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">現場平均（当期）:</span><span class="tooltip-value">${this.formatNumber(data.avgCurrent)}円</span></div>
                    <hr style="border-color: rgba(255,255,255,0.2); margin: 0.5rem 0;">
                    ${comparisonHtml}
                </div>
            `;
        });
    }

    /**
     * 統計分析を描画
     */
    renderStatistics(stats) {
        const container = document.getElementById('statistics-content');
        if (!container) return;

        if (!stats) {
            container.innerHTML = '<p class="text-center text-gray-500">データがありません</p>';
            return;
        }

        if (stats.isSimpleMode) {
            const html = `
                <div class="stats-grid mb-4">
                    <div class="stat-card">
                        <div class="stat-label">📊 総データ件数</div>
                        <div class="stat-value">${stats.count.toLocaleString()}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">💰 当期値合計</div>
                        <div class="stat-value">${this.formatNumber(stats.currentValue.sum)}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">📐 当期値平均</div>
                        <div class="stat-value">${this.formatNumber(stats.currentValue.avg)}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">📍 当期値中央値</div>
                        <div class="stat-value">${this.formatNumber(stats.currentValue.median)}</div>
                    </div>
                </div>
                ${stats.revenue && stats.cost ? `
                    <div class="grid grid-cols-2 gap-4 mb-4">
                        <div class="bg-green-50 border border-green-200 rounded p-4">
                            <h4 class="font-semibold mb-2 text-green-800">💵 売上統計</h4>
                            <div class="space-y-2 text-sm">
                                <div class="flex justify-between"><span>合計:</span><span class="font-semibold">${this.formatNumber(stats.revenue.sum)}</span></div>
                                <div class="flex justify-between"><span>平均:</span><span>${this.formatNumber(stats.revenue.avg)}</span></div>
                                <div class="flex justify-between"><span>件数:</span><span>${stats.revenue.count}</span></div>
                            </div>
                        </div>
                        <div class="bg-orange-50 border border-orange-200 rounded p-4">
                            <h4 class="font-semibold mb-2 text-orange-800">💸 原価統計</h4>
                            <div class="space-y-2 text-sm">
                                <div class="flex justify-between"><span>合計:</span><span class="font-semibold">${this.formatNumber(stats.cost.sum)}</span></div>
                                <div class="flex justify-between"><span>平均:</span><span>${this.formatNumber(stats.cost.avg)}</span></div>
                                <div class="flex justify-between"><span>件数:</span><span>${stats.cost.count}</span></div>
                            </div>
                        </div>
                    </div>
                ` : ''}
            `;
            container.innerHTML = html;
            return;
        }

        const html = `
            <div class="stats-grid mb-4">
                <div class="stat-card" data-tooltip="分析対象となる全データ行の数">
                    <div class="stat-label">📊 総データ件数</div>
                    <div class="stat-value">${stats.count.toLocaleString()}</div>
                </div>
                <div class="stat-card" data-tooltip="設定した閾値を超える異常値の件数">
                    <div class="stat-label">⚠️ 異常値件数</div>
                    <div class="stat-value text-red-600">${stats.anomalyCount.toLocaleString()}</div>
                    <div class="text-xs text-gray-500 mt-1">${((stats.anomalyCount / stats.count) * 100).toFixed(1)}%</div>
                </div>
                <div class="stat-card" data-tooltip="当期の全データ値の合計額">
                    <div class="stat-label">💰 当期値合計</div>
                    <div class="stat-value">${this.formatNumber(stats.currentValue.sum)}</div>
                </div>
                <div class="stat-card" data-tooltip="比較元との差異の合計。正の値は増加、負の値は減少を示す。差異合計 = 当期値合計 - 比較値合計">
                    <div class="stat-label">📈 差異合計</div>
                    <div class="stat-value ${stats.diff.sum >= 0 ? 'text-red-600' : 'text-blue-600'}">
                        ${this.formatNumber(stats.diff.sum, true)}
                    </div>
                </div>
            </div>

            ${stats.revenue && stats.cost ? `
                <div class="grid grid-cols-2 gap-4 mb-4">
                    <div class="bg-green-50 border border-green-200 rounded p-4" data-tooltip="売上項目（基本請負料、その他請負料）の合計と平均">
                        <h4 class="font-semibold mb-2 text-green-800">💵 売上統計</h4>
                        <div class="space-y-2 text-sm">
                            <div class="flex justify-between">
                                <span>合計:</span>
                                <span class="font-semibold">${this.formatNumber(stats.revenue.sum)}</span>
                            </div>
                            <div class="flex justify-between">
                                <span>平均:</span>
                                <span>${this.formatNumber(stats.revenue.avg)}</span>
                            </div>
                            <div class="flex justify-between">
                                <span>件数:</span>
                                <span>${stats.revenue.count}</span>
                            </div>
                        </div>
                    </div>
                    <div class="bg-orange-50 border border-orange-200 rounded p-4" data-tooltip="原価項目（売上以外のすべて）の合計と平均。符号反転済み">
                        <h4 class="font-semibold mb-2 text-orange-800">💸 原価統計</h4>
                        <div class="space-y-2 text-sm">
                            <div class="flex justify-between">
                                <span>合計:</span>
                                <span class="font-semibold">${this.formatNumber(stats.cost.sum)}</span>
                            </div>
                            <div class="flex justify-between">
                                <span>平均:</span>
                                <span>${this.formatNumber(stats.cost.avg)}</span>
                            </div>
                            <div class="flex justify-between">
                                <span>件数:</span>
                                <span>${stats.cost.count}</span>
                            </div>
                        </div>
                    </div>
                </div>
            ` : ''}

            <div class="grid grid-cols-2 gap-4 mb-4">
                <div class="bg-white border rounded p-4">
                    <h4 class="font-semibold mb-3">📊 当期値の分布統計</h4>
                    <div class="space-y-2 text-sm">
                        <div class="flex justify-between" data-tooltip="全データの平均値。データの中心傾向を示す">
                            <span>平均:</span>
                            <span>${this.formatNumber(stats.currentValue.avg)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="データを並べたときの真ん中の値。外れ値の影響を受けにくい">
                            <span>中央値:</span>
                            <span>${this.formatNumber(stats.currentValue.median)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="データのばらつき度合い。値が大きいほど散らばっている">
                            <span>標準偏差:</span>
                            <span>${this.formatNumber(stats.currentValue.stdDev)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="標準偏差の2乗。データの分散度を示す">
                            <span>分散:</span>
                            <span>${this.formatNumber(stats.currentValue.variance)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="最大値と最小値の差。データの範囲を示す">
                            <span>範囲:</span>
                            <span>${this.formatNumber(stats.currentValue.range)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="最も大きい値">
                            <span>最大値:</span>
                            <span>${this.formatNumber(stats.currentValue.max)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="最も小さい値">
                            <span>最小値:</span>
                            <span>${this.formatNumber(stats.currentValue.min)}</span>
                        </div>
                    </div>
                </div>

                <div class="bg-white border rounded p-4">
                    <h4 class="font-semibold mb-3">📈 四分位統計</h4>
                    <div class="space-y-2 text-sm">
                        <div class="flex justify-between" data-tooltip="データの下位25%の境界値">
                            <span>第1四分位数 (Q1):</span>
                            <span>${this.formatNumber(stats.currentValue.q1)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="データの中央値（第2四分位数）">
                            <span>第2四分位数 (Q2):</span>
                            <span>${this.formatNumber(stats.currentValue.median)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="データの上位25%の境界値">
                            <span>第3四分位数 (Q3):</span>
                            <span>${this.formatNumber(stats.currentValue.q3)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="Q3 - Q1。データの中央50%の範囲。外れ値検出に使用">
                            <span>四分位範囲 (IQR):</span>
                            <span>${this.formatNumber(stats.currentValue.iqr)}</span>
                        </div>
                        <div class="text-xs text-gray-500 mt-3 p-2 bg-gray-50 rounded">
                            💡 IQRは外れ値判定に使用。Q1-1.5×IQR未満、またはQ3+1.5×IQR超が外れ値とされる
                        </div>
                    </div>
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div class="bg-white border rounded p-4">
                    <h4 class="font-semibold mb-3">📊 差異統計</h4>
                    <div class="space-y-2 text-sm">
                        <div class="flex justify-between" data-tooltip="全差異の合計。正は増加傾向、負は減少傾向">
                            <span>合計:</span>
                            <span class="${stats.diff.sum >= 0 ? 'text-red-600' : 'text-blue-600'} font-semibold">${this.formatNumber(stats.diff.sum, true)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="差異の平均値">
                            <span>平均:</span>
                            <span>${this.formatNumber(stats.diff.avg, true)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="差異の中央値">
                            <span>中央値:</span>
                            <span>${this.formatNumber(stats.diff.median, true)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="差異のばらつき度合い">
                            <span>標準偏差:</span>
                            <span>${this.formatNumber(stats.diff.stdDev)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="最も増加した項目の差異">
                            <span>最大増加:</span>
                            <span class="text-red-600">${this.formatNumber(stats.diff.max, true)}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="最も減少した項目の差異">
                            <span>最大減少:</span>
                            <span class="text-blue-600">${this.formatNumber(stats.diff.min, true)}</span>
                        </div>
                    </div>
                </div>

                <div class="bg-white border rounded p-4">
                    <h4 class="font-semibold mb-3">📊 差異の分布</h4>
                    <div class="space-y-2 text-sm">
                        <div class="flex justify-between" data-tooltip="差異がプラス（増加）の項目数">
                            <span class="text-red-600">▲ 増加項目:</span>
                            <span class="text-red-600 font-semibold">${stats.diff.positiveCount}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="差異がマイナス（減少）の項目数">
                            <span class="text-blue-600">▼ 減少項目:</span>
                            <span class="text-blue-600 font-semibold">${stats.diff.negativeCount}</span>
                        </div>
                        <div class="flex justify-between" data-tooltip="差異がゼロ（変化なし）の項目数">
                            <span class="text-gray-600">━ 変化なし:</span>
                            <span class="text-gray-600">${stats.diff.zeroCount}</span>
                        </div>
                        <div class="mt-3 pt-3 border-t">
                            <div class="flex justify-between" data-tooltip="差異率の平均値（%）">
                                <span>平均差異率:</span>
                                <span>${this.formatNumber(stats.diffRate.avg, true)}%</span>
                            </div>
                            <div class="flex justify-between" data-tooltip="差異率の中央値（%）">
                                <span>中央差異率:</span>
                                <span>${this.formatNumber(stats.diffRate.median, true)}%</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        container.innerHTML = html;

        // 統計ツールチップの設定
        this.setupStatisticsTooltipListeners();
    }

    /**
     * 統計分析のツールチップリスナー設定
     */
    setupStatisticsTooltipListeners() {
        this.bindContextMenuForTooltipText('#statistics-content [data-tooltip]');
    }

    /**
     * 利益分析を描画
     */
    renderProfitAnalysis(profitData, filters) {
        const container = document.getElementById('profit-content');
        const isSimpleMode = filters.comparisonMode === 'simple_view';
        if (!container) return;

        // 売上項目が選択されていない場合はエラーメッセージ
        const hasRevenue = filters.subjects.some(subject =>
            subject === "基本請負料" || subject === "その他請負料"
        );

        if (!hasRevenue) {
            container.innerHTML = `
                <div class="bg-yellow-50 border border-yellow-200 rounded p-4 text-center">
                    <p class="text-yellow-800 font-semibold">⚠️ 利益分析を表示するには売上項目を選択してください</p>
                    <p class="text-sm text-yellow-700 mt-2">「科目選択」で「基本請負料」または「その他請負料」を選択して、分析を実行してください</p>
                </div>
            `;
            return;
        }

        if (profitData && profitData.error) {
            container.innerHTML = `
                <div class="bg-yellow-50 border border-yellow-200 rounded p-4 text-center">
                    <p class="text-yellow-800 font-semibold">⚠️ ${this.escapeText(profitData.error)}</p>
                </div>
            `;
            return;
        }

        if (!profitData || !Array.isArray(profitData) || profitData.length === 0) {
            container.innerHTML = '<p class="text-center text-gray-500">データがありません</p>';
            return;
        }

        // サマリー統計を計算
        // 注: costは負の値（支出を表す）
        const totalRevenue = profitData.reduce((sum, item) => sum + item.revenue, 0);
        const totalCost = profitData.reduce((sum, item) => sum + item.cost, 0);
        const totalProfit = totalRevenue + totalCost;  // costが負なので足し算
        const avgProfitRate = totalRevenue > 0 ? (totalProfit / totalRevenue * 100) : 0;

        const totalRevenueComparison = profitData.reduce((sum, item) => sum + item.revenueComparison, 0);
        const totalCostComparison = profitData.reduce((sum, item) => sum + item.costComparison, 0);
        const totalProfitComparison = totalRevenueComparison + totalCostComparison;  // costが負なので足し算
        const avgProfitRateComparison = totalRevenueComparison > 0 ? (totalProfitComparison / totalRevenueComparison * 100) : 0;

        const profitDiff = totalProfit - totalProfitComparison;
        const profitRateDiff = avgProfitRate - avgProfitRateComparison;

        // 検索フィルタリング
        let filteredProfitData = profitData;
        if (this.profitSearch && this.profitSearch.trim() !== '') {
            const query = this.profitSearch.toLowerCase();
            filteredProfitData = profitData.filter(item =>
                item.siteName.toLowerCase().includes(query) ||
                item.corpName.toLowerCase().includes(query) ||
                String(item.month).includes(query)
            );
        }

        // ソート適用
        if (this.profitSortColumn) {
            filteredProfitData = this.sortProfitData([...filteredProfitData]);
        }

        let html = `
            <div class="mb-4">
                <input type="text" id="profit-search" placeholder="現場名・法人名・月で検索..." class="form-input" style="max-width: 300px;" value="${this.escapeAttr(this.profitSearch)}">
            </div>
            <div class="stats-grid mb-6">
                <div class="stat-card">
                    <div class="stat-label">総売上</div>
                    <div class="stat-value text-green-600">${this.formatNumber(totalRevenue)}</div>
                    <div class="text-xs text-gray-500 mt-1 compare-only">比較: ${this.formatNumber(totalRevenueComparison)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">総原価</div>
                    <div class="stat-value text-orange-600">${this.formatNumber(totalCost)}</div>
                    <div class="text-xs text-gray-500 mt-1 compare-only">比較: ${this.formatNumber(totalCostComparison)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">総利益</div>
                    <div class="stat-value ${totalProfit >= 0 ? 'text-blue-600' : 'text-red-600'}">
                        ${this.formatNumber(totalProfit, true)}
                    </div>
                    <div class="text-xs ${profitDiff >= 0 ? 'text-blue-600' : 'text-red-600'} mt-1 compare-only">
                        差異: ${this.formatNumber(profitDiff, true)}
                    </div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">平均利益率</div>
                    <div class="stat-value ${avgProfitRate >= 0 ? 'text-blue-600' : 'text-red-600'}">
                        ${this.formatNumber(avgProfitRate, true)}%
                    </div>
                    <div class="text-xs ${profitRateDiff >= 0 ? 'text-blue-600' : 'text-red-600'} mt-1 compare-only">
                        差異: ${this.formatNumber(profitRateDiff, true)}%pt
                    </div>
                </div>
            </div>

            <div class="table-container">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th class="sortable-profit" data-column="siteName" data-help="対象となる現場の名称です。利益は現場×月の単位で集計しています。">現場名 <span class="sort-indicator"></span></th>
                            <th class="sortable-profit" data-column="corpName" data-help="現場を契約している法人（顧客）の名称です。">法人名 <span class="sort-indicator"></span></th>
                            <th class="sortable-profit" data-column="month" data-help="データの対象月です。">月 <span class="sort-indicator"></span></th>
                            <th class="sortable-profit" data-column="revenue" data-help="売上科目（基本請負料・その他請負料）の当期値を、その現場・月で合計した金額です。">売上 <span class="sort-indicator"></span></th>
                            <th class="sortable-profit" data-column="cost" data-help="売上以外の科目（原価）の当期値を合計した金額です。符号反転済みのため、マイナス値で表示されます。">原価 <span class="sort-indicator"></span></th>
                            <th class="sortable-profit" data-column="profit" data-help="売上 ＋ 原価。原価はマイナス値で保持しているため、実質は「売上 − 原価」になります。">利益 <span class="sort-indicator"></span></th>
                            <th class="sortable-profit" data-column="profitRate" data-help="利益 ÷ 売上 × 100。売上が0の場合は0%になります。">利益率(%) <span class="sort-indicator"></span></th>
                            <th class="sortable-profit compare-only" data-column="profitComparison" data-help="比較元（前年同月など）の「比較売上 ＋ 比較原価」で求めた利益です。">比較利益 <span class="sort-indicator"></span></th>
                            <th class="sortable-profit compare-only" data-column="profitRateComparison" data-help="比較利益 ÷ 比較売上 × 100。比較元の利益率です。">比較利益率(%) <span class="sort-indicator"></span></th>
                            <th class="sortable-profit compare-only" data-column="profitDiff" data-help="当期利益 − 比較利益。プラスは利益増、マイナスは利益減を表します。">利益差異 <span class="sort-indicator"></span></th>
                            <th class="sortable-profit compare-only" data-column="profitRateDiff" data-help="当期利益率 − 比較利益率。単位はポイント（%pt）です。">利益率差異 <span class="sort-indicator"></span></th>
                        </tr>
                    </thead>
                    <tbody>
        `;

        filteredProfitData.forEach(item => {
            const profitDiffItem = item.profit - item.profitComparison;
            const profitRateDiffItem = item.profitRate - item.profitRateComparison;

            // ハイライト判定（利益率が大きく変動した場合）
            let highlightClass = '';
            if (!isSimpleMode && Math.abs(profitRateDiffItem) > filters.thresholdRate) {
                highlightClass = profitRateDiffItem > 0 ? 'highlight-decrease' : 'highlight-increase';
            }

            // ツールチップデータ
            const tooltipData = JSON.stringify({
                siteName: item.siteName,
                corpName: item.corpName,
                month: item.month,
                revenue: item.revenue,
                cost: item.cost,
                profit: item.profit,
                profitRate: item.profitRate,
                revenueComparison: item.revenueComparison,
                costComparison: item.costComparison,
                profitComparison: item.profitComparison,
                profitRateComparison: item.profitRateComparison,
                comparisonLabel: item.comparisonLabel,
                hasComparison: !isSimpleMode
            });

            html += `
                <tr class="${highlightClass}" data-tooltip="${this.escapeAttr(tooltipData)}">
                    <td>${this.escapeText(item.siteName)}</td>
                    <td>${this.escapeText(item.corpName)}</td>
                    <td>${item.month}月</td>
                    <td class="text-right text-green-600">${this.formatNumber(item.revenue)}</td>
                    <td class="text-right text-orange-600">${this.formatNumber(item.cost)}</td>
                    <td class="text-right font-semibold ${item.profit >= 0 ? 'text-blue-600' : 'text-red-600'}">
                        ${this.formatNumber(item.profit, true)}
                    </td>
                    <td class="text-right font-semibold">${this.formatNumber(item.profitRate, true)}%</td>
                    <td class="text-right text-gray-600 compare-only">${this.formatNumber(item.profitComparison, true)}</td>
                    <td class="text-right text-gray-600 compare-only">${this.formatNumber(item.profitRateComparison, true)}%</td>
                    <td class="text-right ${profitDiffItem >= 0 ? 'text-blue-600' : 'text-red-600'} compare-only">
                        ${this.formatNumber(profitDiffItem, true)}
                    </td>
                    <td class="text-right ${profitRateDiffItem >= 0 ? 'text-blue-600' : 'text-red-600'} compare-only">
                        ${this.formatNumber(profitRateDiffItem, true)}%pt
                    </td>
                </tr>
            `;
        });

        html += `
                    </tbody>
                </table>
            </div>
        `;

        container.innerHTML = html;

        // 再描画後もソートインジケーターを維持
        if (this.profitSortColumn) {
            const indicator = container.querySelector(
                `.sortable-profit[data-column="${this.profitSortColumn}"] .sort-indicator`
            );
            if (indicator) {
                indicator.textContent = this.profitSortDirection === 'asc' ? ' ▲' : ' ▼';
            }
        }

        // ツールチップ設定
        this.setupProfitTooltipListeners();

        // 検索とソートのリスナー設定
        this.setupProfitListeners(profitData, filters);
    }

    /**
     * 利益分析のツールチップリスナー設定
     */
    setupProfitTooltipListeners() {
        this.bindContextMenuForTooltipData('#profit-content tbody tr[data-tooltip]', (data) => {
            const comparisonHtml = data.hasComparison
                ? `
                    <hr style="border-color: rgba(255,255,255,0.2); margin: 0.5rem 0;">
                    <div class="tooltip-row"><span class="tooltip-key">比較元 (${this.escapeText(data.comparisonLabel)}):</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">  売上:</span><span class="tooltip-value">${this.formatNumber(data.revenueComparison)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">  原価:</span><span class="tooltip-value">${this.formatNumber(data.costComparison)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">  利益:</span><span class="tooltip-value">${this.formatNumber(data.profitComparison, true)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">  利益率:</span><span class="tooltip-value">${this.formatNumber(data.profitRateComparison, true)}%</span></div>
                `
                : `
                    <hr style="border-color: rgba(255,255,255,0.2); margin: 0.5rem 0;">
                    <div class="tooltip-row"><span class="tooltip-key">モード:</span><span class="tooltip-value">単純表示（比較なし）</span></div>
                `;

            return `
                <div class="tooltip-content">
                    <div class="tooltip-label">💰 利益分析詳細</div>
                    <div class="tooltip-row"><span class="tooltip-key">現場:</span><span class="tooltip-value">${this.escapeText(data.siteName)}</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">対象月:</span><span class="tooltip-value">${data.month}月</span></div>
                    <hr style="border-color: rgba(255,255,255,0.2); margin: 0.5rem 0;">
                    <div class="tooltip-row"><span class="tooltip-key">当期売上:</span><span class="tooltip-value">${this.formatNumber(data.revenue)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">当期原価:</span><span class="tooltip-value">${this.formatNumber(data.cost)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">当期利益:</span><span class="tooltip-value" style="color: ${data.profit >= 0 ? '#93c5fd' : '#fca5a5'}">${this.formatNumber(data.profit, true)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">当期利益率:</span><span class="tooltip-value">${this.formatNumber(data.profitRate, true)}%</span></div>
                    ${comparisonHtml}
                </div>
            `;
        });
    }

    /**
     * 検索フィルタリングを適用
     */
    applySearch(data, query) {
        if (!query || query.trim() === '') return data;

        const lowerQuery = query.toLowerCase();
        return data.filter(result => {
            const item = result.item;
            const segment = dataManager.getSegment(item.contract_code);

            return (
                item.site_name.toLowerCase().includes(lowerQuery) ||
                item.corp_name.toLowerCase().includes(lowerQuery) ||
                segment.toLowerCase().includes(lowerQuery) ||
                item.subject_name.toLowerCase().includes(lowerQuery) ||
                String(result.month).includes(lowerQuery)
            );
        });
    }

    /**
     * ソートを適用
     */
    applySorting(data) {
        if (!this.sortColumn) return data;

        return data.sort((a, b) => {
            let aVal, bVal;

            switch (this.sortColumn) {
                case 'site_name':
                    aVal = a.item.site_name;
                    bVal = b.item.site_name;
                    break;
                case 'corp_name':
                    aVal = a.item.corp_name;
                    bVal = b.item.corp_name;
                    break;
                case 'segment':
                    aVal = dataManager.getSegment(a.item.contract_code);
                    bVal = dataManager.getSegment(b.item.contract_code);
                    break;
                case 'subject_name':
                    aVal = a.item.subject_name;
                    bVal = b.item.subject_name;
                    break;
                case 'month':
                    aVal = a.month;
                    bVal = b.month;
                    break;
                case 'current_value':
                    aVal = a.currentValue;
                    bVal = b.currentValue;
                    break;
                case 'comparison_value':
                    aVal = a.comparisonValue;
                    bVal = b.comparisonValue;
                    break;
                case 'diff':
                    aVal = a.diff;
                    bVal = b.diff;
                    break;
                case 'diff_rate':
                    aVal = a.diffRate;
                    bVal = b.diffRate;
                    break;
                default:
                    return 0;
            }

            if (typeof aVal === 'string') {
                const result = aVal.localeCompare(bVal, 'ja');
                return this.sortDirection === 'asc' ? result : -result;
            } else {
                const result = aVal - bVal;
                return this.sortDirection === 'asc' ? result : -result;
            }
        });
    }

    /**
     * ページネーションを適用
     */
    applyPagination(data) {
        const start = (this.currentPage - 1) * this.itemsPerPage;
        const end = start + this.itemsPerPage;
        return data.slice(start, end);
    }

    /**
     * ページネーションを描画
     */
    renderPagination(totalItems) {
        const container = document.getElementById('detail-pagination');
        if (!container) return;

        const totalPages = Math.ceil(totalItems / this.itemsPerPage);

        if (totalPages <= 1) {
            container.innerHTML = '';
            return;
        }

        let html = `
            <button ${this.currentPage === 1 ? 'disabled' : ''} onclick="tableRenderer.goToPage(1)">«</button>
            <button ${this.currentPage === 1 ? 'disabled' : ''} onclick="tableRenderer.goToPage(${this.currentPage - 1})">‹</button>
        `;

        // ページ番号表示（最大5ページ分）
        let startPage = Math.max(1, this.currentPage - 2);
        let endPage = Math.min(totalPages, startPage + 4);

        if (endPage - startPage < 4) {
            startPage = Math.max(1, endPage - 4);
        }

        for (let i = startPage; i <= endPage; i++) {
            html += `
                <button class="${i === this.currentPage ? 'active' : ''}"
                        onclick="tableRenderer.goToPage(${i})">
                    ${i}
                </button>
            `;
        }

        html += `
            <button ${this.currentPage === totalPages ? 'disabled' : ''} onclick="tableRenderer.goToPage(${this.currentPage + 1})">›</button>
            <button ${this.currentPage === totalPages ? 'disabled' : ''} onclick="tableRenderer.goToPage(${totalPages})">»</button>
        `;

        container.innerHTML = html;
    }

    /**
     * ページ移動
     */
    goToPage(page) {
        this.currentPage = page;
        this.renderDetailTable(this.currentData, this.getActiveFilters());
    }

    /**
     * ソートリスナー設定
     * 詳細一覧のヘッダーは再描画されないため、多重登録を避けて一度だけバインドする。
     */
    setupSortListeners() {
        if (this.sortListenersBound) return;
        this.sortListenersBound = true;

        document.querySelectorAll('.sortable').forEach(th => {
            th.addEventListener('click', () => {
                const column = th.dataset.column;
                if (this.sortColumn === column) {
                    this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
                } else {
                    this.sortColumn = column;
                    this.sortDirection = 'asc';
                }

                // ソートインジケーター更新
                document.querySelectorAll('.sortable').forEach(t => {
                    t.classList.remove('sort-asc', 'sort-desc');
                });
                th.classList.add(`sort-${this.sortDirection}`);

                this.renderDetailTable(this.currentData, this.getActiveFilters());
            });
        });
    }

    /**
     * ツールチップリスナー設定
     */
    setupTooltipListeners() {
        this.bindContextMenuForTooltipData('#detail-tbody tr[data-tooltip]', (data) => {
            const typeLabel = data.isRevenue ? '売上' : '原価';
            const signNote = data.isRevenue ? '' : '（符号反転済み）';
            const comparisonHtml = data.hasComparison
                ? `
                    <div class="tooltip-row"><span class="tooltip-key">比較元 (${this.escapeText(data.comparisonLabel)}):</span><span class="tooltip-value">${this.formatNumber(data.comparisonValue)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">差異:</span><span class="tooltip-value" style="color: ${data.diff >= 0 ? '#fca5a5' : '#93c5fd'}">${this.formatNumber(data.diff, true)}円</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">差異率:</span><span class="tooltip-value" style="color: ${data.diffRate >= 0 ? '#fca5a5' : '#93c5fd'}">${this.formatNumber(data.diffRate, true)}%</span></div>
                `
                : `
                    <div class="tooltip-row"><span class="tooltip-key">モード:</span><span class="tooltip-value">単純表示（比較なし）</span></div>
                `;

            return `
                <div class="tooltip-content">
                    <div class="tooltip-label">📊 比較情報 ${signNote}</div>
                    <div class="tooltip-row"><span class="tooltip-key">現場:</span><span class="tooltip-value">${this.escapeText(data.siteName)}</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">科目:</span><span class="tooltip-value">${this.escapeText(data.subject)} (${typeLabel})</span></div>
                    <div class="tooltip-row"><span class="tooltip-key">対象月:</span><span class="tooltip-value">${data.month}月</span></div>
                    <hr style="border-color: rgba(255,255,255,0.2); margin: 0.5rem 0;">
                    <div class="tooltip-row"><span class="tooltip-key">当期値:</span><span class="tooltip-value">${this.formatNumber(data.currentValue)}円</span></div>
                    ${comparisonHtml}
                </div>
            `;
        });
    }

    /**
     * ツールチップの最適な位置を計算（4方向から選択）
     */
    calculateTooltipPosition(mouseX, mouseY, tooltipEl) {
        const offset = 15; // マウスカーソルからの距離
        const padding = 10; // 画面端からの余白
        const tooltipRect = tooltipEl.getBoundingClientRect();
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;

        // 4つの候補位置を計算
        const positions = [
            // 右下
            {
                x: mouseX + offset,
                y: mouseY + offset,
                score: 0
            },
            // 右上
            {
                x: mouseX + offset,
                y: mouseY - tooltipRect.height - offset,
                score: 0
            },
            // 左下
            {
                x: mouseX - tooltipRect.width - offset,
                y: mouseY + offset,
                score: 0
            },
            // 左上
            {
                x: mouseX - tooltipRect.width - offset,
                y: mouseY - tooltipRect.height - offset,
                score: 0
            }
        ];

        // 各位置のスコアを計算（画面内に収まるかどうか）
        positions.forEach(pos => {
            // 横方向のスコア
            if (pos.x >= padding && pos.x + tooltipRect.width <= viewportWidth - padding) {
                pos.score += 10; // 完全に収まる
            } else if (pos.x >= 0 && pos.x + tooltipRect.width <= viewportWidth) {
                pos.score += 5; // ギリギリ収まる
            }

            // 縦方向のスコア
            if (pos.y >= padding && pos.y + tooltipRect.height <= viewportHeight - padding) {
                pos.score += 10; // 完全に収まる
            } else if (pos.y >= 0 && pos.y + tooltipRect.height <= viewportHeight) {
                pos.score += 5; // ギリギリ収まる
            }

            // 右下を優先（同じスコアなら）
            if (pos.x === mouseX + offset && pos.y === mouseY + offset) {
                pos.score += 1;
            }
        });

        // 最高スコアの位置を選択
        const bestPosition = positions.reduce((best, current) =>
            current.score > best.score ? current : best
        );

        // 画面外に出ないように最終調整
        let finalX = bestPosition.x;
        let finalY = bestPosition.y;

        if (finalX + tooltipRect.width > viewportWidth - padding) {
            finalX = viewportWidth - tooltipRect.width - padding;
        }
        if (finalX < padding) {
            finalX = padding;
        }
        if (finalY + tooltipRect.height > viewportHeight - padding) {
            finalY = viewportHeight - tooltipRect.height - padding;
        }
        if (finalY < padding) {
            finalY = padding;
        }

        return { x: finalX, y: finalY };
    }

    /**
     * 科目別サマリーのリスナー設定
     */
    setupSubjectSummaryListeners(summary, filters) {
        // 検索ボックス（再描画で入力欄が作り直されるためフォーカスを戻す）
        const searchInput = document.getElementById('subject-summary-search');
        this.bindSearchInput(searchInput, (value) => {
            this.subjectSummarySearch = value;
            this.renderSubjectSummary(summary, filters);
            this.restoreSearchFocus('subject-summary-search');
        });

        // ソートヘッダー
        document.querySelectorAll('.sortable-subject').forEach(th => {
            th.addEventListener('click', () => {
                const column = th.dataset.column;
                if (this.subjectSummarySortColumn === column) {
                    this.subjectSummarySortDirection = this.subjectSummarySortDirection === 'asc' ? 'desc' : 'asc';
                } else {
                    this.subjectSummarySortColumn = column;
                    this.subjectSummarySortDirection = 'asc';
                }

                this.renderSubjectSummary(summary, filters);
            });
        });
    }

    /**
     * 再描画で作り直された検索欄へフォーカスとカーソル位置を戻す
     */
    restoreSearchFocus(inputId) {
        const input = document.getElementById(inputId);
        if (!input) return;
        input.focus();
        const length = input.value.length;
        try {
            input.setSelectionRange(length, length);
        } catch (_error) {
            // setSelectionRange非対応のinput型では無視
        }
    }

    /**
     * 科目別サマリーのソート
     */
    sortSubjectSummary(data) {
        return data.sort((a, b) => {
            let aVal = a[this.subjectSummarySortColumn];
            let bVal = b[this.subjectSummarySortColumn];

            if (typeof aVal === 'string') {
                const result = aVal.localeCompare(bVal, 'ja');
                return this.subjectSummarySortDirection === 'asc' ? result : -result;
            } else {
                const result = aVal - bVal;
                return this.subjectSummarySortDirection === 'asc' ? result : -result;
            }
        });
    }

    /**
     * 利益分析のリスナー設定
     */
    setupProfitListeners(profitData, filters) {
        // 検索ボックス（再描画で入力欄が作り直されるためフォーカスを戻す）
        const searchInput = document.getElementById('profit-search');
        this.bindSearchInput(searchInput, (value) => {
            this.profitSearch = value;
            this.renderProfitAnalysis(profitData, filters);
            this.restoreSearchFocus('profit-search');
        });

        // ソートヘッダー
        document.querySelectorAll('.sortable-profit').forEach(th => {
            th.addEventListener('click', () => {
                const column = th.dataset.column;
                if (this.profitSortColumn === column) {
                    this.profitSortDirection = this.profitSortDirection === 'asc' ? 'desc' : 'asc';
                } else {
                    this.profitSortColumn = column;
                    this.profitSortDirection = 'asc';
                }

                this.renderProfitAnalysis(profitData, filters);
            });
        });
    }

    /**
     * 利益分析データのソート
     */
    sortProfitData(data) {
        return data.sort((a, b) => {
            let aVal = a[this.profitSortColumn];
            let bVal = b[this.profitSortColumn];

            // profitDiff と profitRateDiff は計算する必要がある
            if (this.profitSortColumn === 'profitDiff') {
                aVal = a.profit - a.profitComparison;
                bVal = b.profit - b.profitComparison;
            } else if (this.profitSortColumn === 'profitRateDiff') {
                aVal = a.profitRate - a.profitRateComparison;
                bVal = b.profitRate - b.profitRateComparison;
            }

            if (typeof aVal === 'string') {
                const result = aVal.localeCompare(bVal, 'ja');
                return this.profitSortDirection === 'asc' ? result : -result;
            } else {
                const result = aVal - bVal;
                return this.profitSortDirection === 'asc' ? result : -result;
            }
        });
    }

    /**
     * 数値フォーマット
     */
    formatNumber(value, showSign = false) {
        if (value === null || value === undefined) return '-';

        const formatted = Math.round(value).toLocaleString();

        if (showSign && value > 0) {
            return `+${formatted}`;
        }

        return formatted;
    }
}

// グローバル関数
function toggleSummaryCard(index) {
    const card = document.getElementById(`site-summary-${index}`);
    if (card) {
        card.style.display = card.style.display === 'none' ? 'block' : 'none';
    }
}

// グローバルインスタンス
var tableRenderer = new TableRenderer();
