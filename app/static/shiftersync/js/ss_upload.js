/**
 * Shifter-Sync 既存シフト編集画面
 * 共通モジュール (ss_common.js) を使用
 */

$(document).ready(function() {
  // アップロードボタン
  $('#uploadBtn').click(function() {
    const fileInput = $('#shiftFile')[0];

    if (!fileInput.files.length) {
      alert('シフトCSVファイルを選択してください');
      return;
    }

    const file = fileInput.files[0];
    const reader = new FileReader();

    reader.onload = function(event) {
      const csvData = event.target.result;
      parseAndDisplayCSV(csvData);
    };

    reader.onerror = function() {
      alert('ファイルの読み込みに失敗しました');
    };

    reader.readAsText(file);
  });

  /**
   * CSVをパースしてカレンダーを表示
   */
  function parseAndDisplayCSV(csvData) {
    const lines = csvData.split('\n').map(line => line.trim()).filter(line => line.length > 0);

    if (lines.length < 3) {
      alert('CSVファイルの形式が正しくありません');
      return;
    }

    // メタデータ行をパース
    const meta = lines[0].split(',');
    if (meta.length < 4) {
      alert('CSVの1行目が不正です（モード,年,月,名前 が必要）');
      return;
    }

    const mode = meta[0].trim();
    const year = parseInt(meta[1]);
    const month = parseInt(meta[2]);
    const name = meta[3].trim();

    // 台数設定
    let capacityEnabled = false;
    let requiredCapacity = 0;

    if (meta.length >= 5) {
      capacityEnabled = true;
      requiredCapacity = parseInt(meta[4]) || 0;
    }

    // バリデーション
    if (!['scene', 'person'].includes(mode)) {
      alert('モードは "scene" または "person" である必要があります');
      return;
    }

    if (isNaN(year) || isNaN(month) || month < 1 || month > 12) {
      alert('年月の指定が不正です');
      return;
    }

    // データ行をパース
    const dataLines = lines.slice(2);
    const initialData = {};

    dataLines.forEach(line => {
      const parts = line.split(',').map(p => p.trim());
      if (parts.length < 1) return;

      const day = parseInt(parts[0]);
      if (isNaN(day) || day < 1 || day > 31) return;

      const entries = parts.slice(1).filter(e => e.length > 0);
      initialData[day] = entries;
    });

    // 状態を設定
    ShifterSync.setState('mode', mode);
    ShifterSync.setState('name', name);
    ShifterSync.setState('capacityEnabled', capacityEnabled);
    ShifterSync.setState('requiredCapacity', requiredCapacity);

    // カレンダーHTMLを構築
    const calendarHTML = `
      <div class="min-h-screen bg-blue-100 flex items-center justify-center px-4" style="padding: 20px 0;">
        <div class="container animate-fade-in">
          <h2>${mode === 'scene' ? '現場' : '人物'}: ${name} (${year}年${month}月)
            ${capacityEnabled ? ` - 必要人数: ${requiredCapacity}人/日` : ''}
          </h2>

          <div style="background: linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%); padding: 12px 20px; border-radius: 8px; margin-bottom: 16px; text-align: center; font-size: 13px; color: #1e40af; font-weight: 600; box-shadow: 0 2px 6px rgba(59, 130, 246, 0.1);">
            📝 操作方法: エントリーをダブルクリックで編集 / [×]ボタンまたは右クリックで削除 / Enterキーで素早く追加
          </div>

          <div class="weekday-header">
            <div>月</div>
            <div>火</div>
            <div>水</div>
            <div>木</div>
            <div>金</div>
            <div>土</div>
            <div>日</div>
          </div>

          <div id="shiftGrid" class="calendar-grid"></div>

          <div style="text-align: center; margin-top: 30px;">
            <button type="button" id="saveBtn" class="btn-danger">
              💾 保存
            </button>
          </div>
        </div>
      </div>
    `;

    $('#inputArea').html(calendarHTML).hide().fadeIn(400);

    // カレンダーを生成
    setTimeout(function() {
      ShifterSync.buildCalendar(year, month, mode, initialData);

      // スムーズスクロール
      $('html, body').animate({
        scrollTop: $('#inputArea').offset().top - 20
      }, 600);
    }, 0);
  }

  // 保存ボタン
  $(document).on('click', '#saveBtn', function() {
    if (!confirm('シフトをCSVファイルとして保存しますか？')) return;

    const csv = ShifterSync.buildCSV();
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);

    const mode = ShifterSync.getState('mode');
    const year = ShifterSync.getState('year');
    const month = ShifterSync.getState('month');
    const name = ShifterSync.getState('name');

    const link = $('<a>')
      .attr('href', url)
      .attr('download', `${mode},${year},${month},${name}_edited.csv`)
      .appendTo('body');

    link[0].click();
    link.remove();
    URL.revokeObjectURL(url);

    alert('保存しました！');
  });
});
