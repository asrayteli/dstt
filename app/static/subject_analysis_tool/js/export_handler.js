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
     * Excelエクスポート（HTML tableからの変換）
     */
    exportToExcel() {
        const results = comparisonEngine.getResults();
        if (results.length === 0) {
            alert('エクスポートするデータがありません');
            return;
        }

        // HTMLテーブルを生成
        let html = '<table>';
        html += '<tr>';
        html += '<th>現場名</th>';
        html += '<th>法人名</th>';
        html += '<th>セグメント</th>';
        html += '<th>科目名</th>';
        html += '<th>月</th>';
        html += '<th>当期値</th>';
        html += '<th>比較値</th>';
        html += '<th>差異</th>';
        html += '<th>差異率(%)</th>';
        html += '</tr>';

        results.forEach(result => {
            const item = result.item;
            const segment = dataManager.rawData.siteMapping[item.contract_code] || '未分類';
            const hasComparison = !!result.hasComparison;

            html += '<tr>';
            html += `<td>${item.site_name}</td>`;
            html += `<td>${item.corp_name}</td>`;
            html += `<td>${segment}</td>`;
            html += `<td>${item.subject_name}</td>`;
            html += `<td>${result.month}月</td>`;
            html += `<td>${result.currentValue}</td>`;
            html += `<td>${hasComparison ? result.comparisonValue : '-'}</td>`;
            html += `<td>${hasComparison ? result.diff : '-'}</td>`;
            html += `<td>${hasComparison ? result.diffRate.toFixed(2) : '-'}</td>`;
            html += '</tr>';
        });

        html += '</table>';

        // Excel形式でダウンロード
        const blob = new Blob([html], {
            type: 'application/vnd.ms-excel;charset=utf-8;'
        });

        this.downloadFile(blob, '科目別分析_詳細一覧.xls');
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
    exportHandler.exportToExcel();
}

function exportSiteSummaryToCSV() {
    exportHandler.exportSiteSummaryToCSV();
}

function exportSubjectSummaryToCSV() {
    exportHandler.exportSubjectSummaryToCSV();
}

// グローバルインスタンス
const exportHandler = new ExportHandler();
