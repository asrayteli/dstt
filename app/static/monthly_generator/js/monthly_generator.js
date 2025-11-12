// 月次ジェネレーター JavaScript

// 初期化
document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('upload-form');
    if (form) {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            processFiles();
        });
    }
});

// ファイル処理のメイン関数
async function processFiles() {
    // バリデーション
    const subjectFile = document.getElementById('subject-file').files[0];
    const siteFile = document.getElementById('site-file').files[0];
    const reportFile = document.getElementById('report-file').files[0];
    const targetMonth = document.getElementById('target-month').value;
    const sheetName = document.getElementById('sheet-name').value;

    if (!subjectFile || !siteFile || !reportFile || !targetMonth || !sheetName) {
        alert('全ての項目を入力してください');
        return;
    }

    // フォームを非表示、進捗を表示
    document.getElementById('upload-form').style.display = 'none';
    document.getElementById('result-container').classList.add('hidden');
    document.getElementById('progress-container').classList.remove('hidden');

    // 進捗インジケーター開始
    startProgress();

    // FormData作成
    const formData = new FormData();
    formData.append('subject_file', subjectFile);
    formData.append('site_file', siteFile);
    formData.append('report_file', reportFile);
    formData.append('target_month', targetMonth);
    formData.append('sheet_name', sheetName);

    try {
        // APIリクエスト
        const response = await fetch('/tools/monthly_generator/api/process', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();

        // 進捗完了
        completeProgress();

        if (result.error) {
            // エラー表示
            showError(result.error, result.details);
            resetForm();
        } else {
            // 成功時
            showSuccess(result);
            downloadFile(result.output_file);
        }

    } catch (error) {
        console.error('Error:', error);
        completeProgress();
        showError('処理中にエラーが発生しました', [error.message]);
        resetForm();
    }
}

// 進捗インジケーター開始
function startProgress() {
    let progress = 0;
    const totalSteps = 7;
    const stepDuration = 800; // 各ステップの所要時間（ミリ秒）

    const interval = setInterval(() => {
        if (progress < totalSteps) {
            progress++;
            updateProgress(progress, totalSteps);
        } else {
            clearInterval(interval);
        }
    }, stepDuration);

    // インターバルIDを保存（必要に応じて停止可能）
    window.progressInterval = interval;
}

// 進捗更新
function updateProgress(currentStep, totalSteps) {
    const percentage = Math.round((currentStep / totalSteps) * 100);

    // プログレスバー更新
    const progressBar = document.getElementById('progress-bar');
    progressBar.style.width = percentage + '%';

    // パーセンテージ更新
    document.getElementById('progress-percentage').textContent = percentage + '%';

    // ステップ表示更新
    const stepItems = document.querySelectorAll('.step-item');
    stepItems.forEach((item, index) => {
        const stepNumber = index + 1;
        item.classList.remove('active', 'completed');

        if (stepNumber < currentStep) {
            item.classList.add('completed');
        } else if (stepNumber === currentStep) {
            item.classList.add('active');
        }
    });
}

// 進捗完了
function completeProgress() {
    if (window.progressInterval) {
        clearInterval(window.progressInterval);
    }

    updateProgress(7, 7); // 100%に設定

    // 少し遅延してから進捗を非表示
    setTimeout(() => {
        document.getElementById('progress-container').classList.add('hidden');
    }, 1000);
}

// 成功時の表示
function showSuccess(result) {
    const container = document.getElementById('result-container');
    const content = document.getElementById('result-content');

    let html = '<div class="result-box">';
    html += '<h3 class="text-lg font-bold mb-4 text-green-600">✓ 処理が完了しました</h3>';
    html += '<p class="mb-4">月次報告書にデータが書き込まれました。ダウンロードが自動で開始されます。</p>';

    // 集計データ表示
    if (result.data) {
        // 単月データ
        html += '<div class="mt-4 p-3 bg-white rounded border border-gray-200">';
        html += '<h4 class="font-semibold mb-2">集計結果（単月）</h4>';
        html += '<table class="min-w-full text-sm">';
        html += '<thead><tr class="border-b">';
        html += '<th class="text-left py-2 px-2">セグメント</th>';
        html += '<th class="text-right py-2 px-2">材料</th>';
        html += '<th class="text-right py-2 px-2">労務</th>';
        html += '<th class="text-right py-2 px-2">経費</th>';
        html += '<th class="text-right py-2 px-2">基本売上</th>';
        html += '<th class="text-right py-2 px-2">その他売上</th>';
        html += '</tr></thead><tbody>';

        for (const [segment, values] of Object.entries(result.data)) {
            html += '<tr class="border-b">';
            html += `<td class="py-2 px-2 font-semibold">${segment}</td>`;
            html += `<td class="text-right py-2 px-2">${formatNumber(values['材料'])}</td>`;
            html += `<td class="text-right py-2 px-2">${formatNumber(values['労務'])}</td>`;
            html += `<td class="text-right py-2 px-2">${formatNumber(values['経費'])}</td>`;
            html += `<td class="text-right py-2 px-2">${formatNumber(values['基本売上'])}</td>`;
            html += `<td class="text-right py-2 px-2">${formatNumber(values['その他売上'])}</td>`;
            html += '</tr>';
        }

        html += '</tbody></table>';
        html += '</div>';

        // 累計データ
        html += '<div class="mt-4 p-3 bg-white rounded border border-gray-200">';
        html += '<h4 class="font-semibold mb-2">集計結果（累計 - 4月から）</h4>';
        html += '<table class="min-w-full text-sm">';
        html += '<thead><tr class="border-b">';
        html += '<th class="text-left py-2 px-2">セグメント</th>';
        html += '<th class="text-right py-2 px-2">材料</th>';
        html += '<th class="text-right py-2 px-2">労務</th>';
        html += '<th class="text-right py-2 px-2">経費</th>';
        html += '<th class="text-right py-2 px-2">基本売上</th>';
        html += '<th class="text-right py-2 px-2">その他売上</th>';
        html += '</tr></thead><tbody>';

        for (const [segment, values] of Object.entries(result.data)) {
            html += '<tr class="border-b">';
            html += `<td class="py-2 px-2 font-semibold">${segment}</td>`;
            html += `<td class="text-right py-2 px-2">${formatNumber(values['材料合算'])}</td>`;
            html += `<td class="text-right py-2 px-2">${formatNumber(values['労務合算'])}</td>`;
            html += `<td class="text-right py-2 px-2">${formatNumber(values['経費合算'])}</td>`;
            html += `<td class="text-right py-2 px-2">${formatNumber(values['基本売上合算'])}</td>`;
            html += `<td class="text-right py-2 px-2">${formatNumber(values['その他売上合算'])}</td>`;
            html += '</tr>';
        }

        html += '</tbody></table>';
        html += '</div>';
    }

    // デバッグ情報表示
    if (result.debug) {
        html += '<div class="mt-4 p-3 bg-yellow-50 rounded border border-yellow-200">';
        html += '<h4 class="font-semibold mb-2 text-sm">デバッグ情報</h4>';
        html += '<div class="text-xs text-gray-700">';
        html += `<p>現場表の契約コード数: ${result.debug.site_dict_count}</p>`;
        html += `<p>一致した契約コード数: ${result.debug.found_contracts_count}</p>`;
        html += `<p>抽出されたデータ行数: ${result.debug.extracted_count}</p>`;
        html += '</div>';
        html += '</div>';
    }

    html += '<div class="mt-4 text-center">';
    html += '<button onclick="resetForm()" class="btn btn-primary">新しい処理を開始</button>';
    html += '</div>';
    html += '</div>';

    content.innerHTML = html;
    container.classList.remove('hidden');
}

// エラー表示
function showError(message, details) {
    const modal = document.getElementById('error-modal');
    const content = document.getElementById('error-content');

    let html = `<p class="font-semibold text-red-600 mb-2">${message}</p>`;

    if (details && details.length > 0) {
        html += '<div class="error-box">';
        html += '<p class="font-semibold mb-2">詳細:</p>';
        html += '<ul class="error-list">';
        details.forEach(detail => {
            html += `<li>${detail}</li>`;
        });
        html += '</ul>';
        html += '</div>';
    }

    content.innerHTML = html;
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
}

// エラーモーダルを閉じる
function closeErrorModal() {
    const modal = document.getElementById('error-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
}

// ファイルダウンロード
function downloadFile(filePath) {
    const filename = filePath.split('/').pop();
    const downloadUrl = `/tools/monthly_generator/api/download/${filename}`;

    // ダウンロードリンクを作成してクリック
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = '月次報告書_出力.xlsx';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// フォームリセット
function resetForm() {
    document.getElementById('upload-form').reset();
    document.getElementById('upload-form').style.display = 'block';
    document.getElementById('progress-container').classList.add('hidden');
    document.getElementById('result-container').classList.add('hidden');
}

// 数値フォーマット
function formatNumber(num) {
    if (typeof num !== 'number') {
        return '0';
    }
    return num.toLocaleString('ja-JP', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    });
}
