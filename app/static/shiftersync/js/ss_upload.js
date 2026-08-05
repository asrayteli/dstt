$(document).ready(function() {
  const commentRowPrefix = '#comment';
  const secondOptionRowPrefix = '#second_option';
  const employeeNameRowPrefix = '#employee_name';
  const employeeNumberRowPrefix = '#employee_number';
  const projectEmployeeNumberRowPrefix = '#project_employee_number';
  const siteRowIdRowPrefix = '#site_row_id';
  const siteIdRowPrefix = '#site_id';
  const siteNameRowPrefix = '#site_name';
  const siteBranchRowIdRowPrefix = '#site_branch_row_id';
  const siteBranchRowPrefix = '#site_branch';
  const showAttendanceTimeRowPrefix = '#show_attendance_time';
  const showReportTimeRowPrefix = '#show_report_time';
  const attendanceTimesRowPrefix = '#attendance_times';
  const reportTimeRowPrefix = '#report_time';
  const metadataFieldByPrefix = {
    [secondOptionRowPrefix]: 'second_option',
    [employeeNameRowPrefix]: 'employee_name',
    [employeeNumberRowPrefix]: 'employee_number',
    [siteRowIdRowPrefix]: 'site_row_id',
    [siteIdRowPrefix]: 'site_id',
    [siteNameRowPrefix]: 'site_name',
    [siteBranchRowIdRowPrefix]: 'site_branch_row_id',
    [siteBranchRowPrefix]: 'site_branch',
    [showAttendanceTimeRowPrefix]: 'show_attendance_time',
    [showReportTimeRowPrefix]: 'show_report_time',
    [attendanceTimesRowPrefix]: 'attendance_times',
    [reportTimeRowPrefix]: 'report_time',
    '#substitute_request_type': 'substitute_request_type',
    '#substitute_helper_employee_name': 'substitute_helper_employee_name',
    '#substitute_helper_employee_number': 'substitute_helper_employee_number',
    '#substitute_helper_site_row_id': 'substitute_helper_site_row_id',
    '#substitute_helper_site_id': 'substitute_helper_site_id',
    '#substitute_helper_site_name': 'substitute_helper_site_name',
    '#substitute_resolved': 'substitute_resolved',
    '#substitute_requester_user_id': 'substitute_requester_user_id',
    '#substitute_requester_name': 'substitute_requester_name',
    '#substitute_requested_at': 'substitute_requested_at',
    '#substitute_helper_user_id': 'substitute_helper_user_id',
    '#substitute_helper_name': 'substitute_helper_name',
    '#substitute_helped_at': 'substitute_helped_at',
    '#substitute_unassigned_helper': 'substitute_unassigned_helper',
    '#substitute_source_project_id': 'substitute_source_project_id',
    '#substitute_source_project_title': 'substitute_source_project_title',
    '#substitute_source_project_mode': 'substitute_source_project_mode',
    '#substitute_source_month_key': 'substitute_source_month_key',
    '#substitute_source_day': 'substitute_source_day',
    '#substitute_source_entry_id': 'substitute_source_entry_id'
  };
  const booleanMetadataFields = new Set([
    'substitute_resolved',
    'substitute_unassigned_helper',
    'show_attendance_time',
    'show_report_time'
  ]);
  // 勤怠時間は "10:00~13:00 / 14:00~19:00" の1セル表現から区間配列へ戻す。
  const listMetadataFields = new Set(['attendance_times']);
  function parseAttendanceTimesCell(value) {
    return String(value || '')
      .split(/[,\n、/]/)
      .map((chunk) => chunk.trim())
      .filter(Boolean)
      .map((chunk) => {
        const [start, end] = chunk.split('~');
        return { start: String(start || '').trim(), end: String(end || '').trim() };
      })
      .filter((range) => range.start || range.end);
  }
  const supportedModes = ['scene', 'person', 'master', 'substitute'];
  const modeLabels = {
    scene: '現場シフト',
    person: '個人シフト',
    master: 'マスターシフト',
    substitute: '代務シフト'
  };

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function decodeCsvBuffer(buffer) {
    const encodings = ['utf-8', 'shift_jis'];
    for (const encoding of encodings) {
      try {
        const decoded = new TextDecoder(encoding, { fatal: true }).decode(buffer);
        if (decoded) {
          return decoded;
        }
      } catch (_) {
        // Try the next encoding.
      }
    }
    return new TextDecoder('utf-8').decode(buffer);
  }

  function parseCsvRows(text) {
    const rows = [];
    let row = [];
    let cell = '';
    let inQuotes = false;

    for (let i = 0; i < text.length; i += 1) {
      const char = text[i];
      const next = text[i + 1];

      if (char === '"') {
        if (inQuotes && next === '"') {
          cell += '"';
          i += 1;
        } else {
          inQuotes = !inQuotes;
        }
        continue;
      }

      if (char === ',' && !inQuotes) {
        row.push(cell);
        cell = '';
        continue;
      }

      if ((char === '\n' || char === '\r') && !inQuotes) {
        if (char === '\r' && next === '\n') {
          i += 1;
        }
        row.push(cell);
        const normalized = row.map((item) => String(item || '').trim());
        if (normalized.some((item) => item.length > 0)) {
          rows.push(normalized);
        }
        row = [];
        cell = '';
        continue;
      }

      cell += char;
    }

    row.push(cell);
    const normalized = row.map((item) => String(item || '').trim());
    if (normalized.some((item) => item.length > 0)) {
      rows.push(normalized);
    }

    return rows;
  }

  function parseEntry(entry) {
    return {
      id: `upload-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      value: String(entry || '').trim(),
      comment: '',
      second_option: '',
      employee_name: '',
      employee_number: '',
      site_row_id: '',
      site_id: '',
      site_name: '',
      site_branch_row_id: '',
      site_branch: '',
      show_attendance_time: false,
      show_report_time: false,
      attendance_times: [],
      report_time: ''
    };
  }

  function parseCsvPayload(csvText) {
    const rows = parseCsvRows(csvText);
    if (rows.length < 2 || rows[0].length < 4) {
      throw new Error('CSV の形式が正しくありません');
    }

    const header = rows[0];
    const mode = header[0].trim();
    const year = parseInt(header[1], 10);
    const month = parseInt(header[2], 10);
    const name = header[3].trim();
    const requiredCapacity = header.length >= 5 ? parseInt(header[4], 10) || 0 : 0;
    const entriesPerDay = {};
    const metadataRows = [];
    let targetEmployeeNumber = '';

    if (!supportedModes.includes(mode)) {
      throw new Error('mode は scene / person / master / substitute に対応です');
    }
    if (!year || year < 1900 || year > 2100) {
      throw new Error('年は 1900 から 2100 の範囲で指定してください');
    }
    if (!month || month < 1 || month > 12) {
      throw new Error('月は 1 から 12 の範囲で指定してください');
    }
    if (!name) {
      throw new Error('対象名が空です');
    }

    rows.slice(2).forEach((row) => {
      if (!row.length) {
        return;
      }
      if (/^\d+$/.test(row[0])) {
        entriesPerDay[row[0]] = row.slice(1).filter(Boolean).map((entry) => parseEntry(entry));
        return;
      }
      if (row[0] === commentRowPrefix) {
        metadataRows.push(row);
        return;
      }
      if (row[0] === projectEmployeeNumberRowPrefix) {
        targetEmployeeNumber = row[1] || '';
        return;
      }
      if (metadataFieldByPrefix[row[0]]) {
        metadataRows.push(row);
      }
    });

    metadataRows.forEach((row) => {
      if (row.length < 4) {
        return;
      }
      const day = String(parseInt(row[1], 10));
      const index = parseInt(row[2], 10);
      if (!entriesPerDay[day] || Number.isNaN(index) || !entriesPerDay[day][index]) {
        return;
      }
      const field = row[0] === commentRowPrefix ? 'comment' : metadataFieldByPrefix[row[0]];
      if (!field) {
        return;
      }
      if (booleanMetadataFields.has(field)) {
        entriesPerDay[day][index][field] = ['1', 'true', 'TRUE'].includes(String(row[3] || '').trim());
      } else if (listMetadataFields.has(field)) {
        entriesPerDay[day][index][field] = parseAttendanceTimesCell(row[3]);
      } else {
        entriesPerDay[day][index][field] = row[3] || '';
      }
    });

    return {
      mode,
      year,
      month,
      name,
      targetEmployeeNumber,
      capacityEnabled: requiredCapacity > 0,
      requiredCapacity,
      entriesPerDay
    };
  }

  function renderEditorShell(payload) {
    const safeName = escapeHtml(payload.name);
    return `
      <section class="ss-section">
        <div class="ss-section-head">
          <div>
            <h2>編集画面</h2>
            <p class="ss-section-note">項目をクリックすると、コメントを含む詳細を確認できます。</p>
          </div>
          <div class="ss-actions">
            <button type="button" id="saveBtn" class="btn-primary">CSV を保存</button>
          </div>
        </div>

        <div class="ss-summary-grid" style="margin-bottom: 18px;">
          <div class="ss-summary-item">
            <div class="ss-summary-label">種別</div>
            <div class="ss-summary-value">${escapeHtml(modeLabels[payload.mode] || payload.mode)}</div>
          </div>
          <div class="ss-summary-item">
            <div class="ss-summary-label">対象</div>
            <div class="ss-summary-value">${safeName}</div>
          </div>
          <div class="ss-summary-item">
            <div class="ss-summary-label">年月</div>
            <div class="ss-summary-value">${payload.year}年${payload.month}月</div>
          </div>
          <div class="ss-summary-item">
            <div class="ss-summary-label">必要人数</div>
            <div class="ss-summary-value">${payload.capacityEnabled ? `${payload.requiredCapacity}人` : '未設定'}</div>
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

  $('#uploadBtn').on('click', function() {
    const fileInput = $('#shiftFile')[0];
    if (!fileInput.files.length) {
      alert('CSV ファイルを選択してください');
      return;
    }

    const reader = new FileReader();
    reader.onload = function(event) {
      try {
        const payload = parseCsvPayload(decodeCsvBuffer(event.target.result));
        ShifterSync.setState('mode', payload.mode);
        ShifterSync.setState('name', payload.name);
        ShifterSync.setState('targetEmployeeNumber', payload.targetEmployeeNumber || '');
        ShifterSync.setState('capacityEnabled', payload.capacityEnabled);
        ShifterSync.setState('requiredCapacity', payload.requiredCapacity);

        $('#inputArea')
          .html(renderEditorShell(payload))
          .stop(true, true)
          .fadeIn(180);

        ShifterSync.buildCalendar(payload.year, payload.month, payload.mode, payload.entriesPerDay);

        $('html, body').animate({
          scrollTop: $('#inputArea').offset().top - 20
        }, 280);
      } catch (error) {
        alert(error.message || 'CSV の読み込みに失敗しました');
      }
    };

    reader.onerror = function() {
      alert('CSV の読み込みに失敗しました');
    };

    reader.readAsArrayBuffer(fileInput.files[0]);
  });

  $(document).on('click', '#saveBtn', function() {
    const mode = ShifterSync.getState('mode');
    const year = ShifterSync.getState('year');
    const month = ShifterSync.getState('month');
    const name = ShifterSync.getState('name');
    downloadCsv(`${mode},${year},${month},${name}_edited.csv`);
  });
});
