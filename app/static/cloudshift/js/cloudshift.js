(function() {
  'use strict';

  const PAGE = window.CLOUDSHIFT_PAGE || {};
  const WEEKDAYS = ['月', '火', '水', '木', '金', '土', '日'];
  const state = {
    projects: [],
    projectPayload: null,
    baseMonth: null,
    guestName: PAGE.authenticatedEditorName || ''
  };

  function deepClone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function showFlash(message, type) {
    const el = document.getElementById('cloud-flash');
    if (!el) return;
    el.className = `cloud-flash ${type || 'info'}`;
    el.textContent = message;
    el.style.display = 'block';
    clearTimeout(showFlash._timer);
    showFlash._timer = setTimeout(() => {
      el.style.display = 'none';
    }, 3200);
  }

  async function requestJson(url, options) {
    const opts = Object.assign({ headers: {} }, options || {});
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

  function unwrapProjectPayload(payload) {
    if (payload && payload.project && payload.project.project) {
      return payload.project;
    }
    return payload;
  }

  function currentProject() {
    return state.projectPayload ? state.projectPayload.project : null;
  }

  function currentMonth() {
    return state.projectPayload ? state.projectPayload.month : null;
  }

  function currentMonthKey() {
    return state.projectPayload ? state.projectPayload.active_month_key : null;
  }

  function parseMonthKey(monthKey) {
    const match = String(monthKey || '').match(/^(\d{4})-(\d{2})$/);
    if (!match) return null;
    return { year: parseInt(match[1], 10), month: parseInt(match[2], 10) };
  }

  function formatMonthKey(monthKey) {
    const parsed = parseMonthKey(monthKey);
    return parsed ? `${parsed.year}年${parsed.month}月` : '未選択';
  }

  function nextMonthKey(monthKey, delta) {
    const parsed = parseMonthKey(monthKey);
    if (!parsed) return null;
    const date = new Date(parsed.year, parsed.month - 1 + delta, 1);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
  }

  function promptMonthKey(defaultValue) {
    const raw = window.prompt('対象の年月を YYYY-MM 形式で入力してください', defaultValue || '');
    if (!raw) return null;
    const match = raw.trim().match(/^(\d{4})-(\d{1,2})$/);
    if (!match) {
      showFlash('YYYY-MM 形式で入力してください', 'error');
      return null;
    }
    const year = parseInt(match[1], 10);
    const month = parseInt(match[2], 10);
    if (month < 1 || month > 12) {
      showFlash('月は1から12で入力してください', 'error');
      return null;
    }
    return `${year}-${String(month).padStart(2, '0')}`;
  }

  function renderWeekdayHeader() {
    return `<div class="weekday-header">${WEEKDAYS.map((day) => `<div>${day}</div>`).join('')}</div>`;
  }

  function buildCalendarHost(targetId) {
    const host = document.getElementById(targetId);
    if (!host) return;
    host.innerHTML = `
      <div class="container" style="padding: 18px;">
        ${renderWeekdayHeader()}
        <div id="shiftGrid" class="calendar-grid"></div>
      </div>
    `;
  }

  function mountEditor(targetId, editable) {
    const project = currentProject();
    const month = currentMonth();
    if (!project || !month) return;
    buildCalendarHost(targetId);
    ShifterSync.setState('mode', project.mode);
    ShifterSync.setState('name', project.title);
    ShifterSync.setState('capacityEnabled', (parseInt(document.getElementById('cloud-required-capacity')?.value || '0', 10) || 0) > 0);
    ShifterSync.setState('requiredCapacity', parseInt(document.getElementById('cloud-required-capacity')?.value || '0', 10) || 0);
    ShifterSync.buildCalendar(month.year, month.month, project.mode, month.entries_per_day || {}, { editable });
  }

  function renderHistory(history) {
    const container = document.getElementById('cloud-history-list');
    if (!container) return;
    if (!history || history.length === 0) {
      container.innerHTML = '<div class="cloud-empty">履歴はまだありません。</div>';
      return;
    }
    container.innerHTML = history.map((item) => {
      const changes = Array.isArray(item.changes) ? item.changes : [];
      return `
        <article class="cloud-history-item">
          <div class="cloud-history-meta">${escapeHtml(item.timestamp || '')} / ${escapeHtml(item.editor_name || '')}</div>
          <ul>${changes.map((change) => `<li>${escapeHtml(change)}</li>`).join('')}</ul>
        </article>
      `;
    }).join('');
  }

  function renderProjectList(activeId) {
    const list = document.getElementById('cloud-project-list');
    if (!list) return;
    if (!state.projects.length) {
      list.innerHTML = '<div class="cloud-empty">まだシフト帳がありません。</div>';
      return;
    }
    list.innerHTML = state.projects.map((project) => `
      <article class="cloud-project-item ${project.id === activeId ? 'active' : ''}" data-project-id="${project.id}">
        <div class="cloud-inline-actions" style="justify-content: space-between; align-items: start;">
          <div>
            <h3 class="cloud-section-title" style="margin-bottom: 4px;">${escapeHtml(project.title)}</h3>
            <div class="cloud-meta">${escapeHtml(project.mode === 'scene' ? '現場シフト' : '個人シフト')} / ${project.month_count}か月</div>
          </div>
          <span class="cloud-badge">${project.latest_month_key ? escapeHtml(project.latest_month_key) : '月未作成'}</span>
        </div>
      </article>
    `).join('');
  }

  function syncWorkspaceVisibility(hasProject) {
    const empty = document.getElementById('cloud-empty-state');
    const workspace = document.getElementById('cloud-workspace');
    if (!empty || !workspace) return;
    empty.classList.toggle('cloud-hidden', hasProject);
    workspace.classList.toggle('cloud-hidden', !hasProject);
  }

  function updateIdentityBadge(text) {
    const badge = document.getElementById('cloud-public-identity');
    if (!badge) return;
    if (!text) {
      badge.classList.add('cloud-hidden');
      badge.textContent = '';
      return;
    }
    badge.classList.remove('cloud-hidden');
    badge.textContent = text;
  }

  function renderWorkspaceCommon(editable) {
    const project = currentProject();
    const month = currentMonth();
    const monthKey = currentMonthKey();
    if (!project) return;

    const monthSelect = document.getElementById('cloud-month-select');
    if (monthSelect) {
      monthSelect.innerHTML = (project.month_keys || []).map((key) => `<option value="${key}" ${key === monthKey ? 'selected' : ''}>${formatMonthKey(key)}</option>`).join('');
    }

    const editorTitle = document.getElementById('cloud-editor-title');
    const editorMeta = document.getElementById('cloud-editor-meta');
    const capacityInput = document.getElementById('cloud-required-capacity');
    const editorPanel = document.getElementById('cloud-editor-panel');
    const emptyPanel = document.getElementById('cloud-month-empty') || document.getElementById('cloud-public-empty');
    const createFirst = document.getElementById('cloud-create-first-month');

    if (!month) {
      if (editorPanel) editorPanel.classList.add('cloud-hidden');
      if (emptyPanel) emptyPanel.classList.remove('cloud-hidden');
      if (createFirst && editable) createFirst.classList.remove('cloud-hidden');
      return;
    }

    if (emptyPanel) emptyPanel.classList.add('cloud-hidden');
    if (editorPanel) editorPanel.classList.remove('cloud-hidden');
    if (editorTitle) editorTitle.textContent = `${formatMonthKey(monthKey)} の編集`;
    if (editorMeta) editorMeta.textContent = `${project.mode === 'scene' ? '現場シフト' : '個人シフト'} / リビジョン ${month.revision}`;
    if (capacityInput) {
      capacityInput.value = month.required_capacity || 0;
      capacityInput.disabled = !editable;
    }
    const capacityField = document.getElementById('cloud-capacity-field');
    if (capacityField) {
      capacityField.classList.toggle('cloud-hidden', !editable);
    }
    mountEditor('cloud-editor-host', editable);
  }

  async function loadHistory(projectId) {
    try {
      const data = await requestJson(`/tools/shiftersync/cloudshift/api/project/${projectId}/history`);
      renderHistory(data.history || []);
    } catch (error) {
      renderHistory([]);
    }
  }

  function applyProjectPayload(payload) {
    state.projectPayload = payload;
    state.baseMonth = payload && payload.month ? deepClone(payload.month) : null;
  }

  async function loadProjects(selectId) {
    const data = await requestJson('/tools/shiftersync/cloudshift/api/list');
    state.projects = data.projects || [];
    renderProjectList(selectId || (currentProject() && currentProject().id));
  }

  async function selectProject(projectId, monthKey) {
    const suffix = monthKey ? `?month_key=${encodeURIComponent(monthKey)}` : '';
    const payload = unwrapProjectPayload(await requestJson(`/tools/shiftersync/cloudshift/api/project/${projectId}${suffix}`));
    applyProjectPayload(payload);
    syncWorkspaceVisibility(true);
    renderOwnerWorkspace();
    renderProjectList(projectId);
    await loadHistory(projectId);
  }

  function renderOwnerWorkspace() {
    const project = currentProject();
    if (!project) {
      syncWorkspaceVisibility(false);
      return;
    }
    syncWorkspaceVisibility(true);
    document.getElementById('cloud-project-title').value = project.title || '';
    document.getElementById('cloud-view-url').value = project.urls.view_url || '';
    document.getElementById('cloud-edit-url').value = project.urls.edit_url || '';
    document.getElementById('cloud-project-badges').innerHTML = `
      <span class="cloud-badge">${project.mode === 'scene' ? '現場シフト' : '個人シフト'}</span>
      <span class="cloud-badge">${(project.month_keys || []).length}か月</span>
      <span class="cloud-badge">更新 ${escapeHtml(project.updated_at || '')}</span>
    `;
    renderWorkspaceCommon(true);
  }

  async function saveTitle() {
    const project = currentProject();
    if (!project) return;
    const title = document.getElementById('cloud-project-title').value.trim();
    const payload = unwrapProjectPayload(await requestJson(`/tools/shiftersync/cloudshift/api/project/${project.id}/meta`, {
      method: 'PUT',
      body: { title }
    }));
    applyProjectPayload(payload);
    renderOwnerWorkspace();
    await loadProjects(project.id);
    await loadHistory(project.id);
    showFlash('タイトルを保存しました', 'success');
  }

  function collectMonthPayload() {
    const month = currentMonth();
    if (!month) return null;
    return {
      base_month: {
        year: month.year,
        month: month.month,
        required_capacity: state.baseMonth ? state.baseMonth.required_capacity : month.required_capacity,
        entries_per_day: state.baseMonth ? state.baseMonth.entries_per_day : month.entries_per_day
      },
      required_capacity: parseInt(document.getElementById('cloud-required-capacity').value || '0', 10) || 0,
      entries_per_day: ShifterSync.getEntriesPerDay()
    };
  }

  async function saveCurrentMonthOwner() {
    const project = currentProject();
    const month = currentMonth();
    if (!project || !month) return;
    const payload = unwrapProjectPayload(await requestJson(`/tools/shiftersync/cloudshift/api/project/${project.id}/month/${month.year}/${month.month}`, {
      method: 'PUT',
      body: collectMonthPayload()
    }));
    applyProjectPayload(payload);
    renderOwnerWorkspace();
    await loadProjects(project.id);
    await loadHistory(project.id);
    showFlash('シフトを保存しました', 'success');
  }

  async function createMonth(targetKey, editableRoute) {
    const project = currentProject();
    if (!project) return;
    const parsed = parseMonthKey(targetKey);
    if (!parsed) return;
    const sourceKey = currentMonthKey();
    const useCopy = sourceKey ? window.confirm(`${formatMonthKey(targetKey)} を現在の月からコピーして作成しますか？\nOK: コピー / キャンセル: 空で作成`) : false;
    const body = {
      year: parsed.year,
      month: parsed.month,
      init_mode: useCopy ? 'copy' : 'blank',
      source_month_key: useCopy ? sourceKey : null
    };
    if (editableRoute === 'owner') {
      const payload = unwrapProjectPayload(await requestJson(`/tools/shiftersync/cloudshift/api/project/${project.id}/month`, { method: 'POST', body }));
      applyProjectPayload(payload);
      renderOwnerWorkspace();
      await loadProjects(project.id);
      await loadHistory(project.id);
    } else {
      if (!ensureGuestName()) return;
      body.editor_name = state.guestName;
      const payload = unwrapProjectPayload(await requestJson(`/tools/shiftersync/cloudshift/api/public/edit/${PAGE.token}/month`, { method: 'POST', body }));
      applyProjectPayload(payload);
      renderPublicWorkspace();
    }
    showFlash(`${formatMonthKey(targetKey)} を作成しました`, 'success');
  }

  async function navigateMonth(delta, editableRoute) {
    const activeKey = currentMonthKey() || (currentProject() && currentProject().latest_month_key);
    if (!activeKey) return;
    const targetKey = nextMonthKey(activeKey, delta);
    if (!targetKey) return;
    const keys = (currentProject().month_keys || []);
    if (keys.includes(targetKey)) {
      if (editableRoute === 'owner') {
        await selectProject(currentProject().id, targetKey);
      } else {
        await loadPublic(targetKey);
      }
      return;
    }
    if (editableRoute === 'view') {
      showFlash('その月はまだ作成されていません', 'info');
      return;
    }
    await createMonth(targetKey, editableRoute);
  }

  async function deleteCurrentMonth() {
    const project = currentProject();
    const month = currentMonth();
    if (!project || !month) return;
    if (!window.confirm(`${formatMonthKey(currentMonthKey())} を削除しますか？`)) return;
    await requestJson(`/tools/shiftersync/cloudshift/api/project/${project.id}/month/${month.year}/${month.month}`, { method: 'DELETE' });
    await selectProject(project.id);
    await loadProjects(project.id);
    showFlash('月データを削除しました', 'success');
  }

  async function deleteCurrentProject() {
    const project = currentProject();
    if (!project) return;
    if (!window.confirm(`シフト帳「${project.title}」を完全削除しますか？`)) return;
    await requestJson(`/tools/shiftersync/cloudshift/api/project/${project.id}`, { method: 'DELETE' });
    state.projectPayload = null;
    state.baseMonth = null;
    syncWorkspaceVisibility(false);
    await loadProjects();
    renderHistory([]);
    showFlash('シフト帳を削除しました', 'success');
  }

  async function regenerateUrls() {
    const project = currentProject();
    if (!project) return;
    if (!window.confirm('閲覧URLと編集URLを再発行します。旧URLは無効になります。よろしいですか？')) return;
    const data = await requestJson(`/tools/shiftersync/cloudshift/api/project/${project.id}/tokens/regenerate`, { method: 'POST', body: {} });
    document.getElementById('cloud-view-url').value = data.urls.view_url || '';
    document.getElementById('cloud-edit-url').value = data.urls.edit_url || '';
    showFlash('URLを再発行しました', 'success');
  }

  function bindOwnerEvents() {
    document.getElementById('cloud-create-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const formData = new FormData(event.currentTarget);
      const data = await requestJson('/tools/shiftersync/cloudshift/api/create', { method: 'POST', body: formData });
      const payload = unwrapProjectPayload(data);
      applyProjectPayload(payload);
      renderOwnerWorkspace();
      await loadProjects(payload.project.id);
      await loadHistory(payload.project.id);
      showFlash('シフト帳を作成しました', 'success');
    });

    document.getElementById('cloud-import-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const formData = new FormData(event.currentTarget);
      const data = await requestJson('/tools/shiftersync/cloudshift/api/create', { method: 'POST', body: formData });
      const payload = unwrapProjectPayload(data);
      applyProjectPayload(payload);
      renderOwnerWorkspace();
      await loadProjects(payload.project.id);
      await loadHistory(payload.project.id);
      showFlash('CSVを取り込みました', 'success');
    });

    document.getElementById('cloud-refresh-projects').addEventListener('click', () => loadProjects(currentProject() && currentProject().id));
    document.getElementById('cloud-project-list').addEventListener('click', (event) => {
      const item = event.target.closest('[data-project-id]');
      if (!item) return;
      selectProject(item.dataset.projectId);
    });
    document.getElementById('cloud-save-title').addEventListener('click', saveTitle);
    document.getElementById('cloud-open-view-url').addEventListener('click', () => window.open(document.getElementById('cloud-view-url').value, '_blank', 'noopener'));
    document.getElementById('cloud-open-edit-url').addEventListener('click', () => window.open(document.getElementById('cloud-edit-url').value, '_blank', 'noopener'));
    document.getElementById('cloud-copy-view-url').addEventListener('click', async () => { await navigator.clipboard.writeText(document.getElementById('cloud-view-url').value); showFlash('閲覧URLをコピーしました', 'success'); });
    document.getElementById('cloud-copy-edit-url').addEventListener('click', async () => { await navigator.clipboard.writeText(document.getElementById('cloud-edit-url').value); showFlash('編集URLをコピーしました', 'success'); });
    document.getElementById('cloud-regenerate-urls').addEventListener('click', regenerateUrls);
    document.getElementById('cloud-save-month').addEventListener('click', saveCurrentMonthOwner);
    document.getElementById('cloud-month-select').addEventListener('change', (event) => selectProject(currentProject().id, event.target.value));
    document.getElementById('cloud-prev-month').addEventListener('click', () => navigateMonth(-1, 'owner'));
    document.getElementById('cloud-next-month').addEventListener('click', () => navigateMonth(1, 'owner'));
    document.getElementById('cloud-add-prev-month').addEventListener('click', () => createMonth(nextMonthKey(currentMonthKey() || currentProject().latest_month_key, -1), 'owner'));
    document.getElementById('cloud-add-next-month').addEventListener('click', () => createMonth(nextMonthKey(currentMonthKey() || currentProject().latest_month_key, 1), 'owner'));
    document.getElementById('cloud-delete-month').addEventListener('click', deleteCurrentMonth);
    document.getElementById('cloud-delete-project').addEventListener('click', deleteCurrentProject);
    document.getElementById('cloud-export-csv').addEventListener('click', () => {
      if (!currentProject() || !currentMonthKey()) return;
      window.location.href = `/tools/shiftersync/cloudshift/api/project/${currentProject().id}/export/csv?month_key=${encodeURIComponent(currentMonthKey())}`;
    });
    document.getElementById('cloud-export-xlsx').addEventListener('click', () => {
      if (!currentProject() || !currentMonthKey()) return;
      window.location.href = `/tools/shiftersync/cloudshift/api/project/${currentProject().id}/export/xlsx?month_key=${encodeURIComponent(currentMonthKey())}`;
    });
    document.getElementById('cloud-create-first-month').addEventListener('click', async () => {
      const project = currentProject();
      if (!project) return;
      const monthKey = promptMonthKey(project.latest_month_key || new Date().toISOString().slice(0, 7));
      if (!monthKey) return;
      await createMonth(monthKey, 'owner');
    });
    document.getElementById('cloud-required-capacity').addEventListener('input', () => {
      const capacity = parseInt(document.getElementById('cloud-required-capacity').value || '0', 10) || 0;
      ShifterSync.setState('capacityEnabled', capacity > 0);
      ShifterSync.setState('requiredCapacity', capacity);
      ShifterSync.updateAllCapacityWarnings();
    });
  }

  function guestNameKey() {
    return `cloudshift:guest:${PAGE.token}`;
  }

  function ensureGuestName() {
    if (PAGE.authenticatedEditorName) {
      state.guestName = PAGE.authenticatedEditorName;
      updateIdentityBadge(`編集者: ${PAGE.authenticatedEditorName}`);
      return true;
    }
    if (!state.guestName) {
      state.guestName = window.localStorage.getItem(guestNameKey()) || '';
    }
    if (!state.guestName) {
      const entered = window.prompt('編集者名を入力してください', '');
      if (!entered || !entered.trim()) {
        showFlash('編集者名を入力すると保存できます', 'error');
        return false;
      }
      state.guestName = entered.trim();
      window.localStorage.setItem(guestNameKey(), state.guestName);
    }
    updateIdentityBadge(`編集者: ${state.guestName}`);
    return true;
  }

  async function loadPublic(monthKey) {
    const suffix = monthKey ? `?month_key=${encodeURIComponent(monthKey)}` : '';
    const payload = await requestJson(`/tools/shiftersync/cloudshift/api/public/${PAGE.accessMode}/${PAGE.token}${suffix}`);
    applyProjectPayload(payload);
    if (PAGE.accessMode === 'edit') {
      ensureGuestName();
    }
    renderPublicWorkspace();
  }

  function renderPublicWorkspace() {
    const project = currentProject();
    if (!project) return;
    renderWorkspaceCommon(PAGE.accessMode === 'edit');
    const saveBtn = document.getElementById('cloud-save-month');
    const addPrev = document.getElementById('cloud-add-prev-month');
    const addNext = document.getElementById('cloud-add-next-month');
    if (saveBtn) saveBtn.classList.toggle('cloud-hidden', PAGE.accessMode !== 'edit');
    if (addPrev) addPrev.classList.toggle('cloud-hidden', PAGE.accessMode !== 'edit');
    if (addNext) addNext.classList.toggle('cloud-hidden', PAGE.accessMode !== 'edit');
    if (PAGE.accessMode !== 'edit') {
      updateIdentityBadge('');
    }
  }

  async function saveCurrentMonthPublic() {
    const month = currentMonth();
    if (!month) return;
    if (!ensureGuestName()) return;
    const payload = unwrapProjectPayload(await requestJson(`/tools/shiftersync/cloudshift/api/public/edit/${PAGE.token}/month/${month.year}/${month.month}`, {
      method: 'PUT',
      body: Object.assign(collectMonthPayload(), { editor_name: state.guestName })
    }));
    applyProjectPayload(payload);
    renderPublicWorkspace();
    showFlash('シフトを保存しました', 'success');
  }

  function bindPublicEvents() {
    document.getElementById('cloud-month-select').addEventListener('change', (event) => loadPublic(event.target.value));
    document.getElementById('cloud-prev-month').addEventListener('click', () => navigateMonth(-1, PAGE.accessMode));
    document.getElementById('cloud-next-month').addEventListener('click', () => navigateMonth(1, PAGE.accessMode));
    document.getElementById('cloud-add-prev-month').addEventListener('click', () => createMonth(nextMonthKey(currentMonthKey() || currentProject().latest_month_key, -1), 'edit'));
    document.getElementById('cloud-add-next-month').addEventListener('click', () => createMonth(nextMonthKey(currentMonthKey() || currentProject().latest_month_key, 1), 'edit'));
    document.getElementById('cloud-save-month').addEventListener('click', saveCurrentMonthPublic);
    document.getElementById('cloud-export-csv').addEventListener('click', () => {
      if (!currentMonthKey()) return;
      window.location.href = `/tools/shiftersync/cloudshift/api/public/${PAGE.accessMode}/${PAGE.token}/export/csv?month_key=${encodeURIComponent(currentMonthKey())}`;
    });
    document.getElementById('cloud-export-xlsx').addEventListener('click', () => {
      if (!currentMonthKey()) return;
      window.location.href = `/tools/shiftersync/cloudshift/api/public/${PAGE.accessMode}/${PAGE.token}/export/xlsx?month_key=${encodeURIComponent(currentMonthKey())}`;
    });
    const createFirst = document.getElementById('cloud-create-first-month');
    if (createFirst) {
      createFirst.addEventListener('click', async () => {
        const monthKey = promptMonthKey(new Date().toISOString().slice(0, 7));
        if (!monthKey) return;
        await createMonth(monthKey, 'edit');
      });
    }
    const capacityInput = document.getElementById('cloud-required-capacity');
    if (capacityInput) {
      capacityInput.addEventListener('input', () => {
        const capacity = parseInt(capacityInput.value || '0', 10) || 0;
        ShifterSync.setState('capacityEnabled', capacity > 0);
        ShifterSync.setState('requiredCapacity', capacity);
      ShifterSync.updateAllCapacityWarnings();
      });
    }
  }

  async function initOwner() {
    const now = new Date();
    document.getElementById('cloud-create-year').value = now.getFullYear();
    document.getElementById('cloud-create-month').value = now.getMonth() + 1;
    bindOwnerEvents();
    await loadProjects();
  }

  async function initPublic() {
    bindPublicEvents();
    await loadPublic();
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (PAGE.type === 'owner') {
      initOwner().catch((error) => showFlash(error.message, 'error'));
      return;
    }
    initPublic().catch((error) => showFlash(error.message, 'error'));
  });
})();
