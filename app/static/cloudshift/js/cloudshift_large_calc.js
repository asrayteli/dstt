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
  function checks(settings) {
    const incoming = (settings && settings.checks) || {};
    const result = clone(CHECKS);
    Object.keys(result).forEach((key) => Object.assign(result[key], incoming[key] || {}));
    return result;
  }
  function violation(code, severity, personId, day, value, threshold, message) {
    return { code, severity, person_id: personId, day, message, value_minutes: value, threshold_minutes: threshold };
  }
  function calculateDay(day, member, entry, codeMap, settings, type) {
    const rawAssignments = entry && Array.isArray(entry.assignments) && entry.assignments.length
      ? entry.assignments : ((entry && entry.value) ? [{ code_key: entry.value }] : []);
    const hasExternalAssignment = rawAssignments.some((item) => item && String(item.source_type || 'local') !== 'local');
    const codeKeys = [];
    rawAssignments.forEach((item) => { if (item && String(item.source_type || 'local') !== 'local') return; const key = String((item && (item.code_key || item.code)) || '').trim(); if (key && !codeKeys.some((saved) => saved.toLocaleLowerCase() === key.toLocaleLowerCase())) codeKeys.push(key); });
    const base = { day, category: codeKeys.length ? 'empty' : (hasExternalAssignment ? 'external' : 'empty'), code_key: codeKeys.join(' + '), leave_kind: '', requested: false, start_minutes: null, end_minutes: null, bind_minutes: 0, break_minutes: 0, work_minutes: 0, overtime_minutes: 0, warnings: [] };
    if (!codeKeys.length) return base;
    const resolved = codeKeys.map((key) => codeMap[key.toLocaleLowerCase()]).filter((code) => { if (!code) base.warnings.push('CODE_UNDEFINED'); return !!code; });
    if (!resolved.length) return base;
    const leaveCodes = resolved.filter((code) => code.category === 'leave');
    const workCodes = resolved.filter((code) => code.category === 'work');
    if (leaveCodes.length && workCodes.length) base.warnings.push('LEAVE_WITH_WORK');
    if (leaveCodes.length && !workCodes.length) {
      if (leaveCodes.length > 1) base.warnings.push('MULTIPLE_LEAVE_CODES');
      base.category = 'leave'; base.code_key = leaveCodes[0].key; base.leave_kind = leaveCodes[0].leave_kind || ''; base.requested = !!leaveCodes[0].requested; return base;
    }
    base.category = entry && entry.holiday_kind ? `${entry.holiday_kind}_holiday_work` : 'work';
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
    const breakValue = explicitBreaks.length ? explicitBreaks.reduce((total, value) => total + value, 0) : Number(settings.break_minutes == null ? 60 : settings.break_minutes);
    base.break_minutes = Math.min(base.bind_minutes, Math.max(0, breakValue || 0));
    base.work_minutes = base.bind_minutes - base.break_minutes;
    const scheduled = entry && entry.bind_override_minutes != null ? Number(entry.bind_override_minutes)
      : member.scheduled_bind_minutes != null ? Number(member.scheduled_bind_minutes)
      : Number(settings.scheduled_bind_minutes == null ? 570 : settings.scheduled_bind_minutes);
    base.overtime_minutes = base.category === 'work' ? Math.max(0, base.bind_minutes - scheduled) : 0;
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
      for (let day = 1; day <= count; day += 1) {
        const result = calculateDay(day, member, byDay[String(day)][String(member.id)] || {}, codeMap, settings, dayType(year, month, day, overrides, input.holidays || []));
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
      const totals = {
        calendar_days: count,
        work_days: personDays.filter((day) => ['work', 'scheduled_holiday_work', 'legal_holiday_work'].includes(day.category) && day.bind_minutes > 0).length,
        bind_total_minutes: bind, break_total_minutes: sum('break_minutes'), work_total_minutes: work,
        payroll_overtime_minutes: payroll, scheduled_holiday_work_minutes: scheduled, legal_holiday_work_minutes: legal,
        payroll_excess_total_minutes: payroll + scheduled + legal, anei_base_minutes: baseMinutes,
        anei_excess_minutes: anei, leave_counts: leaveCounts, max_consecutive_work_days: maximum
      };
      Object.keys(totals).filter((key) => key.endsWith('_minutes')).forEach((key) => { totals[key.replace(/_minutes$/, '_hhmm')] = hhmm(totals[key]); });
      const person = { person_id: member.id, label: member.display_name, days: personDays, totals, violations };
      people.push(person); allViolations.push(...violations);
    });
    return { engine_version: '1.0.0-js', year, month, people, violations: allViolations };
  }

  return { calculate, hhmm, minutes, dayType };
});
