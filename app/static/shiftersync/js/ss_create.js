$(document).ready(function() {
  let mode = "scene";
  let entriesPerDay = {};

  // モード切替
  $('#mode').change(function() {
    mode = $(this).val();
    $('#nameLabel').text(mode === 'scene' ? '現場名:' : '人物名:');
    $('#entryHeader').text(mode === 'scene' ? '出勤者' : '現場');
  });

$('#startBtn').click(function() {
  const year  = parseInt($('#year').val(), 10);
  const month = parseInt($('#month').val(), 10);
  const name  = $('#target_name').val().trim();
  if (!year || !month || !name) {
    alert('全てのフィールドを入力してください');
    return;
  }

  // HTML構築してから buildGrid を呼ぶ
  const inputHTML = `
    <div class="min-h-screen bg-blue-100 flex items-center justify-center px-4">
      <div class="container bg-white rounded-2xl shadow-2xl p-8 w-full max-w-3xl animate-fade-in">

        <h2 id="entryHeader" class="text-2xl font-bold text-center text-blue-600 mb-6">
          ${mode === 'scene' ? '出勤者' : '現場'}
        </h2>

        <!-- 曜日ヘッダー -->
        <div class="weekday-header">
          <div>月</div>
          <div>火</div>
          <div>水</div>
          <div>木</div>
          <div>金</div>
          <div>土</div>
          <div>日</div>
        </div>

        <!-- カレンダー -->
        <div id="shiftGrid" class="calendar-grid"></div>

        <!-- 保存ボタン -->
        <div class="text-center mt-6">
          <button type="button" id="saveBtn" class="bg-red-600 hover:bg-red-700 text-white font-semibold py-2 px-6 rounded-lg shadow transition duration-300">
            保存
          </button>
        </div>

      </div>
    </div>
  `;

  $('#inputArea').html(inputHTML).show();

  // ✅ DOMが描画された後にカレンダーを生成＋スクロール
  setTimeout(function() {
    buildGrid(year, month);
    $('html, body').animate({
      scrollTop: $('#inputArea').offset().top
    }, 600);
  }, 0);
});

  // 保存ボタン
  $(document).on('click', '#saveBtn', function() {
    if (!confirm('シフトをCSVファイルとして保存しますか？')) return;
    const csv  = buildCSV();
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url  = URL.createObjectURL(blob);
    const link = $('<a>')
      .attr('href', url)
      .attr('download', `${mode},${$('#year').val()},${$('#month').val()},${$('#target_name').val()}.csv`)
      .appendTo('body');
    link[0].click();
    link.remove();
  });

  // Enterキーで追加
  $(document).on('keydown', '.entry-input', function(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      const $btn = $(this).siblings('.add-entry-btn');
      $btn.click();
      $(this).focus();
    }
  });

  // 追加ボタン
  $(document).on('click', '.add-entry-btn', function() {
    const day   = $(this).attr('data-day');
    const input = $(`.entry-input[data-day='${day}']`).val().trim();
    if (input && !entriesPerDay[day].includes(input)) {
      entriesPerDay[day].push(input);
      updateDropdown(day);
      $(`.entry-input[data-day='${day}']`).val('');
    }
  });

  // 右クリックで削除 - sceneとperson両方で動作するように修正
  $(document).on('contextmenu', '.entry-select option', function(e) {
    e.preventDefault();
    const opt = $(this);
    if (confirm(`「${opt.text()}」を削除しますか？`)) {
      const day = opt.parent().attr('data-day');
      const valueToRemove = opt.val();
      
      // entriesPerDay配列から該当の値を削除
      entriesPerDay[day] = entriesPerDay[day].filter(v => v !== valueToRemove);
      updateDropdown(day);
    }
  });

  // コピー機能
  $(document).on('click', '.copy-btn', function() {
    const target = parseInt($(this).attr('data-day'), 10);
    const src = parseInt($(`.copy-input[data-day='${target}']`).val(), 10);
    if (!entriesPerDay[src]) {
      alert(`${src}日が存在しません`);
      return;
    }
    entriesPerDay[target] = entriesPerDay[src].slice();
    updateDropdown(target);
  });

  // プルダウンを再描画 - sceneとperson両方で複数表示対応
  function updateDropdown(day) {
    const select = $(`.entry-select[data-day='${day}']`);
    const prev   = select.val() || [];
    select.empty();
    
    entriesPerDay[day].forEach(val => {
      const opt = $('<option>').val(val).text(val);
      // 常に選択状態にして表示する（sceneとperson両方で）
      opt.prop('selected', true);
      select.append(opt);
    });
    
    // selectのサイズを動的に調整（複数項目を表示するため）
    const itemCount = entriesPerDay[day].length;
    if (itemCount > 0) {
      select.attr('size', Math.min(itemCount, 5)); // 最大5行まで表示
    } else {
      select.attr('size', 1);
    }
  }

  // CSVビルド
  function buildCSV() {
    const lines = [
      `${mode},${$('#year').val()},${$('#month').val()},${$('#target_name').val()}`,
      mode === 'scene' ? '日付,出勤者' : '日付,現場'
    ];
    Object.keys(entriesPerDay).forEach(d => {
      if (entriesPerDay[d].length > 0) {
        lines.push(`${d},${entriesPerDay[d].join(',')}`);
      }
    });
    return lines.join("\n");
  }

  // カレンダー作成
  function buildGrid(year, month) {
    const grid = $('#shiftGrid');
    grid.empty();

    const daysInMonth = new Date(year, month, 0).getDate();
    // getDay(): 0(日)…6(土) → 月曜始まり(0)に変換
    const rawDow = new Date(year, month - 1, 1).getDay();
    const firstDow = (rawDow + 6) % 7;  // 月曜=0, 日曜=6

    entriesPerDay = {};

    // 1) 先頭にオフセット分の空セルを追加
    for (let i = 0; i < firstDow; i++) {
      grid.append($('<div>').addClass('day-box empty'));
    }

    // 2) 実際の日付セルを追加
    for (let d = 1; d <= daysInMonth; d++) {
      const dayBox = $('<div>')
        .addClass('day-box')
        .attr('data-day', d);

      // 日付ラベル
      dayBox.append(`<div class="date-label">${d}日</div>`);

      // 出勤者／現場プルダウン - sceneとperson両方で複数表示対応
      const select = $('<select>')
        .addClass('entry-select')
        .attr('data-day', d)
        .attr('multiple', true)  // sceneとperson両方でmultiple属性を付与
        .attr('size', 1);        // 初期は1行表示
      
      dayBox.append(select);

      // 操作説明テキストを追加
      const helpText = $('<div>')
        .addClass('help-text')
        .css({
          'font-size': '11px',
          'color': '#666',
          'margin-bottom': '5px',
          'text-align': 'center'
        })
        .text('右クリックで削除');
      dayBox.append(helpText);

      // 追加用インプット＆ボタンを横並びにするためのコンテナ
      const addContainer = $('<div>').css({
        'display': 'flex',
        'margin-bottom': '5px',
        'gap': '2px'
      });
      
      const input = $('<input>')
        .attr('type', 'text')
        .addClass('entry-input')
        .attr('placeholder', mode === 'scene' ? '人物名' : '現場名')
        .attr('data-day', d)
        .css({
          'flex': '1',
          'padding': '3px',
          'font-size': '12px'
        });
      const btn = $('<button>')
        .attr('type', 'button')
        .addClass('add-entry-btn')
        .text('追加')
        .attr('data-day', d)
        .css({
          'padding': '3px 8px',
          'font-size': '12px'
        });
      
      addContainer.append(input, btn);
      dayBox.append(addContainer);

      // コピー用インプット＆ボタンを横並びにするためのコンテナ
      const copyContainer = $('<div>').css({
        'display': 'flex',
        'gap': '2px'
      });
      
      const copyInput = $('<input>')
        .attr('type', 'number')
        .addClass('copy-input')
        .attr('placeholder', '日')
        .attr('min', 1)
        .attr('max', daysInMonth)
        .attr('data-day', d)
        .css({
          'flex': '1',
          'padding': '3px',
          'font-size': '12px'
        });
      const copyBtn = $('<button>')
        .attr('type', 'button')
        .addClass('copy-btn')
        .text('コピー')
        .attr('data-day', d)
        .css({
          'padding': '3px 8px',
          'font-size': '12px'
        });
      
      copyContainer.append(copyInput, copyBtn);
      dayBox.append(copyContainer);

      grid.append(dayBox);
      entriesPerDay[d] = [];
    }
  }
});