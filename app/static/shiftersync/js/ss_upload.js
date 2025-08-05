$(document).ready(function () {
  let mode = "scene";
  let entriesPerDay = {};

  // アップロードボタンの処理
  $('#uploadBtn').click(function () {
    const fileInput = $('#shiftFile')[0];
    if (!fileInput.files.length) {
      alert('シフトCSVファイルを選択してください');
      return;
    }

    const file = fileInput.files[0];
    const reader = new FileReader();
    reader.onload = function (event) {
      const csvData = event.target.result;
      parseCSV(csvData);
    };
    reader.readAsText(file);
  });

  // CSVを読み取ってカレンダーを構築
  function parseCSV(csvData) {
    const lines = csvData.split('\n').map(line => line.trim()).filter(line => line.length > 0);

    if (lines.length < 3) {
      alert("CSVファイルの形式が正しくありません。");
      return;
    }

    const meta = lines[0].split(',');  // scene,2025,7,テスト現場
    const shiftLines = lines.slice(2); // データ部

    mode = meta[0];
    const year = parseInt(meta[1]);
    const month = parseInt(meta[2]);
    const name = meta[3];

    const data = shiftLines.map(line => line.split(','));

    const inputHTML = `
      <div class="min-h-screen bg-blue-100 flex items-center justify-center px-4">
        <div class="container bg-white rounded-2xl shadow-2xl p-8 w-full max-w-3xl animate-fade-in">
          <h2 id="entryHeader" class="text-2xl font-bold text-center text-blue-600 mb-6">
            ${mode === 'scene' ? '出勤者' : '現場'}
          </h2>

          <div class="weekday-header">
            <div>月</div><div>火</div><div>水</div><div>木</div><div>金</div><div>土</div><div>日</div>
          </div>

          <div id="shiftGrid" class="calendar-grid"></div>

          <div class="text-center mt-6">
            <button type="button" id="saveBtn" class="bg-red-600 hover:bg-red-700 text-white font-semibold py-2 px-6 rounded-lg shadow transition duration-300">
              保存
            </button>
          </div>
        </div>
      </div>
    `;

    $('#inputArea').html(inputHTML).show();

    setTimeout(() => {
      buildGrid(year, month, data);
      $('html, body').animate({ scrollTop: $('#inputArea').offset().top }, 600);
    }, 0);
  }

  // カレンダーを生成
  function buildGrid(year, month, data) {
    const grid = $('#shiftGrid');
    grid.empty();
    entriesPerDay = {};

    const daysInMonth = new Date(year, month, 0).getDate();
    const rawDow = new Date(year, month - 1, 1).getDay();
    const firstDow = (rawDow + 6) % 7;

    for (let i = 0; i < firstDow; i++) {
      grid.append($('<div>').addClass('day-box empty'));
    }

    for (let d = 1; d <= daysInMonth; d++) {
      entriesPerDay[d] = [];

      const dayBox = $('<div>').addClass('day-box').attr('data-day', d);
      dayBox.append(`<div class="date-label">${d}日</div>`);

      const select = $('<select>').addClass('entry-select').attr('data-day', d);
      if (mode === 'scene') select.attr('multiple', true);
      dayBox.append(select);

      const matchedRow = data.find(row => parseInt(row[0], 10) === d);
      if (matchedRow) {
        const values = matchedRow.slice(1).filter(v => v !== '');
        entriesPerDay[d] = values;
        values.forEach(val => {
          select.append($('<option>').val(val).text(val));
        });
      }

      const addContainer = $('<div>').css({ 'display': 'flex', 'margin-bottom': '5px' });
      const input = $('<input>').attr({ type: 'text', 'data-day': d, placeholder: '追加' }).addClass('entry-input');
      const btn = $('<button>').attr({ type: 'button', 'data-day': d }).addClass('add-entry-btn').text('追加');
      addContainer.append(input, btn);
      dayBox.append(addContainer);

      const copyContainer = $('<div>').css({ 'display': 'flex' });
      const copyInput = $('<input>').attr({ type: 'number', min: 1, 'data-day': d, placeholder: 'コピー元' }).addClass('copy-input');
      const copyBtn = $('<button>').attr({ type: 'button', 'data-day': d }).addClass('copy-btn').text('コピー');
      copyContainer.append(copyInput, copyBtn);
      dayBox.append(copyContainer);

      grid.append(dayBox);
    }
  }

  // 保存処理
  $(document).on('click', '#saveBtn', function () {
    if (!confirm('シフトをCSVファイルとして保存しますか？')) return;
    const csv = buildCSV();
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = $('<a>')
      .attr('href', url)
      .attr('download', `${mode},${$('#year').val()},${$('#month').val()},${$('#target_name').val()}.csv`)
      .appendTo('body');
    link[0].click();
    link.remove();
  });

  // CSV生成
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

  // 追加ボタン
  $(document).on('click', '.add-entry-btn', function () {
    const day = $(this).attr('data-day');
    const val = $(`.entry-input[data-day='${day}']`).val().trim();
    if (val && !entriesPerDay[day].includes(val)) {
      entriesPerDay[day].push(val);
      updateDropdown(day);
      $(`.entry-input[data-day='${day}']`).val('');
    }
  });

  // エンターで追加
  $(document).on('keydown', '.entry-input', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      $(this).siblings('.add-entry-btn').click();
    }
  });

  // ドロップダウン再描画
  function updateDropdown(day) {
    const select = $(`.entry-select[data-day='${day}']`);
    const prev = select.val() || [];
    select.empty();
    entriesPerDay[day].forEach(val => {
      const opt = $('<option>').val(val).text(val);
      if (prev.includes(val)) opt.prop('selected', true);
      select.append(opt);
    });
  }

  // コピー機能
  $(document).on('click', '.copy-btn', function () {
    const target = parseInt($(this).attr('data-day'), 10);
    const src = parseInt($(`.copy-input[data-day='${target}']`).val(), 10);
    if (!entriesPerDay[src]) {
      alert(`${src}日が存在しません`);
      return;
    }
    entriesPerDay[target] = entriesPerDay[src].slice();
    updateDropdown(target);
  });

  // 削除（右クリック）
  $(document).on('contextmenu', '.entry-select option', function (e) {
    e.preventDefault();
    const opt = $(this);
    const day = opt.parent().attr('data-day');
    if (confirm(`「${opt.text()}」を削除しますか？`)) {
      entriesPerDay[day] = entriesPerDay[day].filter(v => v !== opt.val());
      updateDropdown(day);
    }
  });
});
