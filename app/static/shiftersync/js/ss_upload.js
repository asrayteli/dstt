$(document).ready(function () {
  let mode = "scene";
  let entriesPerDay = {};
  let capacityEnabled = false;
  let requiredCapacity = 0;
  let year, month, name;

  // オプションマッピング定義
  const optionMappings = {
    'A': '午前',
    'P': '午後',
    '1': '1号車',
    '2': '2号車',
    'E': '早番',
    'L': '遅番'
  };

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

    const meta = lines[0].split(',');  // scene,2025,7,テスト現場[,台数]
    const shiftLines = lines.slice(2); // データ部

    mode = meta[0];
    year = parseInt(meta[1]);
    month = parseInt(meta[2]);
    name = meta[3];
    
    // 台数設定の読み込み
    if (meta.length >= 5) {
      capacityEnabled = true;
      requiredCapacity = parseInt(meta[4]) || 0;
    } else {
      capacityEnabled = false;
      requiredCapacity = 0;
    }

    const data = shiftLines.map(line => line.split(','));

    const inputHTML = `
      <div class="min-h-screen bg-blue-100 flex items-center justify-center px-4">
        <div class="container bg-white rounded-2xl shadow-2xl p-8 w-full max-w-3xl animate-fade-in">
          <h2 id="entryHeader" class="text-2xl font-bold text-center text-blue-600 mb-6">
            ${mode === 'scene' ? '出勤者' : '現場'} - ${name} (${year}年${month}月)
            ${capacityEnabled ? ` - 必要人数: ${requiredCapacity}人/日` : ''}
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

    // 空セルを追加
    for (let i = 0; i < firstDow; i++) {
      grid.append($('<div>').addClass('day-box empty'));
    }

    // 日付セルを追加
    for (let d = 1; d <= daysInMonth; d++) {
      entriesPerDay[d] = [];

      const dayBox = $('<div>').addClass('day-box').attr('data-day', d);
      
      // 日付ラベル
      dayBox.append(`<div class="date-label">${d}日</div>`);

      // オプション使用チェックボックス
      const optionCheckContainer = $('<div>').css({
        'display': 'flex',
        'align-items': 'center',
        'margin-bottom': '8px',
        'justify-content': 'center'
      });
      
      const optionCheckbox = $('<input>')
        .attr('type', 'checkbox')
        .addClass('option-checkbox')
        .attr('data-day', d)
        .css({
          'margin-right': '4px',
          'width': '14px',
          'height': '14px'
        });
      
      const optionLabel = $('<label>')
        .text('オプション使用')
        .css({
          'font-size': '11px',
          'color': '#666',
          'cursor': 'pointer'
        })
        .click(function() {
          optionCheckbox.prop('checked', !optionCheckbox.prop('checked')).trigger('change');
        });
      
      optionCheckContainer.append(optionCheckbox, optionLabel);
      dayBox.append(optionCheckContainer);

      // エントリー表示エリア
      const entryListContainer = $('<div>')
        .addClass('entry-list-container')
        .attr('data-day', d)
        .css({
          'min-height': '60px',
          'border': '1px solid #ccc',
          'border-radius': '6px',
          'padding': '4px',
          'margin-bottom': '8px',
          'background-color': '#f9f9f9'
        });
      
      dayBox.append(entryListContainer);

      // 操作説明テキスト
      const helpText = $('<div>')
        .addClass('help-text')
        .css({
          'font-size': '11px',
          'color': '#666',
          'margin-bottom': '5px',
          'text-align': 'center'
        })
        .text('右クリック:削除 / ダブルクリック:編集');
      dayBox.append(helpText);

      // 追加用コンテナ
      const addContainer = $('<div>').css({
        'display': 'flex',
        'margin-bottom': '5px',
        'gap': '2px',
        'flex-direction': 'column'
      });
      
      // 名前入力欄
      const nameInputContainer = $('<div>').css({
        'display': 'flex',
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
      
      nameInputContainer.append(input, btn);
      
      // オプション選択欄（初期は非表示）
      const optionInputContainer = $('<div>').css({
        'display': 'none',
        'gap': '2px',
        'align-items': 'center',
        'margin-top': '3px'
      }).addClass('option-input-container').attr('data-day', d);
      
      const optionLabel2 = $('<label>').text('オプション:').css({
        'font-size': '11px',
        'color': '#666',
        'min-width': '55px'
      });
      
      const optionSelect = $('<select>')
        .addClass('option-select')
        .attr('data-day', d)
        .css({
          'flex': '1',
          'padding': '2px',
          'font-size': '11px',
          'border': '1px solid #ccc',
          'border-radius': '4px'
        });
      
      // オプション選択肢を追加
      optionSelect.append('<option value="">なし</option>');
      Object.keys(optionMappings).forEach(key => {
        optionSelect.append(`<option value="${key}">${optionMappings[key]}</option>`);
      });
      
      optionInputContainer.append(optionLabel2, optionSelect);
      
      addContainer.append(nameInputContainer, optionInputContainer);
      dayBox.append(addContainer);

      // コピー用コンテナ
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

      // 既存データの読み込み
      const matchedRow = data.find(row => parseInt(row[0], 10) === d);
      if (matchedRow) {
        const values = matchedRow.slice(1).filter(v => v !== '');
        entriesPerDay[d] = values;
      }

      grid.append(dayBox);
    }

    // データを表示に反映
    Object.keys(entriesPerDay).forEach(day => {
      updateDropdown(day);
      updateCapacityWarning(day);
    });
  }

  // オプション使用チェックボックスの制御
  $(document).on('change', '.option-checkbox', function() {
    const day = $(this).attr('data-day');
    const isChecked = $(this).is(':checked');
    const optionContainer = $(`.option-input-container[data-day='${day}']`);
    
    if (isChecked) {
      optionContainer.css('display', 'flex');
    } else {
      optionContainer.css('display', 'none');
      // オプション選択をリセット
      $(`.option-select[data-day='${day}']`).val('');
    }
  });

  // 追加ボタン - オプション対応
  $(document).on('click', '.add-entry-btn', function() {
    const day = $(this).attr('data-day');
    const input = $(`.entry-input[data-day='${day}']`).val().trim();
    const optionValue = $(`.option-select[data-day='${day}']`).val();
    
    if (input) {
      let finalValue = input;
      
      // オプションが選択されている場合は内部形式で保存
      if (optionValue) {
        finalValue = `!${optionValue}!${input}`;
      }
      
      entriesPerDay[day].push(finalValue);
      updateDropdown(day);
      updateCapacityWarning(day);
      $(`.entry-input[data-day='${day}']`).val('');
      $(`.option-select[data-day='${day}']`).val('');
    }
  });

  // エンターで追加
  $(document).on('keydown', '.entry-input', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      $(this).siblings('.add-entry-btn').click();
    }
  });

  // 右クリックで削除
  $(document).on('contextmenu', '.entry-item', function(e) {
    e.preventDefault();
    const $item = $(this);
    if (confirm(`「${$item.text()}」を削除しますか？`)) {
      const day = $item.data('day');
      const valueToRemove = $item.data('value');
      const index = entriesPerDay[day].indexOf(valueToRemove);
      
      if (index !== -1) {
        entriesPerDay[day].splice(index, 1);
        updateDropdown(day);
        updateCapacityWarning(day);
      }
    }
  });

  // ダブルクリックでインライン編集開始
  $(document).on('dblclick', '.entry-item', function(e) {
    e.preventDefault();
    startInlineEdit($(this));
  });

  // インライン編集開始 - オプション対応
  function startInlineEdit($item) {
    const day = $item.data('day');
    const currentValue = $item.data('value'); // 内部形式
    const currentName = parseEntryForEdit(currentValue); // 編集用（名前のみ）
    const index = entriesPerDay[day].indexOf(currentValue);
    
    if (index === -1) return;

    // 編集用inputを作成
    const $editInput = $('<input>')
      .attr('type', 'text')
      .val(currentName)
      .addClass('inline-edit-input')
      .css({
        'position': 'absolute',
        'z-index': '1000',
        'background': 'white',
        'border': '2px solid #2563eb',
        'border-radius': '4px',
        'padding': '6px 10px',
        'font-size': '13px',
        'min-width': Math.max($item.outerWidth() + 20, currentName.length * 8 + 40) + 'px',
        'height': '32px'
      })
      .data('day', day)
      .data('index', index)
      .data('original-value', currentValue);

    // 元の要素の位置に合わせて配置
    const itemOffset = $item.offset();
    
    $editInput.css({
      'position': 'absolute',
      'top': itemOffset.top - 2 + 'px',
      'left': itemOffset.left - 5 + 'px'
    });

    // bodyに追加
    $('body').append($editInput);
    $editInput.focus().select();

    // 編集完了のイベントハンドラ
    $editInput.on('keydown', function(e) {
      if (e.key === 'Enter') {
        finishInlineEdit($(this), true);
      } else if (e.key === 'Escape') {
        finishInlineEdit($(this), false);
      }
    });

    $editInput.on('blur', function() {
      finishInlineEdit($(this), true);
    });
  }

  // インライン編集完了 - オプション対応
  function finishInlineEdit($input, save) {
    const day = $input.data('day');
    const index = $input.data('index');
    const originalValue = $input.data('original-value'); // 内部形式
    const newName = $input.val().trim();

    if (save && newName && newName !== parseEntryForEdit(originalValue)) {
      // 元のオプション部分を保持して新しい名前と結合
      const optionMatch = originalValue.match(/^!([^!]+)!(.+)$/);
      let newValue = newName;
      
      if (optionMatch) {
        const optionKey = optionMatch[1];
        newValue = `!${optionKey}!${newName}`;
      }
      
      entriesPerDay[day][index] = newValue;
      updateDropdown(day);
      updateCapacityWarning(day);
    }

    $input.remove();
  }

  // エントリーリストを再描画 - オプション表示対応
  function updateDropdown(day) {
    const container = $(`.entry-list-container[data-day='${day}']`);
    container.empty();
    
    entriesPerDay[day].forEach((val, index) => {
      // 内部形式を表示用に変換
      const displayText = parseEntryForDisplay(val);
      
      const entryItem = $('<div>')
        .addClass('entry-item')
        .attr('data-day', day)
        .attr('data-value', val) // 内部形式で保存
        .text(displayText) // 表示用テキスト
        .css({
          'background-color': '#ffffff',
          'border': '1px solid #d1d5db',
          'border-radius': '4px',
          'padding': '4px 8px',
          'margin': '2px',
          'cursor': 'pointer',
          'font-size': '12px',
          'display': 'inline-block',
          'user-select': 'none'
        })
        .hover(
          function() { $(this).css('background-color', '#e5e7eb'); },
          function() { $(this).css('background-color', '#ffffff'); }
        );
      
      container.append(entryItem);
    });
  }

  // エントリーを表示用に変換
  function parseEntryForDisplay(entry) {
    const optionMatch = entry.match(/^!([^!]+)!(.+)$/);
    if (optionMatch) {
      const optionKey = optionMatch[1];
      const name = optionMatch[2];
      const optionText = optionMappings[optionKey] || optionKey;
      return `${optionText} ${name}`;
    }
    return entry;
  }

  // エントリーを編集用に変換（名前部分のみ）
  function parseEntryForEdit(entry) {
    const optionMatch = entry.match(/^!([^!]+)!(.+)$/);
    if (optionMatch) {
      return optionMatch[2]; // 名前部分のみ
    }
    return entry;
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
    updateCapacityWarning(target);
  });

  // 台数警告の更新（個別の日付）
  function updateCapacityWarning(day) {
    if (!capacityEnabled || requiredCapacity <= 0) return;
    
    const $dayBox = $(`.day-box[data-day='${day}']`);
    const currentCount = entriesPerDay[day].length;
    
    if (currentCount < requiredCapacity) {
      $dayBox.addClass('capacity-warning');
    } else {
      $dayBox.removeClass('capacity-warning');
    }
  }

  // 保存処理 - 台数設定対応
  $(document).on('click', '#saveBtn', function () {
    if (!confirm('シフトをCSVファイルとして保存しますか？')) return;
    const csv = buildCSV();
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = $('<a>')
      .attr('href', url)
      .attr('download', `${mode},${year},${month},${name}_edited.csv`)
      .appendTo('body');
    link[0].click();
    link.remove();
  });

  // CSV生成 - 台数設定対応
  function buildCSV() {
    let headerLine = `${mode},${year},${month},${name}`;
    if (capacityEnabled) {
      headerLine += `,${requiredCapacity}`;
    }
    
    const lines = [
      headerLine,
      mode === 'scene' ? '日付,出勤者' : '日付,現場'
    ];
    Object.keys(entriesPerDay).forEach(d => {
      if (entriesPerDay[d].length > 0) {
        lines.push(`${d},${entriesPerDay[d].join(',')}`);
      }
    });
    return lines.join("\n");
  }
});