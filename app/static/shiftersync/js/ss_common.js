/**
 * Shifter-Sync 共通モジュール
 * カレンダーUI、ポップアップ、エントリー管理など
 */

const ShifterSync = (function() {
  'use strict';

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

  // 状態管理
  let state = {
    mode: 'scene',
    year: null,
    month: null,
    name: null,
    entriesPerDay: {},
    capacityEnabled: false,
    requiredCapacity: 0,
    currentEditingDay: null,
    selectedOptions: []
  };

  /**
   * カレンダーを生成
   */
  function buildCalendar(year, month, mode, initialData = null) {
    state.year = year;
    state.month = month;
    state.mode = mode;

    const grid = $('#shiftGrid');
    grid.empty();
    state.entriesPerDay = {};

    const daysInMonth = new Date(year, month, 0).getDate();
    const rawDow = new Date(year, month - 1, 1).getDay();
    const firstDow = (rawDow + 6) % 7; // 月曜始まり

    // 空セルを追加
    for (let i = 0; i < firstDow; i++) {
      grid.append($('<div>').addClass('day-box empty'));
    }

    // 日付セルを生成
    for (let d = 1; d <= daysInMonth; d++) {
      const dayBox = createDayBox(d, daysInMonth);
      grid.append(dayBox);
      state.entriesPerDay[d] = [];

      // 初期データがあれば読み込み
      if (initialData && initialData[d]) {
        state.entriesPerDay[d] = initialData[d];
        updateEntryDisplay(d);
        updateCapacityWarning(d);
      }
    }

    attachEventHandlers();
  }

  /**
   * 日付ボックスを作成
   */
  function createDayBox(day, maxDay) {
    const dayBox = $('<div>').addClass('day-box').attr('data-day', day);

    // 日付ラベル
    dayBox.append(`<div class="date-label">${day}日</div>`);

    // エントリー表示エリア
    const entryContainer = $('<div>')
      .addClass('entry-list-container')
      .attr('data-day', day);
    dayBox.append(entryContainer);

    // 入力グループ
    const inputGroup = $('<div>').addClass('input-group');

    // 名前入力欄
    const inputRow = $('<div>').addClass('input-row');
    const input = $('<input>')
      .attr('type', 'text')
      .addClass('entry-input')
      .attr('placeholder', state.mode === 'scene' ? '人物名' : '現場名')
      .attr('data-day', day);
    const addBtn = $('<button>')
      .attr('type', 'button')
      .addClass('add-entry-btn')
      .text('追加')
      .attr('data-day', day);
    inputRow.append(input, addBtn);
    inputGroup.append(inputRow);

    // オプション選択ボタン
    const optionBtn = $('<button>')
      .attr('type', 'button')
      .addClass('option-select-btn')
      .attr('data-day', day)
      .html('<span>📋</span><span>オプション選択</span>');
    inputGroup.append(optionBtn);

    dayBox.append(inputGroup);

    // コピー機能
    const copyRow = $('<div>').addClass('input-row');
    const copyInput = $('<input>')
      .attr('type', 'number')
      .addClass('copy-input')
      .attr('placeholder', '日')
      .attr('min', 1)
      .attr('max', maxDay)
      .attr('data-day', day);
    const copyBtn = $('<button>')
      .attr('type', 'button')
      .addClass('copy-btn')
      .text('コピー')
      .attr('data-day', day);
    copyRow.append(copyInput, copyBtn);
    dayBox.append(copyRow);

    return dayBox;
  }

  /**
   * イベントハンドラーをアタッチ
   */
  function attachEventHandlers() {
    // 名前入力欄でEnter
    $(document).off('keydown', '.entry-input').on('keydown', '.entry-input', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        const day = $(this).attr('data-day');
        $(`.add-entry-btn[data-day='${day}']`).click();
      }
    });

    // コピー入力欄でEnter
    $(document).off('keydown', '.copy-input').on('keydown', '.copy-input', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        const day = $(this).attr('data-day');
        $(`.copy-btn[data-day='${day}']`).click();
      }
    });

    // 追加ボタン
    $(document).off('click', '.add-entry-btn').on('click', '.add-entry-btn', function() {
      const day = $(this).attr('data-day');
      addEntry(day);
    });

    // オプション選択ボタン
    $(document).off('click', '.option-select-btn').on('click', '.option-select-btn', function() {
      const day = $(this).attr('data-day');
      showOptionPopup(day);
    });

    // コピーボタン
    $(document).off('click', '.copy-btn').on('click', '.copy-btn', function() {
      const day = parseInt($(this).attr('data-day'), 10);
      copyEntries(day);
    });

    // エントリー削除ボタン
    $(document).off('click', '.entry-item-delete').on('click', '.entry-item-delete', function(e) {
      e.stopPropagation();
      const day = $(this).closest('.entry-item').data('day');
      const value = $(this).closest('.entry-item').data('value');
      deleteEntry(day, value);
    });

    // エントリーダブルクリック（編集）
    $(document).off('dblclick', '.entry-item').on('dblclick', '.entry-item', function(e) {
      e.preventDefault();
      startInlineEdit($(this));
    });

    // エントリー右クリック（削除）
    $(document).off('contextmenu', '.entry-item').on('contextmenu', '.entry-item', function(e) {
      e.preventDefault();
      const day = $(this).data('day');
      const value = $(this).data('value');
      if (confirm(`「${$(this).find('.entry-item-text').text()}」を削除しますか？`)) {
        deleteEntry(day, value);
      }
    });
  }

  /**
   * エントリーを追加
   */
  function addEntry(day) {
    const input = $(`.entry-input[data-day='${day}']`);
    const name = input.val().trim();

    if (!name) {
      return;
    }

    // オプションがある場合は内部形式で保存
    let finalValue = name;
    const options = getSelectedOptionsForDay(day);

    if (options.length > 0) {
      // 最初のオプションのみ適用（シンプル化）
      finalValue = `!${options[0]}!${name}`;
    }

    state.entriesPerDay[day].push(finalValue);
    updateEntryDisplay(day);
    updateCapacityWarning(day);

    // 入力欄をクリアしてフォーカス
    input.val('').focus();

    // オプションをクリア
    clearOptionsForDay(day);
  }

  /**
   * エントリーを削除
   */
  function deleteEntry(day, value) {
    const index = state.entriesPerDay[day].indexOf(value);
    if (index !== -1) {
      state.entriesPerDay[day].splice(index, 1);
      updateEntryDisplay(day);
      updateCapacityWarning(day);
    }
  }

  /**
   * エントリーをコピー
   */
  function copyEntries(targetDay) {
    const srcDay = parseInt($(`.copy-input[data-day='${targetDay}']`).val(), 10);

    if (!state.entriesPerDay[srcDay]) {
      alert(`${srcDay}日が存在しません`);
      return;
    }

    state.entriesPerDay[targetDay] = state.entriesPerDay[srcDay].slice();
    updateEntryDisplay(targetDay);
    updateCapacityWarning(targetDay);

    // 入力欄をクリア
    $(`.copy-input[data-day='${targetDay}']`).val('');
  }

  /**
   * エントリー表示を更新
   */
  function updateEntryDisplay(day) {
    const container = $(`.entry-list-container[data-day='${day}']`);
    container.empty();

    state.entriesPerDay[day].forEach(value => {
      const displayText = parseEntryForDisplay(value);

      const entryItem = $('<div>')
        .addClass('entry-item')
        .attr('data-day', day)
        .attr('data-value', value);

      const textSpan = $('<span>')
        .addClass('entry-item-text')
        .text(displayText);

      const deleteBtn = $('<button>')
        .addClass('entry-item-delete')
        .html('×');

      entryItem.append(textSpan, deleteBtn);
      container.append(entryItem);
    });
  }

  /**
   * インライン編集を開始
   */
  function startInlineEdit($item) {
    const day = $item.data('day');
    const currentValue = $item.data('value');
    const currentName = parseEntryForEdit(currentValue);
    const index = state.entriesPerDay[day].indexOf(currentValue);

    if (index === -1) return;

    // 編集用input作成
    const $editInput = $('<input>')
      .attr('type', 'text')
      .val(currentName)
      .addClass('inline-edit-input');

    // 位置を計算
    const itemOffset = $item.offset();
    const minWidth = Math.max($item.outerWidth() + 20, currentName.length * 10 + 60);

    $editInput.css({
      top: itemOffset.top - 2 + 'px',
      left: itemOffset.left - 5 + 'px',
      minWidth: minWidth + 'px'
    });

    $editInput.data({
      day: day,
      index: index,
      originalValue: currentValue
    });

    $('body').append($editInput);
    $editInput.focus().select();

    // イベントハンドラ
    $editInput.on('keydown', function(e) {
      if (e.key === 'Enter') {
        finishInlineEdit($(this), true);
      } else if (e.key === 'Escape') {
        finishInlineEdit($(this), false);
      }
    });

    $editInput.on('blur', function() {
      setTimeout(() => finishInlineEdit($(this), true), 100);
    });
  }

  /**
   * インライン編集を完了
   */
  function finishInlineEdit($input, save) {
    if (!$input.length) return;

    const day = $input.data('day');
    const index = $input.data('index');
    const originalValue = $input.data('originalValue');
    const newName = $input.val().trim();

    if (save && newName && newName !== parseEntryForEdit(originalValue)) {
      // オプション部分を保持
      const optionMatch = originalValue.match(/^!([^!]+)!(.+)$/);
      let newValue = newName;

      if (optionMatch) {
        const optionKey = optionMatch[1];
        newValue = `!${optionKey}!${newName}`;
      }

      state.entriesPerDay[day][index] = newValue;
      updateEntryDisplay(day);
      updateCapacityWarning(day);
    }

    $input.remove();
  }

  /**
   * オプション選択ポップアップを表示
   */
  function showOptionPopup(day) {
    state.currentEditingDay = day;
    state.selectedOptions = getSelectedOptionsForDay(day);

    // オーバーレイ作成
    const overlay = $('<div>').addClass('popup-overlay');

    // ポップアップコンテンツ
    const popup = $('<div>').addClass('popup-content');

    // ヘッダー
    popup.append(`<div class="popup-header">オプション選択</div>`);

    // 時間セクション
    const timeSection = createOptionSection('⏰ 時間帯', ['A', 'P', 'E', 'L']);
    popup.append(timeSection);

    // 車両セクション
    const vehicleSection = createOptionSection('🚗 車両タイプ', ['M', 'C', 'O', 'W', 'V']);
    popup.append(vehicleSection);

    // 車番セクション
    const carNumSection = createOptionSection('🔢 車番号', ['N1', 'N2', 'N3', 'N4', 'N5']);
    popup.append(carNumSection);

    // フッター
    const footer = $('<div>').addClass('popup-footer');
    const clearBtn = $('<button>')
      .addClass('popup-clear-btn')
      .text('クリア')
      .click(() => {
        state.selectedOptions = [];
        updateOptionButtonStates();
        updateOptionSelectButton(day);
      });
    const confirmBtn = $('<button>')
      .addClass('popup-confirm-btn')
      .text('決定')
      .click(() => {
        saveOptionsForDay(day);
        overlay.remove();
        // 名前入力欄にフォーカス
        $(`.entry-input[data-day='${day}']`).focus();
      });
    footer.append(clearBtn, confirmBtn);
    popup.append(footer);

    overlay.append(popup);
    $('body').append(overlay);

    // オーバーレイクリックで閉じる
    overlay.on('click', function(e) {
      if (e.target === overlay[0]) {
        overlay.remove();
      }
    });

    // Escapeキーで閉じる
    $(document).one('keydown', function(e) {
      if (e.key === 'Escape') {
        overlay.remove();
      }
    });
  }

  /**
   * オプションセクションを作成
   */
  function createOptionSection(title, optionKeys) {
    const section = $('<div>').addClass('popup-section');
    section.append(`<div class="popup-section-title">${title}</div>`);

    const buttonGrid = $('<div>').addClass('option-buttons');

    optionKeys.forEach(key => {
      const btn = $('<button>')
        .addClass('option-btn')
        .attr('data-option', key)
        .text(optionMappings[key])
        .click(function() {
          toggleOption(key);
        });

      if (state.selectedOptions.includes(key)) {
        btn.addClass('selected');
      }

      buttonGrid.append(btn);
    });

    section.append(buttonGrid);
    return section;
  }

  /**
   * オプションをトグル
   */
  function toggleOption(optionKey) {
    const index = state.selectedOptions.indexOf(optionKey);

    if (index === -1) {
      // 選択中のオプションをクリア（1つのみ選択可能）
      state.selectedOptions = [optionKey];
    } else {
      state.selectedOptions.splice(index, 1);
    }

    updateOptionButtonStates();
  }

  /**
   * オプションボタンの状態を更新
   */
  function updateOptionButtonStates() {
    $('.option-btn').each(function() {
      const key = $(this).attr('data-option');
      if (state.selectedOptions.includes(key)) {
        $(this).addClass('selected');
      } else {
        $(this).removeClass('selected');
      }
    });
  }

  /**
   * 日付のオプションを取得
   */
  function getSelectedOptionsForDay(day) {
    const btn = $(`.option-select-btn[data-day='${day}']`);
    return btn.data('selected-options') || [];
  }

  /**
   * 日付のオプションを保存
   */
  function saveOptionsForDay(day) {
    const btn = $(`.option-select-btn[data-day='${day}']`);
    btn.data('selected-options', state.selectedOptions.slice());
    updateOptionSelectButton(day);
  }

  /**
   * 日付のオプションをクリア
   */
  function clearOptionsForDay(day) {
    const btn = $(`.option-select-btn[data-day='${day}']`);
    btn.data('selected-options', []);
    updateOptionSelectButton(day);
  }

  /**
   * オプション選択ボタンの表示を更新
   */
  function updateOptionSelectButton(day) {
    const btn = $(`.option-select-btn[data-day='${day}']`);
    const options = getSelectedOptionsForDay(day);

    if (options.length > 0) {
      const optionText = options.map(key => optionMappings[key]).join(', ');
      btn.html(`<span>✓</span><span>${optionText}</span>`);
      btn.addClass('has-options');
    } else {
      btn.html('<span>📋</span><span>オプション選択</span>');
      btn.removeClass('has-options');
    }
  }

  /**
   * 台数警告を更新（個別）
   */
  function updateCapacityWarning(day) {
    if (!state.capacityEnabled || state.requiredCapacity <= 0) return;

    const $dayBox = $(`.day-box[data-day='${day}']`);
    const currentCount = state.entriesPerDay[day].length;

    if (currentCount < state.requiredCapacity) {
      $dayBox.addClass('capacity-warning');
    } else {
      $dayBox.removeClass('capacity-warning');
    }
  }

  /**
   * 台数警告を更新（全日付）
   */
  function updateAllCapacityWarnings() {
    if (!state.capacityEnabled || state.requiredCapacity <= 0) {
      $('.day-box').removeClass('capacity-warning');
      return;
    }

    Object.keys(state.entriesPerDay).forEach(day => {
      updateCapacityWarning(day);
    });
  }

  /**
   * エントリーを表示用に変換
   */
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

  /**
   * エントリーを編集用に変換
   */
  function parseEntryForEdit(entry) {
    const optionMatch = entry.match(/^!([^!]+)!(.+)$/);
    if (optionMatch) {
      return optionMatch[2];
    }
    return entry;
  }

  /**
   * CSVを生成
   */
  function buildCSV() {
    let headerLine = `${state.mode},${state.year},${state.month},${state.name}`;
    if (state.capacityEnabled) {
      headerLine += `,${state.requiredCapacity}`;
    }

    const lines = [
      headerLine,
      state.mode === 'scene' ? '日付,出勤者' : '日付,現場'
    ];

    Object.keys(state.entriesPerDay).sort((a, b) => parseInt(a) - parseInt(b)).forEach(day => {
      if (state.entriesPerDay[day].length > 0) {
        lines.push(`${day},${state.entriesPerDay[day].join(',')}`);
      }
    });

    return lines.join("\n");
  }

  /**
   * 状態を設定
   */
  function setState(key, value) {
    state[key] = value;
    if (key === 'capacityEnabled' || key === 'requiredCapacity') {
      updateAllCapacityWarnings();
    }
  }

  /**
   * 状態を取得
   */
  function getState(key) {
    return state[key];
  }

  // 公開API
  return {
    buildCalendar: buildCalendar,
    buildCSV: buildCSV,
    setState: setState,
    getState: getState,
    updateAllCapacityWarnings: updateAllCapacityWarnings
  };
})();
