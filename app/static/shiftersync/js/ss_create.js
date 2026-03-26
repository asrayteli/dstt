$(document).ready(function() {
  let mode = 'scene';
  const employeeSearchCache = new Map();
  let employeeSearchTimer = null;

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function isPersonMode() {
    return mode === 'person';
  }

  function clearEmployeeSelection() {
    $('#target_employee_number').val('');
    $('#target_name').removeAttr('data-selected-employee-name');
    $('#targetSelectedNote').text('');
  }

  function renderEmployeeCandidates(candidates) {
    const panel = $('#targetCandidatePanel');
    if (!isPersonMode()) {
      panel.empty().addClass('ss-hidden');
      return;
    }

    const normalized = (Array.isArray(candidates) ? candidates : [])
      .map((item) => ({
        employee_number: String(item.employee_number || '').trim(),
        employee_name: String(item.employee_name || '').trim(),
        office_name: String(item.office_name || '').trim(),
        job_title: String(item.job_title || '').trim()
      }))
      .filter((item) => item.employee_number && item.employee_name);

    if (!normalized.length) {
      panel.html('<div style="color: var(--ss-muted); font-size: 12px; padding: 4px 2px;">候補が見つかりません</div>').removeClass('ss-hidden');
      return;
    }

    panel.html(normalized.slice(0, 8).map((candidate) => {
      const label = [
        candidate.employee_name,
        candidate.employee_number,
        candidate.office_name,
        candidate.job_title
      ].filter(Boolean).join(' / ');
      return `
        <button
          type="button"
          class="target-employee-candidate"
          data-employee-number="${escapeHtml(candidate.employee_number)}"
          data-employee-name="${escapeHtml(candidate.employee_name)}"
          style="display:block; width:100%; text-align:left; border:1px solid var(--ss-line); border-radius:10px; background:#fff; color:var(--ss-ink); padding:8px 10px; cursor:pointer; margin:0 0 6px;"
        >${escapeHtml(label)}</button>
      `;
    }).join('')).removeClass('ss-hidden');
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
      throw new Error('社員候補の検索に失敗しました');
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

  function scheduleEmployeeSearch() {
    const query = $('#target_name').val().trim();
    const panel = $('#targetCandidatePanel');

    if (!isPersonMode()) {
      panel.empty().addClass('ss-hidden');
      return;
    }

    const selectedName = $('#target_name').attr('data-selected-employee-name') || '';
    if (selectedName && selectedName !== query) {
      clearEmployeeSelection();
    }

    if (!query) {
      panel.empty().addClass('ss-hidden');
      return;
    }

    window.clearTimeout(employeeSearchTimer);
    employeeSearchTimer = window.setTimeout(async () => {
      const latestQuery = $('#target_name').val().trim();
      if (!latestQuery) {
        panel.empty().addClass('ss-hidden');
        return;
      }
      try {
        const candidates = await fetchEmployeeCandidates(latestQuery);
        if ($('#target_name').val().trim() !== latestQuery) {
          return;
        }
        renderEmployeeCandidates(candidates);
      } catch (_) {
        panel.empty().addClass('ss-hidden');
      }
    }, 220);
  }

  function updateTargetFieldMode() {
    $('#nameLabel').text(isPersonMode() ? '社員' : '現場名');
    $('#target_name').attr('placeholder', isPersonMode() ? '社員名' : '現場名');
    if (!isPersonMode()) {
      clearEmployeeSelection();
      $('#targetCandidatePanel').empty().addClass('ss-hidden');
    }
  }

  function renderEditorShell(year, month, name, capacityEnabled, requiredCapacity) {
    const safeName = escapeHtml(name);
    return `
      <section class="ss-section">
        <div class="ss-section-head">
          <div>
            <h2>編集プレビュー</h2>
            <p class="ss-section-note">コメントは下のシフトの中に直接表示され、項目をクリックすると詳細を確認できます。</p>
          </div>
          <div class="ss-actions">
            <button type="button" id="saveBtn" class="btn-primary">CSV を保存</button>
          </div>
        </div>

        <div class="ss-summary-grid" style="margin-bottom: 18px;">
          <div class="ss-summary-item">
            <div class="ss-summary-label">種別</div>
            <div class="ss-summary-value">${mode === 'scene' ? '現場シフト' : '個人シフト'}</div>
          </div>
          <div class="ss-summary-item">
            <div class="ss-summary-label">${mode === 'scene' ? '現場名' : '社員'}</div>
            <div class="ss-summary-value">${safeName}</div>
          </div>
          <div class="ss-summary-item">
            <div class="ss-summary-label">年月</div>
            <div class="ss-summary-value">${year}年${month}月</div>
          </div>
          <div class="ss-summary-item">
            <div class="ss-summary-label">必要人数</div>
            <div class="ss-summary-value">${capacityEnabled ? `${requiredCapacity}人` : '未設定'}</div>
          </div>
        </div>

        <div class="weekday-header">
          <div>月</div>
          <div>火</div>
          <div>水</div>
          <div>木</div>
          <div>金</div>
          <div>土</div>
          <div>日</div>
        </div>
        <div id="shiftGrid" class="calendar-grid"></div>
      </section>
    `;
  }

  function startEditor() {
    const year = parseInt($('#year').val(), 10);
    const month = parseInt($('#month').val(), 10);
    const name = $('#target_name').val().trim();
    const capacityEnabled = $('#enableCapacity').is(':checked');
    const requiredCapacity = parseInt($('#capacity').val(), 10) || 0;

    if (!year || !month || !name) {
      alert('年、月、名前を入力してください');
      return;
    }
    if (month < 1 || month > 12) {
      alert('月は1から12で入力してください');
      return;
    }
    if (capacityEnabled && requiredCapacity <= 0) {
      alert('必要人数を設定する場合は、1以上の数値を入力してください');
      return;
    }

    ShifterSync.setState('mode', mode);
    ShifterSync.setState('name', name);
    ShifterSync.setState('targetEmployeeNumber', isPersonMode() ? $('#target_employee_number').val().trim() : '');
    ShifterSync.setState('capacityEnabled', capacityEnabled);
    ShifterSync.setState('requiredCapacity', requiredCapacity);

    $('#inputArea')
      .html(renderEditorShell(year, month, name, capacityEnabled, requiredCapacity))
      .stop(true, true)
      .fadeIn(180);

    ShifterSync.buildCalendar(year, month, mode);

    $('html, body').animate({
      scrollTop: $('#inputArea').offset().top - 20
    }, 280);
  }

  function downloadCsv(filename) {
    const csv = ShifterSync.buildCSV();
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = $('<a>')
      .attr('href', url)
      .attr('download', filename)
      .appendTo('body');

    link[0].click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  $('#mode').on('change', function() {
    mode = $(this).val();
    updateTargetFieldMode();
  });

  $('#target_name').on('input', function() {
    scheduleEmployeeSearch();
  });

  $(document).on('click', '.target-employee-candidate', function() {
    const name = $(this).attr('data-employee-name') || '';
    const employeeNumber = $(this).attr('data-employee-number') || '';
    $('#target_name').val(name).attr('data-selected-employee-name', name);
    $('#target_employee_number').val(employeeNumber);
    $('#targetCandidatePanel').empty().addClass('ss-hidden');
    $('#targetSelectedNote').text(employeeNumber ? `選択中: ${employeeNumber}` : '');
  });

  $('#enableCapacity').on('change', function() {
    const enabled = $(this).is(':checked');
    $('#capacityInputGroup').stop(true, true)[enabled ? 'slideDown' : 'slideUp'](120);
    $('#capacity').prop('required', enabled);
    if (!enabled) {
      $('#capacity').val('');
    }
  });

  $('#startBtn').on('click', startEditor);

  $('#shiftMetaForm').on('keydown', 'input, select', function(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      startEditor();
    }
  });

  $(document).on('click', '#saveBtn', function() {
    const modeValue = ShifterSync.getState('mode');
    const year = ShifterSync.getState('year');
    const month = ShifterSync.getState('month');
    const name = ShifterSync.getState('name');
    downloadCsv(`${modeValue},${year},${month},${name}.csv`);
  });

  const now = new Date();
  $('#year').val(now.getFullYear());
  $('#month').val(now.getMonth() + 1);
  updateTargetFieldMode();
});
