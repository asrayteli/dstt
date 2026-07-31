(function(root) {
  'use strict';

  const Calc = root.CloudShiftLargeCalc;
  const WEEKDAY_LABELS = ['日', '月', '火', '水', '木', '金', '土'];
  const HOLIDAY_CLASS_LABELS = { legal: '法定休日', scheduled: '所定休日', '': '所定労働日' };
  const HOLIDAY_OVERRIDE_LABELS = { legal: '法定休日にする', scheduled: '所定休日にする', none: '所定労働日にする' };
  const state = {
    host: null, context: null, entries: {}, meta: {}, config: {}, result: null, annual: null,
    tab: 'grid', personalMemberId: '', clipboard: null, focus: { day: 1, member: 0 }, typeBuffer: '', typeTimer: null, showBaseline: false,
    dragAssignment: null, mouseDrag: null, suppressCellClick: false
  };

  function clone(value) { return JSON.parse(JSON.stringify(value == null ? {} : value)); }
  function esc(value) { return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
  function activeMembers() { return (state.config.members || []).filter((member) => member.active !== false); }
  function memberFor(memberId) { return (state.config.members || []).find((member) => String(member.id) === String(memberId)) || null; }
  function memberColumnType(member) {
    const explicit = String((member || {}).column_type || (member || {}).columnType || '').toLowerCase();
    if (explicit === 'substitute' || explicit === 'regular') return explicit;
    return /^sub_/i.test(String((member || {}).id || '')) ? 'substitute' : 'regular';
  }
  function isSubstituteMember(memberId) { return memberColumnType(memberFor(memberId)) === 'substitute'; }
  function activeCodes() { return (state.config.codes || []).filter((code) => code.active !== false); }
  function days() { return new Date(Number(state.context.month.year), Number(state.context.month.month), 0).getDate(); }
  function entryId(day, memberId) { return `ce_${state.context.month.year}${String(state.context.month.month).padStart(2, '0')}${String(day).padStart(2, '0')}_${String(memberId).replace(/[^A-Za-z0-9_-]/g, '_')}`; }
  function entryFor(day, memberId) { return (state.entries[String(day)] || []).find((entry) => String(entry.member_id) === String(memberId)) || null; }
  function assignmentsFor(entry) {
    if (entry && Array.isArray(entry.assignments) && entry.assignments.length) return entry.assignments;
    return entry && entry.value ? [{ id: `legacy_${String(entry.value)}`, code_key: entry.value, source_type: 'local', source_project_id: '', source_project_title: '', source_site_id: '' }] : [];
  }
  function assignmentId(prefix) { return `ca_${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`; }
  function assignmentLabel(item) {
    if (item && item.source_type !== 'local') {
      const site = String(item.source_site_name || item.source_project_title || '他現場');
      const custom = String(item.custom_label || '');
      return custom ? `${site}・${custom}` : site;
    }
    return String((item && item.code_key) || '');
  }
  function entryLabel(entry) { return assignmentsFor(entry).map(assignmentLabel).join(' / '); }
  function durationText(value) { const minutes = Number(value); return value == null || value === '' || !Number.isFinite(minutes) ? '' : `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}`; }
  function durationMinutes(value) { const match = String(value || '').trim().match(/^(\d{1,4}):([0-5]\d)$/); return match ? Number(match[1]) * 60 + Number(match[2]) : null; }
  function help(text) { return `<span class="cloud-large-help" title="${esc(text)}" aria-label="${esc(text)}" tabindex="0">?</span>`; }
  function setEntry(day, memberId, patch) {
    const key = String(day); const member = memberFor(memberId);
    const list = (state.entries[key] || []).filter((entry) => String(entry.member_id) !== String(memberId));
    const current = entryFor(day, memberId) || { id: entryId(day, memberId), member_id: memberId, value: '', assignments: [], holiday_kind: '', time_override: null, bind_override_minutes: null, break_override_minutes: null, comment: '', employee_number: member ? member.employee_number || '' : '', employee_name: member ? member.employee_name || '' : '' };
    const next = Object.assign({}, current, patch || {});
    if (member && memberColumnType(member) !== 'substitute') {
      next.employee_number = member.employee_number || '';
      next.employee_name = member.employee_name || member.display_name || '';
    }
    next.assignments = assignmentsFor(next).map((item, index) => Object.assign({ id: assignmentId(`${day}_${memberId}_${index}`), source_type: 'local', source_project_id: '', source_project_title: '', source_site_row_id: '', source_site_id: '', source_site_name: '', option_key: '', second_option: '', custom_label: '' }, item));
    next.value = next.assignments[0] ? next.assignments[0].code_key : '';
    if (next.assignments.length || next.holiday_kind || next.comment) list.push(next);
    state.entries[key] = list;
    recalculate();
    if (state.context.onChange) state.context.onChange();
  }
  function clearEntry(day, memberId) { setEntry(day, memberId, { value: '', assignments: [], holiday_kind: '', time_override: null, bind_override_minutes: null, break_override_minutes: null, comment: '' }); }
  function codeMap() { const result = {}; activeCodes().forEach((code) => { result[String(code.key).toLocaleLowerCase()] = code; }); return result; }
  function codeFor(entry) { return entry ? codeMap()[String(entry.value || '').toLocaleLowerCase()] : null; }
  function codeForAssignment(item) { return item ? codeMap()[String(item.code_key || '').toLocaleLowerCase()] : null; }
  function assignmentChipsHtml(entry, day, memberId) {
    const canDrag = !!state.context.editable && !state.showBaseline;
    return assignmentsFor(entry).map((item, index) => {
      const code = codeForAssignment(item); const external = item.source_type !== 'local'; const synced = item.source_type === 'sync';
      const shiftLabel = external ? assignmentLabel(item) : (code ? code.label || code.key : item.code_key);
      const employeeName = isSubstituteMember(memberId) ? String((entry || {}).employee_name || '') : '';
      const label = employeeName ? `${employeeName}・${shiftLabel}` : shiftLabel;
      const color = external ? '#dbeafe' : (code && code.color ? code.color : '#e3f2fd');
      const itemEditable = canDrag && !synced;
      const dragAttrs = `${itemEditable ? ' draggable="true"' : ''} data-large-shift-day="${day}" data-large-shift-member="${esc(memberId)}" data-large-assignment-index="${index}" data-large-assignment-id="${esc(item.id || '')}"`;
      const hint = synced ? `${label}。他のシフト帳から同期されています` : itemEditable ? `${label}。クリックで編集、ドラッグで移動・並べ替え` : label;
      return `<div class="cloud-large-job-chip${external ? ' is-external' : ''}${synced ? ' is-synced' : ''}" style="--large-code-color:${esc(color)}" title="${esc(hint)}"${dragAttrs}>${itemEditable ? '<span class="cloud-large-job-drag" role="button" aria-label="ドラッグで移動・並べ替え" title="ドラッグで移動・並べ替え">↕</span>' : ''}<span class="cloud-large-job-main"><span class="cloud-large-job-title">${esc(label)}</span></span>${synced ? '<span class="cloud-large-job-sync">同期</span>' : ''}${itemEditable ? '<button type="button" class="cloud-large-job-delete" aria-label="このシフトを削除" title="このシフトを削除">削除</button>' : ''}</div>`;
    }).join('');
  }
  function transferableEntry(entry) {
    const next = clone(entry || {});
    delete next.id; delete next.member_id; delete next.employee_number; delete next.employee_name;
    return next;
  }
  function recalculate() {
    state.result = Calc.calculate({ config: state.config, year: state.context.month.year, month: state.context.month.month, entries_per_day: state.entries, meta_data: state.meta, holidays: root.SHIFTERSYNC_HOLIDAYS || [] });
  }
  function personResult(memberId) { return (state.result.people || []).find((person) => String(person.person_id) === String(memberId)); }
  function baselineEntry(day, memberId) { return (((state.meta.baseline || {}).entries_per_day || {})[String(day)] || []).find((entry) => String(entry.member_id) === String(memberId)) || null; }
  function comparisonLabel(entry) { if (!entry) return '空'; const parts = [entryLabel(entry) || '空']; if (entry.holiday_kind) parts.push(HOLIDAY_OVERRIDE_LABELS[entry.holiday_kind] || entry.holiday_kind); if (entry.time_override) parts.push(`${entry.time_override.start}-${entry.time_override.end}`); if (entry.break_override_minutes != null) parts.push(`休憩${durationText(entry.break_override_minutes)}`); if (entry.bind_override_minutes != null) parts.push(`基準${durationText(entry.bind_override_minutes)}`); return parts.join(' / '); }
  function comparisonValue(entry, key) {
    const row = entry || {};
    if (key === 'value' || key === 'holiday_kind') return String(row[key] || '');
    if (key === 'time_override') {
      const range = row.time_override;
      return range && range.start && range.end ? { start: String(range.start), end: String(range.end) } : null;
    }
    if (key === 'assignments') return assignmentsFor(row).map((item) => ({ code_key: String(item.code_key || ''), source_type: String(item.source_type || 'local'), source_project_id: String(item.source_project_id || ''), source_project_title: String(item.source_project_title || ''), source_site_row_id: String(item.source_site_row_id || ''), source_site_id: String(item.source_site_id || ''), source_site_name: String(item.source_site_name || ''), option_key: String(item.option_key || ''), second_option: String(item.second_option || ''), custom_label: String(item.custom_label || '') }));
    return row[key] == null || row[key] === '' ? null : Number(row[key]);
  }
  function changed(day, memberId, entry) {
    const baseline = (((state.meta || {}).baseline || {}).entries_per_day || {})[String(day)] || [];
    const previous = baseline.find((item) => String(item.member_id) === String(memberId)) || {};
    const current = entry || {};
    return ['value', 'assignments', 'holiday_kind', 'time_override', 'bind_override_minutes', 'break_override_minutes'].some((key) => JSON.stringify(comparisonValue(previous, key)) !== JSON.stringify(comparisonValue(current, key)));
  }
  function leaveCountsLabel(counts) {
    const labels = { legal_rest: '法定休', scheduled_rest: '所定休', scheduled_rest_requested: '希望休', rest: '休み', rest_requested: '希望休', substitute_rest: '振休', substitute_rest_requested: '希望振休', paid: '有休', paid_requested: '希望有休', special: '特休', special_requested: '希望特休', other: 'その他', other_requested: '希望その他', empty: '未入力' };
    return Object.entries(counts || {}).filter(([, count]) => count).map(([key, count]) => `${labels[key] || key}:${count}`).join(' / ') || '—';
  }
  function dayType(day) { return Calc.dayType(Number(state.context.month.year), Number(state.context.month.month), Number(day), (state.meta || {}).day_types || {}, root.SHIFTERSYNC_HOLIDAYS || []); }
  function weekdayLabel(day) { return WEEKDAY_LABELS[new Date(Number(state.context.month.year), Number(state.context.month.month) - 1, day).getDay()]; }
  function holidayRuleFor(memberId) { return Calc.holidayRule(memberFor(memberId) || {}, state.config.settings || {}); }
  // シフト表・PDF・集計で共通に使う「その日・その人の休日区分」。
  function holidayKindFor(day, memberId, entry) {
    return Calc.effectiveHolidayKind(holidayRuleFor(memberId), Number(state.context.month.year), Number(state.context.month.month), Number(day), entry === undefined ? entryFor(day, memberId) : entry);
  }
  function holidayRuleSummary(rule) {
    const weekdays = (list) => (list || []).map((index) => WEEKDAY_LABELS[index]).join('・');
    const parts = [];
    const legal = weekdays(rule.legal_weekdays); const scheduled = weekdays(rule.scheduled_weekdays);
    parts.push(`法定休日: ${legal || '曜日指定なし'}${(rule.legal_dates || []).length ? ` +指定日${rule.legal_dates.length}件` : ''}`);
    parts.push(`所定休日: ${scheduled || '曜日指定なし'}${(rule.scheduled_dates || []).length ? ` +指定日${rule.scheduled_dates.length}件` : ''}`);
    return parts.join(' / ');
  }
  function flash(message, type) { if (state.context.showFlash) state.context.showFlash(message, type || 'info'); }

  function toolbarHtml() {
    const settings = !!state.context.canManageSettings; const baseline = !!state.context.canManageBaseline;
    return `<div class="cloud-large-toolbar">
      <div class="cloud-large-view-tabs" role="tablist">
        <button class="cloud-large-view-btn${state.tab === 'grid' ? ' is-active' : ''}" data-large-tab="grid">シフト表</button>
        <button class="cloud-large-view-btn${state.tab === 'summary' ? ' is-active' : ''}" data-large-tab="summary">集計・警告 <span>${state.result.violations.length}</span></button>
        <button class="cloud-large-view-btn${state.tab === 'personal' ? ' is-active' : ''}" data-large-tab="personal">個人ビュー</button>
      </div>
      <div class="cloud-large-toolbar-actions">
        ${settings ? '<button class="cloud-btn secondary" data-large-settings>メンバー・コード設定</button>' : ''}
        ${state.meta.baseline ? `<button class="cloud-btn ghost" data-large-baseline-view>${state.showBaseline ? '現在版に戻る' : '基準版時点を表示'}</button>` : ''}
        ${baseline ? `<button class="cloud-btn ghost" data-large-baseline>${state.meta.baseline ? '基準版を再確定' : '現在を基準版に確定'}</button>` : ''}
        ${baseline && state.meta.baseline ? '<button class="cloud-btn ghost" data-large-baseline-clear>基準版を解除</button>' : ''}
        ${state.context.page && state.context.page.type === 'owner' ? '<button class="cloud-btn ghost" data-large-conflict>重複チェック</button>' : ''}
        ${activeMembers().length > 30 ? `<span class="cloud-large-member-warning">${activeMembers().length}人：横幅が大きいため個人ビューも併用してください</span>` : ''}
      </div>
    </div>`;
  }

  function gridHtml() {
    const members = activeMembers();
    if (!members.length || !activeCodes().some((code) => code.category === 'work')) {
      return `<div class="cloud-large-empty"><div class="cloud-large-empty-icon">表</div><h3>最初にメンバーと勤務コードを登録してください</h3><p>休みコードは用意済みです。設定からメンバーと、この現場で使う勤務コードを追加すると編集を始められます。</p>${state.context.canManageSettings ? '<button class="cloud-btn primary" data-large-settings>設定を開く</button>' : ''}</div>`;
    }
    const header = members.map((member) => `<th scope="col"><button class="cloud-large-member-head" data-large-person="${esc(member.id)}"><span>${esc(member.display_name)}</span><small>${memberColumnType(member) === 'substitute' ? '代務' : (member.scheduled_bind_minutes == null ? 'レギュラー' : Calc.hhmm(member.scheduled_bind_minutes))}</small></button></th>`).join('');
    const rows = [];
    for (let day = 1; day <= days(); day += 1) {
      const type = dayType(day); const note = String(((state.meta || {}).day_notes || {})[String(day)] || '');
      const rowUnits = Math.max(1, ...members.map((member) => assignmentsFor(state.showBaseline ? baselineEntry(day, member.id) : entryFor(day, member.id)).length));
      const cells = members.map((member, memberIndex) => {
        const currentEntry = entryFor(day, member.id); const previousEntry = baselineEntry(day, member.id);
        const entry = state.showBaseline ? previousEntry : currentEntry; const person = personResult(member.id);
        const dayResult = person ? person.days[day - 1] : null;
        const warningCount = (person ? person.violations : []).filter((item) => item.day === day).length;
        const classes = ['cloud-large-cell'];
        const holidayKind = holidayKindFor(day, member.id, entry);
        if (day === state.focus.day && memberIndex === state.focus.member) classes.push('is-selected');
        if (holidayKind) classes.push(`is-${holidayKind}`);
        if (entry && entry.comment) classes.push('has-comment');
        if (entry && (entry.time_override || entry.break_override_minutes != null)) classes.push('has-time');
        const isChanged = state.meta.baseline && changed(day, member.id, currentEntry);
        if (!state.showBaseline && isChanged) classes.push('is-changed');
        if (warningCount) classes.push('has-warning');
        const title = [isChanged ? `基準: ${comparisonLabel(previousEntry)} → 現在: ${comparisonLabel(currentEntry)}` : '', holidayKind ? HOLIDAY_CLASS_LABELS[holidayKind] : '', entry && entry.comment, warningCount ? `${warningCount}件の警告` : '', dayResult && dayResult.bind_minutes ? `拘束 ${Calc.hhmm(dayResult.bind_minutes)}` : ''].filter(Boolean).join(' / ');
        return `<td><div role="button" class="${classes.join(' ')}" data-large-day="${day}" data-large-member="${esc(member.id)}" data-large-member-index="${memberIndex}" tabindex="${day === state.focus.day && memberIndex === state.focus.member ? '0' : '-1'}" title="${esc(title)}"><div class="cloud-large-code">${assignmentChipsHtml(entry, day, member.id)}</div>${entry && (entry.time_override || entry.break_override_minutes != null) ? '<span class="cloud-large-icon" aria-label="時間上書き">◷</span>' : ''}${entry && entry.comment ? '<span class="cloud-large-comment-dot" aria-label="コメントあり"></span>' : ''}${warningCount ? `<span class="cloud-large-warning-count">${warningCount}</span>` : ''}</div></td>`;
      }).join('');
      rows.push(`<tr class="is-${type}" style="--large-row-units:${rowUnits}"><th scope="row"><button class="cloud-large-day-head" data-large-day-settings="${day}"><span>${state.context.month.month}/${day} (${weekdayLabel(day)})</span><small>${{ weekday: '平日', saturday: '土曜', holiday: '日祝' }[type]}${((state.meta.day_types || {})[String(day)]) ? '・変更' : ''}</small>${note ? `<em title="${esc(note)}">${esc(note)}</em>` : ''}</button></th>${cells}</tr>`);
    }
    const footerRows = [
      ['拘束計', (totals) => totals.bind_total_hhmm || '0:00', 'その月の始業から終業まで（休憩を含む）の合計です'],
      ['労働計', (totals) => `${totals.work_total_hhmm || '0:00'}${totals.paid_leave_credit_minutes ? ` ＋${totals.paid_leave_credit_hhmm}` : ''}`, 'ダイヤごとに決められた労働時間の合計です。＋以降は有休・希望有休を所定労働時間として換算した時間で、左の労働時間には含めていません'],
      ['①所定内残業', (totals) => totals.payroll_overtime_hhmm || '0:00', '所定労働日の残業時間（その日の拘束－所定拘束）の合計です'],
      ['②所定休日労働', (totals) => totals.scheduled_holiday_work_hhmm || '0:00', '所定休日に指定された日に働いた労働時間です'],
      ['③法定休日労働', (totals) => totals.legal_holiday_work_hhmm || '0:00', '法定休日に指定された日に働いた労働時間です'],
    ].map(([label, value, hint]) => `<tr><th title="${esc(hint)}">${esc(label)}</th>${members.map((member) => `<td>${esc(value(totalsFor(member.id)))}</td>`).join('')}</tr>`).join('');
    return `<div class="cloud-large-grid-scroll"><table class="cloud-large-grid" role="grid"><thead><tr><th class="cloud-large-date-corner">日付・ダイヤ</th>${header}</tr></thead><tbody>${rows.join('')}</tbody><tfoot>${footerRows}</tfoot></table></div>${agreementHtml(members)}<p class="cloud-large-key-help">シフトをクリック: 編集　セル余白をクリック: 選択　シフトをドラッグ: 1件ずつ移動　Delete: セルをクリア　Ctrl+C / Ctrl+V: コピー・貼付</p>`;
  }

  function totalsFor(memberId) { return (personResult(memberId) || {}).totals || {}; }
  function annualTotalsFor(memberId) { return ((state.annual || {}).people || []).find((person) => String(person.person_id) === String(memberId)) || null; }
  function annualRangeLabel() {
    const fiscalYear = (state.annual || {}).fiscal_year;
    return fiscalYear == null ? '' : `${fiscalYear}年4月〜${Number(fiscalYear) + 1}年3月`;
  }
  function agreementHtml(members) {
    const annualReady = !!(state.annual && (state.annual.people || []).length);
    const months = ((state.annual || {}).months || []).length;
    const rows = [
      ['36協定対象', '①+②+③', (memberId) => totalsFor(memberId).agreement36_hhmm || '0:00', '時間外労働（所定内残業＋所定休日労働）と法定休日労働の合計です'],
      ['特別条項対象', '①+②', (memberId) => totalsFor(memberId).special_clause_hhmm || '0:00', '法定休日労働を除いた時間外労働の合計です'],
      ['960対象（当月）', '①+②', (memberId) => totalsFor(memberId).annual960_hhmm || '0:00', '年間960時間の対象となる当月分です'],
      [`960対象（年度累計）`, annualReady ? `${annualRangeLabel()} / ${months}か月` : '未計算', (memberId) => { const annual = annualTotalsFor(memberId); return annual ? annual.annual960_hhmm || '0:00' : '—'; }, '同じシフト帳の4月〜翌年3月の保存済みの月をサーバーで合計した値です'],
    ].map(([label, formula, value, hint]) => `<tr><th title="${esc(hint)}">${esc(label)}<small>${esc(formula)}</small></th>${members.map((member) => `<td>${esc(value(member.id))}</td>`).join('')}</tr>`).join('');
    return `<section class="cloud-large-agreement"><div class="cloud-large-agreement-head"><h3>36協定・特別条項・960時間の対象時間</h3><p>①所定内残業（所定労働日の残業） ②所定休日労働 ③法定休日労働。休日の区分は設定の「休日ルール」に従います。${annualReady ? '年度累計は保存済みの月をサーバーで合計した値です（編集中の内容は保存後に反映されます）。' : '年度累計はサーバーで計算中です。表示されない場合は「集計・警告」タブを開いてください。'}</p></div><div class="cloud-large-agreement-scroll"><table><thead><tr><th>集計</th>${members.map((member) => `<th>${esc(member.display_name)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  function summaryHtml() {
    const rows = activeMembers().map((member) => {
      const person = personResult(member.id) || {}; const totals = person.totals || {};
      const leaves = leaveCountsLabel(totals.leave_counts);
      const annual = annualTotalsFor(member.id);
      return `<tr><th><button data-large-person="${esc(member.id)}">${esc(member.display_name)}</button></th><td>${totals.bind_total_hhmm || '0:00'}</td><td>${totals.work_total_hhmm || '0:00'}${totals.paid_leave_credit_minutes ? ` <b class="cloud-large-plus">＋${esc(totals.paid_leave_credit_hhmm)}</b>` : ''}</td><td>${totals.payroll_overtime_hhmm || '0:00'}</td><td>${totals.scheduled_holiday_work_hhmm || '0:00'}</td><td>${totals.legal_holiday_work_hhmm || '0:00'}</td><td>${totals.agreement36_hhmm || '0:00'}</td><td>${totals.special_clause_hhmm || '0:00'}</td><td>${totals.annual960_hhmm || '0:00'}</td><td>${annual ? esc(annual.annual960_hhmm || '0:00') : '—'}</td><td>${totals.anei_excess_hhmm || '0:00'}</td><td>${totals.work_days || 0}</td><td>${esc(leaves)}</td><td>${totals.max_consecutive_work_days || 0}</td><td><span class="cloud-large-severity ${person.violations && person.violations.length ? 'warning' : 'ok'}">${(person.violations || []).length}</span></td></tr>`;
    }).join('');
    const warnings = (state.result.violations || []).map((item) => `<button class="cloud-large-warning-row is-${esc(item.severity)}" data-large-jump="${item.day || ''}" data-large-jump-member="${esc(item.person_id)}"><strong>${esc((personResult(item.person_id) || {}).label || '')}${item.day ? ` / ${item.day}日` : ''}</strong><span>${esc(item.message || item.code)}</span><code>${esc(item.code)}</code></button>`).join('') || '<p class="cloud-large-ok">現在の設定で警告はありません。</p>';
    const changes = [];
    if (state.meta.baseline) activeMembers().forEach((member) => { for (let day = 1; day <= days(); day += 1) { const current = entryFor(day, member.id); const previous = baselineEntry(day, member.id); if (changed(day, member.id, current)) changes.push(`<button class="cloud-large-warning-row" data-large-jump="${day}" data-large-jump-member="${esc(member.id)}"><strong>${esc(member.display_name)} / ${day}日</strong><span>${esc(comparisonLabel(previous))} → ${esc(comparisonLabel(current))}</span><code>変更</code></button>`); } });
    const changesHtml = changes.join('') || '<p class="cloud-large-ok">基準版からの変更はありません。</p>';
    const annualNote = state.annual && (state.annual.people || []).length
      ? `960対象の年度累計は ${esc(annualRangeLabel())} のうち保存済みの ${((state.annual || {}).months || []).length}か月分です。`
      : '960対象の年度累計はサーバー集計の取得後に表示されます。';
    return `<div class="cloud-large-summary"><p class="cloud-panel-note">労働の「＋」は有休・希望有休を所定労働時間として換算した時間で、労働時間そのものには含めていません。${annualNote}</p><div class="cloud-large-summary-table-wrap"><table><thead><tr><th>メンバー</th><th>拘束</th><th>労働</th><th title="所定労働日の残業">①所定内残業</th><th>②所定休日労働</th><th>③法定休日労働</th><th title="①+②+③">36協定</th><th title="①+②">特別条項</th><th title="①+②の当月分">960対象</th><th title="4月〜翌年3月の累計">960年度累計</th><th>安衛長時間</th><th>勤務日数</th><th>休み内訳</th><th>最大連勤</th><th>警告</th></tr></thead><tbody>${rows}</tbody></table></div><section><h3>警告一覧</h3><div class="cloud-large-warning-list">${warnings}</div></section>${state.meta.baseline ? `<section><h3>基準版からの変更（${changes.length}件）</h3><div class="cloud-large-warning-list">${changesHtml}</div></section>` : ''}</div>`;
  }

  function personalHtml() {
    const members = activeMembers();
    if (!state.personalMemberId || !members.some((member) => String(member.id) === String(state.personalMemberId))) state.personalMemberId = members[0] ? members[0].id : '';
    const person = personResult(state.personalMemberId) || { days: [], totals: {}, violations: [] };
    const options = members.map((member) => `<option value="${esc(member.id)}"${String(member.id) === String(state.personalMemberId) ? ' selected' : ''}>${esc(member.display_name)}</option>`).join('');
    const rows = person.days.map((item) => {
      const entry = entryFor(item.day, state.personalMemberId);
      const period = item.start_minutes == null ? '—' : `${Calc.hhmm(item.start_minutes)}–${Calc.hhmm(item.end_minutes)}`;
      const category = { work: '通常勤務', scheduled_holiday_work: '所定休日勤務', legal_holiday_work: '法定休日勤務', leave: '休み', empty: '未入力', external: '他現場（時間未連携）' }[item.category] || item.category || '—';
      return `<button class="cloud-large-person-day" data-large-day="${item.day}" data-large-member="${esc(state.personalMemberId)}"><span>${state.context.month.month}/${item.day} (${weekdayLabel(item.day)})</span><strong>${esc(item.code_key || '—')}</strong><span class="cloud-large-person-metrics"><small>始終 <b>${period}</b></small><small>休憩 <b>${Calc.hhmm(item.break_minutes)}</b></small><small>拘束 <b>${Calc.hhmm(item.bind_minutes)}</b></small><small>労働 <b>${Calc.hhmm(item.work_minutes)}</b></small><small>超過 <b>${Calc.hhmm(item.overtime_minutes)}</b></small><small>区分 <b>${esc(category)}</b></small>${item.warnings && item.warnings.length ? `<small class="is-warning">${esc(item.warnings.join(' / '))}</small>` : ''}${entry && entry.comment ? `<small class="is-comment">${esc(entry.comment)}</small>` : ''}</span></button>`;
    }).join('');
    const totals = person.totals || {};
    const annual = annualTotalsFor(state.personalMemberId);
    return `<div class="cloud-large-personal"><label class="cloud-field"><span>メンバー</span><select class="cloud-input" data-large-personal-select>${options}</select></label><div class="cloud-large-person-total"><span>拘束 <b>${totals.bind_total_hhmm || '0:00'}</b></span><span>労働 <b>${totals.work_total_hhmm || '0:00'}${totals.paid_leave_credit_minutes ? ` ＋${esc(totals.paid_leave_credit_hhmm)}` : ''}</b></span><span>①所定内残業 <b>${totals.payroll_overtime_hhmm || '0:00'}</b></span><span>②所定休日労働 <b>${totals.scheduled_holiday_work_hhmm || '0:00'}</b></span><span>③法定休日労働 <b>${totals.legal_holiday_work_hhmm || '0:00'}</b></span><span>36協定 <b>${totals.agreement36_hhmm || '0:00'}</b></span><span>特別条項 <b>${totals.special_clause_hhmm || '0:00'}</b></span><span>960対象 <b>${totals.annual960_hhmm || '0:00'}</b></span><span>960年度累計 <b>${annual ? esc(annual.annual960_hhmm || '0:00') : '—'}</b></span><span>勤務日数 <b>${totals.work_days || 0}</b></span><span>最大連勤 <b>${totals.max_consecutive_work_days || 0}</b></span><span>警告 <b>${(person.violations || []).length}</b></span></div><p class="cloud-panel-note">休み内訳: ${esc(leaveCountsLabel(totals.leave_counts))}　休日ルール: ${esc(holidayRuleSummary(holidayRuleFor(state.personalMemberId)))}</p><div class="cloud-large-person-days">${rows}</div></div>`;
  }

  function render(recompute) {
    if (!state.host) return;
    if (recompute !== false) recalculate();
    const body = state.tab === 'summary' ? summaryHtml() : state.tab === 'personal' ? personalHtml() : gridHtml();
    state.host.innerHTML = `<section class="cloud-large" data-large-root>${toolbarHtml()}${body}<div data-large-modal-root></div></section>`;
    bind();
  }

  async function refreshServerResult(options) {
    const silent = !!(options && options.silent);
    // 先読みでは年度累計だけを取り込む。編集中に当月の集計をサーバー値で上書きしないため。
    const annualOnly = !!(options && options.annualOnly);
    if (!state.context.requestJson) return;
    const project = state.context.project; const month = state.context.month; const page = state.context.page || {};
    const path = page.type === 'owner'
      ? `/tools/shiftersync/cloudshift/api/project/${project.id}/month/${month.year}/${month.month}/worktime`
      : `/tools/shiftersync/cloudshift/api/public/${page.accessMode}/${page.token}/month/${month.year}/${month.month}/worktime`;
    try {
      const data = await state.context.requestJson(path);
      if (!data || !data.result) return;
      state.annual = data.result.fiscal_year_totals || null;
      if (!annualOnly) state.result = data.result;
      render(false);
    } catch (error) {
      // 起動直後の先読みは静かに諦める（月未作成などで警告を出さない）。
      if (!silent) flash(`サーバ集計を取得できませんでした: ${error.message}`, 'error');
    }
  }

  function modal(title, body, footer) {
    const rootNode = state.host.querySelector('[data-large-modal-root]');
    rootNode.innerHTML = `<div class="cloud-large-modal-backdrop" data-large-close><section class="cloud-large-modal" role="dialog" aria-modal="true"><header><h3>${esc(title)}</h3><button type="button" data-large-close aria-label="閉じる">×</button></header><div class="cloud-large-modal-body">${body}</div><footer>${footer || '<button class="cloud-btn secondary" data-large-close>閉じる</button>'}</footer></section></div>`;
    rootNode.querySelectorAll('[data-large-close]').forEach((node) => node.addEventListener('click', (event) => { if (event.target === node || node.tagName === 'BUTTON') rootNode.innerHTML = ''; }));
    return rootNode.querySelector('.cloud-large-modal');
  }

  function optionMappings() {
    return Object.assign({ A: '午前', P: '午後', E: '早番', L: '遅番', TEMP: '臨時便', M: 'マイクロ', C: '中型', O: '大型', W: 'ワゴン', V: '役員車両', N1: '1号車', N2: '2号車', N3: '3号車', N4: '4号車', N5: '5号車', SUB: '代務', TRAIN: '研修' }, state.context.optionMappings || {});
  }

  function openOtherSitePicker(onInsert) {
    const mappings = optionMappings(); const sections = Array.isArray(state.context.optionSections) ? state.context.optionSections : [
      { title: '時間帯', optionKeys: ['A','P','E','L','TEMP'] }, { title: '車両タイプ', optionKeys: ['M','C','O','W','V'] }, { title: '車番号', optionKeys: ['N1','N2','N3','N4','N5'] }
    ];
    const overlay = root.document.createElement('div'); overlay.className = 'cloud-large-modal-backdrop cloud-large-submodal-backdrop';
    overlay.innerHTML = `<section class="cloud-large-modal cloud-large-other-site-modal" role="dialog" aria-modal="true"><header><h3>他現場の仕事を追加</h3><button type="button" data-other-close aria-label="閉じる">×</button></header><div class="cloud-large-modal-body"><div class="cloud-large-site-picker-step" data-site-search-step><label class="cloud-field"><span>現場検索</span><input class="cloud-input" data-other-site-query placeholder="契約番号または現場名"></label><div class="cloud-large-site-results" data-other-site-results><span class="cloud-large-loading">現場を読み込んでいます…</span></div></div><div class="cloud-large-site-picker-step" data-site-option-step hidden></div></div><footer><button type="button" class="cloud-btn secondary" data-other-close>キャンセル</button><button type="button" class="cloud-btn primary" data-other-insert disabled>この仕事を追加</button></footer></section>`;
    root.document.body.appendChild(overlay);
    const close = () => overlay.remove(); overlay.querySelectorAll('[data-other-close]').forEach((button) => button.addEventListener('click', close)); overlay.addEventListener('click', (event) => { if (event.target === overlay) close(); });
    const query = overlay.querySelector('[data-other-site-query]'); const results = overlay.querySelector('[data-other-site-results]'); const optionStep = overlay.querySelector('[data-site-option-step]'); const insert = overlay.querySelector('[data-other-insert]');
    let selectedSite = null; let selectedOption = ''; let selectedSecondOption = ''; let timer = null;
    const optionButtons = (keys, attribute) => (keys || []).map((key) => `<button type="button" class="cloud-large-option-btn" ${attribute}="${esc(key)}">${esc(mappings[key] || key)}</button>`).join('');
    function renderOptions() {
      optionStep.hidden = false;
      optionStep.innerHTML = `<div class="cloud-large-picked-site"><strong>${esc(selectedSite.site_name || '')}</strong><span>${esc(selectedSite.site_id || '')}</span><button type="button" data-other-change-site>現場を変更</button></div><div class="cloud-large-option-sections"><div><h4>オプション</h4>${sections.map((section) => `<section><h5>${esc(section.title)}</h5><div class="cloud-large-option-buttons">${optionButtons(section.optionKeys, 'data-other-option')}</div></section>`).join('')}</div><div><h4>代務・研修</h4><section><div class="cloud-large-option-buttons">${optionButtons(['SUB','TRAIN'], 'data-other-second-option')}</div></section></div></div><label class="cloud-field"><span>シフト表に表示する文字 ${help('現場名の後ろに表示します。例: 応援、受付、巡回')}</span><input class="cloud-input" data-other-custom-label maxlength="120" placeholder="例: 応援"></label><p class="cloud-panel-note">表示は「${esc(selectedSite.site_name || '現場名')}・入力した文字」になります。オプションは既存CloudShiftとの連携情報として保存します。</p>`;
      overlay.querySelector('[data-other-change-site]').addEventListener('click', () => { selectedSite = null; optionStep.hidden = true; overlay.querySelector('[data-site-search-step]').hidden = false; insert.disabled = true; query.focus(); });
      overlay.querySelectorAll('[data-other-option]').forEach((button) => button.addEventListener('click', () => { selectedOption = selectedOption === button.dataset.otherOption ? '' : button.dataset.otherOption; overlay.querySelectorAll('[data-other-option]').forEach((item) => item.classList.toggle('is-selected', item.dataset.otherOption === selectedOption)); }));
      overlay.querySelectorAll('[data-other-second-option]').forEach((button) => button.addEventListener('click', () => { selectedSecondOption = selectedSecondOption === button.dataset.otherSecondOption ? '' : button.dataset.otherSecondOption; overlay.querySelectorAll('[data-other-second-option]').forEach((item) => item.classList.toggle('is-selected', item.dataset.otherSecondOption === selectedSecondOption)); }));
      const custom = overlay.querySelector('[data-other-custom-label]'); custom.addEventListener('input', () => { insert.disabled = !custom.value.trim(); }); custom.focus();
    }
    function renderSites(items) {
      if (!items.length) { results.innerHTML = '<p class="cloud-panel-note">候補が見つかりません。</p>'; return; }
      results.innerHTML = items.map((site, index) => `<button type="button" data-other-site-index="${index}"><strong>${esc(site.site_name || '')}</strong><span>${esc(site.site_id || '')}${site.active_branch_count != null ? ` / 有効枝番号 ${esc(site.active_branch_count)}件` : ''}</span></button>`).join('');
      results.querySelectorAll('[data-other-site-index]').forEach((button) => button.addEventListener('click', () => { selectedSite = items[Number(button.dataset.otherSiteIndex)]; overlay.querySelector('[data-site-search-step]').hidden = true; renderOptions(); }));
    }
    async function loadSites() {
      results.innerHTML = '<span class="cloud-large-loading">現場を読み込んでいます…</span>';
      try { const response = await fetch(`/tools/siteplus/api/cloudshift/sites?q=${encodeURIComponent(query.value.trim())}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } }); if (!response.ok) throw new Error('現場候補を取得できませんでした'); const payload = await response.json(); renderSites(Array.isArray(payload.sites) ? payload.sites : []); } catch (error) { results.innerHTML = `<p class="cloud-large-search-error">${esc(error.message)}</p>`; }
    }
    query.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(loadSites, 180); });
    insert.addEventListener('click', async () => {
      const custom = overlay.querySelector('[data-other-custom-label]'); if (!selectedSite || !custom || !custom.value.trim()) return;
      let sceneProject = null;
      try { const data = await state.context.requestJson('/tools/shiftersync/cloudshift/api/list'); sceneProject = (data.projects || []).find((project) => project.mode === 'scene' && (String((project.site || {}).site_row_id || '') === String(selectedSite.id || selectedSite.site_row_id || '') || String((project.site || {}).site_id || '') === String(selectedSite.site_id || ''))) || null; } catch (_) { /* SitePlus metadata alone is enough to insert */ }
      onInsert({ id: assignmentId('scene'), code_key: selectedOption || '他現場', source_type: 'scene', source_project_id: sceneProject ? sceneProject.id : '', source_project_title: sceneProject ? sceneProject.title : '', source_site_row_id: String(selectedSite.id || selectedSite.site_row_id || ''), source_site_id: String(selectedSite.site_id || ''), source_site_name: String(selectedSite.site_name || ''), option_key: selectedOption, second_option: selectedSecondOption, custom_label: custom.value.trim() });
      close();
    });
    loadSites(); query.focus();
  }

  function openCell(day, memberId) {
    const entry = entryFor(day, memberId) || {}; const editable = !!state.context.editable;
    const substitute = isSubstituteMember(memberId);
    let selectedEmployee = { employee_number: String(entry.employee_number || ''), employee_name: String(entry.employee_name || '') };
    const workCodes = activeCodes().filter((code) => code.category === 'work'); const leaveCodes = activeCodes().filter((code) => code.category === 'leave');
    let selectedAssignments = clone(assignmentsFor(entry));
    const chips = (rows) => rows.map((code) => `<button type="button" class="cloud-large-code-chip" data-large-pick-code="${esc(code.key)}" data-large-code-category="${esc(code.category)}" style="--large-code-color:${esc(code.color || '#e3f2fd')}">${esc(code.label || code.key)}<small>${esc(code.key)}</small></button>`).join('');
    const time = entry.time_override || {};
    const autoHolidayKind = Calc.holidayClass(holidayRuleFor(memberId), Number(state.context.month.year), Number(state.context.month.month), Number(day));
    const body = `<div class="cloud-large-cell-editor">
      ${substitute ? `<section class="cloud-large-substitute-person"><div class="cloud-large-section-title"><h4>担当者</h4><span>代務列では必須です</span></div><div class="cloud-large-selected-person" data-cell-selected-person></div><label class="cloud-field"><span>社員を検索</span><input class="cloud-input" data-cell-employee-query placeholder="社員番号・氏名"></label><div class="cloud-large-employee-results" data-cell-employee-results></div></section>` : ''}
      <section class="cloud-large-selected-section"><h4>この日の仕事・休み ${help('勤務は複数選択できます。休みを選ぶと、その休み1つだけに切り替わります。')}</h4><div class="cloud-large-selected-assignments" data-selected-assignments></div></section>
      <section><div class="cloud-large-section-title"><h4>この現場の勤務</h4><span>複数選択できます</span></div><div class="cloud-large-code-palette">${chips(workCodes)}</div></section>
      ${state.context.canPickOtherSite && !substitute ? `<section><div class="cloud-large-section-title"><h4>別の現場の仕事</h4><span>既存CloudShiftの現場・オプション情報を使います</span></div><button type="button" class="cloud-btn secondary" data-cell-other-scene>他現場</button></section>` : ''}
      ${substitute ? '' : `<section><h4>休み</h4><div class="cloud-large-code-palette">${chips(leaveCodes)}</div></section>`}
      <section class="cloud-large-holiday-cell"><div class="cloud-large-section-title"><h4>この日の休日区分</h4><span>ルール: ${esc(HOLIDAY_CLASS_LABELS[autoHolidayKind])}</span></div><select class="cloud-input" data-cell-holiday><option value="">休日ルールに従う（${esc(HOLIDAY_CLASS_LABELS[autoHolidayKind])}）</option><option value="none">この日だけ所定労働日にする</option><option value="scheduled">この日だけ所定休日にする</option><option value="legal">この日だけ法定休日にする</option></select><p class="cloud-panel-note">休日出勤・残業の集計はこの区分で分かれます。ふだんは「休日ルールに従う」のままで構いません。</p></section>
      <details class="cloud-fold"${entry.time_override || entry.bind_override_minutes != null || entry.break_override_minutes != null || entry.comment ? ' open' : ''}><summary class="cloud-fold-summary">詳細・コメント</summary><div class="cloud-fold-content cloud-large-detail-grid"><label class="cloud-field"><span>始業上書き ${help('この現場の勤務が1つのときは、その勤務コードの時刻より優先します。他の帳簿から同期された勤務だけの日は、この時間を設定すると拘束・労働時間に集計されます。')}</span><input class="cloud-input" type="time" data-cell-start value="${esc(time.start || '')}"></label><label class="cloud-field"><span>終業上書き</span><input class="cloud-input" type="time" data-cell-end value="${esc(time.end || '')}"></label><label class="cloud-field"><span>休憩上書き（時間:分） ${help('この日だけ休憩を変えるときに入力します。空欄ならダイヤ個別の休憩、それも無ければ共通設定の休憩を使います。例: 0:45')}</span><input class="cloud-input" inputmode="numeric" placeholder="ダイヤ・共通設定を使用" data-cell-break value="${esc(durationText(entry.break_override_minutes))}"></label><label class="cloud-field"><span>所定拘束（時間:分） ${help('この日の残業判定の基準です。例: 9:30')}</span><input class="cloud-input" inputmode="numeric" placeholder="9:30" data-cell-bind value="${esc(durationText(entry.bind_override_minutes))}"></label><label class="cloud-field cloud-field-wide"><span>コメント</span><textarea class="cloud-input" rows="3" data-cell-comment>${esc(entry.comment || '')}</textarea></label></div></details></div>`;
    const footer = editable ? '<button class="cloud-btn danger" data-cell-clear>クリア</button><button class="cloud-btn secondary" data-large-close>キャンセル</button><button class="cloud-btn primary" data-cell-save>反映</button>' : '<button class="cloud-btn secondary" data-large-close>閉じる</button>';
    const dialog = modal(`${day}日 / ${(activeMembers().find((m) => String(m.id) === String(memberId)) || {}).display_name || ''}`, body, footer);
    function renderSelectedEmployee() {
      const target = dialog.querySelector('[data-cell-selected-person]');
      if (!target) return;
      target.innerHTML = selectedEmployee.employee_name
        ? `<span class="cloud-large-selected-chip">${esc(selectedEmployee.employee_name)}${selectedEmployee.employee_number ? ` <small>${esc(selectedEmployee.employee_number)}</small>` : ''}<button type="button" data-cell-clear-person aria-label="担当者を解除">×</button></span>`
        : '<span class="cloud-large-no-selection">担当者が未選択です</span>';
      target.querySelector('[data-cell-clear-person]')?.addEventListener('click', () => {
        selectedEmployee = { employee_number: '', employee_name: '' };
        renderSelectedEmployee();
      });
    }
    function renderSelected() {
      const target = dialog.querySelector('[data-selected-assignments]');
      target.innerHTML = selectedAssignments.length ? selectedAssignments.map((item, index) => `<span class="cloud-large-selected-chip">${esc(assignmentLabel(item))}${item.source_type === 'sync' ? '<small>同期</small>' : `<button type="button" data-remove-assignment="${index}" aria-label="削除">×</button>`}</span>`).join('') : '<span class="cloud-large-no-selection">未選択</span>';
      target.querySelectorAll('[data-remove-assignment]').forEach((button) => button.addEventListener('click', () => { selectedAssignments.splice(Number(button.dataset.removeAssignment), 1); renderSelected(); }));
      dialog.querySelectorAll('[data-large-pick-code]').forEach((button) => button.classList.toggle('is-selected', selectedAssignments.some((item) => item.source_type === 'local' && String(item.code_key).toLocaleLowerCase() === String(button.dataset.largePickCode).toLocaleLowerCase())));
    }
    dialog.querySelectorAll('[data-large-pick-code]').forEach((button) => button.addEventListener('click', () => {
      if (button.dataset.largeCodeCategory === 'leave') {
        selectedAssignments = [{ id: assignmentId('leave'), code_key: button.dataset.largePickCode, source_type: 'local', source_project_id: '', source_project_title: '', source_site_id: '' }];
      } else {
        selectedAssignments = selectedAssignments.filter((item) => { const code = codeForAssignment(item); return !code || code.category !== 'leave'; });
        const index = selectedAssignments.findIndex((item) => item.source_type === 'local' && String(item.code_key).toLocaleLowerCase() === String(button.dataset.largePickCode).toLocaleLowerCase());
        if (index >= 0) selectedAssignments.splice(index, 1); else selectedAssignments.push({ id: assignmentId('local'), code_key: button.dataset.largePickCode, source_type: 'local', source_project_id: '', source_project_title: '', source_site_id: '' });
      }
      renderSelected();
    }));
    const otherButton = dialog.querySelector('[data-cell-other-scene]');
    if (otherButton) otherButton.addEventListener('click', () => openOtherSitePicker((assignment) => { selectedAssignments = selectedAssignments.filter((item) => { const code = codeForAssignment(item); return !code || code.category !== 'leave'; }); selectedAssignments.push(assignment); renderSelected(); }));
    renderSelected();
    renderSelectedEmployee();
    const holidaySelect = dialog.querySelector('[data-cell-holiday]');
    holidaySelect.value = String(entry.holiday_kind || '');
    holidaySelect.disabled = !editable;
    const employeeQuery = dialog.querySelector('[data-cell-employee-query]');
    if (employeeQuery) {
      let employeeTimer;
      employeeQuery.addEventListener('input', () => {
        clearTimeout(employeeTimer);
        employeeTimer = setTimeout(async () => {
          const results = dialog.querySelector('[data-cell-employee-results]');
          if (!employeeQuery.value.trim()) { results.innerHTML = ''; return; }
          try {
            const response = await fetch(`/tools/pluslist/api/search_employee?q=${encodeURIComponent(employeeQuery.value.trim())}`);
            if (!response.ok) throw new Error('社員検索を利用できません');
            const payload = await response.json();
            const people = Array.isArray(payload) ? payload : (payload.results || payload.employees || payload.items || []);
            results.innerHTML = people.length ? people.map((person, index) => `<button type="button" data-cell-person-index="${index}"><b>${esc(person.employee_name || person.name || '')}</b><small>${esc(person.employee_number || '')} / ${esc(person.office_name || '')}</small></button>`).join('') : '<p class="cloud-large-search-error">候補が見つかりません。</p>';
            results.querySelectorAll('[data-cell-person-index]').forEach((button) => button.addEventListener('click', () => {
              const person = people[Number(button.dataset.cellPersonIndex)] || {};
              selectedEmployee = { employee_number: String(person.employee_number || ''), employee_name: String(person.employee_name || person.name || '') };
              employeeQuery.value = ''; results.innerHTML = ''; renderSelectedEmployee();
            }));
          } catch (error) { results.innerHTML = `<p class="cloud-large-search-error">${esc(error.message)}</p>`; }
        }, 220);
      });
    }
    const save = dialog.querySelector('[data-cell-save]');
    if (save) save.addEventListener('click', () => {
      const start = dialog.querySelector('[data-cell-start]').value; const end = dialog.querySelector('[data-cell-end]').value;
      if ((start && !end) || (!start && end) || (start && end && Calc.minutes(start) >= Calc.minutes(end, true))) { flash('始業・終業の組み合わせを確認してください', 'error'); return; }
      const bindRaw = dialog.querySelector('[data-cell-bind]').value;
      const bindMinutes = bindRaw === '' ? null : durationMinutes(bindRaw);
      if (bindRaw !== '' && bindMinutes == null) { flash('所定拘束は「9:30」のように時間:分で入力してください', 'error'); return; }
      const breakRaw = dialog.querySelector('[data-cell-break]').value;
      const breakMinutes = breakRaw === '' ? null : durationMinutes(breakRaw);
      if (breakRaw !== '' && breakMinutes == null) { flash('休憩上書きは「0:45」のように時間:分で入力してください', 'error'); return; }
      const localAssignmentCount = selectedAssignments.filter((item) => String(item.source_type || 'local') === 'local').length;
      if (localAssignmentCount > 1 && (start || end)) { flash('始業・終業の上書きは、この現場の勤務が1つ以下のときだけ利用できます', 'error'); return; }
      if (substitute && selectedAssignments.length && !selectedEmployee.employee_name) { flash('代務の担当者を選択してください', 'error'); return; }
      setEntry(day, memberId, { assignments: selectedAssignments, value: selectedAssignments[0] ? selectedAssignments[0].code_key : '', holiday_kind: dialog.querySelector('[data-cell-holiday]').value, time_override: start && end ? { start, end } : null, bind_override_minutes: bindMinutes, break_override_minutes: breakMinutes, comment: dialog.querySelector('[data-cell-comment]').value.trim(), employee_number: substitute ? selectedEmployee.employee_number : entry.employee_number, employee_name: substitute ? selectedEmployee.employee_name : entry.employee_name });
      render();
    });
    const clear = dialog.querySelector('[data-cell-clear]'); if (clear) clear.addEventListener('click', () => { clearEntry(day, memberId); render(); });
  }

  function openDay(day) {
    if (!state.context.metaEditable) {
      if (state.context.editorMode === 'draft') flash('日メモとダイヤ変更は通常モードで保存してください', 'info');
      return;
    }
    const body = `<div class="cloud-large-detail-grid"><label class="cloud-field"><span>ダイヤ種別</span><select class="cloud-input" data-day-type><option value="">暦から自動</option><option value="weekday">平日</option><option value="saturday">土曜</option><option value="holiday">日祝</option></select></label><label class="cloud-field cloud-field-wide"><span>日メモ（行事・点検など）</span><textarea class="cloud-input" data-day-note rows="4">${esc((state.meta.day_notes || {})[String(day)] || '')}</textarea></label></div>`;
    const dialog = modal(`${day}日の設定`, body, '<button class="cloud-btn secondary" data-large-close>キャンセル</button><button class="cloud-btn primary" data-day-save>反映</button>');
    dialog.querySelector('[data-day-type]').value = (state.meta.day_types || {})[String(day)] || '';
    dialog.querySelector('[data-day-save]').addEventListener('click', () => {
      state.meta.day_types = state.meta.day_types || {}; state.meta.day_notes = state.meta.day_notes || {};
      const type = dialog.querySelector('[data-day-type]').value; const note = dialog.querySelector('[data-day-note]').value.trim();
      if (type) state.meta.day_types[String(day)] = type; else delete state.meta.day_types[String(day)];
      if (note) state.meta.day_notes[String(day)] = note; else delete state.meta.day_notes[String(day)];
      if (state.context.onChange) state.context.onChange(); render();
    });
  }

  function openPerson(memberId) {
    const person = personResult(memberId); if (!person) return;
    const rows = person.days.filter((day) => day.code_key || day.warnings.length).map((day) => `<tr><td>${day.day}</td><td>${esc(day.code_key)}</td><td>${esc(day.category)}</td><td>${day.start_minutes == null ? '' : Calc.hhmm(day.start_minutes)}</td><td>${day.end_minutes == null ? '' : Calc.hhmm(day.end_minutes)}</td><td>${Calc.hhmm(day.bind_minutes)}</td><td>${Calc.hhmm(day.work_minutes)}</td><td>${Calc.hhmm(day.overtime_minutes)}</td></tr>`).join('');
    const totals = person.totals;
    modal(person.label, `<div class="cloud-large-person-total"><span>拘束 <b>${totals.bind_total_hhmm}</b></span><span>労働 <b>${totals.work_total_hhmm}</b></span><span>残業 <b>${totals.payroll_overtime_hhmm}</b></span><span>最大連勤 <b>${totals.max_consecutive_work_days}日</b></span></div><div class="cloud-large-detail-table"><table><thead><tr><th>日</th><th>コード</th><th>区分</th><th>始業</th><th>終業</th><th>拘束</th><th>労働</th><th>超過</th></tr></thead><tbody>${rows}</tbody></table></div>`);
  }

  function normalizedRule(rule) {
    const source = rule && typeof rule === 'object' ? rule : {};
    return {
      mode: source.mode === 'custom' ? 'custom' : 'default',
      legal_weekdays: Array.isArray(source.legal_weekdays) ? source.legal_weekdays.map(Number) : [],
      scheduled_weekdays: Array.isArray(source.scheduled_weekdays) ? source.scheduled_weekdays.map(Number) : [],
      legal_dates: Array.isArray(source.legal_dates) ? source.legal_dates.slice() : [],
      scheduled_dates: Array.isArray(source.scheduled_dates) ? source.scheduled_dates.slice() : []
    };
  }
  function sharedHolidayRule() {
    state.config.settings = state.config.settings || {};
    if (!state.config.settings.holiday_rule) state.config.settings.holiday_rule = { legal_weekdays: [0], scheduled_weekdays: [6], legal_dates: [], scheduled_dates: [] };
    return state.config.settings.holiday_rule;
  }
  function holidayRuleFieldsHtml(rule, scope) {
    const normalized = normalizedRule(rule);
    const section = (kind, title, note) => {
      const weekdays = WEEKDAY_LABELS.map((label, index) => `<label class="cloud-large-weekday"><input type="checkbox" data-holiday-weekday="${kind}" value="${index}"${normalized[`${kind}_weekdays`].includes(index) ? ' checked' : ''}><span>${label}</span></label>`).join('');
      const dates = normalized[`${kind}_dates`];
      const chips = dates.length
        ? dates.map((iso) => `<span class="cloud-large-selected-chip">${esc(iso)}<button type="button" data-holiday-date-remove="${esc(iso)}" data-holiday-date-kind="${kind}" aria-label="この指定日を削除">×</button></span>`).join('')
        : '<span class="cloud-large-no-selection">指定日はありません</span>';
      return `<section class="cloud-large-holiday-section is-${kind}"><h5>${esc(title)} ${help(note)}</h5><div class="cloud-large-weekday-row">${weekdays}</div><div class="cloud-large-holiday-dates">${chips}</div><div class="cloud-large-holiday-add"><input class="cloud-input" type="date" data-holiday-date-input="${kind}"><button type="button" class="cloud-btn secondary" data-holiday-date-add="${kind}">指定日を追加</button></div></section>`;
    };
    return `<div class="cloud-large-holiday-rule" data-holiday-scope="${esc(scope)}">${section('legal', '法定休日', '週1日（または4週4日）の法定休日です。この日の勤務は法定休日労働として集計し、36協定の休日労働に入ります。')}${section('scheduled', '所定休日', '法定休日以外の会社の休みです。この日の勤務は所定休日労働として集計し、特別条項・960時間の対象に入ります。')}<p class="cloud-panel-note">指定日は曜日より優先します。同じ日が法定・所定の両方に該当する場合は法定休日として扱います。</p></div>`;
  }
  function bindHolidayRule(scopeNode, rule, afterChange) {
    if (!scopeNode) return;
    // 曜日はチェック状態がそのまま見た目なので再描画せず、見出しの要約だけ追従させる。
    const refreshSummary = () => {
      const summary = scopeNode.closest('details') ? scopeNode.closest('details').querySelector('.cloud-fold-summary') : null;
      if (summary) summary.textContent = `休日ルール：${holidayRuleSummary(normalizedRule(rule))}`;
    };
    scopeNode.querySelectorAll('[data-holiday-weekday]').forEach((input) => input.addEventListener('change', () => {
      const key = `${input.dataset.holidayWeekday}_weekdays`;
      const value = Number(input.value);
      const list = (rule[key] || []).map(Number).filter((item) => item !== value);
      if (input.checked) list.push(value);
      rule[key] = list.sort((left, right) => left - right);
      refreshSummary();
    }));
    scopeNode.querySelectorAll('[data-holiday-date-add]').forEach((button) => button.addEventListener('click', () => {
      const kind = button.dataset.holidayDateAdd;
      const input = scopeNode.querySelector(`[data-holiday-date-input="${kind}"]`);
      const value = String(input.value || '').trim();
      if (!value) { flash('追加する日付を選んでください', 'error'); return; }
      const key = `${kind}_dates`;
      rule[key] = Array.from(new Set([...(rule[key] || []), value])).sort();
      input.value = '';
      if (afterChange) afterChange();
    }));
    scopeNode.querySelectorAll('[data-holiday-date-remove]').forEach((button) => button.addEventListener('click', () => {
      const key = `${button.dataset.holidayDateKind}_dates`;
      rule[key] = (rule[key] || []).filter((item) => String(item) !== String(button.dataset.holidayDateRemove));
      if (afterChange) afterChange();
    }));
  }
  function settingsHolidaysHtml() {
    const rule = sharedHolidayRule();
    const custom = (state.config.members || []).filter((member) => String(((member.holiday_rule || {}).mode) || 'default') === 'custom');
    return `<div class="cloud-large-settings-intro"><div><h3>共通の休日ルール</h3><p>法定休日と所定休日を曜日と指定日で決めます。土日休みでない現場は、ここを実際の休みに合わせてください。この設定が休日出勤・残業の集計の基準になります。</p></div></div>${holidayRuleFieldsHtml(rule, 'shared')}<p class="cloud-panel-note">個別に違う休みの人は「メンバー・代務列」の各カードで個人設定にできます。現在の個人設定: ${custom.length ? esc(custom.map((member) => member.display_name).join('、')) : 'なし（全員が共通ルール）'}</p>`;
  }

  function settingsMembersHtml() {
    return `<div class="cloud-large-settings-intro"><div><h3>シフト表の列</h3><p>レギュラーは月を通して同じ担当者です。代務列は、日ごとに担当者とダイヤを入力します。カードをドラッグすると列順を変えられます。</p></div><div class="cloud-large-settings-actions"><button class="cloud-btn secondary" data-member-add>レギュラーを自由入力</button><button class="cloud-btn secondary" data-substitute-add>代務列を追加</button></div></div><div class="cloud-large-employee-search"><input class="cloud-input" data-employee-query placeholder="社員番号・氏名でレギュラーを検索して追加"><div data-employee-results></div></div><div class="cloud-large-setting-list">${(state.config.members || []).map((member, index) => {
      const substitute = memberColumnType(member) === 'substitute';
      const rule = normalizedRule(member.holiday_rule);
      const personal = rule.mode === 'custom';
      const summary = personal ? holidayRuleSummary(rule) : `共通ルール（${holidayRuleSummary(normalizedRule(sharedHolidayRule()))}）`;
      return `<article class="cloud-large-setting-card${substitute ? ' is-substitute' : ''}" draggable="true" data-member-index="${index}"><header><span class="cloud-large-drag" aria-hidden="true">⋮⋮</span><span class="cloud-large-kind-badge">${substitute ? '代務列' : 'レギュラー'}</span></header><div class="cloud-large-setting-row"><label class="cloud-field"><span>シフト表での表示名</span><input class="cloud-input" data-member-field="display_name" value="${esc(member.display_name)}"></label>${substitute ? '' : `<label class="cloud-field"><span>社員番号 ${help('社員検索から追加した場合に表示します。自由入力のレギュラーは空欄です。')}</span><input class="cloud-input" value="${esc(member.employee_number || '')}" readonly></label><label class="cloud-field"><span>1日の所定拘束（時間:分） ${help('この人だけ共通設定と異なる残業判定の基準にしたいときに入力します。')}</span><input class="cloud-input" inputmode="numeric" placeholder="共通設定を使用" data-member-field="scheduled_bind_minutes" value="${esc(durationText(member.scheduled_bind_minutes))}"></label>`}<label class="cloud-large-check"><input type="checkbox" data-member-field="active"${member.active !== false ? ' checked' : ''}> 表に表示</label></div><details class="cloud-fold cloud-large-member-holiday"${personal ? ' open' : ''}><summary class="cloud-fold-summary">休日ルール：${esc(summary)}</summary><div class="cloud-fold-content"><label class="cloud-large-check"><input type="checkbox" data-member-holiday-mode${personal ? ' checked' : ''}> この人だけ休日を個別に設定する</label>${personal ? holidayRuleFieldsHtml(rule, `member:${index}`) : '<p class="cloud-panel-note">共通の休日ルールを使います。チェックを入れると、この人だけの法定休日・所定休日を曜日と指定日で決められます。</p>'}</div></details></article>`;
    }).join('')}</div>`;
  }
  function timeFields(code, dayTypeKey, label) { const range = (code.times || {})[dayTypeKey] || {}; return `<fieldset><legend>${label}</legend><input class="cloud-input" type="time" data-code-time="${dayTypeKey}:start" value="${esc(range.start || '')}"><span>–</span><input class="cloud-input" type="time" data-code-time="${dayTypeKey}:end" value="${esc(range.end || '')}"></fieldset>`; }
  function settingsCodesHtml(selectedCategory) {
    const leaveKinds = [['legal_rest','法定休'],['scheduled_rest','所定休'],['substitute_rest','振休'],['paid','有休'],['special','特別休暇'],['other','その他']];
    const category = selectedCategory === 'leave' ? 'leave' : 'work';
    const cards = (state.config.codes || []).map((code, index) => ({ code, index })).filter((item) => item.code.category === category).map(({ code, index }) => `<article class="cloud-large-setting-card cloud-large-code-card" draggable="true" data-code-index="${index}" style="--large-code-color:${esc(code.color || '#e3f2fd')}"><header><span class="cloud-large-drag" aria-hidden="true">⋮⋮</span><span class="cloud-large-code-preview">${esc(code.label || code.key)}</span><span class="cloud-large-kind-badge">${code.category === 'work' ? 'ダイヤ' : '休日'}</span></header><div class="cloud-large-setting-row"><label class="cloud-field"><span>ユーザーに見せる名前</span><input class="cloud-input" data-code-field="label" value="${esc(code.label)}"></label><label class="cloud-field"><span>入力用の略号 ${help('キーボード入力やデータ連携で使う、重複しない短い呼び名です。普段は自動作成された値のままで構いません。')}</span><input class="cloud-input" data-code-field="key" value="${esc(code.key)}"></label><label class="cloud-field cloud-large-color-field"><span>表示色 ${help('右の色を変えると、このカード上部ですぐ確認できます。')}</span><input class="cloud-input" type="color" data-code-field="color" value="${esc(code.color || '#e3f2fd')}"></label><label class="cloud-large-check"><input type="checkbox" data-code-field="active"${code.active !== false ? ' checked' : ''}> 選択肢に表示</label></div>${code.category === 'work' ? `<div class="cloud-large-time-sets">${timeFields(code, 'weekday', '平日')}${timeFields(code, 'saturday', '土曜')}${timeFields(code, 'holiday', '日祝')}</div><label class="cloud-field cloud-large-duration-field"><span>このダイヤだけ休憩を変える（時間:分） ${help('空欄なら計算設定の共通休憩を使います。')}</span><input class="cloud-input" inputmode="numeric" placeholder="共通設定を使用" data-code-field="break_minutes" value="${esc(durationText(code.break_minutes))}"></label>` : `<div class="cloud-large-setting-row"><label class="cloud-field"><span>集計上の休日種別 ${help('有休マネージャーや既存CloudShiftと同じ意味で集計します。')}</span><select class="cloud-input" data-code-field="leave_kind">${leaveKinds.map(([kind,label]) => `<option value="${kind}"${code.leave_kind === kind ? ' selected' : ''}>${label}</option>`).join('')}</select></label><label class="cloud-large-check"><input type="checkbox" data-code-field="requested"${code.requested ? ' checked' : ''}> 本人の希望として集計</label></div>`}</article>`).join('');
    return `<div class="cloud-large-settings-intro"><div><h3>シフトの選択肢</h3><p>ダイヤと休日を分けて設定します。名前と色は入力画面・シフト表の両方に反映されます。</p></div><button class="cloud-btn secondary" data-code-add="${category}">${category === 'work' ? 'ダイヤを追加' : '休日を追加'}</button></div><div class="cloud-large-code-tabs" role="tablist"><button type="button" class="${category === 'work' ? 'is-active' : ''}" data-code-category-tab="work">ダイヤ</button><button type="button" class="${category === 'leave' ? 'is-active' : ''}" data-code-category-tab="leave">休日</button></div><div class="cloud-large-setting-list">${cards}</div>`;
  }
  function settingsCalcHtml() {
    const checks = state.config.settings.checks || {};
    const definitions = [
      ['kaizen_monthly_bind','月の拘束時間','1か月の始業から終業まで（休憩を含む）の合計がこの時間を超えたら警告します。','warn_minutes','警告する時間'],
      ['kaizen_daily_bind','1日の拘束時間','その日の最初の始業から最後の終業までが警告時間を超えたら知らせ、上限超過は強い警告にします。','warn_minutes','警告する時間'],
      ['kaizen_rest_period','勤務間の休息','前日の終業から翌日の始業までの休息が、この時間より短い場合に警告します。','min_minutes','必要な休息'],
      ['overtime_monthly','給与上の残業','各日の「拘束時間－所定拘束」の月合計がこの時間を超えたら警告します。','warn_minutes','警告する時間'],
      ['anei_long_work','安全衛生上の長時間労働','月の実労働から法定の基準時間を引いた値がこの時間を超えたら警告します。','warn_minutes','警告する時間'],
      ['consecutive_days','連続勤務日数','休みを挟まず勤務した日数が、この日数を超えたら警告します。','warn_days','警告する日数']
    ];
    const rows = definitions.map(([key,label,description,valueKey,valueLabel]) => { const item = checks[key] || {}; return `<article class="cloud-large-calc-card" data-check-key="${key}"><div><label class="cloud-large-check"><input type="checkbox" data-check-enabled${item.enabled ? ' checked' : ''}> ${label}</label><p>${description}</p></div><label class="cloud-field"><span>${valueLabel}</span><input class="cloud-input" ${valueKey === 'warn_days' ? 'type="number" min="1"' : 'inputmode="numeric" placeholder="時間:分"'} data-check-value="${valueKey}" value="${esc(valueKey === 'warn_days' ? item[valueKey] : durationText(item[valueKey]))}"></label>${key === 'kaizen_daily_bind' ? `<label class="cloud-field"><span>1日の上限</span><input class="cloud-input" inputmode="numeric" placeholder="時間:分" data-check-value="max_minutes" value="${esc(durationText(item.max_minutes))}"></label>` : ''}</article>`; }).join('');
    return `<div class="cloud-large-settings-intro"><div><h3>集計と警告の基準</h3><p>すべて「時間:分」で表示します。各警告は集計画面の警告一覧に反映され、シフトそのものを自動変更することはありません。</p></div></div><section class="cloud-large-calc-basics"><label class="cloud-field"><span>共通の休憩時間 ${help('勤務コードに個別の休憩がない日の、実労働時間を計算するときに差し引きます。セルごとの休憩上書きがあればそちらを優先します。')}</span><input class="cloud-input" inputmode="numeric" data-setting-field="break_minutes" value="${esc(durationText(state.config.settings.break_minutes))}"></label><label class="cloud-field"><span>1日の所定拘束 ${help('各日の給与上の残業を「実際の拘束－この時間」で計算します。メンバー別設定があればそちらを優先します。')}</span><input class="cloud-input" inputmode="numeric" data-setting-field="scheduled_bind_minutes" value="${esc(durationText(state.config.settings.scheduled_bind_minutes))}"></label><label class="cloud-field"><span>有休1日の所定労働時間 ${help('有休・希望有休を表下部の「＋○○」に換算するときの1日あたりの時間です。空欄なら「所定拘束－共通休憩」を使います。')}</span><input class="cloud-input" inputmode="numeric" placeholder="所定拘束－休憩を使用" data-setting-optional-field="paid_leave_work_minutes" value="${esc(durationText(state.config.settings.paid_leave_work_minutes))}"></label></section><div class="cloud-large-check-list">${rows}</div>`;
  }

  function openSettings() {
    const originalConfig = state.config; state.config = clone(state.config); let committed = false;
    const body = `<div class="cloud-large-settings"><nav><button class="is-active" data-settings-tab="members">メンバー・代務列</button><button data-settings-tab="codes">シフトの選択肢</button><button data-settings-tab="holidays">休日ルール</button><button data-settings-tab="calc">集計と警告</button></nav><div data-settings-content>${settingsMembersHtml()}</div></div>`;
    const dialog = modal('大規模シフトの設定', body, '<button class="cloud-btn secondary" data-large-close>キャンセル</button><button class="cloud-btn primary" data-settings-save>設定を保存</button>');
    state.host.querySelectorAll('[data-large-modal-root] [data-large-close]').forEach((node) => node.addEventListener('click', (event) => { if ((event.target === node || node.tagName === 'BUTTON') && !committed) state.config = originalConfig; }));
    let tab = 'members'; let codeCategory = 'work';
    function renderContent() {
      dialog.querySelector('[data-settings-content]').innerHTML = tab === 'codes' ? settingsCodesHtml(codeCategory) : tab === 'holidays' ? settingsHolidaysHtml() : tab === 'calc' ? settingsCalcHtml() : settingsMembersHtml();
      bindSettingsContent();
    }
    function bindSettingsContent() {
      function bindReorder(selector, collection) {
        let sourceIndex = -1;
        dialog.querySelectorAll(selector).forEach((card) => {
          const cardIndex = () => Number(card.dataset.memberIndex != null ? card.dataset.memberIndex : card.dataset.codeIndex);
          card.addEventListener('dragstart', () => { sourceIndex = cardIndex(); card.classList.add('is-dragging'); });
          card.addEventListener('dragend', () => card.classList.remove('is-dragging'));
          card.addEventListener('dragover', (event) => event.preventDefault());
          card.addEventListener('drop', (event) => {
            event.preventDefault();
            const targetIndex = cardIndex();
            if (sourceIndex < 0 || sourceIndex === targetIndex) return;
            const [moved] = collection.splice(sourceIndex, 1);
            collection.splice(targetIndex, 0, moved);
            collection.forEach((item, index) => { item.order = (index + 1) * 10; });
            renderContent();
          });
        });
      }
      bindReorder('[data-member-index]', state.config.members);
      bindReorder('[data-code-index]', state.config.codes);
      bindHolidayRule(dialog.querySelector('[data-holiday-scope="shared"]'), sharedHolidayRule(), renderContent);
      dialog.querySelectorAll('[data-member-index]').forEach((card) => {
        const member = state.config.members[Number(card.dataset.memberIndex)];
        const modeInput = card.querySelector('[data-member-holiday-mode]');
        if (!modeInput) return;
        modeInput.addEventListener('change', () => {
          const rule = normalizedRule(member.holiday_rule);
          if (modeInput.checked) {
            const empty = !rule.legal_weekdays.length && !rule.scheduled_weekdays.length && !rule.legal_dates.length && !rule.scheduled_dates.length;
            // 個別設定へ切り替えた直後は共通ルールを写して、空の休日設定から始まらないようにする。
            member.holiday_rule = Object.assign(empty ? normalizedRule(sharedHolidayRule()) : rule, { mode: 'custom' });
          } else {
            member.holiday_rule = Object.assign(rule, { mode: 'default' });
          }
          renderContent();
        });
        member.holiday_rule = normalizedRule(member.holiday_rule);
        bindHolidayRule(card.querySelector(`[data-holiday-scope="member:${card.dataset.memberIndex}"]`), member.holiday_rule, renderContent);
      });
      dialog.querySelectorAll('[data-member-index]').forEach((card) => card.querySelectorAll('[data-member-field]').forEach((input) => input.addEventListener('change', () => { const member = state.config.members[Number(card.dataset.memberIndex)]; const key = input.dataset.memberField; if (input.type === 'checkbox') member[key] = input.checked; else if (key === 'scheduled_bind_minutes') { const parsed = input.value === '' ? null : durationMinutes(input.value); if (input.value !== '' && parsed == null) { flash('時間は「9:30」のように時間:分で入力してください', 'error'); input.value = durationText(member[key]); return; } member[key] = parsed; } else member[key] = input.value; })));
      const addMember = dialog.querySelector('[data-member-add]'); if (addMember) addMember.addEventListener('click', () => { const name = root.prompt('表示名を入力してください'); if (!name || !name.trim()) return; state.config.members.push({ id: `mem_${Date.now().toString(36)}_${Math.random().toString(36).slice(2,7)}`, display_name: name.trim(), column_type: 'regular', employee_number: '', employee_name: name.trim(), scheduled_bind_minutes: null, order: (state.config.members.length + 1) * 10, active: true, note: '' }); renderContent(); });
      const addSubstitute = dialog.querySelector('[data-substitute-add]'); if (addSubstitute) addSubstitute.addEventListener('click', () => { const suggested = `代務${(state.config.members || []).filter((item) => memberColumnType(item) === 'substitute').length + 1}`; const name = root.prompt('代務列の表示名を入力してください', suggested); if (!name || !name.trim()) return; state.config.members.push({ id: `sub_${Date.now().toString(36)}_${Math.random().toString(36).slice(2,7)}`, display_name: name.trim(), column_type: 'substitute', employee_number: '', employee_name: '', scheduled_bind_minutes: null, order: (state.config.members.length + 1) * 10, active: true, note: '' }); renderContent(); });
      const query = dialog.querySelector('[data-employee-query]'); if (query) { let timer; query.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(async () => { const results = dialog.querySelector('[data-employee-results]'); if (query.value.trim().length < 1) { results.innerHTML = ''; return; } try { const response = await fetch(`/tools/pluslist/api/search_employee?q=${encodeURIComponent(query.value.trim())}`); if (!response.ok) throw new Error('社員検索を利用できません'); const data = await response.json(); const list = Array.isArray(data) ? data : (data.results || data.employees || data.items || []); results.innerHTML = list.map((item) => `<button type="button" data-employee-number="${esc(item.employee_number)}" data-employee-name="${esc(item.employee_name)}"><b>${esc(item.employee_name)}</b><small>${esc(item.employee_number)} / ${esc(item.office_name || '')}</small></button>`).join(''); results.querySelectorAll('[data-employee-number]').forEach((button) => button.addEventListener('click', () => { state.config.members.push({ id: `mem_${Date.now().toString(36)}_${Math.random().toString(36).slice(2,7)}`, display_name: button.dataset.employeeName, column_type: 'regular', employee_number: button.dataset.employeeNumber, employee_name: button.dataset.employeeName, scheduled_bind_minutes: null, order: (state.config.members.length + 1) * 10, active: true, note: '' }); renderContent(); })); } catch (error) { results.innerHTML = `<p class="cloud-large-search-error">${esc(error.message)}。自由入力で追加できます。</p>`; } }, 250); }); }
      dialog.querySelectorAll('[data-code-index]').forEach((card) => {
        const code = state.config.codes[Number(card.dataset.codeIndex)];
        card.querySelectorAll('[data-code-field]').forEach((input) => {
          const update = () => { const key = input.dataset.codeField; if (input.type === 'checkbox') code[key] = input.checked; else if (key === 'break_minutes') { const parsed = input.value === '' ? null : durationMinutes(input.value); if (input.value !== '' && parsed == null) { flash('休憩は「1:00」のように時間:分で入力してください', 'error'); input.value = durationText(code[key]); return; } code[key] = parsed; } else code[key] = input.value; if (key === 'color') card.style.setProperty('--large-code-color', code.color); if (key === 'label') { const preview = card.querySelector('.cloud-large-code-preview'); if (preview) preview.textContent = code.label || code.key; } };
          input.addEventListener('change', update); if (input.dataset.codeField === 'color' || input.dataset.codeField === 'label') input.addEventListener('input', update);
        });
        card.querySelectorAll('[data-code-time]').forEach((input) => input.addEventListener('change', () => { const [type] = input.dataset.codeTime.split(':'); const fieldset = input.closest('fieldset'); const start = fieldset.querySelector(`[data-code-time="${type}:start"]`).value; const end = fieldset.querySelector(`[data-code-time="${type}:end"]`).value; code.times = code.times || {}; code.times[type] = start && end ? { start, end } : null; }));
      });
      dialog.querySelectorAll('[data-code-category-tab]').forEach((button) => button.addEventListener('click', () => { codeCategory = button.dataset.codeCategoryTab === 'leave' ? 'leave' : 'work'; renderContent(); }));
      dialog.querySelectorAll('[data-code-add]').forEach((button) => button.addEventListener('click', () => { const category = button.dataset.codeAdd; const name = root.prompt(category === 'work' ? 'ユーザーに見せるダイヤ名を入力してください（例: 早番）' : 'ユーザーに見せる休日名を入力してください'); if (!name || !name.trim()) return; const base = name.trim().replace(/\s+/g, '').slice(0, 8) || (category === 'work' ? '勤務' : '休み'); let key = base; let suffix = 2; const used = new Set((state.config.codes || []).map((item) => String(item.key).toLocaleLowerCase())); while (used.has(key.toLocaleLowerCase())) { key = `${base}${suffix}`; suffix += 1; } state.config.codes.push({ key, label: name.trim(), category, times: { weekday: null, saturday: null, holiday: null }, break_minutes: null, leave_kind: category === 'leave' ? 'other' : '', requested: false, color: category === 'leave' ? '#e5e7eb' : '#e3f2fd', order: (state.config.codes.length + 1) * 10, active: true, note: '' }); renderContent(); }));
      dialog.querySelectorAll('[data-setting-field]').forEach((input) => input.addEventListener('change', () => { const key = input.dataset.settingField; const parsed = durationMinutes(input.value); if (parsed == null) { flash('時間は「1:00」のように時間:分で入力してください', 'error'); input.value = durationText(state.config.settings[key]); return; } state.config.settings[key] = parsed; }));
      dialog.querySelectorAll('[data-setting-optional-field]').forEach((input) => input.addEventListener('change', () => { const key = input.dataset.settingOptionalField; const parsed = input.value.trim() === '' ? null : durationMinutes(input.value); if (input.value.trim() !== '' && parsed == null) { flash('時間は「8:00」のように時間:分で入力してください', 'error'); input.value = durationText(state.config.settings[key]); return; } state.config.settings[key] = parsed; }));
      dialog.querySelectorAll('[data-check-key]').forEach((row) => { const item = state.config.settings.checks[row.dataset.checkKey]; row.querySelector('[data-check-enabled]').addEventListener('change', (event) => { item.enabled = event.target.checked; }); row.querySelectorAll('[data-check-value]').forEach((input) => input.addEventListener('change', () => { const key = input.dataset.checkValue; const parsed = key === 'warn_days' ? Number(input.value) : durationMinutes(input.value); if (!Number.isFinite(parsed) || parsed < 0) { flash(key === 'warn_days' ? '日数を入力してください' : '時間は「12:00」のように時間:分で入力してください', 'error'); input.value = key === 'warn_days' ? item[key] : durationText(item[key]); return; } item[key] = parsed; })); });
    }
    dialog.querySelectorAll('[data-settings-tab]').forEach((button) => button.addEventListener('click', () => { tab = button.dataset.settingsTab; dialog.querySelectorAll('[data-settings-tab]').forEach((item) => item.classList.toggle('is-active', item === button)); renderContent(); }));
    bindSettingsContent();
    dialog.querySelector('[data-settings-save]').addEventListener('click', async () => { try { const data = await state.context.requestJson(`/tools/shiftersync/cloudshift/api/project/${state.context.project.id}/large-config`, { method: 'PUT', body: { large_config: state.config } }); committed = true; state.config = clone(data.large_config); state.context.project.large_config = clone(data.large_config); if (state.context.onChange) state.context.onChange(); flash('設定を保存しました', 'success'); render(); } catch (error) { flash(error.message, 'error'); } });
  }

  async function baseline(clear) {
    if (!state.context.project.id) return;
    const message = clear ? '基準版を解除しますか？' : '現在の正式シフトを変更比較の基準にしますか？';
    if (state.context.confirm && !(await state.context.confirm(message))) return;
    try {
      const payload = await state.context.requestJson(`/tools/shiftersync/cloudshift/api/project/${state.context.project.id}/month/${state.context.month.year}/${state.context.month.month}/baseline`, { method: 'POST', body: clear ? { clear: true } : {} });
      if (state.context.onPayload) state.context.onPayload(payload.project || payload);
      flash(clear ? '基準版を解除しました' : '基準版を確定しました', 'success');
    } catch (error) { flash(error.message, 'error'); }
  }

  function focusCell(day, memberIndex) {
    const maxDay = days(); const maxMember = activeMembers().length - 1;
    state.focus.day = Math.max(1, Math.min(maxDay, day)); state.focus.member = Math.max(0, Math.min(maxMember, memberIndex));
    state.host.querySelectorAll('[data-large-member-index]').forEach((cell) => { const selected = Number(cell.dataset.largeDay) === state.focus.day && Number(cell.dataset.largeMemberIndex) === state.focus.member; cell.tabIndex = selected ? 0 : -1; cell.classList.toggle('is-selected', selected); if (selected) { cell.focus(); cell.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } });
  }

  function draggedAssignment(source) {
    const entry = entryFor(source.day, source.memberId); const assignments = assignmentsFor(entry);
    let index = source.assignmentId ? assignments.findIndex((item) => String(item.id || '') === String(source.assignmentId)) : -1;
    if (index < 0) index = Number(source.assignmentIndex);
    return index >= 0 && index < assignments.length ? { entry, assignments, index, assignment: assignments[index] } : null;
  }

  function moveAssignmentTo(targetCell, source, targetAssignmentId, placement) {
    if (!source || !state.context.editable || state.showBaseline) return false;
    const targetDay = Number(targetCell.dataset.largeDay); const targetMember = targetCell.dataset.largeMember;
    const moving = draggedAssignment(source); if (!moving) return false;
    const sameCell = source.day === targetDay && String(source.memberId) === String(targetMember);
    if (sameCell && (!targetAssignmentId || String(targetAssignmentId) === String(moving.assignment.id || ''))) return false;
    const targetEntry = entryFor(targetDay, targetMember); const targetAssignments = assignmentsFor(targetEntry);
    if (!sameCell && isSubstituteMember(targetMember) && !(targetEntry && targetEntry.employee_name)) {
      flash('空の代務セルへ移動する前に、ダブルクリックして担当者を選択してください', 'error');
      return false;
    }
    if (!sameCell && targetAssignments.length >= 12) { flash('1つのセルに登録できる勤務は12件までです', 'error'); return false; }
    const nextTargetAssignments = clone(sameCell ? moving.assignments.filter((_, index) => index !== moving.index) : targetAssignments);
    let insertIndex = nextTargetAssignments.length;
    if (targetAssignmentId) {
      const targetIndex = nextTargetAssignments.findIndex((item) => String(item.id || '') === String(targetAssignmentId));
      if (targetIndex >= 0) insertIndex = placement === 'before' ? targetIndex : targetIndex + 1;
    }
    nextTargetAssignments.splice(insertIndex, 0, clone(moving.assignment));
    if (sameCell) {
      if (nextTargetAssignments.map((item) => item.id).join('\u0000') === moving.assignments.map((item) => item.id).join('\u0000')) return false;
      setEntry(targetDay, targetMember, { assignments: nextTargetAssignments, value: nextTargetAssignments[0] ? nextTargetAssignments[0].code_key : '' });
      state.focus = { day: targetDay, member: Number(targetCell.dataset.largeMemberIndex) }; state.suppressCellClick = true; state.dragAssignment = null; state.mouseDrag = null;
      flash(`${assignmentLabel(moving.assignment)}を並べ替えました`, 'success'); render();
      setTimeout(() => { state.suppressCellClick = false; }, 0); return true;
    }
    const targetPayload = transferableEntry(targetEntry || {});
    targetPayload.assignments = nextTargetAssignments; targetPayload.value = nextTargetAssignments[0].code_key;
    setEntry(targetDay, targetMember, targetPayload);
    const nextSourceAssignments = moving.assignments.filter((_, index) => index !== moving.index);
    setEntry(source.day, source.memberId, { assignments: nextSourceAssignments, value: nextSourceAssignments[0] ? nextSourceAssignments[0].code_key : '' });
    state.focus = { day: targetDay, member: Number(targetCell.dataset.largeMemberIndex) }; state.suppressCellClick = true; state.dragAssignment = null; state.mouseDrag = null;
    flash(`${assignmentLabel(moving.assignment)}を移動しました`, 'success'); render();
    setTimeout(() => { state.suppressCellClick = false; }, 0); return true;
  }

  function bindAssignmentChip(chip) {
    const synced = chip.classList.contains('is-synced');
    const source = () => ({ day: Number(chip.dataset.largeShiftDay), memberId: chip.dataset.largeShiftMember, assignmentIndex: Number(chip.dataset.largeAssignmentIndex), assignmentId: chip.dataset.largeAssignmentId || '' });
    const targetCell = chip.closest('.cloud-large-cell');
    const placement = (event) => { const rect = chip.getBoundingClientRect(); return event.clientY < rect.top + rect.height / 2 ? 'before' : 'after'; };
    chip.addEventListener('mousedown', (event) => { if (!synced && event.button === 0 && !event.target.closest('.cloud-large-job-delete') && state.context.editable && !state.showBaseline) state.mouseDrag = source(); });
    chip.addEventListener('click', (event) => {
      if (state.suppressCellClick || event.target.closest('.cloud-large-job-drag,.cloud-large-job-delete')) return;
      event.preventDefault(); event.stopPropagation();
      focusCell(source().day, Number(targetCell.dataset.largeMemberIndex));
      if (!state.showBaseline) openCell(source().day, source().memberId);
    });
    chip.addEventListener('dblclick', (event) => { event.preventDefault(); event.stopPropagation(); });
    chip.querySelector('.cloud-large-job-drag')?.addEventListener('click', (event) => { event.preventDefault(); event.stopPropagation(); });
    chip.querySelector('.cloud-large-job-delete')?.addEventListener('click', (event) => {
      event.preventDefault(); event.stopPropagation();
      const current = draggedAssignment(source()); if (!current) return;
      const remaining = current.assignments.filter((_, index) => index !== current.index);
      setEntry(source().day, source().memberId, { assignments: remaining, value: remaining[0] ? remaining[0].code_key : '' });
      flash(`${assignmentLabel(current.assignment)}を削除しました`, 'success'); render();
    });
    chip.addEventListener('dragstart', (event) => { event.stopPropagation(); state.dragAssignment = source(); state.mouseDrag = null; state.suppressCellClick = true; chip.classList.add('is-dragging'); if (event.dataTransfer) { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', JSON.stringify({ cloudshift_large_assignment_move: 1 })); } });
    chip.addEventListener('dragover', (event) => { if (!state.dragAssignment || state.showBaseline) return; event.preventDefault(); event.stopPropagation(); state.host.querySelectorAll('.cloud-large-job-chip.is-drop-before,.cloud-large-job-chip.is-drop-after').forEach((item) => item.classList.remove('is-drop-before', 'is-drop-after')); chip.classList.add(placement(event) === 'before' ? 'is-drop-before' : 'is-drop-after'); if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'; });
    chip.addEventListener('dragleave', () => chip.classList.remove('is-drop-before', 'is-drop-after'));
    chip.addEventListener('drop', (event) => { if (!state.dragAssignment || !targetCell) return; event.preventDefault(); event.stopPropagation(); const sourceData = state.dragAssignment; chip.classList.remove('is-drop-before', 'is-drop-after'); moveAssignmentTo(targetCell, sourceData, chip.dataset.largeAssignmentId || '', placement(event)); });
    chip.addEventListener('dragend', () => { state.host.querySelectorAll('.is-dragging,.is-drop-target,.is-drop-before,.is-drop-after').forEach((item) => item.classList.remove('is-dragging', 'is-drop-target', 'is-drop-before', 'is-drop-after')); state.dragAssignment = null; state.mouseDrag = null; setTimeout(() => { state.suppressCellClick = false; }, 0); });
  }

  function bindCell(cell) {
    cell.addEventListener('click', () => { if (state.suppressCellClick) return; focusCell(Number(cell.dataset.largeDay), Number(cell.dataset.largeMemberIndex)); });
    cell.addEventListener('dblclick', (event) => { event.preventDefault(); if (!state.showBaseline) openCell(Number(cell.dataset.largeDay), cell.dataset.largeMember); });
    cell.addEventListener('focus', () => { state.focus = { day: Number(cell.dataset.largeDay), member: Number(cell.dataset.largeMemberIndex) }; });
    cell.addEventListener('mouseover', (event) => { if (state.mouseDrag && event.buttons === 1) cell.classList.add('is-drop-target'); });
    cell.addEventListener('mouseout', () => cell.classList.remove('is-drop-target'));
    cell.addEventListener('mouseup', () => { const source = state.mouseDrag; state.mouseDrag = null; cell.classList.remove('is-drop-target'); if (source) moveAssignmentTo(cell, source); });
    cell.addEventListener('dragover', (event) => { if (!state.dragAssignment || state.showBaseline) return; event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'; cell.classList.add('is-drop-target'); });
    cell.addEventListener('dragleave', () => cell.classList.remove('is-drop-target'));
    cell.addEventListener('drop', (event) => {
      event.preventDefault(); cell.classList.remove('is-drop-target'); if (!state.dragAssignment || !state.context.editable || state.showBaseline) return;
      const source = state.dragAssignment; state.mouseDrag = null; moveAssignmentTo(cell, source);
    });
    cell.querySelectorAll('[data-large-assignment-index]').forEach(bindAssignmentChip);
    cell.addEventListener('keydown', async (event) => {
      const day = Number(cell.dataset.largeDay); const index = Number(cell.dataset.largeMemberIndex); const memberId = cell.dataset.largeMember;
      if (event.key === 'ArrowUp') { event.preventDefault(); focusCell(day - 1, index); return; }
      if (event.key === 'ArrowDown') { event.preventDefault(); focusCell(day + 1, index); return; }
      if (event.key === 'ArrowLeft') { event.preventDefault(); focusCell(day, index - 1); return; }
      if (event.key === 'ArrowRight') { event.preventDefault(); focusCell(day, index + 1); return; }
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (!state.showBaseline) openCell(day, memberId); return; }
      if ((event.key === 'Delete' || event.key === 'Backspace') && state.context.editable && !state.showBaseline) { event.preventDefault(); clearEntry(day, memberId); render(); return; }
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'c') { event.preventDefault(); state.clipboard = clone(entryFor(day, memberId) || {}); try { await navigator.clipboard.writeText(JSON.stringify({ cloudshift_large: 1, entry: state.clipboard })); } catch (_) { /* browser permission */ } flash('セルをコピーしました'); return; }
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'v' && state.context.editable && !state.showBaseline) { event.preventDefault(); let copied = state.clipboard; try { const text = await navigator.clipboard.readText(); const parsed = JSON.parse(text); if (parsed.cloudshift_large) copied = parsed.entry; } catch (_) { /* use in-memory */ } if (copied) { setEntry(day, memberId, transferableEntry(copied)); render(); } return; }
      if (state.context.editable && !state.showBaseline && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault(); clearTimeout(state.typeTimer); state.typeBuffer += event.key;
        const matches = activeCodes().filter((code) => String(code.key).toLocaleLowerCase().startsWith(state.typeBuffer.toLocaleLowerCase()));
        const exact = matches.find((code) => String(code.key).toLocaleLowerCase() === state.typeBuffer.toLocaleLowerCase());
        // 休日区分の上書きは残す（キーボード入力はダイヤの差し替えだけを行う）。
        if (exact || matches.length === 1) { const code = exact || matches[0]; setEntry(day, memberId, { value: code.key, assignments: [{ id: assignmentId('typed'), code_key: code.key, source_type: 'local', source_project_id: '', source_project_title: '', source_site_id: '' }] }); render(); state.typeBuffer = ''; }
        state.typeTimer = setTimeout(() => { state.typeBuffer = ''; }, 800);
      }
    });
  }

  function conflictKindLabel(kind) {
    return { time_overlap: '勤務時間の重複', double_booking: '二重配置', leave_work: '休みと勤務が同日' }[kind] || kind;
  }

  async function openConflictCheck() {
    if (!state.context.requestJson) return;
    const project = state.context.project; const month = state.context.month;
    const monthKey = `${month.year}-${String(month.month).padStart(2, '0')}`;
    const dialog = modal('重複チェック', '<p class="cloud-large-loading">同じ年月の自分のシフト帳と照合しています…</p>', '<button class="cloud-btn secondary" data-large-close>閉じる</button>');
    const bodyNode = dialog.querySelector('.cloud-large-modal-body');
    try {
      const data = await state.context.requestJson(`/tools/shiftersync/cloudshift/api/project/${project.id}/large-conflict-check`, { method: 'POST', body: { month_key: monthKey } });
      const conflicts = (data && data.conflicts) || [];
      const compared = (data && data.compared_book_count) || 0;
      if (!conflicts.length) {
        bodyNode.innerHTML = `<p class="cloud-large-ok">同じ年月の自分のシフト帳（${compared}件）と照合しました。同一人物の同日二重配置は見つかりませんでした。</p>`;
        return;
      }
      const rows = conflicts.map((item) => `<div class="cloud-large-warning-row is-${item.kind === 'leave_work' ? 'warning' : 'violation'}"><strong>${esc(item.person_label)} / ${item.day}日</strong><span>${esc(item.left.display)} ／ ${esc(item.right.display)}</span><code>${esc(conflictKindLabel(item.kind))}</code></div>`).join('');
      bodyNode.innerHTML = `<p class="cloud-panel-note">同じ年月の自分のシフト帳（${compared}件）を横断し、社員番号を基準に同一人物の同日二重配置を確認しました（個人・現場へ同期反映されたコピーは除外しています）。<strong>${conflicts.length}件</strong>の要確認があります。</p><div class="cloud-large-warning-list">${rows}</div>`;
    } catch (error) {
      bodyNode.innerHTML = `<p class="cloud-large-ok" style="color:#b91c1c;font-weight:600;">重複チェックに失敗しました: ${esc(error.message)}</p>`;
    }
  }

  function bind() {
    state.host.querySelectorAll('[data-large-tab]').forEach((button) => button.addEventListener('click', () => { state.tab = button.dataset.largeTab; render(); if (state.tab === 'summary') refreshServerResult(); }));
    const conflictButton = state.host.querySelector('[data-large-conflict]'); if (conflictButton) conflictButton.addEventListener('click', openConflictCheck);
    state.host.querySelectorAll('[data-large-settings]').forEach((button) => button.addEventListener('click', openSettings));
    state.host.querySelectorAll('[data-large-person]').forEach((button) => button.addEventListener('click', () => openPerson(button.dataset.largePerson)));
    state.host.querySelectorAll('[data-large-day-settings]').forEach((button) => button.addEventListener('click', () => openDay(Number(button.dataset.largeDaySettings))));
    state.host.querySelectorAll('.cloud-large-cell').forEach(bindCell);
    state.host.querySelectorAll('.cloud-large-person-day').forEach((button) => button.addEventListener('click', () => openCell(Number(button.dataset.largeDay), button.dataset.largeMember)));
    const personal = state.host.querySelector('[data-large-personal-select]'); if (personal) personal.addEventListener('change', () => { state.personalMemberId = personal.value; render(); });
    const baselineSet = state.host.querySelector('[data-large-baseline]'); if (baselineSet) baselineSet.addEventListener('click', () => baseline(false));
    const baselineClear = state.host.querySelector('[data-large-baseline-clear]'); if (baselineClear) baselineClear.addEventListener('click', () => baseline(true));
    const baselineView = state.host.querySelector('[data-large-baseline-view]'); if (baselineView) baselineView.addEventListener('click', () => { state.showBaseline = !state.showBaseline; state.tab = 'grid'; render(); });
    state.host.querySelectorAll('[data-large-jump]').forEach((button) => button.addEventListener('click', () => { if (!button.dataset.largeJump) return; const memberIndex = activeMembers().findIndex((member) => String(member.id) === String(button.dataset.largeJumpMember)); state.tab = 'grid'; render(); setTimeout(() => focusCell(Number(button.dataset.largeJump), memberIndex), 0); }));
  }

  function mount(host, context) {
    state.host = host; state.context = context; state.entries = clone(context.entries || {}); state.meta = clone(context.meta || { day_types: {}, day_notes: {} }); state.meta.day_types = state.meta.day_types || {}; state.meta.day_notes = state.meta.day_notes || {}; state.config = clone(context.project.large_config || { version: 1, members: [], codes: [], settings: { break_minutes: 60, scheduled_bind_minutes: 570, paid_leave_work_minutes: null, holiday_rule: { legal_weekdays: [0], scheduled_weekdays: [6], legal_dates: [], scheduled_dates: [] }, checks: {} } }); state.annual = null; state.showBaseline = false;
    if (root.matchMedia && root.matchMedia('(max-width: 720px)').matches && context.page && context.page.accessMode === 'pwa') state.tab = 'personal';
    render();
    // 960対象の年度累計はサーバーでしか出せないため、開いた直後に先読みしておく。
    refreshServerResult({ silent: true, annualOnly: true });
  }

  root.CloudShiftLarge = {
    mount,
    getEntries: () => clone(state.entries),
    getMeta: () => clone(state.meta),
    getResult: () => clone(state.result),
    fingerprint: () => JSON.stringify({ entries: state.entries, meta: state.meta })
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
