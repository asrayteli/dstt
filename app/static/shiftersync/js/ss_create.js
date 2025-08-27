$(document).ready(function() {
  let mode = "scene";
  let entriesPerDay = {};
  let capacityEnabled = false;
  let requiredCapacity = 0;

  // オプションマッピング定義
  const optionMappings = {
    'A': '午前',
    'P': '午後',
    'E': '早番',
    'L': '遅番',
    'M': 'マイクロ',
    'C': '中型',
    'O': '大型',
    'W': 'ワゴン',
    'V': '役員車両',
    'N1': '1号車',
    'N2': '2号車',
    'N3': '3号車',
    'N4': '4号車',
    'N5': '5号車'
    
  };

  // モード切替
  $('#mode').change(function() {
    mode = $(this).val();
    $('#nameLabel').text(mode === 'scene' ? '現場名:' : '人物名:');
    $('#entryHeader').text(mode === 'scene' ? '出勤者' : '現場');
  });

  // 台数設定チェックボックスの制御
  $('#enableCapacity').change(function() {
    capacityEnabled = $(this).is(':checked');
    if (capacityEnabled) {
      $('#capacityInputGroup').show();
      $('#capacity').attr('required', true);
    } else {
      $('#capacityInputGroup').hide();
      $('#capacity').attr('required', false);
      // すでにカレンダーが表示されている場合は背景色をリセット
      if ($('#shiftGrid').children().length > 0) {
        $('.day-box').removeClass('capacity-warning');
      }
    }
  });

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
    
    // 台数設定が有効な場合の追加バリデーション
    if (capacityEnabled) {
      requiredCapacity = parseInt($('#capacity').val()) || 0;
      if (requiredCapacity <= 0) {
        alert('台数設定を使用する場合は、必要人数を1以上で入力してください');
        return;
      }
    }
    
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

  // 台数入力時のリアルタイム更新
  $('#capacity').on('input', function() {
    requiredCapacity = parseInt($(this).val()) || 0;
    // すでにカレンダーが表示されている場合は背景色を更新
    if ($('#shiftGrid').children().length > 0) {
      updateCapacityWarnings();
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

    // 編集用inputを作成 - サイズを大きく調整
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
    updateCapacityWarning(target);
  });

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

  // 台数警告の更新（全日付）
  function updateCapacityWarnings() {
    if (!capacityEnabled || requiredCapacity <= 0) {
      $('.day-box').removeClass('capacity-warning');
      return;
    }
    
    Object.keys(entriesPerDay).forEach(day => {
      updateCapacityWarning(day);
    });
  }

  // CSVビルド - 台数設定を含める
  function buildCSV() {
    let headerLine = `${mode},${$('#year').val()},${$('#month').val()},${$('#target_name').val()}`;
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

      // 出勤者／現場リスト表示エリア
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

      // 操作説明テキストを追加
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

      // 追加用インプット＆ボタンを横並びにするためのコンテナ
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