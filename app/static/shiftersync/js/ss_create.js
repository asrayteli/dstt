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
          出勤者
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

  // 右クリックで削除
  $(document).on('contextmenu', '.entry-select option', function(e) {
    e.preventDefault();
    const opt = $(this);
    if (confirm(`「${opt.text()}」を削除しますか？`)) {
      const day = opt.parent().attr('data-day');
      entriesPerDay[day] = entriesPerDay[day].filter(v => v !== opt.val());
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

  // プルダウンを再描画
  function updateDropdown(day) {
    const select = $(`.entry-select[data-day='${day}']`);
    const prev   = select.val() || [];
    select.empty();
    entriesPerDay[day].forEach(val => {
      const opt = $('<option>').val(val).text(val);
      if (prev.includes(val)) opt.prop('selected', true);
      select.append(opt);
    });
  }

  // CSVビルド
  function buildCSV() {
    const lines = [
      `${mode},${$('#year').val()},${$('#month').val()},${$('#target_name').val()}`,
      mode === 'scene' ? '日付,出勤者' : '日付,現場'
    ];
    Object.keys(entriesPerDay).forEach(d => {
      lines.push(`${d},${entriesPerDay[d].join(',')}`);
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

    // CSSで7列グリッドを設定しているので、行数の設定は不要
    // grid.css() での grid-template-rows 設定を削除

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

      // 出勤者／現場プルダウン
      const select = $('<select>')
        .addClass('entry-select')
        .attr('data-day', d);
      if (mode === 'scene') select.attr('multiple', true);
      dayBox.append(select);

      // 追加用インプット＆ボタンを横並びにするためのコンテナ
      const addContainer = $('<div>').css({
        'display': 'flex',
        'margin-bottom': '5px'
      });
      
      const input = $('<input>')
        .attr('type', 'text')
        .addClass('entry-input')
        .attr('placeholder', '追加')
        .attr('data-day', d);
      const btn = $('<button>')
        .attr('type', 'button')
        .addClass('add-entry-btn')
        .text('追加')
        .attr('data-day', d);
      
      addContainer.append(input, btn);
      dayBox.append(addContainer);

      // コピー用インプット＆ボタンを横並びにするためのコンテナ
      const copyContainer = $('<div>').css({
        'display': 'flex'
      });
      
      const copyInput = $('<input>')
        .attr('type', 'number')
        .addClass('copy-input')
        .attr('placeholder', 'コピー元')
        .attr('min', 1)
        .attr('data-day', d);
      const copyBtn = $('<button>')
        .attr('type', 'button')
        .addClass('copy-btn')
        .text('コピー')
        .attr('data-day', d);
      
      copyContainer.append(copyInput, copyBtn);
      dayBox.append(copyContainer);

      grid.append(dayBox);
      entriesPerDay[d] = [];
    }
  }
});