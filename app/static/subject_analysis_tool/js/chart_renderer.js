// グラフレンダラー - Chart.jsを使用したグラフ描画

class ChartRenderer {
    constructor() {
        this.chartInstance = null;
        this.colors = [
            '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6',
            '#ec4899', '#14b8a6', '#f97316', '#6366f1', '#84cc16'
        ];
    }

    /**
     * グラフを描画
     */
    renderChart(filters) {
        const chartType = document.getElementById('chart-type').value;
        const trendData = comparisonEngine.getMonthlyTrend(filters);

        if (this.chartInstance) {
            this.chartInstance.destroy();
        }

        const ctx = document.getElementById('main-chart');
        if (!ctx) return;

        const datasets = trendData.datasets.map((dataset, index) => ({
            label: dataset.label,
            data: dataset.data,
            backgroundColor: this.getColor(index, 0.6),
            borderColor: this.getColor(index, 1),
            borderWidth: 2,
            fill: chartType === 'stacked'
        }));

        const config = {
            type: chartType === 'stacked' ? 'bar' : chartType,
            data: {
                labels: trendData.months.map(m => `${m}月`),
                datasets: datasets
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: {
                            boxWidth: 12,
                            font: {
                                size: 11
                            }
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                let label = context.dataset.label || '';
                                if (label) {
                                    label += ': ';
                                }
                                label += Math.round(context.parsed.y).toLocaleString();
                                return label;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        stacked: chartType === 'stacked',
                        grid: {
                            display: false
                        }
                    },
                    y: {
                        stacked: chartType === 'stacked',
                        beginAtZero: true,
                        ticks: {
                            callback: function(value) {
                                return value.toLocaleString();
                            }
                        }
                    }
                }
            }
        };

        this.chartInstance = new Chart(ctx, config);
    }

    /**
     * ヒートマップを描画
     */
    renderHeatmap(results, filters) {
        const container = document.getElementById('heatmap-content');
        if (!container) return;

        if (results.length === 0) {
            container.innerHTML = '<p class="text-center text-gray-500">データがありません</p>';
            return;
        }

        // 現場×月のマトリクスを作成
        const matrix = {};
        const sites = new Set();
        const months = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3];

        results.forEach(result => {
            const siteKey = `${result.item.site_name} (${result.item.corp_name})`;
            sites.add(siteKey);

            if (!matrix[siteKey]) {
                matrix[siteKey] = {};
            }

            if (!matrix[siteKey][result.month]) {
                matrix[siteKey][result.month] = {
                    count: 0,
                    totalDiff: 0,
                    totalDiffRate: 0
                };
            }

            matrix[siteKey][result.month].count++;
            matrix[siteKey][result.month].totalDiff += result.diff;
            matrix[siteKey][result.month].totalDiffRate += result.diffRate;
        });

        // 差異率の範囲を計算
        let maxDiffRate = 0;
        Object.keys(matrix).forEach(site => {
            months.forEach(month => {
                if (matrix[site][month]) {
                    const avgRate = Math.abs(matrix[site][month].totalDiffRate / matrix[site][month].count);
                    if (avgRate > maxDiffRate) {
                        maxDiffRate = avgRate;
                    }
                }
            });
        });

        // HTML生成
        let html = '<div style="overflow-x: auto;">';
        html += '<table class="data-table">';
        html += '<thead><tr><th class="heatmap-header">現場</th>';

        months.forEach(month => {
            html += `<th class="heatmap-header">${month}月</th>`;
        });

        html += '</tr></thead><tbody>';

        Array.from(sites).forEach(site => {
            html += `<tr><td class="heatmap-header">${site}</td>`;

            months.forEach(month => {
                const data = matrix[site][month];
                let bgColor = '#ffffff';
                let textContent = '-';

                if (data) {
                    const avgDiffRate = data.totalDiffRate / data.count;
                    const intensity = Math.min(Math.abs(avgDiffRate) / maxDiffRate, 1);

                    if (avgDiffRate > 0) {
                        // 増加 = 赤系
                        bgColor = `rgba(239, 68, 68, ${intensity * 0.7})`;
                    } else if (avgDiffRate < 0) {
                        // 減少 = 青系
                        bgColor = `rgba(59, 130, 246, ${intensity * 0.7})`;
                    }

                    textContent = `${avgDiffRate > 0 ? '+' : ''}${Math.round(avgDiffRate)}%`;
                }

                html += `<td class="heatmap-cell" style="background-color: ${bgColor};"
                         title="${site} ${month}月: ${textContent}">
                         ${textContent}
                         </td>`;
            });

            html += '</tr>';
        });

        html += '</tbody></table></div>';

        // 凡例追加
        html += `
            <div class="mt-4 flex items-center justify-center gap-4 text-sm">
                <span>減少</span>
                <div class="flex gap-1">
                    <div class="w-8 h-4" style="background-color: rgba(59, 130, 246, 0.7);"></div>
                    <div class="w-8 h-4" style="background-color: rgba(59, 130, 246, 0.4);"></div>
                    <div class="w-8 h-4" style="background-color: rgba(255, 255, 255, 1);"></div>
                    <div class="w-8 h-4" style="background-color: rgba(239, 68, 68, 0.4);"></div>
                    <div class="w-8 h-4" style="background-color: rgba(239, 68, 68, 0.7);"></div>
                </div>
                <span>増加</span>
            </div>
        `;

        container.innerHTML = html;
    }

    /**
     * 色を取得
     */
    getColor(index, alpha = 1) {
        const color = this.colors[index % this.colors.length];
        if (alpha === 1) return color;

        // HEXをRGBAに変換
        const r = parseInt(color.slice(1, 3), 16);
        const g = parseInt(color.slice(3, 5), 16);
        const b = parseInt(color.slice(5, 7), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    /**
     * チャートを破棄
     */
    destroy() {
        if (this.chartInstance) {
            this.chartInstance.destroy();
            this.chartInstance = null;
        }
    }
}

// グローバル関数（グラフ種類変更時に呼ばれる）
function renderChart() {
    // 分析実行時のフィルターを優先する（未適用のUI変更や担当者絞り込みの解決済みコード一覧を保つため）
    const filters = (typeof currentFilters !== 'undefined' && currentFilters)
        ? currentFilters
        : filterController.getFilters();
    chartRenderer.renderChart(filters);
}

// グローバルインスタンス
const chartRenderer = new ChartRenderer();
