/** Pure timezone and interval helpers. No DOM, provider or storage dependency. */
export function dayKey(value, zone) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return value;
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return '';
  return new Intl.DateTimeFormat('sv-SE', {
    timeZone: zone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).format(d);
}
export function shiftDay(key, delta) {
  const d = new Date(key + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + delta);
  return d.toISOString().slice(0, 10);
}
export function wallInput(value, zone) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return value + 'T00:00';
  const p = new Intl.DateTimeFormat('en-GB', {
    timeZone: zone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  }).formatToParts(new Date(value));
  const get = k => p.find(x => x.type === k)?.value;
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`;
}
export function wallToISO(value, zone) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) throw Error('请选择有效日期和时间。');
  if (!zone) {
    const d = new Date(value);
    if (!Number.isFinite(d.getTime())) throw Error('无效时间');
    return d.toISOString();
  }
  const target = Date.parse(value + 'Z');
  let guess = target;
  for (let i = 0; i < 5; i++) {
    const shown = Date.parse(wallInput(new Date(guess).toISOString(), zone) + 'Z');
    if (shown === target) return new Date(guess).toISOString();
    guess += target - shown;
  }
  throw Error('所选时间不存在，可能处于夏令时切换，请重新选择。');
}
export function segments(events, key, zone) {
  const from = Date.parse(wallToISO(key + 'T00:00', zone)),
    to = Date.parse(wallToISO(shiftDay(key, 1) + 'T00:00', zone)),
    rows = [];
  for (const event of events) {
    if (event.all_day) {
      if (event.start_time?.slice(0, 10) <= key && key < event.end_time?.slice(0, 10)) rows.push({
        event,
        allDay: true,
        start: 0,
        end: 1440
      });
      continue;
    }
    const a = Date.parse(event.start_time),
      b = Date.parse(event.end_time);
    if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a || a >= to || b <= from) continue;
    const minute = v => {
      const s = wallInput(v, zone).slice(11);
      return Number(s.slice(0, 2)) * 60 + Number(s.slice(3));
    };
    const start = a < from ? 0 : minute(event.start_time),
      end = b >= to ? 1440 : minute(event.end_time);
    rows.push({
      event,
      allDay: false,
      start,
      end: Math.max(start + 1, end),
      continued: a < from,
      continues: b > to
    });
  }
  return rows.sort((a, b) => Number(b.allDay) - Number(a.allDay) || a.start - b.start || a.end - b.end);
}
export function layoutIntervals(rows) {
  const result = rows.filter(r => !r.allDay).map(r => ({
    ...r
  })).sort((a, b) => a.start - b.start || b.end - a.end);
  let group = [],
    ends = [],
    groupEnd = -1;
  const finish = () => {
    for (const r of group) r.columns = ends.length;
    group = [];
    ends = [];
  };
  for (const r of result) {
    if (r.start >= groupEnd) {
      finish();
      groupEnd = -1;
    }
    let col = ends.findIndex(end => end <= r.start);
    if (col < 0) col = ends.length;
    ends[col] = r.end;
    r.column = col;
    group.push(r);
    groupEnd = Math.max(groupEnd, r.end);
  }
  finish();
  return result;
}
export function gaps(rows, from = 540, to = 1080) {
  if (rows.some(r => r.allDay)) return [];
  let cursor = from;
  const result = [];
  for (const r of [...rows].sort((a, b) => a.start - b.start)) {
    if (r.end <= from || r.start >= to) continue;
    if (r.start > cursor) result.push([cursor, Math.min(r.start, to)]);
    cursor = Math.max(cursor, r.end);
  }
  if (cursor < to) result.push([cursor, to]);
  return result.filter(([a, b]) => b - a >= 30);
}
export function conflictCount(rows) {
  const r = rows.filter(r => !r.allDay);
  let n = 0;
  for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) if (r[i].start < r[j].end && r[j].start < r[i].end) n++;
  return n;
}
export const clockLabel = n => `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
