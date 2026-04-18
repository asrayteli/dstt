/**
 * Shared Shifter-Sync calendar editor.
 * Used by ShifterSync screens and CloudShift.
 */

const ShifterSync = (function() {
  'use strict';

  const optionMappings = {
    A: '\u5348\u524d',
    P: '\u5348\u5f8c',
    E: '\u65e9\u756a',
    L: '\u9045\u756a',
    TEMP: '\u81e8\u6642\u4fbf',
    M: '\u30de\u30a4\u30af\u30ed',
    C: '\u4e2d\u578b',
    O: '\u5927\u578b',
    W: '\u30ef\u30b4\u30f3',
    V: '\u5f79\u54e1\u8eca\u4e21',
    N1: '1\u53f7\u8eca',
    N2: '2\u53f7\u8eca',
    N3: '3\u53f7\u8eca',
    N4: '4\u53f7\u8eca',
    N5: '5\u53f7\u8eca'
  };

  const leaveOptionMappings = {
    PAID: '\u6709\u4f11',
    COMP: '\u4ee3\u4f11',
    CONDOLENCE: '\u6176\u5f14\u4f11\u6687',
    CARE: '\u4ecb\u8b77\u4f11\u6687',
    REFRESH: '\u30ea\u30d5\u30ec\u30c3\u30b7\u30e5\u4f11\u6687',
    OTHER: '\u305d\u306e\u4ed6'
  };

  const allOptionMappings = Object.assign({}, optionMappings, leaveOptionMappings);
  const shiftTimeOptionKeys = ['A', 'P', 'E', 'L', 'TEMP'];
  const leaveOptionKeys = Object.keys(leaveOptionMappings);
  const vehicleOptionKeys = ['M', 'C', 'O', 'W', 'V'];
  const vehicleNumberOptionKeys = ['N1', 'N2', 'N3', 'N4', 'N5'];
  const commentRowPrefix = '#comment';
  const employeeNumberRowPrefix = '#employee_number';
  const projectEmployeeNumberRowPrefix = '#project_employee_number';
  const siteRowIdRowPrefix = '#site_row_id';
  const siteIdRowPrefix = '#site_id';
  const siteNameRowPrefix = '#site_name';
  const siteBranchRowIdRowPrefix = '#site_branch_row_id';
  const siteBranchRowPrefix = '#site_branch';
  const shiftSyncSourceTypes = ['scene_shift', 'person_shift'];

  const state = {
    mode: 'scene',
    year: null,
    month: null,
    name: null,
    targetEmployeeNumber: '',
    holidays: new Set(Array.isArray(window.SHIFTERSYNC_HOLIDAYS) ? window.SHIFTERSYNC_HOLIDAYS : []),
    entriesPerDay: {},
    capacityEnabled: false,
    requiredCapacity: 0,
    editable: true,
    selectedOptions: {},
    modalDay: null,
    modalEntryId: null,
    siteContext: null,
    siteBranches: []
  };

  const employeeSearchCache = new Map();
  const employeeSearchTimers = new WeakMap();
  const siteSearchCache = new Map();
  const siteSearchTimers = new WeakMap();

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function insertTextAtCursor(element, text) {
    if (!element) {
      return;
    }
    const value = String(element.value || '');
    const start = Number.isInteger(element.selectionStart) ? element.selectionStart : value.length;
    const end = Number.isInteger(element.selectionEnd) ? element.selectionEnd : value.length;
    element.value = `${value.slice(0, start)}${text}${value.slice(end)}`;
    const nextCursor = start + text.length;
    if (typeof element.setSelectionRange === 'function') {
      element.setSelectionRange(nextCursor, nextCursor);
    }
    element.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function makeEntryId() {
    return `entry-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  }

  function parseEntryValue(value) {
    const text = String(value || '').trim();
    const match = text.match(/^!([^!]+)!(.+)$/);
    if (!match) {
      return { optionKey: null, name: text };
    }
    return { optionKey: match[1], name: match[2] };
  }

  function formatEntryValue(optionKey, name) {
    const safeName = String(name || '').trim();
    if (!safeName) {
      return '';
    }
    return optionKey ? `!${optionKey}!${safeName}` : safeName;
  }

  function normalizeSiteBranchRowId(value) {
    const text = String(value || '').trim();
    if (!text || !/^\d+$/.test(text)) {
      return '';
    }
    return parseInt(text, 10) > 0 ? text : '';
  }

  function normalizeSiteRowId(value) {
    const text = String(value || '').trim();
    if (!text || !/^\d+$/.test(text)) {
      return '';
    }
    return parseInt(text, 10) > 0 ? text : '';
  }

  function normalizeSiteBranch(branch) {
    if (!branch || typeof branch !== 'object') {
      return null;
    }
    const id = normalizeSiteBranchRowId(branch.id || branch.site_branch_row_id || branch.siteBranchRowId || '');
    const siteBranch = String(branch.site_branch || branch.siteBranch || '').trim();
    if (!id && !siteBranch) {
      return null;
    }
    return {
      id,
      site_branch: siteBranch,
      cloudshift_option_key: String(branch.cloudshift_option_key || branch.cloudshiftOptionKey || '').trim().toUpperCase(),
      option_label: String(branch.option_label || branch.optionLabel || '').trim(),
      is_active: branch.is_active !== false
    };
  }

  function currentSiteBranches() {
    if (!Array.isArray(state.siteBranches)) {
      return [];
    }
    return state.siteBranches.map((branch) => normalizeSiteBranch(branch)).filter(Boolean);
  }

  function isLinkedSceneSiteContext() {
    const siteContext = state.siteContext && typeof state.siteContext === 'object' ? state.siteContext : null;
    return isSceneMode() && !!(siteContext && siteContext.is_linked);
  }

  function hasActiveSiteBranches() {
    return currentSiteBranches().some((branch) => branch.is_active !== false);
  }

  function findSiteBranchByRowId(siteBranchRowId) {
    const normalizedId = normalizeSiteBranchRowId(siteBranchRowId);
    if (!normalizedId) {
      return null;
    }
    return currentSiteBranches().find((branch) => branch.id === normalizedId) || null;
  }

  function siteBranchOptionLabel(branch) {
    if (!branch) {
      return '';
    }
    return String(branch.option_label || allOptionMappings[branch.cloudshift_option_key] || branch.cloudshift_option_key || '').trim();
  }

  function siteBranchChoiceLabel(branch) {
    if (!branch) {
      return '';
    }
    const branchCode = String(branch.site_branch || '').trim();
    const optionLabel = siteBranchOptionLabel(branch);
    return optionLabel ? `${branchCode} / ${optionLabel}` : branchCode;
  }

  function entryBranchState(entry) {
    const normalized = normalizeEntry(entry);
    if (!normalized) {
      return { site_branch_row_id: '', site_branch: '', label: '', is_missing: false };
    }

    const storedBranchRowId = normalizeSiteBranchRowId(normalized.site_branch_row_id || '');
    const storedBranch = String(normalized.site_branch || '').trim();
    const liveBranch = findSiteBranchByRowId(storedBranchRowId);
    if (liveBranch) {
      return {
        site_branch_row_id: liveBranch.id,
        site_branch: liveBranch.site_branch,
        label: siteBranchChoiceLabel(liveBranch),
        is_missing: false
      };
    }
    if (storedBranchRowId || storedBranch) {
      return {
        site_branch_row_id: storedBranchRowId,
        site_branch: storedBranch,
        label: storedBranch ? `${storedBranch} / 現在は無効` : '現在は無効',
        is_missing: true
      };
    }
    return { site_branch_row_id: '', site_branch: '', label: '', is_missing: false };
  }

  function entryBranchIssue(entry) {
    if (!isLinkedSceneSiteContext()) {
      return { code: '', label: '', tone: '' };
    }
    const branchState = entryBranchState(entry);
    if (branchState.is_missing) {
      return { code: 'missing', label: '旧枝参照', tone: 'danger' };
    }
    return { code: '', label: '', tone: '' };
  }

  function summarizeDayBranchIssues(day) {
    if (!isLinkedSceneSiteContext()) {
      return { text: '', tone: '', missingCount: 0, unassignedCount: 0, noBranches: false };
    }
    const entries = getDayEntries(day).filter((entry) => String(entry && entry.value ? entry.value : '').trim());
    if (!entries.length) {
      return { text: '', tone: '', missingCount: 0, unassignedCount: 0, noBranches: false };
    }
    let missingCount = 0;
    entries.forEach((entry) => {
      const issue = entryBranchIssue(entry);
      if (issue.code === 'missing') {
        missingCount += 1;
      }
    });
    const parts = [];
    if (missingCount) {
      parts.push(`旧枝参照 ${missingCount}件`);
    }
    return {
      text: parts.join(' / '),
      tone: missingCount ? 'danger' : '',
      missingCount,
      unassignedCount: 0,
      noBranches: false
    };
  }

  function siteBranchCandidatesForOption(optionKey) {
    const branches = currentSiteBranches().filter((branch) => branch.is_active !== false);
    if (!branches.length) {
      return [];
    }
    const normalizedOptionKey = String(optionKey || '').trim().toUpperCase();
    if (!normalizedOptionKey) {
      return branches;
    }
    const matched = branches.filter((branch) => branch.cloudshift_option_key === normalizedOptionKey);
    return matched.length ? matched : branches;
  }

  function autoBranchFieldsForOption(optionKey) {
    const candidates = siteBranchCandidatesForOption(optionKey);
    if (candidates.length !== 1) {
      return { site_branch_row_id: '', site_branch: '' };
    }
    return {
      site_branch_row_id: candidates[0].id,
      site_branch: candidates[0].site_branch
    };
  }

  function normalizeEntry(entry) {
    if (!entry) {
      return null;
    }
    if (typeof entry === 'string') {
      const value = entry.trim();
      if (!value) {
        return null;
      }
      return {
        id: makeEntryId(),
        value,
        comment: '',
        employee_number: '',
        site_row_id: '',
        site_id: '',
        site_name: '',
        site_branch_row_id: '',
        site_branch: '',
        sync_source_type: '',
        sync_source_project_id: '',
        sync_source_project_title: '',
        sync_source_month_key: '',
        sync_source_day: '',
        sync_source_entry_id: ''
      };
    }

    const value = String(entry.value || '').trim();
    if (!value) {
      return null;
    }
    return {
      id: String(entry.id || makeEntryId()),
      value,
      comment: String(entry.comment || '').trim(),
      employee_number: String(entry.employee_number || entry.employeeNumber || '').trim(),
      site_row_id: normalizeSiteRowId(entry.site_row_id || entry.siteRowId || ''),
      site_id: String(entry.site_id || entry.siteId || '').trim(),
      site_name: String(entry.site_name || entry.siteName || '').trim(),
      site_branch_row_id: normalizeSiteBranchRowId(entry.site_branch_row_id || entry.siteBranchRowId || ''),
      site_branch: String(entry.site_branch || entry.siteBranch || '').trim(),
      sync_source_type: String(entry.sync_source_type || entry.syncSourceType || '').trim(),
      sync_source_project_id: String(entry.sync_source_project_id || entry.syncSourceProjectId || '').trim(),
      sync_source_project_title: String(entry.sync_source_project_title || entry.syncSourceProjectTitle || '').trim(),
      sync_source_month_key: String(entry.sync_source_month_key || entry.syncSourceMonthKey || '').trim(),
      sync_source_day: String(entry.sync_source_day || entry.syncSourceDay || '').trim(),
      sync_source_entry_id: String(entry.sync_source_entry_id || entry.syncSourceEntryId || '').trim()
    };
  }

  function cloneEntry(entry, withNewId) {
    const normalized = normalizeEntry(entry);
    if (!normalized) {
      return null;
    }
    return {
      id: withNewId ? makeEntryId() : normalized.id,
      value: normalized.value,
      comment: normalized.comment,
      employee_number: normalized.employee_number,
      site_row_id: normalized.site_row_id,
      site_id: normalized.site_id,
      site_name: normalized.site_name,
      site_branch_row_id: normalized.site_branch_row_id,
      site_branch: normalized.site_branch,
      sync_source_type: normalized.sync_source_type,
      sync_source_project_id: normalized.sync_source_project_id,
      sync_source_project_title: normalized.sync_source_project_title,
      sync_source_month_key: normalized.sync_source_month_key,
      sync_source_day: normalized.sync_source_day,
      sync_source_entry_id: normalized.sync_source_entry_id
    };
  }

  function isSyncedEntry(entry) {
    const normalized = normalizeEntry(entry);
    if (!normalized) {
      return false;
    }
    return shiftSyncSourceTypes.includes(String(normalized.sync_source_type || '').trim());
  }

  function canOpenSyncedSourceEntry(entry) {
    const normalized = normalizeEntry(entry);
    if (!normalized || !isSyncedEntry(normalized)) {
      return false;
    }
    return typeof window.CLOUDSHIFT_CAN_OPEN_SYNC_SOURCE === 'function'
      ? !!window.CLOUDSHIFT_CAN_OPEN_SYNC_SOURCE(normalized)
      : false;
  }

  function normalizeDayEntries(rawEntries) {
    if (!Array.isArray(rawEntries)) {
      return [];
    }
    return rawEntries.map((entry) => normalizeEntry(entry)).filter(Boolean);
  }

  function getDayEntries(day) {
    return state.entriesPerDay[String(day)] || [];
  }

  function setDayEntries(day, entries) {
    state.entriesPerDay[String(day)] = normalizeDayEntries(entries);
  }

  function getSelectedOptionsForDay(day) {
    return state.selectedOptions[String(day)] || [];
  }

  function setSelectedOptionsForDay(day, options) {
    state.selectedOptions[String(day)] = Array.isArray(options) ? options.slice(0, 1) : [];
  }

  function clearSelectedOptionsForDay(day) {
    state.selectedOptions[String(day)] = [];
  }

  function isPersonMode() {
    return state.mode === 'person';
  }

  function isSceneMode() {
    return state.mode === 'scene';
  }

  function isEntryEmployeeSearchEnabled() {
    return isSceneMode();
  }

  function isEntrySiteSearchEnabled() {
    return isPersonMode();
  }

  function pad2(value) {
    return String(value).padStart(2, '0');
  }

  function calendarDateKey(year, month, day) {
    return `${String(year).padStart(4, '0')}-${pad2(month)}-${pad2(day)}`;
  }

  function getDayToneClass(year, month, day) {
    const dateKey = calendarDateKey(year, month, day);
    if (state.holidays.has(dateKey)) {
      return 'is-holiday';
    }
    const weekday = new Date(year, month - 1, day).getDay();
    if (weekday === 0) {
      return 'is-sunday';
    }
    if (weekday === 6) {
      return 'is-saturday';
    }
    return '';
  }

  function getOptionSectionsForMode(mode) {
    const sections = [
      { title: '\u6642\u9593\u5e2f', optionKeys: shiftTimeOptionKeys }
    ];
    if (mode === 'person') {
      sections.push({ title: '\u4f11\u6687\u7a2e\u5225', optionKeys: leaveOptionKeys });
    }
    sections.push(
      { title: '\u8eca\u4e21\u30bf\u30a4\u30d7', optionKeys: vehicleOptionKeys },
      { title: '\u8eca\u756a\u53f7', optionKeys: vehicleNumberOptionKeys }
    );
    return sections;
  }

  function getSelectableOptionKeysForMode(mode) {
    const keys = shiftTimeOptionKeys.slice();
    if (mode === 'person') {
      keys.push(...leaveOptionKeys);
    }
    keys.push(...vehicleOptionKeys, ...vehicleNumberOptionKeys);
    return keys;
  }

  function normalizeEmployeeCandidate(candidate) {
    if (!candidate || typeof candidate !== 'object') {
      return null;
    }
    const employeeNumber = String(candidate.employee_number || candidate.employeeNumber || '').trim();
    const employeeName = String(candidate.employee_name || candidate.employeeName || '').trim();
    if (!employeeNumber || !employeeName) {
      return null;
    }
    return {
      employee_number: employeeNumber,
      employee_name: employeeName,
      office_name: String(candidate.office_name || candidate.officeName || '').trim(),
      job_title: String(candidate.job_title || candidate.jobTitle || '').trim()
    };
  }

  function normalizeSiteCandidate(candidate) {
    if (!candidate || typeof candidate !== 'object') {
      return null;
    }
    const siteRowId = normalizeSiteRowId(candidate.id || candidate.site_row_id || candidate.siteRowId || '');
    const siteId = String(candidate.site_id || candidate.siteId || '').trim();
    const siteName = String(candidate.site_name || candidate.siteName || '').trim();
    if (!siteRowId && !siteId) {
      return null;
    }
    return {
      site_row_id: siteRowId,
      site_id: siteId,
      site_name: siteName,
      active_branch_count: parseInt(candidate.active_branch_count || candidate.activeBranchCount || '0', 10) || 0
    };
  }

  function getSelectedSiteDataForInput($input) {
    if (!$input || !$input.length) {
      return { site_row_id: '', site_id: '', site_name: '' };
    }
    const selectedRowId = String($input.attr('data-site-row-id') || '').trim();
    const selectedSiteId = String($input.attr('data-site-id') || '').trim();
    const selectedSiteName = String($input.attr('data-selected-site-name') || '').trim();
    const currentName = String($input.val() || '').trim();
    if (!currentName || !selectedSiteName || currentName !== selectedSiteName) {
      return { site_row_id: '', site_id: '', site_name: '' };
    }
    return {
      site_row_id: normalizeSiteRowId(selectedRowId),
      site_id: selectedSiteId,
      site_name: selectedSiteName
    };
  }

  function getEmployeeSearchPanelForInput($input) {
    if (!$input || !$input.length) {
      return $();
    }

    const kind = String($input.attr('data-search-kind') || '');
    if (kind === 'modal') {
      return $('#ss-entry-modal-candidate-panel');
    }

    const day = String($input.attr('data-day') || '');
    if (!day) {
      return $();
    }
    return $(`.ss-candidate-panel[data-search-kind='day'][data-day='${day}']`);
  }

  function getEmployeeSelectionNoteForInput($input) {
    if (!$input || !$input.length) {
      return $();
    }

    const kind = String($input.attr('data-search-kind') || '');
    if (kind === 'modal') {
      return $('#ss-entry-modal-selected-note');
    }

    const day = String($input.attr('data-day') || '');
    if (!day) {
      return $();
    }
    return $(`.ss-selected-note[data-search-kind='day'][data-day='${day}']`);
  }

  function clearEmployeeSelectionForInput($input) {
    if (!$input || !$input.length) {
      return;
    }
    $input.removeAttr('data-employee-number');
    $input.removeAttr('data-selected-employee-name');
    $input.attr('data-search-token', `cleared-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
    const kind = String($input.attr('data-search-kind') || '');
    if (kind === 'modal') {
      const employeeField = $('#ss-entry-modal-employee-number');
      if (employeeField.length) {
        employeeField.val('');
      }
    }
    const note = getEmployeeSelectionNoteForInput($input);
    if (note.length) {
      note.text('').addClass('ss-hidden');
    }
  }

  function setEmployeeSelectionForInput($input, candidate) {
    const normalized = normalizeEmployeeCandidate(candidate);
    if (!$input || !$input.length || !normalized) {
      return;
    }

    $input.val(normalized.employee_name);
    $input.attr('data-employee-number', normalized.employee_number);
    $input.attr('data-selected-employee-name', normalized.employee_name);
    $input.attr('data-search-token', `selected-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);

    const kind = String($input.attr('data-search-kind') || '');
    if (kind === 'modal') {
      const employeeField = $('#ss-entry-modal-employee-number');
      if (employeeField.length) {
        employeeField.val(normalized.employee_number);
      }
    }

    const note = getEmployeeSelectionNoteForInput($input);
    if (note.length) {
      note.text(`\u9078\u629e\u4e2d: ${normalized.employee_number}`).removeClass('ss-hidden');
    }

    const panel = getEmployeeSearchPanelForInput($input);
    if (panel.length) {
      panel.empty().addClass('ss-hidden');
    }
  }

  function buildEmployeeCandidateText(candidate) {
    const normalized = normalizeEmployeeCandidate(candidate);
    if (!normalized) {
      return '';
    }

    return [normalized.employee_name, normalized.employee_number].filter(Boolean).join(' / ');
  }

  function renderEmployeeSearchResults($panel, $input, candidates, emptyMessage) {
    if (!$panel || !$panel.length || !$input || !$input.length) {
      return;
    }

    $panel.empty();

    const list = $('<div>').css({
      display: 'grid',
      gap: '6px'
    });

    const normalizedCandidates = Array.isArray(candidates)
      ? candidates.map((item) => normalizeEmployeeCandidate(item)).filter(Boolean)
      : [];

    if (!normalizedCandidates.length) {
      list.append(
        $('<div>')
          .css({
            color: '#6b7280',
            fontSize: '12px',
            padding: '4px 2px'
          })
          .text(emptyMessage || '\u5019\u88dc\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093')
      );
    } else {
      const panelKind = String($panel.attr('data-search-kind') || '');
      const panelDay = String($panel.attr('data-day') || '');
      normalizedCandidates.slice(0, 8).forEach((candidate) => {
        const button = $('<button>')
          .attr('type', 'button')
          .addClass('ss-employee-candidate-btn')
          .attr('data-search-kind', panelKind)
          .attr('data-day', panelDay)
          .attr('data-employee-number', candidate.employee_number)
          .attr('data-employee-name', candidate.employee_name)
          .css({
            border: '1px solid #cfe1f6',
            borderRadius: '10px',
            background: '#fff',
            color: '#18324c',
            cursor: 'pointer',
            padding: '8px 10px',
            textAlign: 'left',
            width: '100%'
          })
          .text(buildEmployeeCandidateText(candidate))
          .on('mousedown', function(event) {
            event.preventDefault();
          })
          .on('click', function() {
            setEmployeeSelectionForInput($input, candidate);
          });
        list.append(button);
      });
    }

    $panel.append(list).removeClass('ss-hidden');
  }

  async function fetchEmployeeCandidates(query) {
    const key = String(query || '').trim();
    if (!key) {
      return [];
    }

    if (employeeSearchCache.has(key)) {
      return employeeSearchCache.get(key);
    }

    const response = await fetch(`/tools/pluslist/api/search_employee?q=${encodeURIComponent(key)}`, {
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json'
      }
    });

    if (!response.ok) {
      throw new Error('\u793e\u54e1\u5019\u88dc\u306e\u691c\u7d22\u306b\u5931\u6557\u3057\u307e\u3057\u305f');
    }

    const payload = await response.json();
    const candidates = Array.isArray(payload)
      ? payload
      : Array.isArray(payload.results)
        ? payload.results
        : Array.isArray(payload.items)
          ? payload.items
          : Array.isArray(payload.employees)
            ? payload.employees
            : [];
    employeeSearchCache.set(key, candidates);
    return candidates;
  }

  function scheduleEmployeeSearchForInput($input) {
    if (!isEntryEmployeeSearchEnabled() || !$input || !$input.length) {
      return;
    }

    const query = String($input.val() || '').trim();
    const panel = getEmployeeSearchPanelForInput($input);
    if (!query) {
      clearEmployeeSelectionForInput($input);
      if (panel.length) {
        panel.empty().addClass('ss-hidden');
      }
      return;
    }

    const currentSelectedName = String($input.attr('data-selected-employee-name') || '');
    if (currentSelectedName && currentSelectedName !== query) {
      clearEmployeeSelectionForInput($input);
    }

    const element = $input[0];
    const previousTimer = employeeSearchTimers.get(element);
    if (previousTimer) {
      window.clearTimeout(previousTimer);
    }

    const timer = window.setTimeout(async () => {
      const latestQuery = String($input.val() || '').trim();
      if (!latestQuery) {
        if (panel.length) {
          panel.empty().addClass('ss-hidden');
        }
        return;
      }

      const searchToken = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      $input.attr('data-search-token', searchToken);
      try {
        const candidates = await fetchEmployeeCandidates(latestQuery);
        if ($input.attr('data-search-token') !== searchToken) {
          return;
        }
        if (String($input.val() || '').trim() !== latestQuery) {
          return;
        }
        renderEmployeeSearchResults(panel, $input, candidates, '\u5019\u88dc\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093');
      } catch (_) {
        if (panel.length) {
          panel.empty().addClass('ss-hidden');
        }
      }
    }, 220);
    employeeSearchTimers.set(element, timer);
  }

  function scheduleEmployeeSearchForModal() {
    const $input = $('#ss-entry-modal-name');
    if (!isEntryEmployeeSearchEnabled() || !$input.length) {
      return;
    }

    const query = String($input.val() || '').trim();
    const panel = $('#ss-entry-modal-candidate-panel');
    if (!query) {
      clearEmployeeSelectionForInput($input);
      if (panel.length) {
        panel.empty().addClass('ss-hidden');
      }
      return;
    }

    const currentSelectedName = String($input.attr('data-selected-employee-name') || '');
    if (currentSelectedName && currentSelectedName !== query) {
      clearEmployeeSelectionForInput($input);
    }

    const element = $input[0];
    const previousTimer = employeeSearchTimers.get(element);
    if (previousTimer) {
      window.clearTimeout(previousTimer);
    }

    const timer = window.setTimeout(async () => {
      const latestQuery = String($input.val() || '').trim();
      if (!latestQuery) {
        if (panel.length) {
          panel.empty().addClass('ss-hidden');
        }
        return;
      }

      const searchToken = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      $input.attr('data-search-token', searchToken);
      try {
        const candidates = await fetchEmployeeCandidates(latestQuery);
        if ($input.attr('data-search-token') !== searchToken) {
          return;
        }
        if (String($input.val() || '').trim() !== latestQuery) {
          return;
        }
        renderEmployeeSearchResults(panel, $input, candidates, '\u5019\u88dc\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093');
      } catch (_) {
        if (panel.length) {
          panel.empty().addClass('ss-hidden');
        }
      }
    }, 220);
    employeeSearchTimers.set(element, timer);
  }

  function clearSiteSelectionForInput($input) {
    if (!$input || !$input.length) {
      return;
    }
    $input.removeAttr('data-site-row-id');
    $input.removeAttr('data-site-id');
    $input.removeAttr('data-selected-site-name');
    $input.attr('data-search-token', `cleared-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
    const note = getEmployeeSelectionNoteForInput($input);
    if (note.length) {
      note.text('').addClass('ss-hidden');
    }
  }

  function setSiteSelectionForInput($input, candidate) {
    const normalized = normalizeSiteCandidate(candidate);
    if (!$input || !$input.length || !normalized) {
      return;
    }
    $input.val(normalized.site_name);
    $input.attr('data-site-row-id', normalized.site_row_id);
    $input.attr('data-site-id', normalized.site_id);
    $input.attr('data-selected-site-name', normalized.site_name);
    $input.attr('data-search-token', `selected-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
    const note = getEmployeeSelectionNoteForInput($input);
    if (note.length) {
      const label = [normalized.site_id, normalized.site_name].filter(Boolean).join(' / ');
      note.text(label ? `選択中: ${label}` : '').toggleClass('ss-hidden', !label);
    }
    const panel = getEmployeeSearchPanelForInput($input);
    if (panel.length) {
      panel.empty().addClass('ss-hidden');
    }
  }

  function renderSiteSearchResults($panel, $input, candidates, emptyMessage) {
    if (!$panel || !$panel.length || !$input || !$input.length) {
      return;
    }
    $panel.empty();
    const list = $('<div>').css({
      display: 'grid',
      gap: '6px'
    });
    const normalizedCandidates = Array.isArray(candidates)
      ? candidates.map((item) => normalizeSiteCandidate(item)).filter(Boolean)
      : [];
    if (!normalizedCandidates.length) {
      list.append(
        $('<div>')
          .css({
            color: '#6b7280',
            fontSize: '12px',
            padding: '4px 2px'
          })
          .text(emptyMessage || '候補が見つかりません')
      );
    } else {
      const panelKind = String($panel.attr('data-search-kind') || '');
      const panelDay = String($panel.attr('data-day') || '');
      normalizedCandidates.slice(0, 8).forEach((candidate) => {
        const button = $('<button>')
          .attr('type', 'button')
          .addClass('ss-site-candidate-btn')
          .attr('data-search-kind', panelKind)
          .attr('data-day', panelDay)
          .attr('data-site-row-id', candidate.site_row_id)
          .attr('data-site-id', candidate.site_id)
          .attr('data-site-name', candidate.site_name)
          .css({
            border: '1px solid #cfe1f6',
            borderRadius: '10px',
            background: '#fff',
            color: '#18324c',
            cursor: 'pointer',
            padding: '8px 10px',
            textAlign: 'left',
            width: '100%'
          })
          .text([candidate.site_id, candidate.site_name].filter(Boolean).join(' / '))
          .on('mousedown', function(event) {
            event.preventDefault();
          })
          .on('click', function() {
            setSiteSelectionForInput($input, candidate);
          });
        list.append(button);
      });
    }
    $panel.append(list).removeClass('ss-hidden');
  }

  async function fetchSiteCandidates(query) {
    const key = String(query || '').trim();
    if (!key) {
      return [];
    }
    if (siteSearchCache.has(key)) {
      return siteSearchCache.get(key);
    }
    const response = await fetch(`/tools/siteplus/api/cloudshift/sites?q=${encodeURIComponent(key)}`, {
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json'
      }
    });
    if (!response.ok) {
      throw new Error('現場候補の検索に失敗しました');
    }
    const payload = await response.json();
    const candidates = Array.isArray(payload.sites) ? payload.sites : [];
    siteSearchCache.set(key, candidates);
    return candidates;
  }

  function scheduleSiteSearchForInput($input) {
    if (!isEntrySiteSearchEnabled() || !$input || !$input.length) {
      return;
    }
    const query = String($input.val() || '').trim();
    const panel = getEmployeeSearchPanelForInput($input);
    if (!query) {
      clearSiteSelectionForInput($input);
      if (panel.length) {
        panel.empty().addClass('ss-hidden');
      }
      return;
    }
    const currentSelectedName = String($input.attr('data-selected-site-name') || '');
    if (currentSelectedName && currentSelectedName !== query) {
      clearSiteSelectionForInput($input);
    }
    const element = $input[0];
    const previousTimer = siteSearchTimers.get(element);
    if (previousTimer) {
      window.clearTimeout(previousTimer);
    }
    const timer = window.setTimeout(async () => {
      const latestQuery = String($input.val() || '').trim();
      if (!latestQuery) {
        if (panel.length) {
          panel.empty().addClass('ss-hidden');
        }
        return;
      }
      const searchToken = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      $input.attr('data-search-token', searchToken);
      try {
        const candidates = await fetchSiteCandidates(latestQuery);
        if ($input.attr('data-search-token') !== searchToken) {
          return;
        }
        if (String($input.val() || '').trim() !== latestQuery) {
          return;
        }
        renderSiteSearchResults(panel, $input, candidates, '候補が見つかりません');
      } catch (_) {
        if (panel.length) {
          panel.empty().addClass('ss-hidden');
        }
      }
    }, 220);
    siteSearchTimers.set(element, timer);
  }

  function scheduleSiteSearchForModal() {
    const $input = $('#ss-entry-modal-name');
    if (!isEntrySiteSearchEnabled() || !$input.length) {
      return;
    }
    const query = String($input.val() || '').trim();
    const panel = $('#ss-entry-modal-candidate-panel');
    if (!query) {
      clearSiteSelectionForInput($input);
      if (panel.length) {
        panel.empty().addClass('ss-hidden');
      }
      return;
    }
    const currentSelectedName = String($input.attr('data-selected-site-name') || '');
    if (currentSelectedName && currentSelectedName !== query) {
      clearSiteSelectionForInput($input);
    }
    const element = $input[0];
    const previousTimer = siteSearchTimers.get(element);
    if (previousTimer) {
      window.clearTimeout(previousTimer);
    }
    const timer = window.setTimeout(async () => {
      const latestQuery = String($input.val() || '').trim();
      if (!latestQuery) {
        if (panel.length) {
          panel.empty().addClass('ss-hidden');
        }
        return;
      }
      const searchToken = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      $input.attr('data-search-token', searchToken);
      try {
        const candidates = await fetchSiteCandidates(latestQuery);
        if ($input.attr('data-search-token') !== searchToken) {
          return;
        }
        if (String($input.val() || '').trim() !== latestQuery) {
          return;
        }
        renderSiteSearchResults(panel, $input, candidates, '候補が見つかりません');
      } catch (_) {
        if (panel.length) {
          panel.empty().addClass('ss-hidden');
        }
      }
    }, 220);
    siteSearchTimers.set(element, timer);
  }

  function getEntryDisplayParts(entry) {
    const normalized = normalizeEntry(entry);
    if (!normalized) {
      return {
        title: '',
        comment: '',
        employee_number: '',
        branch_label: '',
        branch_missing: false,
        branch_issue_label: '',
        branch_issue_tone: ''
      };
    }
    const parsed = parseEntryValue(normalized.value);
    const branchState = entryBranchState(normalized);
    const branchIssue = entryBranchIssue(normalized);
    const baseTitle = parsed.optionKey ? `${parsed.name} ${allOptionMappings[parsed.optionKey] || parsed.optionKey}` : parsed.name;
    return {
      title: branchState.site_branch ? `${baseTitle} / 枝${branchState.site_branch}` : baseTitle,
      comment: normalized.comment || '',
      employee_number: normalized.employee_number || '',
      branch_label: branchState.label,
      branch_missing: branchState.is_missing,
      branch_issue_label: branchIssue.label,
      branch_issue_tone: branchIssue.tone
    };
  }

  function getCommentPreview(comment) {
    const text = String(comment || '').trim().replace(/\s*\r?\n\s*/g, ' / ');
    if (!text) {
      return '';
    }
    return text.length > 24 ? `${text.slice(0, 24)}...` : text;
  }

  function getSelectedEmployeeNumberForInput($input) {
    if (!$input || !$input.length) {
      return '';
    }

    const selectedNumber = String($input.attr('data-employee-number') || '').trim();
    const selectedName = String($input.attr('data-selected-employee-name') || '').trim();
    const currentName = String($input.val() || '').trim();
    if (!selectedNumber || !selectedName || !currentName) {
      return '';
    }
    return currentName === selectedName ? selectedNumber : '';
  }

  function csvEscape(value) {
    const text = String(value ?? '');
    if (/[",\n\r]/.test(text)) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  }

  function ensureModalScaffold() {
    if (document.getElementById('ss-day-detail-modal')) {
      return;
    }

    const wrapper = document.createElement('div');
    wrapper.innerHTML = `
      <div id="ss-day-detail-modal" class="ss-modal-shell ss-hidden">
        <div class="ss-modal-backdrop" data-close-modal="day"></div>
        <div class="ss-modal-panel">
          <div class="ss-modal-header">
            <div>
              <h3 id="ss-day-detail-title" class="ss-modal-title">\u65e5\u5225\u8a73\u7d30</h3>
              <p id="ss-day-detail-subtitle" class="ss-modal-subtitle"></p>
            </div>
            <button type="button" class="ss-modal-close" data-close-modal="day">\u9589\u3058\u308b</button>
          </div>
          <div id="ss-day-detail-body" class="ss-modal-body"></div>
        </div>
      </div>
      <div id="ss-entry-modal" class="ss-modal-shell ss-hidden">
        <div class="ss-modal-backdrop" data-close-modal="entry"></div>
        <div class="ss-modal-panel ss-modal-panel-narrow">
          <div class="ss-modal-header">
            <div>
              <h3 id="ss-entry-modal-title" class="ss-modal-title">\u30a8\u30f3\u30c8\u30ea\u8a73\u7d30</h3>
              <p id="ss-entry-modal-subtitle" class="ss-modal-subtitle"></p>
            </div>
            <button type="button" class="ss-modal-close" data-close-modal="entry">\u9589\u3058\u308b</button>
          </div>
          <div id="ss-entry-modal-body" class="ss-modal-body"></div>
        </div>
      </div>
    `;
    document.body.appendChild(wrapper);
  }

  function closeModal(kind) {
    const target = document.getElementById(kind === 'day' ? 'ss-day-detail-modal' : 'ss-entry-modal');
    if (target) {
      target.classList.add('ss-hidden');
    }
  }

  function bindModalEvents() {
    $(document).off('click', '[data-close-modal]').on('click', '[data-close-modal]', function() {
      closeModal($(this).attr('data-close-modal'));
    });
  }

  function updateCapacityWarning(day) {
    const box = $(`.day-box[data-day='${day}']`);
    if (!box.length) {
      return;
    }
    if (!state.capacityEnabled || state.requiredCapacity <= 0) {
      box.removeClass('capacity-warning');
      return;
    }
    box.toggleClass('capacity-warning', getDayEntries(day).length < state.requiredCapacity);
  }

  function updateAllCapacityWarnings() {
    Object.keys(state.entriesPerDay).forEach((day) => updateCapacityWarning(day));
  }

  function updateBranchWarning(day) {
    const box = $(`.day-box[data-day='${day}']`);
    if (!box.length) {
      return;
    }
    const note = box.find('.day-branch-warning');
    const summary = summarizeDayBranchIssues(day);
    box.removeClass('branch-warning branch-danger branch-unavailable');
    if (!note.length) {
      return;
    }
    if (!summary.text) {
      note.text('').addClass('ss-hidden').removeClass('is-warning is-danger');
      return;
    }
    box.addClass(summary.tone === 'danger' ? 'branch-danger' : 'branch-warning');
    if (summary.noBranches) {
      box.addClass('branch-unavailable');
    }
    note
      .text(summary.text)
      .removeClass('ss-hidden')
      .toggleClass('is-warning', summary.tone !== 'danger')
      .toggleClass('is-danger', summary.tone === 'danger');
  }

  function updateAllBranchWarnings() {
    Object.keys(state.entriesPerDay).forEach((day) => updateBranchWarning(day));
  }

  function replaceEntriesPerDay(entriesPerDay) {
    const nextEntries = {};
    const source = entriesPerDay && typeof entriesPerDay === 'object' ? entriesPerDay : {};
    const daysInMonth = state.year && state.month ? new Date(state.year, state.month, 0).getDate() : 0;

    if (daysInMonth > 0) {
      for (let day = 1; day <= daysInMonth; day += 1) {
        const key = String(day);
        nextEntries[key] = normalizeDayEntries(source[key] || source[day] || []);
      }
    } else {
      Object.keys(source).forEach((day) => {
        nextEntries[String(day)] = normalizeDayEntries(source[day]);
      });
    }

    state.entriesPerDay = nextEntries;
    Object.keys(nextEntries).forEach((day) => {
      updateEntryDisplay(day);
      updateCapacityWarning(day);
      updateBranchWarning(day);
    });
  }

  function updateEntryDisplay(day) {
    const container = $(`.entry-list-container[data-day='${day}']`);
    container.empty();

    const entries = getDayEntries(day);
    if (!entries.length) {
      container.append('<div class="entry-empty-note">\u307e\u3060\u767b\u9332\u3055\u308c\u3066\u3044\u307e\u305b\u3093</div>');
      updateBranchWarning(day);
      return;
    }

    entries.forEach((entry) => {
      const parts = getEntryDisplayParts(entry);
      const syncedEntry = isSyncedEntry(entry);
      const item = $('<div>')
        .addClass('entry-item')
        .toggleClass('has-branch-warning', parts.branch_issue_tone === 'warning')
        .toggleClass('has-branch-danger', parts.branch_issue_tone === 'danger')
        .attr('data-day', day)
        .attr('data-entry-id', entry.id)
        .append(
          $('<div>')
            .addClass('entry-item-main')
            .append(
              $('<div>').addClass('entry-item-title').text(parts.title),
              parts.branch_issue_label
                ? $('<div>').addClass(`entry-item-status is-${parts.branch_issue_tone || 'warning'}`).text(parts.branch_issue_label)
                : null,
              $('<div>').addClass(`entry-item-comment${parts.comment ? '' : ' is-empty'}`).text(parts.comment ? getCommentPreview(parts.comment) : '\u30b3\u30e1\u30f3\u30c8\u306a\u3057')
            )
        );

      if (state.editable && !syncedEntry) {
        item.append(
          $('<button>')
            .attr('type', 'button')
            .addClass('entry-item-delete')
            .text('\u524a\u9664')
        );
      }

      container.append(item);
    });
    updateBranchWarning(day);
  }

  function createDayBox(day, maxDay) {
    const dayBox = $('<div>').addClass('day-box').attr('data-day', day);
    const toneClass = getDayToneClass(state.year, state.month, day);
    if (toneClass) {
      dayBox.addClass(toneClass);
    }
    const header = $('<button>')
      .attr('type', 'button')
      .addClass('date-label day-detail-trigger')
      .attr('data-day', day)
      .text(`${day}\u65e5`);
    dayBox.append(header);
    dayBox.append(
      $('<div>')
        .addClass('day-branch-warning ss-hidden')
    );

    const entryContainer = $('<div>')
      .addClass('entry-list-container')
      .attr('data-day', day);
    dayBox.append(entryContainer);

    if (!state.editable) {
      dayBox.append(
        $('<div>')
          .addClass('day-box-actions')
          .append(
            $('<button>')
              .attr('type', 'button')
              .addClass('day-detail-trigger muted-link')
              .attr('data-day', day)
              .text('\u8a73\u7d30\u3092\u898b\u308b')
          )
      );
      return dayBox;
    }

    const inputGroup = $('<div>').addClass('input-group');
    const inputRow = $('<div>').addClass('input-row');
    const nameInput = $('<input>')
      .attr('type', 'text')
      .addClass('entry-input')
      .attr('placeholder', state.mode === 'scene' ? '\u4eba\u7269\u540d' : '\u73fe\u5834\u540d')
      .attr('data-day', day);
    if (isEntryEmployeeSearchEnabled()) {
      nameInput
        .addClass('ss-employee-search-input')
        .attr('data-search-kind', 'day');
    } else if (isEntrySiteSearchEnabled()) {
      nameInput
        .addClass('ss-site-search-input')
        .attr('data-search-kind', 'day');
    }
    const addBtn = $('<button>')
      .attr('type', 'button')
      .addClass('add-entry-btn')
      .attr('data-day', day)
      .text('\u8ffd\u52a0');
    inputRow.append(nameInput, addBtn);

    const commentInput = $('<textarea>')
      .addClass('entry-comment-input')
      .attr('placeholder', '\u30b3\u30e1\u30f3\u30c8')
      .attr('rows', 2)
      .attr('data-day', day);

    const selectionNote = $('<div>')
      .addClass('ss-selected-note ss-hidden')
      .attr('data-search-kind', 'day')
      .attr('data-day', day);

    const candidatePanel = $('<div>')
      .addClass('ss-candidate-panel ss-hidden')
      .attr('data-search-kind', 'day')
      .attr('data-day', day);

    const optionBtn = $('<button>')
      .attr('type', 'button')
      .addClass('option-select-btn')
      .attr('data-day', day)
      .html('<span>\u8a2d\u5b9a</span><span>OP\u7121\u3057</span>');

    const toolDetailBtn = $('<button>')
      .attr('type', 'button')
      .addClass('detail-btn day-detail-trigger')
      .attr('data-day', day)
      .text('\u8a73\u7d30');

    const copyInput = $('<input>')
      .attr('type', 'number')
      .addClass('copy-input')
      .attr('placeholder', '\u30b3\u30d4\u30fc\u5143\u65e5')
      .attr('min', 1)
      .attr('max', maxDay)
      .attr('data-day', day);
    const copyBtn = $('<button>')
      .attr('type', 'button')
      .addClass('copy-btn')
      .attr('data-day', day)
      .text('\u30b3\u30d4\u30fc');
    const controlsGrid = $('<div>').addClass('day-controls-grid');
    controlsGrid.append(optionBtn, toolDetailBtn, copyInput, copyBtn);

    inputGroup.append(inputRow, selectionNote, candidatePanel, commentInput, controlsGrid);
    dayBox.append(inputGroup);
    return dayBox;
  }

  function clearEventHandlers() {
    $(document).off('keydown', '.entry-input');
    $(document).off('keydown', '.entry-comment-input');
    $(document).off('keydown', '#ss-entry-modal-comment');
    $(document).off('keydown', '.copy-input');
    $(document).off('input', '.ss-employee-search-input');
    $(document).off('input', '.ss-site-search-input');
    $(document).off('click', '.ss-employee-candidate-btn');
    $(document).off('click', '.ss-site-candidate-btn');
    $(document).off('click', '.add-entry-btn');
    $(document).off('click', '.option-select-btn');
    $(document).off('click', '.copy-btn');
    $(document).off('click', '.entry-item-delete');
    $(document).off('click', '.entry-item');
    $(document).off('click', '.day-detail-trigger');
    $(document).off('click', '.ss-entry-edit-btn');
    $(document).off('click', '.ss-entry-delete-btn');
    $(document).off('click', '.ss-entry-save-btn');
    $(document).off('change', '#ss-entry-modal-option');
    $(document).off('change', '#ss-entry-modal-site-branch');
    $(document).off('click', '.ss-open-sync-source-btn');
  }

  function attachEventHandlers() {
    clearEventHandlers();

    $(document).on('click', '.day-detail-trigger', function(e) {
      e.preventDefault();
      openDayDetail($(this).attr('data-day'));
    });

    $(document).on('click', '.entry-item', function(e) {
      if ($(e.target).closest('.entry-item-delete').length > 0) {
        return;
      }
      openEntryModal($(this).data('day'), $(this).data('entryId'));
    });

    if (!state.editable) {
      return;
    }

    $(document).on('keydown', '.entry-input', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        addEntry($(this).attr('data-day'));
      }
    });

    $(document).on('keydown', '.entry-comment-input', function(e) {
      if (e.key !== 'Enter') {
        return;
      }
      if (e.altKey) {
        e.preventDefault();
        insertTextAtCursor(this, '\n');
        return;
      }
      e.preventDefault();
      addEntry($(this).attr('data-day'));
    });

    $(document).on('keydown', '#ss-entry-modal-comment', function(e) {
      if (e.key !== 'Enter') {
        return;
      }
      if (e.altKey) {
        e.preventDefault();
        insertTextAtCursor(this, '\n');
        return;
      }
      e.preventDefault();
    });

    $(document).on('input', '.ss-employee-search-input', function() {
      if (!isEntryEmployeeSearchEnabled()) {
        return;
      }
      scheduleEmployeeSearchForInput($(this));
    });

    $(document).on('input', '.ss-site-search-input', function() {
      if (!isEntrySiteSearchEnabled()) {
        return;
      }
      scheduleSiteSearchForInput($(this));
    });

    $(document).on('click', '.ss-employee-candidate-btn', function() {
      const $btn = $(this);
      const kind = String($btn.attr('data-search-kind') || '');
      if (kind === 'modal') {
        const $input = $('#ss-entry-modal-name');
        setEmployeeSelectionForInput($input, {
          employee_number: $btn.attr('data-employee-number') || '',
          employee_name: $btn.attr('data-employee-name') || $btn.text() || ''
        });
        return;
      }

      const day = String($btn.attr('data-day') || '');
      const $input = $(`.entry-input[data-day='${day}']`);
      setEmployeeSelectionForInput($input, {
        employee_number: $btn.attr('data-employee-number') || '',
        employee_name: $btn.attr('data-employee-name') || $btn.text() || ''
      });
      updateDayEmployeeSelectionNote(day);
    });

    $(document).on('click', '.ss-site-candidate-btn', function() {
      const $btn = $(this);
      const kind = String($btn.attr('data-search-kind') || '');
      const payload = {
        site_row_id: $btn.attr('data-site-row-id') || '',
        site_id: $btn.attr('data-site-id') || '',
        site_name: $btn.attr('data-site-name') || ''
      };
      if (kind === 'modal') {
        setSiteSelectionForInput($('#ss-entry-modal-name'), payload);
        return;
      }
      const day = String($btn.attr('data-day') || '');
      const $input = $(`.entry-input[data-day='${day}']`);
      setSiteSelectionForInput($input, payload);
      updateDaySiteSelectionNote(day);
    });

    $(document).on('keydown', '.copy-input', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        copyEntries($(this).attr('data-day'));
      }
    });

    $(document).on('click', '.add-entry-btn', function() {
      addEntry($(this).attr('data-day'));
    });

    $(document).on('click', '.option-select-btn', function() {
      showOptionPopup($(this).attr('data-day'));
    });

    $(document).on('click', '.copy-btn', function() {
      copyEntries($(this).attr('data-day'));
    });

    $(document).on('click', '.entry-item-delete', function(e) {
      e.stopPropagation();
      const item = $(this).closest('.entry-item');
      deleteEntry(item.data('day'), item.data('entryId'));
    });

    $(document).on('click', '.ss-entry-edit-btn', function() {
      openEntryModal($(this).data('day'), $(this).data('entryId'));
    });

    $(document).on('click', '.ss-entry-delete-btn', function() {
      deleteEntry($(this).data('day'), $(this).data('entryId'));
      openDayDetail($(this).data('day'));
    });

    $(document).on('click', '.ss-entry-save-btn', function() {
      saveEntryFromModal();
    });

    $(document).on('change', '#ss-entry-modal-option', function() {
      refreshEntryModalBranchOptions();
    });

    $(document).on('change', '#ss-entry-modal-site-branch', function() {
      const selected = $(this).find('option:selected');
      $(this).attr('data-current-row-id', normalizeSiteBranchRowId($(this).val()));
      $(this).attr('data-current-branch', String(selected.attr('data-site-branch') || '').trim());
      if ($(this).val()) {
        setEntryModalBranchMessage('');
      }
    });

    $(document).on('click', '.ss-open-sync-source-btn', function() {
      if (typeof window.CLOUDSHIFT_OPEN_SYNC_SOURCE !== 'function') {
        return;
      }
      const payload = {
        sync_source_type: $(this).attr('data-sync-source-type') || '',
        sync_source_project_id: $(this).attr('data-sync-source-project-id') || '',
        sync_source_project_title: $(this).attr('data-sync-source-project-title') || '',
        sync_source_month_key: $(this).attr('data-sync-source-month-key') || '',
        sync_source_day: $(this).attr('data-sync-source-day') || '',
        sync_source_entry_id: $(this).attr('data-sync-source-entry-id') || '',
      };
      window.CLOUDSHIFT_OPEN_SYNC_SOURCE(payload);
    });
  }

  function addEntry(day) {
    const dayKey = String(day);
    const nameInput = $(`.entry-input[data-day='${dayKey}']`);
    const commentInput = $(`.entry-comment-input[data-day='${dayKey}']`);
    const name = nameInput.val().trim();
    if (!name) {
      return;
    }

    const options = getSelectedOptionsForDay(dayKey);
    const autoBranchFields = isSceneMode() ? autoBranchFieldsForOption(options[0] || null) : { site_branch_row_id: '', site_branch: '' };
    const selectedSite = isPersonMode() ? getSelectedSiteDataForInput(nameInput) : { site_row_id: '', site_id: '', site_name: '' };
    const entry = normalizeEntry({
      id: makeEntryId(),
      value: formatEntryValue(options[0] || null, name),
      comment: commentInput.val().trim(),
      employee_number: getSelectedEmployeeNumberForInput(nameInput),
      site_row_id: selectedSite.site_row_id,
      site_id: selectedSite.site_id,
      site_name: selectedSite.site_name,
      site_branch_row_id: autoBranchFields.site_branch_row_id,
      site_branch: autoBranchFields.site_branch
    });
    if (!entry) {
      return;
    }

    const nextEntries = getDayEntries(dayKey).slice();
    nextEntries.push(entry);
    setDayEntries(dayKey, nextEntries);
    updateEntryDisplay(dayKey);
    updateCapacityWarning(dayKey);
    nameInput.val('').focus();
    commentInput.val('');
    clearEmployeeSelectionForInput(nameInput);
    clearSiteSelectionForInput(nameInput);
    clearSelectedOptionsForDay(dayKey);
    updateOptionSelectButton(dayKey);
  }

  function copyEntries(targetDay) {
    const sourceDay = parseInt($(`.copy-input[data-day='${targetDay}']`).val(), 10);
    if (!sourceDay || !state.entriesPerDay[String(sourceDay)]) {
      alert('\u30b3\u30d4\u30fc\u5143\u65e5\u306e\u30c7\u30fc\u30bf\u304c\u3042\u308a\u307e\u305b\u3093');
      return;
    }
    const copiedEntries = getDayEntries(sourceDay)
      .filter((entry) => !isSyncedEntry(entry))
      .map((entry) => cloneEntry(entry, true));
    setDayEntries(targetDay, copiedEntries);
    updateEntryDisplay(targetDay);
    updateCapacityWarning(targetDay);
    $(`.copy-input[data-day='${targetDay}']`).val('');
  }

  function deleteEntry(day, entryId) {
    const nextEntries = getDayEntries(day).filter((entry) => entry.id !== entryId);
    setDayEntries(day, nextEntries);
    updateEntryDisplay(day);
    updateCapacityWarning(day);
    closeModal('entry');
  }

  function createOptionSection(title, optionKeys, dayKey) {
    const section = $('<div>').addClass('popup-section');
    section.append(`<div class="popup-section-title">${title}</div>`);
    const grid = $('<div>').addClass('option-buttons');
    optionKeys.forEach((key) => {
      const btn = $('<button>')
        .attr('type', 'button')
        .addClass('option-btn')
        .attr('data-day', dayKey)
        .attr('data-option', key)
        .text(allOptionMappings[key] || key)
        .on('click', function() {
          const current = getSelectedOptionsForDay(dayKey);
          if (current[0] === key) {
            clearSelectedOptionsForDay(dayKey);
          } else {
            setSelectedOptionsForDay(dayKey, [key]);
          }
          updateOptionButtonStates(dayKey, $(this).closest('.popup-overlay'));
        });
      grid.append(btn);
    });
    section.append(grid);
    return section;
  }

  function updateOptionButtonStates(dayKey, overlay) {
    const selected = getSelectedOptionsForDay(dayKey)[0] || null;
    overlay.find('.option-btn').each(function() {
      $(this).toggleClass('selected', $(this).attr('data-option') === selected);
    });
  }

  function updateOptionSelectButton(day) {
    const btn = $(`.option-select-btn[data-day='${day}']`);
    if (!btn.length) {
      return;
    }
    const selected = getSelectedOptionsForDay(day)[0];
    if (!selected) {
      btn.html('<span>\u8a2d\u5b9a</span><span>OP\u7121\u3057</span>');
      btn.removeClass('has-options');
      return;
    }
    btn.html(`<span>\u8a2d\u5b9a</span><span>${allOptionMappings[selected] || selected}</span>`);
    btn.addClass('has-options');
  }

  function showOptionPopup(day) {
    const dayKey = String(day);
    const overlay = $('<div>').addClass('popup-overlay');
    const popup = $('<div>').addClass('popup-content');
    popup.append('<div class="popup-header">\u30aa\u30d7\u30b7\u30e7\u30f3\u9078\u629e</div>');
    getOptionSectionsForMode(state.mode).forEach((section) => {
      popup.append(createOptionSection(section.title, section.optionKeys, dayKey));
    });

    const footer = $('<div>').addClass('popup-footer');
    footer.append(
      $('<button>')
        .addClass('popup-clear-btn')
        .text('\u30af\u30ea\u30a2')
        .on('click', function() {
          clearSelectedOptionsForDay(dayKey);
          updateOptionButtonStates(dayKey, overlay);
        })
    );
    footer.append(
      $('<button>')
        .addClass('popup-confirm-btn')
        .text('\u78ba\u5b9a')
        .on('click', function() {
          updateOptionSelectButton(dayKey);
          overlay.remove();
          $(`.entry-input[data-day='${dayKey}']`).focus();
        })
    );
    popup.append(footer);
    overlay.append(popup);
    $('body').append(overlay);

    updateOptionButtonStates(dayKey, overlay);

    overlay.on('click', function(e) {
      if (e.target === overlay[0]) {
        overlay.remove();
      }
    });
  }

  function setEntryModalBranchMessage(message) {
    const note = $('#ss-entry-modal-branch-note');
    if (!note.length) {
      return;
    }
    const text = String(message || '').trim();
    if (!text) {
      note.text('').addClass('ss-hidden');
      return;
    }
    note.text(text).removeClass('ss-hidden');
  }

  function refreshEntryModalBranchOptions() {
    const select = $('#ss-entry-modal-site-branch');
    if (!select.length) {
      return;
    }

    const optionKey = $('#ss-entry-modal-option').val() || '';
    const candidates = siteBranchCandidatesForOption(optionKey);
    const currentRowId = normalizeSiteBranchRowId(select.attr('data-current-row-id') || '');
    const currentBranch = String(select.attr('data-current-branch') || '').trim();
    const previousValue = normalizeSiteBranchRowId(select.val() || '');
    const currentBranchExists = currentRowId && candidates.some((branch) => branch.id === currentRowId);
    const preservedBranch = currentRowId && !currentBranchExists
      ? {
          id: currentRowId,
          site_branch: currentBranch,
          cloudshift_option_key: '',
          option_label: '',
          is_active: false,
        }
      : null;

    const options = ['<option value="">未設定</option>'];
    if (preservedBranch) {
      options.push(
        `<option value="${escapeHtml(preservedBranch.id)}" data-site-branch="${escapeHtml(preservedBranch.site_branch)}">${escapeHtml(preservedBranch.site_branch ? `${preservedBranch.site_branch} / 現在は無効` : '現在は無効')}</option>`
      );
    }
    candidates.forEach((branch) => {
      options.push(
        `<option value="${escapeHtml(branch.id)}" data-site-branch="${escapeHtml(branch.site_branch)}">${escapeHtml(siteBranchChoiceLabel(branch))}</option>`
      );
    });
    select.html(options.join(''));

    let nextValue = previousValue && select.find(`option[value="${previousValue}"]`).length ? previousValue : '';
    if (!nextValue && currentRowId && select.find(`option[value="${currentRowId}"]`).length) {
      nextValue = currentRowId;
    }
    if (!nextValue && candidates.length === 1) {
      nextValue = candidates[0].id;
      select.attr('data-current-row-id', candidates[0].id);
      select.attr('data-current-branch', candidates[0].site_branch);
      setEntryModalBranchMessage(`候補が1件のため 枝${candidates[0].site_branch} を自動選択しました`);
    } else if (!currentSiteBranches().length) {
      setEntryModalBranchMessage('この現場に有効な枝番号がありません');
    } else if (optionKey && !candidates.some((branch) => branch.cloudshift_option_key === String(optionKey).trim().toUpperCase())) {
      setEntryModalBranchMessage('一致する枝番号がないため、登録済みの枝番号をすべて表示しています');
    } else if (preservedBranch) {
      setEntryModalBranchMessage('現在は無効な枝番号が設定されています。必要に応じて再選択してください');
    } else {
      setEntryModalBranchMessage('');
    }

    select.val(nextValue);
  }

  function openDayDetail(day) {
    ensureModalScaffold();
    const modal = $('#ss-day-detail-modal');
    const entries = getDayEntries(day);
    $('#ss-day-detail-title').text(`${state.year}\u5e74${state.month}\u6708${day}\u65e5\u306e\u8a73\u7d30`);
    $('#ss-day-detail-subtitle').text(`${state.mode === 'scene' ? '\u73fe\u5834' : '\u500b\u4eba'}: ${state.name || ''}`);

    const body = $('#ss-day-detail-body');
    let html = '';

    if (!entries.length) {
      html += '<div class="ss-detail-empty">\u3053\u306e\u65e5\u306f\u307e\u3060\u767b\u9332\u304c\u3042\u308a\u307e\u305b\u3093</div>';
    } else {
      html += entries.map((entry, index) => {
        const parts = getEntryDisplayParts(entry);
        const syncedEntry = isSyncedEntry(entry);
        return `
          <article class="ss-detail-card">
            <div class="ss-detail-row">
              <div>
                <div class="ss-detail-label">\u30a8\u30f3\u30c8\u30ea ${index + 1}</div>
                <div class="ss-detail-value-row">
                  <div class="ss-detail-value">${escapeHtml(parts.title)}</div>
                  ${parts.branch_issue_label ? `<span class="ss-issue-pill is-${escapeHtml(parts.branch_issue_tone || 'warning')}">${escapeHtml(parts.branch_issue_label)}</span>` : ''}
                </div>
              </div>
              ${state.editable && !syncedEntry ? `
                <div class="ss-detail-actions">
                  <button type="button" class="ss-entry-edit-btn muted-link" data-day="${day}" data-entry-id="${entry.id}">\u7de8\u96c6</button>
                  <button type="button" class="ss-entry-delete-btn muted-link danger" data-day="${day}" data-entry-id="${entry.id}">\u524a\u9664</button>
                </div>
              ` : ''}
            </div>
            <div class="ss-detail-comment-block">
              <div class="ss-detail-label">\u30b3\u30e1\u30f3\u30c8</div>
              <div class="ss-detail-comment">${parts.comment ? escapeHtml(parts.comment) : '<span class="ss-detail-empty-text">\u30b3\u30e1\u30f3\u30c8\u306a\u3057</span>'}</div>
            </div>
            ${parts.branch_label ? `
              <div class="ss-detail-comment-block">
                <div class="ss-detail-label">\u679d\u756a\u53f7</div>
                <div class="ss-detail-comment">${escapeHtml(parts.branch_label)}</div>
              </div>
            ` : ''}
          </article>
        `;
      }).join('');
    }

    if (state.editable && (state.mode === 'scene' || state.mode === 'person')) {
      html += `
        <div class="ss-detail-assist-action">
          <button type="button" class="btn-secondary day-assist-trigger" data-day="${day}">\u30a2\u30b7\u30b9\u30c8</button>
        </div>
      `;
    }

    body.html(html);
    modal.removeClass('ss-hidden');
  }

  function openEntryModal(day, entryId) {
    ensureModalScaffold();
    const entries = getDayEntries(day);
    const entry = entries.find((item) => item.id === entryId);
    if (!entry) {
      return;
    }

    const parsed = parseEntryValue(entry.value);
    $('#ss-entry-modal-title').text(`${day}\u65e5\u306e\u30a8\u30f3\u30c8\u30ea\u8a73\u7d30`);
    $('#ss-entry-modal-subtitle').text(state.editable ? '\u5185\u5bb9\u3092\u78ba\u8a8d\u3057\u3001\u5909\u66f4\u3057\u3066\u304f\u3060\u3055\u3044' : '\u5185\u5bb9\u3092\u78ba\u8a8d\u3067\u304d\u307e\u3059');

    const body = $('#ss-entry-modal-body');
    const optionText = parsed.optionKey ? allOptionMappings[parsed.optionKey] || parsed.optionKey : '\u306a\u3057';
    const branchState = entryBranchState(entry);
    const siteContext = state.siteContext && typeof state.siteContext === 'object' ? state.siteContext : null;
    const canChooseBranch = isSceneMode() && !!(siteContext && siteContext.is_linked);
    const hasBranches = currentSiteBranches().length > 0;
    const syncedEntry = isSyncedEntry(entry);
    const canOpenSource = canOpenSyncedSourceEntry(entry);
    const selectedSite = {
      site_row_id: String(entry.site_row_id || '').trim(),
      site_id: String(entry.site_id || '').trim(),
      site_name: String(entry.site_name || parsed.name || '').trim()
    };

    if (!state.editable || syncedEntry) {
      body.html(`
        <div class="ss-detail-form">
          ${syncedEntry ? `
            <div class="ss-detail-field">
              <div class="ss-detail-label">\u540c\u671f</div>
              <div class="ss-detail-static">\u81ea\u52d5\u53cd\u6620\u30a8\u30f3\u30c8\u30ea\u3067\u3059\u3002\u7de8\u96c6\u306f\u5143\u306e\u30b7\u30d5\u30c8\u5074\u3067\u884c\u3063\u3066\u304f\u3060\u3055\u3044\u3002</div>
            </div>
          ` : ''}
          <div class="ss-detail-field">
            <div class="ss-detail-label">\u540d\u524d</div>
            <div class="ss-detail-static">${escapeHtml(parsed.name)}</div>
          </div>
          <div class="ss-detail-field">
            <div class="ss-detail-label">\u30aa\u30d7\u30b7\u30e7\u30f3</div>
            <div class="ss-detail-static">${escapeHtml(optionText)}</div>
          </div>
          <div class="ss-detail-field">
            <div class="ss-detail-label">\u30b3\u30e1\u30f3\u30c8</div>
            <div class="ss-detail-static">${entry.comment ? escapeHtml(entry.comment) : '<span class="ss-detail-empty-text">\u30b3\u30e1\u30f3\u30c8\u306a\u3057</span>'}</div>
          </div>
          ${selectedSite.site_id || selectedSite.site_name ? `
            <div class="ss-detail-field">
              <div class="ss-detail-label">\u73fe\u5834\u30ea\u30f3\u30af</div>
              <div class="ss-detail-static">${escapeHtml([selectedSite.site_id, selectedSite.site_name].filter(Boolean).join(' / '))}</div>
            </div>
          ` : ''}
          ${branchState.label ? `
            <div class="ss-detail-field">
              <div class="ss-detail-label">\u679d\u756a\u53f7</div>
              <div class="ss-detail-static">${escapeHtml(branchState.label)}</div>
            </div>
          ` : ''}
          ${syncedEntry && canOpenSource ? `
            <div class="ss-detail-actions foot">
              <button
                type="button"
                class="btn-primary ss-open-sync-source-btn"
                data-sync-source-type="${escapeHtml(entry.sync_source_type || '')}"
                data-sync-source-project-id="${escapeHtml(entry.sync_source_project_id || '')}"
                data-sync-source-project-title="${escapeHtml(entry.sync_source_project_title || '')}"
                data-sync-source-month-key="${escapeHtml(entry.sync_source_month_key || '')}"
                data-sync-source-day="${escapeHtml(entry.sync_source_day || '')}"
                data-sync-source-entry-id="${escapeHtml(entry.sync_source_entry_id || '')}"
              >反映元を編集</button>
            </div>
          ` : ''}
        </div>
      `);
      $('#ss-entry-modal').removeClass('ss-hidden');
      return;
    }

    const availableOptionKeys = getSelectableOptionKeysForMode(state.mode);
    body.html(`
      <div class="ss-detail-form">
        <input type="hidden" id="ss-entry-modal-day" value="${day}">
        <input type="hidden" id="ss-entry-modal-id" value="${escapeHtml(entry.id)}">
        <input type="hidden" id="ss-entry-modal-employee-number" value="${escapeHtml(entry.employee_number || '')}">
        <div class="ss-detail-field">
          <label class="ss-detail-label" for="ss-entry-modal-name">\u540d\u524d</label>
          <input
            id="ss-entry-modal-name"
            class="ss-detail-input${isEntryEmployeeSearchEnabled() ? ' ss-employee-search-input' : ''}${isEntrySiteSearchEnabled() ? ' ss-site-search-input' : ''}"
            type="text"
            value="${escapeHtml(parsed.name)}"
            ${isEntryEmployeeSearchEnabled() || isEntrySiteSearchEnabled() ? 'data-search-kind="modal"' : ''}
          >
          <div id="ss-entry-modal-selected-note" class="ss-selected-note${entry.employee_number || selectedSite.site_id ? '' : ' ss-hidden'}">${entry.employee_number ? `選択中: ${escapeHtml(parsed.name)} / ${escapeHtml(entry.employee_number)}` : selectedSite.site_id ? `選択中: ${escapeHtml([selectedSite.site_id, selectedSite.site_name].filter(Boolean).join(' / '))}` : ''}</div>
          <div id="ss-entry-modal-candidate-panel" class="ss-candidate-panel ss-hidden" data-search-kind="modal"></div>
        </div>
        <div class="ss-detail-field">
          <label class="ss-detail-label" for="ss-entry-modal-option">\u30aa\u30d7\u30b7\u30e7\u30f3</label>
          <select id="ss-entry-modal-option" class="ss-detail-input">
            <option value="">\u306a\u3057</option>
            ${availableOptionKeys.map((key) => `<option value="${key}" ${key === parsed.optionKey ? 'selected' : ''}>${escapeHtml(allOptionMappings[key] || key)}</option>`).join('')}
          </select>
        </div>
        <div class="ss-detail-field">
          <label class="ss-detail-label" for="ss-entry-modal-comment">\u30b3\u30e1\u30f3\u30c8</label>
          <textarea id="ss-entry-modal-comment" class="ss-detail-textarea" rows="4">${escapeHtml(entry.comment)}</textarea>
        </div>
        ${canChooseBranch ? `
          <div class="ss-detail-field">
            <label class="ss-detail-label" for="ss-entry-modal-site-branch">\u679d\u756a\u53f7</label>
            <select
              id="ss-entry-modal-site-branch"
              class="ss-detail-input"
              data-current-row-id="${escapeHtml(branchState.site_branch_row_id || '')}"
              data-current-branch="${escapeHtml(branchState.site_branch || '')}"
            ></select>
            <div id="ss-entry-modal-branch-note" class="ss-selected-note${hasBranches || branchState.label ? '' : ' ss-hidden'}"></div>
          </div>
        ` : isSceneMode() ? `
          <div class="ss-detail-field">
            <div class="ss-detail-label">\u679d\u756a\u53f7</div>
            <div class="ss-detail-static">${siteContext && siteContext.is_linked ? 'この現場に有効な枝番号がありません' : '現場未設定のため選択できません'}</div>
          </div>
        ` : ''}
        <div class="ss-detail-actions foot">
          <button type="button" class="btn-secondary" data-close-modal="entry">\u9589\u3058\u308b</button>
          <button type="button" class="btn-primary ss-entry-save-btn">\u4fdd\u5b58</button>
        </div>
      </div>
    `);

    if (isEntryEmployeeSearchEnabled() && entry.employee_number) {
      const $modalNameInput = $('#ss-entry-modal-name');
      $modalNameInput.attr('data-employee-number', String(entry.employee_number || ''));
      $modalNameInput.attr('data-selected-employee-name', parsed.name);
      $modalNameInput.attr('data-search-token', `selected-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
    }

    if (isEntrySiteSearchEnabled() && (selectedSite.site_row_id || selectedSite.site_id)) {
      const $modalNameInput = $('#ss-entry-modal-name');
      $modalNameInput.attr('data-site-row-id', selectedSite.site_row_id);
      $modalNameInput.attr('data-site-id', selectedSite.site_id);
      $modalNameInput.attr('data-selected-site-name', selectedSite.site_name || parsed.name);
      $modalNameInput.attr('data-search-token', `selected-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
    }

    if (canChooseBranch) {
      refreshEntryModalBranchOptions();
    }

    $('#ss-entry-modal').removeClass('ss-hidden');
  }

  function saveEntryFromModal() {
    const day = $('#ss-entry-modal-day').val();
    const entryId = $('#ss-entry-modal-id').val();
    const name = $('#ss-entry-modal-name').val().trim();
    if (!name) {
      alert('\u540d\u524d\u3092\u5165\u529b\u3057\u3066\u304f\u3060\u3055\u3044');
      return;
    }

    const optionKey = $('#ss-entry-modal-option').val() || null;
    const comment = $('#ss-entry-modal-comment').val().trim();
    const $nameInput = $('#ss-entry-modal-name');
    const employeeNumber = getSelectedEmployeeNumberForInput($nameInput);
    const selectedSiteForSave = isPersonMode() ? getSelectedSiteDataForInput($nameInput) : { site_row_id: '', site_id: '', site_name: '' };
    const siteBranchSelect = $('#ss-entry-modal-site-branch');
    const siteBranchRowId = siteBranchSelect.length ? normalizeSiteBranchRowId(siteBranchSelect.val()) : '';
    const selectedBranch = siteBranchSelect.length ? siteBranchSelect.find('option:selected') : $();
    const siteBranch = siteBranchRowId
      ? String(selectedBranch.attr('data-site-branch') || siteBranchSelect.attr('data-current-branch') || '').trim()
      : '';
    const nextEntries = getDayEntries(day).map((entry) => {
      if (entry.id !== entryId) {
        return entry;
      }
      return normalizeEntry({
        id: entry.id,
        value: formatEntryValue(optionKey, name),
        comment,
        employee_number: employeeNumber,
        site_row_id: selectedSiteForSave.site_row_id,
        site_id: selectedSiteForSave.site_id,
        site_name: selectedSiteForSave.site_name,
        site_branch_row_id: siteBranchRowId,
        site_branch: siteBranch
      });
    });

    setDayEntries(day, nextEntries);
    updateEntryDisplay(day);
    updateCapacityWarning(day);
    closeModal('entry');
  }

  function updateDayEmployeeSelectionNote(dayKey) {
    const $input = $(`.entry-input[data-day='${dayKey}']`);
    if (!$input.length) {
      return;
    }
    const note = $(`.ss-selected-note[data-search-kind='day'][data-day='${dayKey}']`);
    if (!note.length) {
      return;
    }
    const selectedNumber = String($input.attr('data-employee-number') || '').trim();
    const selectedName = String($input.attr('data-selected-employee-name') || '').trim();
    if (!selectedNumber) {
      note.text('').addClass('ss-hidden');
      return;
    }
    note
      .text(selectedName ? `\u9078\u629e\u4e2d: ${selectedName} / ${selectedNumber}` : `\u9078\u629e\u4e2d: ${selectedNumber}`)
      .removeClass('ss-hidden');
  }

  function updateDaySiteSelectionNote(dayKey) {
    const $input = $(`.entry-input[data-day='${dayKey}']`);
    if (!$input.length) {
      return;
    }
    const note = $(`.ss-selected-note[data-search-kind='day'][data-day='${dayKey}']`);
    if (!note.length) {
      return;
    }
    const selected = getSelectedSiteDataForInput($input);
    const label = [selected.site_id, selected.site_name].filter(Boolean).join(' / ');
    if (!label) {
      note.text('').addClass('ss-hidden');
      return;
    }
    note.text(`選択中: ${label}`).removeClass('ss-hidden');
  }

  function buildCalendar(year, month, mode, initialData = null, options = {}) {
    state.year = year;
    state.month = month;
    state.mode = mode;
    state.editable = Object.prototype.hasOwnProperty.call(options, 'editable') ? !!options.editable : true;
    state.holidays = new Set(
      Array.isArray(options.holidays)
        ? options.holidays.map((value) => String(value))
        : Array.isArray(window.SHIFTERSYNC_HOLIDAYS)
          ? window.SHIFTERSYNC_HOLIDAYS.map((value) => String(value))
          : []
    );
    state.entriesPerDay = {};
    state.selectedOptions = {};

    ensureModalScaffold();
    bindModalEvents();

    const grid = $('#shiftGrid');
    grid.empty();

    const daysInMonth = new Date(year, month, 0).getDate();
    const rawDow = new Date(year, month - 1, 1).getDay();
    const firstDow = (rawDow + 6) % 7;

    for (let i = 0; i < firstDow; i += 1) {
      grid.append($('<div>').addClass('day-box empty'));
    }

    for (let day = 1; day <= daysInMonth; day += 1) {
      const key = String(day);
      setDayEntries(day, initialData && (initialData[key] || initialData[day]) ? (initialData[key] || initialData[day]) : []);
      const dayBox = createDayBox(day, daysInMonth);
      grid.append(dayBox);
      updateEntryDisplay(day);
      updateCapacityWarning(day);
    }

    attachEventHandlers();
  }

  function buildCSV() {
    const header = [`${state.mode}`, `${state.year}`, `${state.month}`, `${state.name}`];
    if (state.capacityEnabled && state.requiredCapacity > 0) {
      header.push(String(state.requiredCapacity));
    }

    const rows = [
      header,
      ['\u65e5\u4ed8', state.mode === 'scene' ? '\u51fa\u52e4\u8005' : '\u73fe\u5834']
    ];

    const commentRows = [];
    const employeeNumberRows = [];
    const siteRowIdRows = [];
    const siteIdRows = [];
    const siteNameRows = [];
    const siteBranchRowIdRows = [];
    const siteBranchRows = [];
    Object.keys(state.entriesPerDay)
      .sort((a, b) => parseInt(a, 10) - parseInt(b, 10))
      .forEach((day) => {
        const entries = getDayEntries(day);
        if (entries.length > 0) {
          rows.push([day, ...entries.map((entry) => entry.value)]);
        }
        entries.forEach((entry, index) => {
          if (entry.comment) {
            commentRows.push([commentRowPrefix, day, index, entry.comment]);
          }
          if (entry.employee_number) {
            employeeNumberRows.push([employeeNumberRowPrefix, day, index, entry.employee_number]);
          }
          if (entry.site_row_id) {
            siteRowIdRows.push([siteRowIdRowPrefix, day, index, entry.site_row_id]);
          }
          if (entry.site_id) {
            siteIdRows.push([siteIdRowPrefix, day, index, entry.site_id]);
          }
          if (entry.site_name) {
            siteNameRows.push([siteNameRowPrefix, day, index, entry.site_name]);
          }
          if (entry.site_branch_row_id) {
            siteBranchRowIdRows.push([siteBranchRowIdRowPrefix, day, index, entry.site_branch_row_id]);
          }
          if (entry.site_branch) {
            siteBranchRows.push([siteBranchRowPrefix, day, index, entry.site_branch]);
          }
        });
      });

    return rows
      .concat(commentRows)
      .concat(employeeNumberRows)
      .concat(siteRowIdRows)
      .concat(siteIdRows)
      .concat(siteNameRows)
      .concat(siteBranchRowIdRows)
      .concat(siteBranchRows)
      .concat(state.mode === 'person' && state.targetEmployeeNumber ? [[projectEmployeeNumberRowPrefix, state.targetEmployeeNumber]] : [])
      .map((row) => row.map((cell) => csvEscape(cell)).join(','))
      .join('\n');
  }

  function setState(key, value) {
    state[key] = value;
    if (key === 'capacityEnabled' || key === 'requiredCapacity') {
      updateAllCapacityWarnings();
      updateAllBranchWarnings();
    }
    if (key === 'siteContext' || key === 'siteBranches') {
      Object.keys(state.entriesPerDay).forEach((day) => updateEntryDisplay(day));
      updateAllBranchWarnings();
    }
  }

  function getState(key) {
    return state[key];
  }

  function getOptionMappings() {
    return Object.assign({}, allOptionMappings);
  }

  function getOptionSectionsForModeExport(mode) {
    return getOptionSectionsForMode(mode).map((section) => ({
      title: section.title,
      optionKeys: section.optionKeys.slice()
    }));
  }

  function getEntriesPerDay() {
    const snapshot = {};
    Object.keys(state.entriesPerDay).forEach((day) => {
      snapshot[day] = getDayEntries(day).map((entry) => cloneEntry(entry, false));
    });
    return snapshot;
  }

  function getSelectedOptionsForDayExport(day) {
    return getSelectedOptionsForDay(day);
  }

  return {
    buildCalendar,
    buildCSV,
    setState,
    getState,
    replaceEntriesPerDay,
    getEntriesPerDay,
    getSelectedOptionsForDay: getSelectedOptionsForDayExport,
    updateAllCapacityWarnings,
    updateAllBranchWarnings,
    getOptionMappings,
    getOptionSectionsForMode: getOptionSectionsForModeExport,
    openEntryModal
  };
})();
