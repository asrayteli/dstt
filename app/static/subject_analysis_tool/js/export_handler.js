// エクスポートハンドラー - CSV/Excelエクスポート機能

class ExportHandler {
    /**
     * CSVエクスポート
     */
    exportToCSV() {
        const results = comparisonEngine.getResults();
        if (results.length === 0) {
            alert('エクスポートするデータがありません');
            return;
        }

        // CSVヘッダー
        const headers = [
            '現場名',
            '法人名',
            'セグメント',
            '科目名',
            '月',
            '当期値',
            '比較値',
            '差異',
            '差異率(%)'
        ];

        // CSVデータ生成
        const rows = [headers];
        results.forEach(result => {
            const item = result.item;
            const segment = dataManager.rawData.siteMapping[item.contract_code] || '未分類';
            const hasComparison = !!result.hasComparison;

            rows.push([
                item.site_name,
                item.corp_name,
                segment,
                item.subject_name,
                `${result.month}月`,
                result.currentValue,
                hasComparison ? result.comparisonValue : '-',
                hasComparison ? result.diff : '-',
                hasComparison ? result.diffRate.toFixed(2) : '-'
            ]);
        });

        // CSV文字列生成
        const csvContent = rows.map(row =>
            row.map(cell => {
                // 文字列の場合はダブルクォートで囲む
                if (typeof cell === 'string') {
                    return `"${cell.replace(/"/g, '""')}"`;
                }
                return cell;
            }).join(',')
        ).join('\n');

        // BOM付きUTF-8で出力
        const bom = '\uFEFF';
        const blob = new Blob([bom + csvContent], { type: 'text/csv;charset=utf-8;' });

        // ダウンロード
        this.downloadFile(blob, '科目別分析_詳細一覧.csv');
    }

    /**
     * Excelエクスポート（本物の .xlsx）
     */
    async exportToExcel() {
        const results = comparisonEngine.getResults();
        if (results.length === 0) {
            alert('エクスポートするデータがありません');
            return;
        }

        const payload = {
            filters: typeof currentFilters !== 'undefined' ? currentFilters : {},
            results: results.map((result) => ({
                month: result.month,
                currentValue: result.currentValue,
                comparisonValue: result.comparisonValue,
                diff: result.diff,
                diffRate: result.diffRate,
                hasComparison: !!result.hasComparison,
                isAnomaly: !!result.isAnomaly,
                segment: dataManager.rawData.siteMapping[result.item.contract_code] || '未分類',
                item: {
                    contract_code: result.item.contract_code,
                    site_name: result.item.site_name,
                    corp_name: result.item.corp_name,
                    subject_name: result.item.subject_name,
                    is_revenue: result.item.is_revenue
                }
            }))
        };

        const response = await fetch('/tools/subject_analysis_tool/api/export/xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || 'Excel出力に失敗しました');
        }

        const blob = await response.blob();
        this.downloadFile(blob, '科目別分析_詳細一覧.xlsx');
    }

    /**
     * サマリーをCSVエクスポート
     */
    exportSiteSummaryToCSV() {
        const summary = comparisonEngine.getSiteSummary();
        if (summary.length === 0) {
            alert('エクスポートするデータがありません');
            return;
        }

        const headers = [
            '現場名',
            '法人名',
            'セグメント',
            '当期合計',
            '比較合計',
            '差異',
            '差異率(%)',
            '異常値件数'
        ];

        const rows = [headers];
        summary.forEach(site => {
            rows.push([
                site.siteName,
                site.corpName,
                site.segment,
                site.totalCurrent,
                site.totalComparison,
                site.totalDiff,
                site.totalDiffRate.toFixed(2),
                site.anomalyCount
            ]);
        });

        const csvContent = rows.map(row =>
            row.map(cell => {
                if (typeof cell === 'string') {
                    return `"${cell.replace(/"/g, '""')}"`;
                }
                return cell;
            }).join(',')
        ).join('\n');

        const bom = '\uFEFF';
        const blob = new Blob([bom + csvContent], { type: 'text/csv;charset=utf-8;' });

        this.downloadFile(blob, '科目別分析_現場別サマリー.csv');
    }

    /**
     * 科目別サマリーをCSVエクスポート
     */
    exportSubjectSummaryToCSV() {
        const summary = comparisonEngine.getSubjectSummary();
        if (summary.length === 0) {
            alert('エクスポートするデータがありません');
            return;
        }

        const headers = [
            '科目名',
            '現場数',
            '当期合計',
            '比較合計',
            '差異',
            '差異率(%)',
            '異常値件数'
        ];

        const rows = [headers];
        summary.forEach(subject => {
            rows.push([
                subject.subjectName,
                subject.siteCount,
                subject.totalCurrent,
                subject.totalComparison,
                subject.totalDiff,
                subject.totalDiffRate.toFixed(2),
                subject.anomalyCount
            ]);
        });

        const csvContent = rows.map(row =>
            row.map(cell => {
                if (typeof cell === 'string') {
                    return `"${cell.replace(/"/g, '""')}"`;
                }
                return cell;
            }).join(',')
        ).join('\n');

        const bom = '\uFEFF';
        const blob = new Blob([bom + csvContent], { type: 'text/csv;charset=utf-8;' });

        this.downloadFile(blob, '科目別分析_科目別サマリー.csv');
    }

    /**
     * ファイルダウンロード
     */
    downloadFile(blob, filename) {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = filename;

        document.body.appendChild(a);
        a.click();

        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
    }
}

// グローバル関数
function exportToCSV() {
    exportHandler.exportToCSV();
}

function exportToExcel() {
    exportHandler.exportToExcel().catch((error) => {
        alert(`Excel出力エラー: ${error.message}`);
        console.error('Excel export error:', error);
    });
}

function exportSiteSummaryToCSV() {
    exportHandler.exportSiteSummaryToCSV();
}

function exportSubjectSummaryToCSV() {
    exportHandler.exportSubjectSummaryToCSV();
}

// グローバルインスタンス
const exportHandler = new ExportHandler();
