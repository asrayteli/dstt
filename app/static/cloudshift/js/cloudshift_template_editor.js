(function() {
  'use strict';

  const CONFIG = window.CS_TEMPLATE_EDITOR || {};
  const PROJECT_ID = CONFIG.projectId || '';
  const MODE = CONFIG.mode === 'person' ? 'person' : 'scene';

  // 曜日基準 / 週基準の「14枠グリッド」用の固定カレンダー。
  // 2026年6月は月曜始まりなので、1〜7日が上段（通常の月〜日）、
  // 8〜14日が下段（祝日だった場合の月〜日）のちょうど2行になる。
  const WEEKDAY_GRID_YEAR = 2026;
  const WEEKDAY_GRID_MONTH = 6;
  const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'];
  const SLOT_DAY_LABELS = {};
  const SLOT_DAY_TONES = {};
  for (let i = 0; i < 7; i += 1) {
    SLOT_DAY_LABELS[i + 1] = WEEKDAY_LABELS[i];
    SLOT_DAY_LABELS[i + 8] = `${WEEKDAY_LABELS[i]}（祝）`;
    SLOT_DAY_TONES[i + 8] = 'is-holiday';
  }

  const state = {
    templateId: '',
    year: parseInt(CONFIG.defaultYear, 10) || new Date().getFullYear(),
    month: parseInt(CONFIG.defaultMonth, 10) || (new Date().getMonth() + 1),
    // 基準ごとの入力バッファ。基準を切り替えても入力内容は失われない。
    dateEntries: {},        // 日付基準: 日(1..N)キー
    weekdaySlots: {},       // 曜日/週基準: w0..w6 / h0..h6 キー
    activeLayout: 'date',   // 'date' | 'weekday'
    saving: false,
    savedFingerprint: ''
  };

  function $(id) {
    return document.getElementById(id);
  }

  function showFlash(message, type) {
    const el = $('cloud-flash');
    if (!el) {
      return;
    }
    el.className = `cloud-flash ${type || 'info'}`;
    el.textContent = message;
    el.style.display = 'block';
    clearTimeout(showFlash._timer);
    showFlash._timer = setTimeout(() => {
      el.style.display = 'none';
    }, 3200);
  }

  function setStatus(text) {
    const el = $('tpl-status');
    if (el) {
      el.textContent = text || '';
    }
  }

  async function requestJson(url, options) {
    const opts = Object.assign({ headers: {}, credentials: 'same-origin' }, options || {});
    if (opts.body && !(opts.body instanceof FormData) && !opts.headers['Content-Type']) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    const response = await fetch(url, opts);
    const isJson = (response.headers.get('content-type') || '').includes('application/json');
    const payload = isJson ? await response.json() : null;
    if (!response.ok) {
      throw new Error((payload && payload.error) || 'リクエストに失敗しました');
    }
    return payload;
  }

  function templateIdFromUrl() {
    try {
      const params = new URLSearchParams(window.location.search);
      return String(params.get('template_id') || '').trim();
    } catch (error) {
      return '';
    }
  }

  function readBasis() {
    const value = $('tpl-basis').value;
    return value === 'weekday' || value === 'week' ? value : 'date';
  }

  function layoutForBasis(basis) {
    return basis === 'date' ? 'date' : 'weekday';
  }

  function readOptions() {
    return {
      apply_mode: $('tpl-apply-mode').value,
      holiday_mode: $('tpl-holiday-mode').value,
      target_filter: $('tpl-target-filter').value
    };
  }

  // 14枠グリッド（日1..7=通常、8..14=祝日）と保存形式（w0..w6 / h0..h6）の相互変換。
  function slotsToGridData(slots) {
    const data = {};
    for (let i = 0; i < 7; i += 1) {
      data[String(i + 1)] = (slots && Array.isArray(slots[`w${i}`])) ? slots[`w${i}`] : [];
      data[String(i + 8)] = (slots && Array.isArray(slots[`h${i}`])) ? slots[`h${i}`] : [];
    }
    return data;
  }

  function gridDataToSlots(entries) {
    const slots = {};
    for (let i = 0; i < 7; i += 1) {
      const weekdayEntries = (entries && entries[String(i + 1)]) || [];
      const holidayEntries = (entries && entries[String(i + 8)]) || [];
      if (weekdayEntries.length) {
        slots[`w${i}`] = weekdayEntries;
      }
      if (holidayEntries.length) {
        slots[`h${i}`] = holidayEntries;
      }
    }
    return slots;
  }

  // 現在のカレンダー入力を、表示中レイアウトのバッファへ取り込む。
  function captureActiveLayout() {
    const entries = ShifterSync.getEntriesPerDay ? ShifterSync.getEntriesPerDay() : {};
    if (state.activeLayout === 'weekday') {
      state.weekdaySlots = gridDataToSlots(entries);
    } else {
      state.dateEntries = entries;
    }
  }

  function updateBasisNote() {
    const basis = readBasis();
    const holidayField = $('tpl-holiday-field');
    if (holidayField) {
      holidayField.style.display = basis === 'date' ? 'none' : '';
    }
    document.querySelectorAll('.tpl-date-only').forEach((el) => {
      el.style.display = basis === 'date' ? '' : 'none';
    });
    const note = $('tpl-basis-note');
    if (!note) {
      return;
    }
    if (basis === 'weekday') {
      note.textContent = '曜日基準：月〜日の7枠＋祝日だった場合の7枠でパターンを作ります。反映先の月では、同じ曜日の日へ繰り返し適用されます（祝日枠が空の祝日は通常枠で埋まります）。';
    } else if (basis === 'week') {
      note.textContent = '週基準：月〜日の7枠＋祝日だった場合の7枠で1週間分を作ります。反映時に日付を指定すると、その日を含む「月曜始まりの1週間」だけに反映されます。';
    } else {
      note.textContent = '日付基準：代表月の「1日・2日…」の内容が、反映先の月の同じ日付へそのまま入ります（存在しない日付はスキップ）。';
    }
  }

  async function loadSiteBranchesIfNeeded() {
    const site = CONFIG.site;
    if (MODE !== 'scene' || !site || !site.is_linked || !site.site_id) {
      return;
    }
    try {
      const response = await fetch(`/tools/siteplus/api/cloudshift/sites/${encodeURIComponent(site.site_id)}/branches`, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' }
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const branches = Array.isArray(payload.branches) ? payload.branches : [];
      ShifterSync.setState('siteContext', site);
      ShifterSync.setState('siteBranches', branches);
    } catch (error) {
      /* 枝番号が取れなくても、基本オプションでの編集は継続できる */
    }
  }

  function buildCalendar() {
    ShifterSync.setState('mode', MODE);
    ShifterSync.setState('name', $('tpl-name').value || '');
    ShifterSync.setState('capacityEnabled', false);
    ShifterSync.setState('requiredCapacity', 0);
    if (state.activeLayout === 'weekday') {
      ShifterSync.buildCalendar(WEEKDAY_GRID_YEAR, WEEKDAY_GRID_MONTH, MODE, slotsToGridData(state.weekdaySlots), {
        editable: true,
        dayCount: 14,
        dayLabels: SLOT_DAY_LABELS,
        dayToneClasses: SLOT_DAY_TONES,
        holidays: []
      });
    } else {
      ShifterSync.buildCalendar(state.year, state.month, MODE, state.dateEntries || {}, { editable: true });
    }
  }

  // 入力内容の指紋。保存時点と比較して未保存変更の有無を判定する（閲覧だけでは変化しない）。
  function currentFingerprint() {
    try {
      captureActiveLayout();
      return JSON.stringify({
        name: String($('tpl-name').value || '').trim(),
        basis: readBasis(),
        options: readOptions(),
        year: state.year,
        month: state.month,
        dateEntries: state.dateEntries,
        weekdaySlots: state.weekdaySlots
      });
    } catch (error) {
      return '';
    }
  }

  function markSavedBaseline() {
    state.savedFingerprint = currentFingerprint();
  }

  function hasUnsavedChanges() {
    return state.savedFingerprint !== currentFingerprint();
  }

  function syncYearMonthInputs() {
    $('tpl-year').value = state.year;
    $('tpl-month').value = state.month;
  }

  function switchBasisLayout() {
    const nextLayout = layoutForBasis(readBasis());
    if (nextLayout === state.activeLayout) {
      updateBasisNote();
      return;
    }
    // 切り替え前の入力を退避してからレイアウトを差し替える（内容は捨てない）。
    captureActiveLayout();
    state.activeLayout = nextLayout;
    updateBasisNote();
    buildCalendar();
  }

  function reloadRepresentativeMonth() {
    if (state.activeLayout !== 'date') {
      return;
    }
    const year = parseInt($('tpl-year').value, 10);
    const month = parseInt($('tpl-month').value, 10);
    if (!year || year < 2000 || year > 2100 || !month || month < 1 || month > 12) {
      showFlash('代表月は 2000〜2100 年・1〜12 月で入力してください', 'error');
      return;
    }
    // 既存の入力内容は日付キーのまま引き継ぐ（新しい月に無い日付は自動的に落ちる）。
    captureActiveLayout();
    state.year = year;
    state.month = month;
    buildCalendar();
    showFlash(`${year}年${month}月で編集中`, 'info');
  }

  function collectPayload() {
    const name = String($('tpl-name').value || '').trim();
    if (!name) {
      throw new Error('テンプレート名を入力してください');
    }
    captureActiveLayout();
    const basis = readBasis();
    const payload = {
      name,
      basis,
      representative_year: state.year,
      representative_month: state.month,
      options: readOptions()
    };
    if (layoutForBasis(basis) === 'weekday') {
      payload.weekday_slots = state.weekdaySlots;
    } else {
      payload.entries_per_day = state.dateEntries;
    }
    return payload;
  }

  function savedCountText(template) {
    if (!template) {
      return '保存しました';
    }
    const unit = template.slot_format === 'weekday' ? '枠' : '日分';
    return `保存しました（${Number(template.filled_day_count || 0)} ${unit}）`;
  }

  function notifyOpener(template) {
    try {
      if (window.opener && !window.opener.closed) {
        window.opener.postMessage(
          { type: 'cloudshift-template-saved', projectId: PROJECT_ID, template: template || null },
          window.location.origin
        );
      }
    } catch (error) {
      /* opener へ通知できなくても保存自体は完了している */
    }
  }

  async function saveTemplate() {
    if (state.saving) {
      return;
    }
    let payload;
    try {
      payload = collectPayload();
    } catch (error) {
      showFlash(error.message, 'error');
      return;
    }
    state.saving = true;
    setStatus('保存中…');
    const saveButton = $('tpl-save');
    if (saveButton) {
      saveButton.disabled = true;
    }
    try {
      let result;
      if (state.templateId) {
        result = await requestJson(
          `/tools/shiftersync/cloudshift/api/project/${PROJECT_ID}/templates/${state.templateId}`,
          { method: 'PUT', body: payload }
        );
      } else {
        result = await requestJson(
          `/tools/shiftersync/cloudshift/api/project/${PROJECT_ID}/templates`,
          { method: 'POST', body: payload }
        );
      }
      const template = result && result.template ? result.template : null;
      if (template && template.id) {
        state.templateId = template.id;
        // 以後の保存は同じテンプレートへの上書きにし、URL も更新しておく。
        try {
          const url = new URL(window.location.href);
          url.searchParams.set('template_id', template.id);
          window.history.replaceState(null, '', url.toString());
        } catch (error) {
          /* URL 更新に失敗しても致命的ではない */
        }
      }
      markSavedBaseline();
      setStatus(savedCountText(template));
      showFlash('テンプレートを保存しました', 'success');
      notifyOpener(template);
    } catch (error) {
      setStatus('');
      showFlash(error.message || '保存に失敗しました', 'error');
    } finally {
      state.saving = false;
      if (saveButton) {
        saveButton.disabled = false;
      }
    }
  }

  function applyTemplateToForm(template) {
    if (!template) {
      return;
    }
    state.templateId = template.id || '';
    $('tpl-name').value = template.name || '';
    const basis = template.basis === 'weekday' || template.basis === 'week' ? template.basis : 'date';
    $('tpl-basis').value = basis;
    const options = template.options || {};
    if (options.apply_mode) {
      $('tpl-apply-mode').value = options.apply_mode;
    }
    if (options.holiday_mode) {
      $('tpl-holiday-mode').value = options.holiday_mode;
    }
    if (options.target_filter) {
      $('tpl-target-filter').value = options.target_filter;
    }
    if (template.representative_year && template.representative_month) {
      state.year = template.representative_year;
      state.month = template.representative_month;
    }
    state.activeLayout = layoutForBasis(basis);
    if (state.activeLayout === 'weekday') {
      // 旧形式（代表月の日付キー）の曜日基準テンプレートもサーバー側で
      // weekday_slots に変換して返されるため、常にこちらを使えばよい。
      state.weekdaySlots = template.weekday_slots || {};
    } else {
      state.dateEntries = template.slots || {};
    }
  }

  async function loadExistingTemplate(templateId) {
    const result = await requestJson(
      `/tools/shiftersync/cloudshift/api/project/${PROJECT_ID}/templates/${templateId}`
    );
    const template = result && result.template ? result.template : null;
    applyTemplateToForm(template);
    return template;
  }

  function bindEvents() {
    $('tpl-form').addEventListener('submit', (event) => {
      event.preventDefault();
      saveTemplate();
    });
    $('tpl-reload-month').addEventListener('click', reloadRepresentativeMonth);
    $('tpl-basis').addEventListener('change', switchBasisLayout);
    $('tpl-close').addEventListener('click', () => {
      window.close();
    });
    $('tpl-name').addEventListener('input', () => {
      ShifterSync.setState('name', $('tpl-name').value || '');
    });
    window.addEventListener('beforeunload', (event) => {
      if (hasUnsavedChanges()) {
        event.preventDefault();
        event.returnValue = '';
      }
    });
  }

  async function init() {
    if (!PROJECT_ID) {
      showFlash('プロジェクトを特定できませんでした', 'error');
      return;
    }
    syncYearMonthInputs();
    bindEvents();
    await loadSiteBranchesIfNeeded();

    const templateId = templateIdFromUrl();
    if (templateId) {
      try {
        await loadExistingTemplate(templateId);
        syncYearMonthInputs();
        document.title = `DSTT - テンプレート編集 - ${$('tpl-name').value || ''}`;
      } catch (error) {
        showFlash('テンプレートの読み込みに失敗しました。新規作成として開始します。', 'error');
        state.templateId = '';
        state.dateEntries = {};
        state.weekdaySlots = {};
        state.activeLayout = layoutForBasis(readBasis());
      }
    } else {
      state.activeLayout = layoutForBasis(readBasis());
    }
    updateBasisNote();
    buildCalendar();
    // 読み込み直後（=保存済み状態）を基準にして、以降の編集だけを未保存として扱う。
    markSavedBaseline();
  }

  document.addEventListener('DOMContentLoaded', () => {
    init().catch((error) => showFlash(error.message || '初期化に失敗しました', 'error'));
  });
})();
