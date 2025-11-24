// テーブルレンダラー - テーブル表示、ソート、ページネーション

class TableRenderer {
    constructor() {
        this.currentPage = 1;
        this.itemsPerPage = 50;
        this.sortColumn = null;
        this.sortDirection = 'asc';
        this.currentData = [];
    }

    /**
     * 詳細一覧テーブルを描画
     */
    renderDetailTable(results, filters) {
        this.currentData = results;
        const tbody = document.getElementById('detail-tbody');
        const countEl = document.getElementById('detail-count');

        if (!tbody) return;

        if (results.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="text-center text-gray-500">データがありません</td></tr>';
            countEl.textContent = '0';
            return;
        }

        countEl.textContent = results.length.toLocaleString();

        // ソート適用
        const sortedResults = this.applySorting([...results]);

        // ページネーション適用
        const paginatedResults = this.applyPagination(sortedResults);

        // テーブル描画
        let html = '';
        paginatedResults.forEach(result => {
            const item = result.item;
            const segment = dataManager.rawData.siteMapping[item.contract_code] || '未分類';

            // ハイライトクラス決定
            let highlightClass = '';
            if (filters.highlightEnabled && result.isAnomaly) {
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
            if (filters.displayMode === 'value_only') {
                comparisonValueDisplay = '-';
                diffDisplay = '-';
                diffRateDisplay = '-';
            } else if (filters.displayMode === 'diff_only') {
                currentValueDisplay = '-';
                comparisonValueDisplay = '-';
                diffRateDisplay = '-';
            } else if (filters.displayMode === 'rate_only') {
                currentValueDisplay = '-';
                comparisonValueDisplay = '-';
                diffDisplay = '-';
            }

            html += `
                <tr class="${highlightClass}">
                    <td>${item.site_name}</td>
                    <td>${item.corp_name}</td>
                    <td>${segment}</td>
                    <td>${item.subject_name}</td>
                    <td>${result.month}月</td>
                    <td class="text-right">${currentValueDisplay}</td>
                    <td class="text-right">${comparisonValueDisplay}</td>
                    <td class="text-right">${diffDisplay}</td>
                    <td class="text-right">${diffRateDisplay}</td>
                </tr>
            `;
        });

        tbody.innerHTML = html;

        // ページネーション更新
        this.renderPagination(sortedResults.length);

        // ソートイベント設定
        this.setupSortListeners();
    }

    /**
     * 現場別サマリーを描画
     */
    renderSiteSummary(summary, filters) {
        const container = document.getElementById('site-summary-content');
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
                                ${site.siteName} (${site.corpName})
                            </div>
                            <div class="text-sm text-gray-600">
                                ${site.segment} | 異常値: ${site.anomalyCount}件
                            </div>
                        </div>
                        <div class="text-right">
                            <div class="text-lg font-bold ${site.totalDiff >= 0 ? 'text-red-600' : 'text-blue-600'}">
                                ${this.formatNumber(site.totalDiff, true)}
                            </div>
                            <div class="text-sm text-gray-600">
                                (${this.formatNumber(site.totalDiffRate, true)}%)
                            </div>
                        </div>
                    </div>
                    <div class="summary-card-body" id="site-summary-${index}" style="display: none;">
                        <table class="data-table">
                            <thead>
                                <tr>
                                    <th>科目</th>
                                    <th>月</th>
                                    <th>当期値</th>
                                    <th>比較値</th>
                                    <th>差異</th>
                                    <th>差異率</th>
                                </tr>
                            </thead>
                            <tbody>
            `;

            site.items.forEach(result => {
                const highlightClass = filters.highlightEnabled && result.isAnomaly
                    ? (result.diff > 0 ? 'highlight-increase' : 'highlight-decrease')
                    : '';

                html += `
                    <tr class="${highlightClass}">
                        <td>${result.item.subject_name}</td>
                        <td>${result.month}月</td>
                        <td class="text-right">${this.formatNumber(result.currentValue)}</td>
                        <td class="text-right">${this.formatNumber(result.comparisonValue)}</td>
                        <td class="text-right">${this.formatNumber(result.diff, true)}</td>
                        <td class="text-right">${this.formatNumber(result.diffRate, true)}%</td>
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
    }

    /**
     * 科目別サマリーを描画
     */
    renderSubjectSummary(summary, filters) {
        const container = document.getElementById('subject-summary-content');
        if (!container) return;

        if (summary.length === 0) {
            container.innerHTML = '<p class="text-center text-gray-500">データがありません</p>';
            return;
        }

        let html = `
            <table class="data-table">
                <thead>
                    <tr>
                        <th>科目名</th>
                        <th>現場数</th>
                        <th>異常値件数</th>
                        <th>当期合計</th>
                        <th>比較合計</th>
                        <th>差異</th>
                        <th>差異率</th>
                    </tr>
                </thead>
                <tbody>
        `;

        summary.forEach(subject => {
            const highlightClass = filters.highlightEnabled &&
                (subject.anomalyCount > 0 ||
                    Math.abs(subject.totalDiffRate) > filters.thresholdRate)
                ? (subject.totalDiff > 0 ? 'highlight-increase' : 'highlight-decrease')
                : '';

            html += `
                <tr class="${highlightClass}">
                    <td>${subject.subjectName}</td>
                    <td class="text-right">${subject.siteCount}</td>
                    <td class="text-right">${subject.anomalyCount}</td>
                    <td class="text-right">${this.formatNumber(subject.totalCurrent)}</td>
                    <td class="text-right">${this.formatNumber(subject.totalComparison)}</td>
                    <td class="text-right">${this.formatNumber(subject.totalDiff, true)}</td>
                    <td class="text-right">${this.formatNumber(subject.totalDiffRate, true)}%</td>
                </tr>
            `;
        });

        html += `
                </tbody>
            </table>
        `;

        container.innerHTML = html;
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

        const html = `
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">総データ件数</div>
                    <div class="stat-value">${stats.count.toLocaleString()}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">異常値件数</div>
                    <div class="stat-value text-red-600">${stats.anomalyCount.toLocaleString()}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">当期値合計</div>
                    <div class="stat-value">${this.formatNumber(stats.currentValue.sum)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">差異合計</div>
                    <div class="stat-value ${stats.diff.sum >= 0 ? 'text-red-600' : 'text-blue-600'}">
                        ${this.formatNumber(stats.diff.sum, true)}
                    </div>
                </div>
            </div>

            <div class="grid grid-cols-3 gap-4 mt-6">
                <div class="bg-white border rounded p-4">
                    <h4 class="font-semibold mb-3">当期値統計</h4>
                    <div class="space-y-2 text-sm">
                        <div class="flex justify-between">
                            <span>平均:</span>
                            <span>${this.formatNumber(stats.currentValue.avg)}</span>
                        </div>
                        <div class="flex justify-between">
                            <span>中央値:</span>
                            <span>${this.formatNumber(stats.currentValue.median)}</span>
                        </div>
                        <div class="flex justify-between">
                            <span>最大値:</span>
                            <span>${this.formatNumber(stats.currentValue.max)}</span>
                        </div>
                        <div class="flex justify-between">
                            <span>最小値:</span>
                            <span>${this.formatNumber(stats.currentValue.min)}</span>
                        </div>
                        <div class="flex justify-between">
                            <span>標準偏差:</span>
                            <span>${this.formatNumber(stats.currentValue.stdDev)}</span>
                        </div>
                    </div>
                </div>

                <div class="bg-white border rounded p-4">
                    <h4 class="font-semibold mb-3">差異統計</h4>
                    <div class="space-y-2 text-sm">
                        <div class="flex justify-between">
                            <span>平均:</span>
                            <span>${this.formatNumber(stats.diff.avg, true)}</span>
                        </div>
                        <div class="flex justify-between">
                            <span>中央値:</span>
                            <span>${this.formatNumber(stats.diff.median, true)}</span>
                        </div>
                        <div class="flex justify-between">
                            <span>最大値:</span>
                            <span>${this.formatNumber(stats.diff.max, true)}</span>
                        </div>
                        <div class="flex justify-between">
                            <span>最小値:</span>
                            <span>${this.formatNumber(stats.diff.min, true)}</span>
                        </div>
                        <div class="flex justify-between">
                            <span>標準偏差:</span>
                            <span>${this.formatNumber(stats.diff.stdDev)}</span>
                        </div>
                    </div>
                </div>

                <div class="bg-white border rounded p-4">
                    <h4 class="font-semibold mb-3">差異率統計（%）</h4>
                    <div class="space-y-2 text-sm">
                        <div class="flex justify-between">
                            <span>平均:</span>
                            <span>${this.formatNumber(stats.diffRate.avg, true)}%</span>
                        </div>
                        <div class="flex justify-between">
                            <span>中央値:</span>
                            <span>${this.formatNumber(stats.diffRate.median, true)}%</span>
                        </div>
                        <div class="flex justify-between">
                            <span>最大値:</span>
                            <span>${this.formatNumber(stats.diffRate.max, true)}%</span>
                        </div>
                        <div class="flex justify-between">
                            <span>最小値:</span>
                            <span>${this.formatNumber(stats.diffRate.min, true)}%</span>
                        </div>
                        <div class="flex justify-between">
                            <span>標準偏差:</span>
                            <span>${this.formatNumber(stats.diffRate.stdDev)}%</span>
                        </div>
                    </div>
                </div>
            </div>
        `;

        container.innerHTML = html;
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
                    aVal = dataManager.rawData.siteMapping[a.item.contract_code] || '未分類';
                    bVal = dataManager.rawData.siteMapping[b.item.contract_code] || '未分類';
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
        const filters = filterController.getFilters();
        this.renderDetailTable(this.currentData, filters);
    }

    /**
     * ソートリスナー設定
     */
    setupSortListeners() {
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

                const filters = filterController.getFilters();
                this.renderDetailTable(this.currentData, filters);
            });
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
const tableRenderer = new TableRenderer();
