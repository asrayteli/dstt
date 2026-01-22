/**
 * Shifter-Sync 新規作成画面
 * 共通モジュール (ss_common.js) を使用
 */

$(document).ready(function() {
  let mode = 'scene';

  // モード切替
  $('#mode').change(function() {
    mode = $(this).val();
    $('#nameLabel').text(mode === 'scene' ? '現場名:' : '人物名:');
  });

  // 台数設定チェックボックス
  $('#enableCapacity').change(function() {
    const enabled = $(this).is(':checked');
    if (enabled) {
      $('#capacityInputGroup').slideDown(200);
      $('#capacity').attr('required', true);
    } else {
      $('#capacityInputGroup').slideUp(200);
      $('#capacity').attr('required', false);
    }
  });

  // フォーム内でEnterキー処理
  const formFields = ['#mode', '#year', '#month', '#target_name', '#capacity'];

  formFields.forEach((selector, index) => {
    $(selector).on('keydown', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();

        // 次のフィールドへ移動
        if (index < formFields.length - 1) {
          $(formFields[index + 1]).focus();
        } else {
          // 最後のフィールドなら作成開始
          $('#startBtn').click();
        }
      }
    });
  });

  // 台数入力時のリアルタイム更新
  $('#capacity').on('input', function() {
    const capacity = parseInt($(this).val()) || 0;
    ShifterSync.setState('requiredCapacity', capacity);
  });

  // 作成開始ボタン
  $('#startBtn').click(function() {
    const year = parseInt($('#year').val(), 10);
    const month = parseInt($('#month').val(), 10);
    const name = $('#target_name').val().trim();
    const capacityEnabled = $('#enableCapacity').is(':checked');
    const requiredCapacity = parseInt($('#capacity').val()) || 0;

    // バリデーション
    if (!year || !month || !name) {
      alert('年、月、名前を入力してください');
      return;
    }

    if (month < 1 || month > 12) {
      alert('月は1～12の範囲で入力してください');
      return;
    }

    if (capacityEnabled && requiredCapacity <= 0) {
      alert('台数設定を使用する場合は、必要人数を1以上で入力してください');
      return;
    }

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
      ShifterSync.buildCalendar(year, month, mode);

      // スムーズスクロール
      $('html, body').animate({
        scrollTop: $('#inputArea').offset().top - 20
      }, 600);
    }, 0);
  });

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
      .attr('download', `${mode},${year},${month},${name}.csv`)
      .appendTo('body');

    link[0].click();
    link.remove();
    URL.revokeObjectURL(url);

    alert('保存しました！');
  });
});
