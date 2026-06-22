(function() {
  'use strict';

  const CONFIG = window.CS_TEMPLATE_EDITOR || {};
  const PROJECT_ID = CONFIG.projectId || '';
  const MODE = CONFIG.mode === 'person' ? 'person' : 'scene';

  const state = {
    templateId: '',
    year: parseInt(CONFIG.defaultYear, 10) || new Date().getFullYear(),
    month: parseInt(CONFIG.defaultMonth, 10) || (new Date().getMonth() + 1),
    saving: false,
    dirtySinceSave: false
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
    return $('tpl-basis').value === 'weekday' ? 'weekday' : 'date';
  }

  function readOptions() {
    return {
      apply_mode: $('tpl-apply-mode').value,
      holiday_mode: $('tpl-holiday-mode').value,
      target_filter: $('tpl-target-filter').value
    };
  }

  function updateBasisNote() {
    const basis = readBasis();
    const holidayField = $('tpl-holiday-field');
    if (holidayField) {
      holidayField.style.display = basis === 'weekday' ? '' : 'none';
    }
    const note = $('tpl-basis-note');
    if (!note) {
      return;
    }
    if (basis === 'weekday') {
      note.textContent = '曜日基準：代表月の「各曜日の最初に現れる日」がその曜日のパターンになります。反映先の月では、同じ曜日の日へ繰り返し適用されます。';
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

  function buildCalendar(initialData) {
    ShifterSync.setState('mode', MODE);
    ShifterSync.setState('name', $('tpl-name').value || '');
    ShifterSync.setState('capacityEnabled', false);
    ShifterSync.setState('requiredCapacity', 0);
    ShifterSync.buildCalendar(state.year, state.month, MODE, initialData || {}, { editable: true });
  }

  function syncYearMonthInputs() {
    $('tpl-year').value = state.year;
    $('tpl-month').value = state.month;
  }

  function reloadRepresentativeMonth() {
    const year = parseInt($('tpl-year').value, 10);
    const month = parseInt($('tpl-month').value, 10);
    if (!year || year < 2000 || year > 2100 || !month || month < 1 || month > 12) {
      showFlash('代表月は 2000〜2100 年・1〜12 月で入力してください', 'error');
      return;
    }
    // 既存の入力内容は日付キーのまま引き継ぐ（新しい月に無い日付は自動的に落ちる）。
    const current = ShifterSync.getEntriesPerDay ? ShifterSync.getEntriesPerDay() : {};
    state.year = year;
    state.month = month;
    buildCalendar(current);
    showFlash(`${year}年${month}月で編集中`, 'info');
  }

  function collectPayload() {
    const name = String($('tpl-name').value || '').trim();
    if (!name) {
      throw new Error('テンプレート名を入力してください');
    }
    return {
      name,
      basis: readBasis(),
      representative_year: state.year,
      representative_month: state.month,
      entries_per_day: ShifterSync.getEntriesPerDay ? ShifterSync.getEntriesPerDay() : {},
      options: readOptions()
    };
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
      state.dirtySinceSave = false;
      setStatus(`保存しました（${template ? template.filled_day_count : 0} 日分）`);
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
    $('tpl-basis').value = template.basis === 'weekday' ? 'weekday' : 'date';
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
  }

  async function loadExistingTemplate(templateId) {
    const result = await requestJson(
      `/tools/shiftersync/cloudshift/api/project/${PROJECT_ID}/templates/${templateId}`
    );
    const template = result && result.template ? result.template : null;
    applyTemplateToForm(template);
    return template ? (template.slots || {}) : {};
  }

  function bindEvents() {
    $('tpl-form').addEventListener('submit', (event) => {
      event.preventDefault();
      saveTemplate();
    });
    $('tpl-reload-month').addEventListener('click', reloadRepresentativeMonth);
    $('tpl-basis').addEventListener('change', updateBasisNote);
    $('tpl-close').addEventListener('click', () => {
      window.close();
    });
    $('tpl-name').addEventListener('input', () => {
      ShifterSync.setState('name', $('tpl-name').value || '');
    });
    window.addEventListener('beforeunload', (event) => {
      if (state.dirtySinceSave) {
        event.preventDefault();
        event.returnValue = '';
      }
    });
    // カレンダー編集を検知して未保存フラグを立てる（保存忘れ警告用）。
    const host = document.querySelector('.cloud-editor-host');
    if (host) {
      host.addEventListener('click', () => {
        state.dirtySinceSave = true;
      });
    }
  }

  async function init() {
    if (!PROJECT_ID) {
      showFlash('プロジェクトを特定できませんでした', 'error');
      return;
    }
    syncYearMonthInputs();
    updateBasisNote();
    bindEvents();
    await loadSiteBranchesIfNeeded();

    let initialData = {};
    const templateId = templateIdFromUrl();
    if (templateId) {
      try {
        initialData = await loadExistingTemplate(templateId);
        syncYearMonthInputs();
        updateBasisNote();
        document.title = `DSTT - テンプレート編集 - ${$('tpl-name').value || ''}`;
      } catch (error) {
        showFlash('テンプレートの読み込みに失敗しました。新規作成として開始します。', 'error');
        state.templateId = '';
        initialData = {};
      }
    }
    buildCalendar(initialData);
  }

  document.addEventListener('DOMContentLoaded', () => {
    init().catch((error) => showFlash(error.message || '初期化に失敗しました', 'error'));
  });
})();
