(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.CloudShiftLargeCalc = api;
})(typeof globalThis !== 'undefined' ? globalThis : window, function() {
  'use strict';

  const CHECKS = {
    kaizen_monthly_bind: { enabled: true, warn_minutes: 16860 },
    kaizen_daily_bind: { enabled: true, warn_minutes: 780, max_minutes: 900 },
    kaizen_rest_period: { enabled: true, min_minutes: 540 },
    overtime_monthly: { enabled: false, warn_minutes: 2700 },
    anei_long_work: { enabled: false, warn_minutes: 4800 },
    consecutive_days: { enabled: false, warn_days: 7 }
  };

  const DEFAULT_HOLIDAY_RULE = { legal_weekdays: [0], scheduled_weekdays: [6], legal_dates: [], scheduled_dates: [] };

  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function minutes(text, allow24) {
    const match = String(text || '').match(/^(\d{1,2}):(\d{2})$/);
    if (!match) return null;
    const hour = Number(match[1]); const minute = Number(match[2]);
    if (minute > 59 || hour > (allow24 ? 24 : 23) || (hour === 24 && minute !== 0)) return null;
    return hour * 60 + minute;
  }
  function hhmm(value) {
    const v = Math.max(0, Number(value) || 0);
    return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, '0')}`;
  }
  function daysInMonth(year, month) { return new Date(year, month, 0).getDate(); }
  function iso(year, month, day) { return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`; }
  function dayType(year, month, day, overrides, holidays) {
    const override = String((overrides || {})[String(day)] || '');
    if (['weekday', 'saturday', 'holiday'].includes(override)) return override;
    const date = new Date(year, month - 1, day); const weekday = date.getDay();
    if (weekday === 0 || (holidays || []).includes(iso(year, month, day))) return 'holiday';
    return weekday === 6 ? 'saturday' : 'weekday';
  }
  function normalizeRule(rule) {
    const source = rule && typeof rule === 'object' ? rule : {};
    const weekdays = (value) => (Array.isArray(value) ? value : []).map(Number).filter((item) => Number.isInteger(item) && item >= 0 && item <= 6);
    const dates = (value) => (Array.isArray(value) ? value : []).map((item) => String(item || '').trim()).filter(Boolean);
    return {
      legal_weekdays: weekdays(source.legal_weekdays),
      scheduled_weekdays: weekdays(source.scheduled_weekdays),
      legal_dates: dates(source.legal_dates),
      scheduled_dates: dates(source.scheduled_dates)
    };
  }
  // メンバーに適用される休日ルール。個人設定が custom でなければ共通設定を使う。
  function holidayRule(member, settings) {
    const memberRule = (member || {}).holiday_rule;
    if (memberRule && String(memberRule.mode || 'default') === 'custom') return normalizeRule(memberRule);
    const shared = (settings || {}).holiday_rule;
    return normalizeRule(shared === undefined || shared === null ? DEFAULT_HOLIDAY_RULE : shared);
  }
  // 指定日は曜日より強く、法定休日は所定休日より強い（サーバー側と同じ規則）。
  function holidayClass(rule, year, month, day) {
    const normalized = normalizeRule(rule);
    const key = iso(Number(year), Number(month), Number(day));
    const weekday = new Date(Number(year), Number(month) - 1, Number(day)).getDay();
    if (normalized.legal_dates.includes(key)) return 'legal';
    if (normalized.scheduled_dates.includes(key)) return 'scheduled';
    if (normalized.legal_weekdays.includes(weekday)) return 'legal';
    if (normalized.scheduled_weekdays.includes(weekday)) return 'scheduled';
    return '';
  }
  function effectiveHolidayKind(rule, year, month, day, entry) {
    const override = String((entry || {}).holiday_kind || '').trim().toLowerCase();
    if (override === 'none') return '';
    if (override === 'legal' || override === 'scheduled') return override;
    return holidayClass(rule, year, month, day);
  }
  function breakMinutes(entry, codeBreaks, bind, settings) {
    let value;
    if (entry && entry.break_override_minutes != null && entry.break_override_minutes !== '') value = Number(entry.break_override_minutes);
    else if (codeBreaks.length) value = codeBreaks.reduce((total, item) => total + item, 0);
    else value = Number(settings.break_minutes == null ? 60 : settings.break_minutes);
    return Math.min(bind, Math.max(0, value || 0));
  }
  function paidLeaveCredit(code, entry, member, settings) {
    if (!code || code.leave_kind !== 'paid') return 0;
    if (settings.paid_leave_work_minutes != null && settings.paid_leave_work_minutes !== '') return Math.max(0, Number(settings.paid_leave_work_minutes) || 0);
    return Math.max(0, scheduledBind(entry, member, settings) - Number(settings.break_minutes == null ? 60 : settings.break_minutes));
  }
  function scheduledBind(entry, member, settings) {
    if (entry && entry.bind_override_minutes != null && entry.bind_override_minutes !== '') return Number(entry.bind_override_minutes);
    if (member && member.scheduled_bind_minutes != null) return Number(member.scheduled_bind_minutes);
    return Number(settings.scheduled_bind_minutes == null ? 570 : settings.scheduled_bind_minutes);
  }
  function checks(settings) {
    const incoming = (settings && settings.checks) || {};
    const result = clone(CHECKS);
    Object.keys(result).forEach((key) => Object.assign(result[key], incoming[key] || {}));
    return result;
  }
  function violation(code, severity, personId, day, value, threshold, message) {
    return { code, severity, person_id: personId, day, message, value_minutes: value, threshold_minutes: threshold };
  }
  function calculateDay(day, member, entry, codeMap, settings, type, holidayKind) {
    const rawAssignments = entry && Array.isArray(entry.assignments) && entry.assignments.length
      ? entry.assignments : ((entry && entry.value) ? [{ code_key: entry.value }] : []);
    const hasExternalAssignment = rawAssignments.some((item) => item && String(item.source_type || 'local') !== 'local');
    const codeKeys = [];
    rawAssignments.forEach((item) => { if (item && String(item.source_type || 'local') !== 'local') return; const key = String((item && (item.code_key || item.code)) || '').trim(); if (key && !codeKeys.some((saved) => saved.toLocaleLowerCase() === key.toLocaleLowerCase())) codeKeys.push(key); });
    const base = { day, category: codeKeys.length ? 'empty' : (hasExternalAssignment ? 'external' : 'empty'), code_key: codeKeys.join(' + '), leave_kind: '', requested: false, start_minutes: null, end_minutes: null, bind_minutes: 0, break_minutes: 0, work_minutes: 0, overtime_minutes: 0, warnings: [], paid_credit_minutes: 0 };
    if (!codeKeys.length) {
      // 他帳から同期された勤務だけのセルは、時刻上書きがあるときのみ勤務として集計する
      // （サーバー側 worktime_engine と同じ規則）。
      const override = hasExternalAssignment && entry ? entry.time_override : null;
      if (override) {
        const start = minutes(override.start, false); const end = minutes(override.end, true);
        if (start !== null && end !== null && start < end) {
          base.category = holidayKind ? `${holidayKind}_holiday_work` : 'work';
          base.start_minutes = start; base.end_minutes = end;
          base.bind_minutes = end - start;
          base.break_minutes = breakMinutes(entry, [], base.bind_minutes, settings);
          base.work_minutes = base.bind_minutes - base.break_minutes;
          base.overtime_minutes = base.category === 'work' ? Math.max(0, base.bind_minutes - scheduledBind(entry, member, settings)) : 0;
        }
      }
      return base;
    }
    const resolved = codeKeys.map((key) => codeMap[key.toLocaleLowerCase()]).filter((code) => { if (!code) base.warnings.push('CODE_UNDEFINED'); return !!code; });
    if (!resolved.length) return base;
    const leaveCodes = resolved.filter((code) => code.category === 'leave');
    const workCodes = resolved.filter((code) => code.category === 'work');
    if (leaveCodes.length && workCodes.length) base.warnings.push('LEAVE_WITH_WORK');
    if (leaveCodes.length && !workCodes.length) {
      if (leaveCodes.length > 1) base.warnings.push('MULTIPLE_LEAVE_CODES');
      base.category = 'leave'; base.code_key = leaveCodes[0].key; base.leave_kind = leaveCodes[0].leave_kind || ''; base.requested = !!leaveCodes[0].requested;
      base.paid_credit_minutes = paidLeaveCredit(leaveCodes[0], entry, member, settings);
      return base;
    }
    base.category = holidayKind ? `${holidayKind}_holiday_work` : 'work';
    const intervals = []; const explicitBreaks = [];
    workCodes.forEach((code) => {
      let range = workCodes.length === 1 && entry && entry.time_override ? entry.time_override : ((code.times || {})[type] || null);
      if (!range && type !== 'weekday' && (code.times || {}).weekday) { range = code.times.weekday; base.warnings.push('TIME_SET_FALLBACK'); }
      if (!range) { base.warnings.push('TIME_UNDEFINED'); return; }
      const start = minutes(range.start, false); const end = minutes(range.end, true);
      if (start === null || end === null || start >= end) { base.warnings.push('TIME_UNDEFINED'); return; }
      intervals.push([start, end]);
      if (code.break_minutes != null) explicitBreaks.push(Number(code.break_minutes));
    });
    base.code_key = workCodes.map((code) => code.key).join(' + ');
    if (!intervals.length) { base.warnings = [...new Set(base.warnings)]; return base; }
    intervals.sort((left, right) => left[0] - right[0]);
    const merged = [];
    intervals.forEach(([start, end]) => {
      const previous = merged[merged.length - 1];
      if (!previous || start >= previous[1]) merged.push([start, end]);
      else { base.warnings.push('TIME_OVERLAP'); previous[1] = Math.max(previous[1], end); }
    });
    base.start_minutes = intervals[0][0]; base.end_minutes = Math.max(...intervals.map((range) => range[1]));
    base.bind_minutes = merged.reduce((total, range) => total + range[1] - range[0], 0);
    base.break_minutes = breakMinutes(entry, explicitBreaks, base.bind_minutes, settings);
    base.work_minutes = base.bind_minutes - base.break_minutes;
    base.overtime_minutes = base.category === 'work' ? Math.max(0, base.bind_minutes - scheduledBind(entry, member, settings)) : 0;
    base.warnings = [...new Set(base.warnings)];
    return base;
  }

  function calculate(input) {
    const config = input.config || {}; const settings = config.settings || {};
    const year = Number(input.year); const month = Number(input.month); const count = daysInMonth(year, month);
    const codes = (config.codes || []).filter((code) => code.active !== false);
    const codeMap = {}; codes.forEach((code) => { codeMap[String(code.key || '').toLocaleLowerCase()] = code; });
    const entries = input.entries_per_day || {}; const overrides = ((input.meta_data || {}).day_types) || {};
    const byDay = {};
    for (let day = 1; day <= count; day += 1) {
      byDay[String(day)] = {};
      (entries[String(day)] || []).forEach((entry) => { if (entry && entry.member_id) byDay[String(day)][String(entry.member_id)] = entry; });
    }
    const check = checks(settings); const people = []; const allViolations = [];
    const baseMinutes = { 28: 9600, 29: 9900, 30: 10260, 31: 10620 }[count];
    (config.members || []).filter((member) => member.active !== false).forEach((member) => {
      const personDays = []; const violations = []; const leaveCounts = {};
      let consecutive = 0; let maximum = 0; let previousWork = null;
      const rule = holidayRule(member, settings);
      for (let day = 1; day <= count; day += 1) {
        const entry = byDay[String(day)][String(member.id)] || {};
        const result = calculateDay(day, member, entry, codeMap, settings, dayType(year, month, day, overrides, input.holidays || []), effectiveHolidayKind(rule, year, month, day, entry));
        personDays.push(result);
        const working = ['work', 'scheduled_holiday_work', 'legal_holiday_work'].includes(result.category) && result.bind_minutes > 0;
        if (working) {
          consecutive += 1; maximum = Math.max(maximum, consecutive);
          if (check.kaizen_daily_bind.enabled && result.bind_minutes > Number(check.kaizen_daily_bind.warn_minutes)) violations.push(violation('KAIZEN_DAILY_BIND', 'warning', member.id, day, result.bind_minutes, Number(check.kaizen_daily_bind.warn_minutes), `${day}日の拘束時間が目安を超えています`));
          if (check.kaizen_daily_bind.enabled && result.bind_minutes > Number(check.kaizen_daily_bind.max_minutes)) violations.push(violation('KAIZEN_DAILY_BIND_MAX', 'violation', member.id, day, result.bind_minutes, Number(check.kaizen_daily_bind.max_minutes), `${day}日の拘束時間が上限を超えています`));
          if (check.kaizen_rest_period.enabled && previousWork && previousWork.day === day - 1 && result.start_minutes != null && previousWork.end_minutes != null) {
            const rest = (1440 - previousWork.end_minutes) + result.start_minutes;
            if (rest < Number(check.kaizen_rest_period.min_minutes)) violations.push(violation('KAIZEN_REST_PERIOD', 'violation', member.id, day, rest, Number(check.kaizen_rest_period.min_minutes), `${day}日の休息期間が不足しています`));
          }
          previousWork = result;
        } else {
          consecutive = 0; previousWork = null;
          if (result.category === 'leave' || result.category === 'empty') {
            const key = result.category === 'empty' ? 'empty' : `${result.leave_kind || 'other'}${result.requested ? '_requested' : ''}`;
            leaveCounts[key] = (leaveCounts[key] || 0) + 1;
          }
        }
        const warningMessages = {
          CODE_UNDEFINED: '登録されていないシフトです', TIME_UNDEFINED: '勤務時間が設定されていません',
          TIME_SET_FALLBACK: 'この曜日の時間がないため平日の時間を使いました',
          TIME_OVERLAP: '複数の勤務時間が重なっています（重複時間は1回だけ集計しました）',
          LEAVE_WITH_WORK: '勤務と休みが同じ日に入っています（勤務のみ計算しました）',
          MULTIPLE_LEAVE_CODES: '休みが複数入っています（先頭の休みを集計しました）'
        };
        result.warnings.forEach((warning) => violations.push(violation(warning, 'info', member.id, day, 0, 0, warningMessages[warning] || warning)));
      }
      const sum = (key, filter) => personDays.filter(filter || (() => true)).reduce((total, day) => total + Number(day[key] || 0), 0);
      const bind = sum('bind_minutes'); const work = sum('work_minutes');
      const payroll = sum('overtime_minutes', (day) => day.category === 'work');
      const scheduled = sum('work_minutes', (day) => day.category === 'scheduled_holiday_work');
      const legal = sum('work_minutes', (day) => day.category === 'legal_holiday_work');
      const anei = Math.max(0, work - baseMinutes);
      if (check.kaizen_monthly_bind.enabled && bind > Number(check.kaizen_monthly_bind.warn_minutes)) violations.push(violation('KAIZEN_MONTHLY_BIND', 'warning', member.id, null, bind, Number(check.kaizen_monthly_bind.warn_minutes), '月間拘束時間が目安を超えています'));
      if (check.overtime_monthly.enabled && payroll > Number(check.overtime_monthly.warn_minutes)) violations.push(violation('OVERTIME_MONTHLY', 'warning', member.id, null, payroll, Number(check.overtime_monthly.warn_minutes), '給与残業が目安を超えています'));
      if (check.anei_long_work.enabled && anei > Number(check.anei_long_work.warn_minutes)) violations.push(violation('ANEI_LONG_WORK', 'warning', member.id, null, anei, Number(check.anei_long_work.warn_minutes), '長時間労働が目安を超えています'));
      if (check.consecutive_days.enabled && maximum > Number(check.consecutive_days.warn_days)) violations.push(violation('CONSECUTIVE_DAYS', 'warning', member.id, null, maximum * 1440, Number(check.consecutive_days.warn_days) * 1440, '連続勤務日数が目安を超えています'));
      const paidCredit = sum('paid_credit_minutes');
      const totals = {
        calendar_days: count,
        work_days: personDays.filter((day) => ['work', 'scheduled_holiday_work', 'legal_holiday_work'].includes(day.category) && day.bind_minutes > 0).length,
        bind_total_minutes: bind, break_total_minutes: sum('break_minutes'), work_total_minutes: work,
        payroll_overtime_minutes: payroll, scheduled_holiday_work_minutes: scheduled, legal_holiday_work_minutes: legal,
        payroll_excess_total_minutes: payroll + scheduled + legal, anei_base_minutes: baseMinutes,
        anei_excess_minutes: anei, leave_counts: leaveCounts, max_consecutive_work_days: maximum,
        // 1=所定内残業 / 2=所定休日労働 / 3=法定休日労働
        agreement36_minutes: payroll + scheduled + legal,
        special_clause_minutes: payroll + scheduled,
        annual960_minutes: payroll + scheduled,
        paid_leave_credit_minutes: paidCredit,
        paid_leave_days: personDays.filter((day) => day.category === 'leave' && day.leave_kind === 'paid').length,
        work_with_paid_total_minutes: work + paidCredit
      };
      Object.keys(totals).filter((key) => key.endsWith('_minutes')).forEach((key) => { totals[key.replace(/_minutes$/, '_hhmm')] = hhmm(totals[key]); });
      const person = { person_id: member.id, label: member.display_name, days: personDays, totals, violations };
      people.push(person); allViolations.push(...violations);
    });
    return { engine_version: '1.2.0-js', year, month, people, violations: allViolations };
  }

  return { calculate, hhmm, minutes, dayType, holidayRule, holidayClass, effectiveHolidayKind };
});
