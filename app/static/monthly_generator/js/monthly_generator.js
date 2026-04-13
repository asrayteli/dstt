document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('upload-form');
    if (form) {
        form.addEventListener('submit', (event) => {
            event.preventDefault();
            processFiles();
        });
    }

    const siteSource = document.getElementById('site-source');
    if (siteSource) {
        siteSource.addEventListener('change', syncSiteSourceMode);
    }

    syncSiteSourceMode();
});

function syncSiteSourceMode() {
    const siteSource = document.getElementById('site-source');
    const siteFile = document.getElementById('site-file');
    const siteManagerId = document.getElementById('site-manager-id');
    const filePanel = document.getElementById('site-source-file-panel');
    const dbPanel = document.getElementById('site-source-db-panel');
    const useDb = !siteSource || siteSource.value === 'db';

    if (filePanel) {
        filePanel.style.display = useDb ? 'none' : 'block';
    }
    if (dbPanel) {
        dbPanel.style.display = useDb ? 'block' : 'none';
    }
    if (siteFile) {
        siteFile.required = !useDb;
        if (useDb) {
            siteFile.value = '';
        }
    }
    if (siteManagerId) {
        siteManagerId.required = useDb;
        if (!useDb) {
            siteManagerId.value = '';
        }
    }
}

async function processFiles(options = {}) {
    const { ignoreMissingSubjectSites = false } = options;
    const subjectFile = document.getElementById('subject-file')?.files?.[0];
    const siteFile = document.getElementById('site-file')?.files?.[0];
    const reportFile = document.getElementById('report-file')?.files?.[0];
    const prevYearSubjectFile = document.getElementById('prev-year-subject-file')?.files?.[0];
    const prevYearSiteFile = document.getElementById('prev-year-site-file')?.files?.[0];
    const forecastFile = document.getElementById('forecast-file')?.files?.[0];
    const budgetFile = document.getElementById('budget-file')?.files?.[0];
    const targetMonth = document.getElementById('target-month')?.value || '';
    const sheetName = document.getElementById('sheet-name')?.value || '';
    const siteSource = document.getElementById('site-source')?.value || 'db';
    const siteManagerId = (document.getElementById('site-manager-id')?.value || '').trim();

    if (!subjectFile || !reportFile || !targetMonth || !sheetName) {
        alert('必須項目をすべて入力してください。');
        return;
    }
    if (siteSource === 'file' && !siteFile) {
        alert('現場表読み込みモードでは現行現場表 CSV を選択してください。');
        return;
    }
    if (siteSource === 'db' && !siteManagerId) {
        alert('現場リストPLUS参照モードでは担当者IDを入力してください。');
        return;
    }

    const formData = new FormData();
    formData.append('subject_file', subjectFile);
    formData.append('report_file', reportFile);
    formData.append('target_month', targetMonth);
    formData.append('sheet_name', sheetName);
    formData.append('site_source', siteSource);
    if (siteFile) formData.append('site_file', siteFile);
    if (siteManagerId) formData.append('site_manager_id', siteManagerId);
    if (prevYearSubjectFile) formData.append('prev_year_subject_file', prevYearSubjectFile);
    if (prevYearSiteFile) formData.append('prev_year_site_file', prevYearSiteFile);
    if (forecastFile) formData.append('forecast_file', forecastFile);
    if (budgetFile) formData.append('budget_file', budgetFile);
    if (ignoreMissingSubjectSites) formData.append('ignore_missing_subject_sites', '1');

    document.getElementById('upload-form').style.display = 'none';
    document.getElementById('result-container').classList.add('hidden');
    document.getElementById('progress-container').classList.remove('hidden');
    startProgress();

    try {
        const response = await fetch('/tools/monthly_generator/api/process', {
            method: 'POST',
            body: formData
        });
        const result = await response.json();
        completeProgress();

        if (result.confirmation_required) {
            if (ignoreMissingSubjectSites) {
                showError(result.message || '確認が必要です。', result.details || []);
                restoreFormWithFiles();
                return;
            }

            const shouldContinue = await requestMissingSiteConfirmation(
                result.message,
                result.missing_subject_sites || []
            );
            if (shouldContinue) {
                await processFiles({ ignoreMissingSubjectSites: true });
            } else {
                restoreFormWithFiles();
            }
            return;
        }

        if (!response.ok || result.error) {
            showError(result.error || '処理に失敗しました。', result.details || []);
            restoreFormWithFiles();
            return;
        }

        showSuccess(result);
        downloadFile(result.output_file);
    } catch (error) {
        console.error(error);
        completeProgress();
        showError('処理中にエラーが発生しました。', [error.message]);
        restoreFormWithFiles();
    }
}

function startProgress() {
    let progress = 0;
    const totalSteps = 7;

    if (window.progressInterval) {
        clearInterval(window.progressInterval);
    }

    window.progressInterval = setInterval(() => {
        if (progress >= totalSteps) {
            clearInterval(window.progressInterval);
            return;
        }
        progress += 1;
        updateProgress(progress, totalSteps);
    }, 800);
}

function updateProgress(currentStep, totalSteps) {
    const percentage = Math.round((currentStep / totalSteps) * 100);
    const progressBar = document.getElementById('progress-bar');
    if (progressBar) {
        progressBar.style.width = `${percentage}%`;
    }

    const percentageLabel = document.getElementById('progress-percentage');
    if (percentageLabel) {
        percentageLabel.textContent = `${percentage}%`;
    }

    document.querySelectorAll('.step-item').forEach((item, index) => {
        const stepNumber = index + 1;
        item.classList.remove('active', 'completed');
        if (stepNumber < currentStep) {
            item.classList.add('completed');
        } else if (stepNumber === currentStep) {
            item.classList.add('active');
        }
    });
}

function completeProgress() {
    if (window.progressInterval) {
        clearInterval(window.progressInterval);
    }
    updateProgress(7, 7);
    setTimeout(() => {
        document.getElementById('progress-container').classList.add('hidden');
    }, 500);
}

function showSuccess(result) {
    const container = document.getElementById('result-container');
    const content = document.getElementById('result-content');
    if (!container || !content) return;

    const sections = [];
    sections.push('<div class="result-box">');
    sections.push('<h3 class="mb-4 text-lg font-bold text-green-600">処理が完了しました</h3>');
    sections.push('<p class="mb-4 text-sm text-gray-700">集計結果を確認して、そのまま出力ファイルをダウンロードできます。</p>');

    if (result.data) {
        sections.push(renderSummaryTable('当年データ', result.data));
    }
    if (result.prev_year_data) {
        sections.push(renderSummaryTable('前年データ', result.prev_year_data));
    }
    if (result.forecast_data) {
        sections.push(renderSummaryTable('予算データ', result.forecast_data));
    }
    if (result.budget_data) {
        if (result.budget_data['原点']) {
            sections.push(renderSummaryTable('予算 CSV (原点)', result.budget_data['原点']));
        }
        if (result.budget_data['累計']) {
            sections.push(renderSummaryTable('予算 CSV (累計)', result.budget_data['累計']));
        }
    }
    if (result.warnings && result.warnings.length > 0) {
        sections.push('<div class="mt-4 rounded border border-yellow-200 bg-yellow-50 p-3">');
        sections.push('<h4 class="mb-2 font-semibold text-yellow-800">警告</h4>');
        sections.push('<ul class="list-disc list-inside text-sm text-yellow-800">');
        result.warnings.forEach((warning) => sections.push(`<li>${escapeHtml(warning)}</li>`));
        sections.push('</ul></div>');
    }
    if (result.debug) {
        sections.push('<div class="mt-4 rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">');
        sections.push(`<div>現場マスタ件数: ${formatNumber(result.debug.site_dict_count || 0)}</div>`);
        sections.push(`<div>一致契約コード数: ${formatNumber(result.debug.found_contracts_count || 0)}</div>`);
        sections.push(`<div>抽出明細数: ${formatNumber(result.debug.extracted_count || 0)}</div>`);
        sections.push('</div>');
    }

    sections.push('<div class="mt-4 text-center">');
    sections.push('<button onclick="resetForm()" class="btn btn-primary">新しい処理を始める</button>');
    sections.push('</div>');
    sections.push('</div>');

    content.innerHTML = sections.join('');
    container.classList.remove('hidden');
}

function renderSummaryTable(title, data) {
    const rows = Object.entries(data);
    if (!rows.length) return '';

    const html = [];
    html.push('<div class="mt-4 rounded border border-gray-200 bg-white p-3">');
    html.push(`<h4 class="mb-2 font-semibold text-gray-800">${escapeHtml(title)}</h4>`);
    html.push('<div class="overflow-x-auto">');
    html.push('<table class="min-w-full text-sm">');
    html.push('<thead><tr class="border-b text-left">');
    html.push('<th class="px-2 py-2">セグメント</th>');
    html.push('<th class="px-2 py-2 text-right">基本売上</th>');
    html.push('<th class="px-2 py-2 text-right">その他売上</th>');
    html.push('<th class="px-2 py-2 text-right">材料</th>');
    html.push('<th class="px-2 py-2 text-right">労務</th>');
    html.push('<th class="px-2 py-2 text-right">経費</th>');
    html.push('</tr></thead><tbody>');

    rows.forEach(([segment, values]) => {
        html.push('<tr class="border-b">');
        html.push(`<td class="px-2 py-2 font-semibold">${escapeHtml(segment)}</td>`);
        html.push(`<td class="px-2 py-2 text-right">${formatNumber(values['基本売上'])}</td>`);
        html.push(`<td class="px-2 py-2 text-right">${formatNumber(values['その他売上'])}</td>`);
        html.push(`<td class="px-2 py-2 text-right">${formatNumber(values['材料'])}</td>`);
        html.push(`<td class="px-2 py-2 text-right">${formatNumber(values['労務'])}</td>`);
        html.push(`<td class="px-2 py-2 text-right">${formatNumber(values['経費'])}</td>`);
        html.push('</tr>');
    });

    html.push('</tbody></table></div></div>');
    return html.join('');
}

function showError(message, details) {
    const modal = document.getElementById('error-modal');
    const content = document.getElementById('error-content');
    if (!modal || !content) return;

    const parts = [`<p class="mb-2 font-semibold text-red-600">${escapeHtml(message)}</p>`];
    if (details && details.length > 0) {
        parts.push('<div class="error-box">');
        parts.push('<p class="mb-2 font-semibold">詳細</p>');
        parts.push('<ul class="error-list">');
        details.forEach((detail) => parts.push(`<li>${escapeHtml(detail)}</li>`));
        parts.push('</ul></div>');
    }

    content.innerHTML = parts.join('');
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
}

function closeErrorModal() {
    const modal = document.getElementById('error-modal');
    if (!modal) return;
    modal.classList.add('hidden');
    modal.style.display = 'none';
}

function requestMissingSiteConfirmation(message, missingSites) {
    const modal = document.getElementById('missing-sites-modal');
    const content = document.getElementById('missing-sites-content');

    if (!modal || !content) {
        const lines = (missingSites || []).map((item) => {
            const siteName = item.site_name || '不明';
            return `契約コード: ${item.contract_code} / 現場名: ${siteName}`;
        });
        return Promise.resolve(window.confirm([message || '確認が必要です。', ...lines].join('\n')));
    }

    const parts = [
        `<p class="mb-3 font-semibold text-amber-700">${escapeHtml(message || '確認が必要です。')}</p>`,
        '<div class="rounded border border-amber-200 bg-amber-50 p-4">',
        '<p class="mb-2 text-sm text-amber-900">以下の現場は科目別推移表に見つかりませんでした。</p>',
        '<ul class="error-list">'
    ];

    (missingSites || []).forEach((item) => {
        parts.push(
            `<li class="text-amber-900">契約コード: ${escapeHtml(item.contract_code)} / 現場名: ${escapeHtml(item.site_name || '不明')}</li>`
        );
    });

    parts.push('</ul></div>');
    parts.push('<p class="mt-3 text-sm text-slate-600">無視して続行すると、上記の現場は今回の月次集計から除外します。</p>');

    content.innerHTML = parts.join('');
    modal.classList.remove('hidden');
    modal.style.display = 'flex';

    return new Promise((resolve) => {
        window.missingSitesConfirmResolver = resolve;
    });
}

function closeMissingSitesModal(shouldContinue) {
    const modal = document.getElementById('missing-sites-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
    }

    const resolver = window.missingSitesConfirmResolver;
    window.missingSitesConfirmResolver = null;
    if (resolver) {
        resolver(Boolean(shouldContinue));
    }
}

function downloadFile(filePath) {
    const filename = String(filePath || '').split('/').pop();
    if (!filename) return;

    const link = document.createElement('a');
    link.href = `/tools/monthly_generator/api/download/${filename}`;
    link.download = '月次集計結果.xlsx';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

function restoreFormWithFiles() {
    document.getElementById('upload-form').style.display = 'block';
    document.getElementById('progress-container').classList.add('hidden');
    document.getElementById('result-container').classList.add('hidden');
    closeMissingSitesModal(false);
}

function resetForm() {
    const form = document.getElementById('upload-form');
    if (form) {
        form.reset();
    }
    syncSiteSourceMode();
    restoreFormWithFiles();
}

function formatNumber(num) {
    if (typeof num !== 'number' || Number.isNaN(num)) {
        return '0';
    }
    return num.toLocaleString('ja-JP', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    });
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
