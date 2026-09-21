/** My itinerary: rendering only; native sources and interval math stay separate. */
import { send, calendarCfg, state } from './state.js';
import { dayKey, shiftDay, wallInput, segments, timedEventLayout, gaps, conflictCount, clockLabel } from './calendar_math.js';
import { eventDetails, eventEditor, closeEventEditor } from './calendar_forms.js';
import { openCalendarSources, receiveNativeStatus, resetNativeRequests } from './calendar_sources.js';
export const onNativeCalendarStatus = receiveNativeStatus;
const $ = id => document.getElementById(id),
  zone = () => calendarCfg.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
const today = () => dayKey(new Date().toISOString(), zone());
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
})[c]);
export const sourceKey = e => `${e.source || 'local'}:${e.remote_calendar || ''}`;
const data = {
  events: [],
  date: today(),
  view: 'day',
  hidden: new Set(),
  query: ''
};
export function openCalendarSourceSettings() {
  openCalendarSources(data, () => {
    remember();
    render();
  });
}
let serial = 0,
  requestId = '',
  syncTimer = null;
try {
  const p = JSON.parse(localStorage.getItem('hc-itinerary') || '{}');
  if (['day', 'week', 'month', 'agenda'].includes(p.view)) data.view = p.view;
  data.hidden = new Set(p.hidden || []);
} catch {}
function remember() {
  try {
    localStorage.setItem('hc-itinerary', JSON.stringify({
      view: data.view,
      hidden: [...data.hidden]
    }));
  } catch {}
}
function visible() {
  return data.events.filter(e => !data.hidden.has(sourceKey(e)) && (!data.query || `${e.title} ${e.location}`.toLowerCase().includes(data.query.toLowerCase())));
}
function days() {
  if (data.view === 'day') return [data.date];
  if (data.view === 'week') {
    const dow = new Date(data.date + 'T12:00Z').getUTCDay(),
      first = shiftDay(data.date, -((dow + 6) % 7));
    return Array.from({
      length: 7
    }, (_, i) => shiftDay(first, i));
  }
  if (data.view === 'agenda') return Array.from({
    length: 30
  }, (_, i) => shiftDay(data.date, i));
  const first = data.date.slice(0, 7) + '-01',
    dow = new Date(first + 'T12:00Z').getUTCDay();
  return Array.from({
    length: 42
  }, (_, i) => shiftDay(first, i - (dow + 6) % 7));
}
function requestEvents() {
  const d = days();
  requestId = `calendar-${++serial}`;
  send({
    type: 'list_calendar_events',
    request_id: requestId,
    from_time: shiftDay(d[0], -1),
    to_time: shiftDay(d.at(-1), 2)
  });
}
const displayDate = (key, options = {}) => new Date(key + 'T12:00:00').toLocaleDateString(undefined, {
  month: 'short',
  day: 'numeric',
  ...options
});
const clock = iso => new Date(iso).toLocaleTimeString(undefined, {
  timeZone: zone(),
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23'
});
const sourceName = e => ({
  macos: '本机日历',
  google: 'Google',
  caldav: 'CalDAV',
  local: 'HushClaw'
})[e.source || 'local'] || e.source;
function chip(e) {
  return `<button class="it-event" data-event="${esc(e.event_id)}" data-source="${esc(e.source || 'local')}"><strong>${esc(e.title)}</strong><small>${e.all_day ? '全天' : esc(clock(e.start_time))}</small></button>`;
}
function navigate(date, view = data.view) {
  data.date = date;
  data.view = view;
  remember();
  render();
  requestEvents();
}
function render() {
  if (!$('cal-content')) return;
  const events = visible(),
    range = days(),
    current = today();
  $('cal-title').textContent = data.view === 'month' ? data.date.slice(0, 7) : data.view === 'day' ? displayDate(data.date, {
    year: 'numeric',
    weekday: 'short'
  }) : `${displayDate(range[0])} – ${displayDate(range.at(-1))}`;
  $('cal-timezone').textContent = zone();
  $('cal-date-picker').value = data.date;
  document.querySelectorAll('.cal-view-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.view === data.view);
    b.setAttribute('aria-pressed', String(b.dataset.view === data.view));
  });
  const daily = segments(events, data.date, zone());
  const next = events.filter(e => !e.all_day && Date.parse(e.end_time) > Date.now()).sort((a, b) => Date.parse(a.start_time) - Date.parse(b.start_time))[0];
  const conflict = conflictCount(daily),
    free = gaps(daily).sort((a, b) => b[1] - b[0] - (a[1] - a[0]))[0];
  const nextText = next ? `${Date.parse(next.start_time) <= Date.now() ? '进行中' : '下一场'} · ${dayKey(next.start_time, zone()) !== current ? displayDate(dayKey(next.start_time, zone())) + ' ' : ''}${clock(next.start_time)} ${next.title}` : '';
  const dayLabel = data.view === 'day' ? '' : displayDate(data.date) + ' · ';
  $('cal-summary').innerHTML = `<span>${esc(dayLabel + `${daily.length} 项行程`)}</span>${conflict ? `<span class="it-warning">${conflict} 对行程时间重叠</span>` : ''}${free ? `<span class="it-muted">${clockLabel(free[0])}–${clockLabel(free[1])} 当前筛选中无行程</span>` : ''}${data.date === current && next ? `<span class="it-next" title="已加载来源中的下一场行程">${esc(nextText)}</span>` : ''}`;
  const host = $('cal-content'),
    scroll = host.scrollTop,
    scope = data.view + data.date,
    changed = host.dataset.scope !== scope;
  host.dataset.scope = scope;
  host.dataset.view = data.view;
  if (data.view === 'month') month(host, range, events, current);else if (data.view === 'agenda') agenda(host, range, events, current);else timeline(host, range, events, current);
  host.querySelectorAll('[data-event]').forEach(b => b.addEventListener('click', ev => {
    ev.stopPropagation();
    const e = data.events.find(e => e.event_id === b.dataset.event);
    if (e) eventDetails(e, data.date, zone());
  }));
  host.scrollTop = changed && ['day', 'week'].includes(data.view) ? (data.date === current ? Math.max(0, Number(wallInput(new Date().toISOString(), zone()).slice(11, 13)) - 1) : 8) * 52 : scroll;
}
function timeline(host, range, events, current) {
  let html = `<div class="it-time-layout" style="--it-days:${range.length}"><div class="it-time-head"><span></span>${range.map(d => `<button class="${d === current ? 'is-today' : ''}" data-date="${d}">${esc(displayDate(d, {
    weekday: 'short'
  }))}</button>`).join('')}</div><div class="it-allday"><span>全天</span>${range.map(d => `<div>${segments(events, d, zone()).filter(r => r.allDay).map(r => chip(r.event)).join('')}</div>`).join('')}</div><div class="it-time-grid"><div class="it-hours">${Array.from({
    length: 24
  }, (_, i) => `<span>${clockLabel(i * 60)}</span>`).join('')}</div>`;
  for (const d of range) {
    html += `<div class="it-day-column ${d === current ? 'is-today' : ''}">`;
    for (let h = 0; h < 24; h++) html += `<button class="it-slot" data-new="${d}T${String(h).padStart(2, '0')}:00" aria-label="${d} ${h}:00 新建行程" style="top:${h * 52}px"></button>`;
    for (const r of timedEventLayout(segments(events, d, zone()))) {
      const label = `${r.event.title}，${clockLabel(r.start)}–${clockLabel(r.end)}${r.conflict ? '，时间冲突' : ''}`;
      html += `<button class="it-event it-timed" data-density="${r.density}" data-event="${esc(r.event.event_id)}" data-source="${esc(r.event.source || 'local')}" aria-label="${esc(label)}" title="${esc(label)}" style="top:${r.top}px;height:${r.height}px;left:calc(${r.column / r.columns * 100}% + 3px);width:calc(${100 / r.columns}% - 6px)"><strong>${r.continued ? '↳ ' : ''}${esc(r.event.title)}</strong>${r.density !== 'compact' ? `<small>${clockLabel(r.start)}–${clockLabel(r.end)}${r.conflict ? ' · 冲突' : ''}</small>` : ''}${range.length === 1 && r.showLocation ? `<small>${esc(r.event.location || sourceName(r.event))}</small>` : ''}</button>`;
    }
    if (d === current) {
      const text = wallInput(new Date().toISOString(), zone()).slice(11),
        minute = Number(text.slice(0, 2)) * 60 + Number(text.slice(3));
      html += `<div class="it-now" style="top:${minute / 60 * 52}px"><span>${text}</span></div>`;
    }
    html += '</div>';
  }
  host.innerHTML = html + '</div></div>';
  host.querySelectorAll('[data-date]').forEach(b => b.addEventListener('click', () => navigate(b.dataset.date, 'day')));
  host.querySelectorAll('[data-new]').forEach(b => b.addEventListener('click', () => eventEditor(null, data.date, zone(), b.dataset.new)));
}
function month(host, range, events, current) {
  host.innerHTML = `<div class="it-month-head">${['一', '二', '三', '四', '五', '六', '日'].map(d => `<span>周${d}</span>`).join('')}</div><div class="it-month-grid">${range.map(d => {
    const rows = segments(events, d, zone());
    return `<section class="it-month-day ${d === current ? 'is-today' : ''} ${d.slice(0, 7) !== data.date.slice(0, 7) ? 'outside-month' : ''}"><button class="it-date" data-date="${d}">${Number(d.slice(8))}</button>${rows.slice(0, 3).map(r => chip(r.event)).join('')}${rows.length > 3 ? `<button class="it-more" data-date="${d}">还有 ${rows.length - 3} 项</button>` : ''}</section>`;
  }).join('')}</div>`;
  host.querySelectorAll('[data-date]').forEach(b => b.addEventListener('click', () => navigate(b.dataset.date, 'day')));
}
function agenda(host, range, events, current) {
  let html = '';
  for (const d of range) {
    const rows = segments(events, d, zone());
    if (!rows.length) continue;
    html += `<section class="it-agenda-day"><h3>${esc(displayDate(d, {
      weekday: 'short'
    }))}${d === current ? ' · 今天' : ''}</h3>${rows.map(r => `<button class="it-agenda-event" data-event="${esc(r.event.event_id)}"><time>${r.allDay ? '全天' : clockLabel(r.start) + '–' + clockLabel(r.end)}</time><span><strong>${esc(r.event.title)}</strong><small>${esc(r.event.location || '未设置地点')} · ${esc(sourceName(r.event))}${r.event.source && r.event.source !== 'local' ? ' · 只读' : ''}${r.continued ? ' · 跨日行程' : ''}</small></span></button>`).join('')}</section>`;
  }
  host.innerHTML = html || '<div class="it-empty"><strong>未来 30 天没有显示的行程</strong><p>可新建本地行程，或在「日历来源」中选择要展示的日历。</p></div>';
}
export function renderCalendarEvents(items, reply = {}) {
  if (reply.request_id && reply.request_id !== requestId) return;
  data.events = Array.isArray(items) ? items : [];
  render();
}
function resetSyncButton() {
  clearTimeout(syncTimer);
  syncTimer = null;
  if ($('cal-sync-btn')) $('cal-sync-btn').disabled = false;
}
export function resetCalSyncUi() {
  resetSyncButton();
  resetNativeRequests();
}
export function onCalendarSyncDone(reply) {
  resetSyncButton();
  if (Array.isArray(reply.items)) renderCalendarEvents(reply.items);
  $('cal-sync-status').textContent = reply.error ? '部分来源刷新失败：' + reply.error : `已刷新 ${new Date().toLocaleTimeString()}`;
}
export function onCalendarEventCreated(item) {
  if (!item) return;
  closeEventEditor();
  data.events = data.events.filter(e => e.event_id !== item.event_id);
  data.events.push(item);
  render();
}
export const onCalendarEventUpdated = onCalendarEventCreated;
export function onCalendarEventDeleted(id) {
  data.events = data.events.filter(e => e.event_id !== id);
  render();
}
export function checkCalendarTimezone() {
  render();
}
export function initCalendar() {
  function step(n) {
    if (data.view === 'month') {
      const d = new Date(data.date.slice(0, 7) + '-01T12:00Z');
      d.setUTCMonth(d.getUTCMonth() + n);
      navigate(d.toISOString().slice(0, 10));
    } else navigate(shiftDay(data.date, n * (data.view === 'week' ? 7 : data.view === 'agenda' ? 30 : 1)));
  }
  $('cal-prev')?.addEventListener('click', () => step(-1));
  $('cal-next')?.addEventListener('click', () => step(1));
  $('cal-today')?.addEventListener('click', () => navigate(today()));
  document.querySelectorAll('.cal-view-btn').forEach(b => b.addEventListener('click', () => navigate(data.date, b.dataset.view)));
  $('cal-date-picker')?.addEventListener('change', ev => {
    if (ev.target.value) navigate(ev.target.value);
  });
  $('cal-search')?.addEventListener('input', ev => {
    data.query = ev.target.value;
    render();
  });
  $('cal-new-btn')?.addEventListener('click', () => eventEditor(null, data.date, zone()));
  $('cal-sources-btn')?.addEventListener('click', openCalendarSourceSettings);
  $('cal-sync-btn')?.addEventListener('click', () => {
    $('cal-sync-btn').disabled = true;
    $('cal-sync-status').textContent = '正在刷新…';
    syncTimer = setTimeout(() => {
      resetSyncButton();
      $('cal-sync-status').textContent = '刷新超时，保留上次数据';
    }, 125000);
    send({
      type: 'force_sync_caldav'
    });
  });
  setInterval(() => {
    if (!document.hidden && state.tab === 'calendar') {
      render();
      requestEvents();
    }
  }, 60000);
  render();
}
