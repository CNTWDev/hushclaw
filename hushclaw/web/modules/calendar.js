/**
 * modules/calendar.js — Calendar panel UI
 *
 * State: calState holds all events + current view/month.
 * The panel supports Month grid view and Agenda (list) view.
 * Events are created/edited via a shared modal form.
 */

import { send, calendarCfg } from "./state.js";
import { openConfirm } from "./modal.js";

// ─── Internal state ───────────────────────────────────────────────────────────

let _syncTimeoutId = null;   // module-scope so resetCalSyncUi() can clear it

const calState = {
  events: [],           // all loaded calendar_events from server
  year: new Date().getFullYear(),
  month: new Date().getMonth(), // 0-indexed
  view: "month",        // "month" | "agenda"
  editingId: null,      // event_id being edited, or null for new
  selectedColor: "indigo",
};

// ─── Color map ────────────────────────────────────────────────────────────────

const COLOR_HEX = {
  indigo:  "#6366f1",
  sky:     "#0ea5e9",
  emerald: "#10b981",
  amber:   "#f59e0b",
  rose:    "#f43f5e",
  violet:  "#8b5cf6",
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ─── Timezone helpers ─────────────────────────────────────────────────────────

/** Returns the effective display timezone (configured > browser fallback). */
function _tz() {
  return calendarCfg.timezone || undefined; // undefined = browser's local timezone
}

// Cached Intl.DateTimeFormat for isoToDateKey — re-created only on tz change.
let _dtfTz = undefined;
let _dtfFmt = null;
function _getDateFmt() {
  const tz = _tz();
  if (tz !== _dtfTz) {
    _dtfTz = tz;
    _dtfFmt = new Intl.DateTimeFormat("sv-SE", tz ? { timeZone: tz } : {});
  }
  return _dtfFmt;
}

/**
 * Convert an ISO string to the "YYYY-MM-DD" date key in the effective timezone.
 * Uses sv-SE locale which produces ISO-format dates (YYYY-MM-DD).
 */
function isoToDateKey(isoStr) {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    if (isNaN(d)) return isoStr.slice(0, 10);
    return _getDateFmt().format(d);
  } catch { return isoStr.slice(0, 10); }
}

function formatDate(isoStr) {
  if (!isoStr) return "";
  const d = new Date(isoStr);
  if (isNaN(d)) return isoStr;
  const opts = { month: "short", day: "numeric", year: "numeric" };
  const tz = _tz();
  if (tz) opts.timeZone = tz;
  return d.toLocaleDateString(undefined, opts);
}

function formatTime(isoStr) {
  if (!isoStr) return "";
  const d = new Date(isoStr);
  if (isNaN(d)) return "";
  const opts = { hour: "2-digit", minute: "2-digit" };
  const tz = _tz();
  if (tz) opts.timeZone = tz;
  return d.toLocaleTimeString(undefined, opts);
}

/**
 * Convert an ISO UTC string to a value for <input type="datetime-local">,
 * displayed in the effective timezone.
 */
function isoToLocalInput(isoStr) {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    if (isNaN(d)) return "";
    const tz = _tz();
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(d);
    const get = type => (parts.find(p => p.type === type)?.value ?? "00");
    const h = get("hour") === "24" ? "00" : get("hour"); // midnight edge case
    return `${get("year")}-${get("month")}-${get("day")}T${h}:${get("minute")}`;
  } catch { return ""; }
}

/**
 * Convert a datetime-local value (wall-clock in the effective timezone) to
 * a UTC ISO-8601 string with Z suffix for storage.
 */
function localInputToIso(localStr) {
  if (!localStr) return "";
  try {
    const tz = _tz();
    if (!tz) {
      // No configured timezone: browser interprets datetime-local as local time
      const d = new Date(localStr);
      return isNaN(d) ? localStr : d.toISOString().slice(0, 19) + "Z";
    }
    // Treat localStr as wall-clock time in tz.
    // Method: parse as UTC, compute tz offset at that moment, subtract.
    const asUtc = new Date(localStr + "Z"); // interpret wall clock as UTC momentarily
    if (isNaN(asUtc)) return localStr;
    // Find what the tz shows for that UTC moment
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(asUtc);
    const get = type => parseInt(parts.find(p => p.type === type)?.value ?? "0");
    const shownMs = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"));
    // tzOffsetMs = shownMs - asUtc.getTime()  (positive for UTC+ zones)
    const tzOffsetMs = shownMs - asUtc.getTime();
    const utcMs = asUtc.getTime() - tzOffsetMs;
    return new Date(utcMs).toISOString().slice(0, 19) + "Z";
  } catch { return localStr; }
}

function eventsOnDate(year, month, day) {
  const targetKey = `${year}-${String(month+1).padStart(2,"0")}-${String(day).padStart(2,"0")}`;
  return calState.events.filter(e => isoToDateKey(e.start_time) === targetKey);
}

function monthTitle(year, month) {
  return new Date(year, month, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

// ─── Month grid renderer ──────────────────────────────────────────────────────

function renderMonthView() {
  const grid = document.getElementById("cal-month-grid");
  if (!grid) return;

  const { year, month } = calState;
  document.getElementById("cal-title").textContent = monthTitle(year, month);

  const firstDay = new Date(year, month, 1).getDay(); // 0=Sun
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const today = new Date();
  const todayY = today.getFullYear(), todayM = today.getMonth(), todayD = today.getDate();
  const now = new Date();

  // Build date-index in ONE pass (O(n)) instead of 31× eventsOnDate (O(31n)).
  const monthPrefix = `${year}-${String(month + 1).padStart(2, "0")}`;
  const byDate = new Map();
  for (const e of calState.events) {
    const key = isoToDateKey(e.start_time);
    if (!key.startsWith(monthPrefix)) continue;
    if (!byDate.has(key)) byDate.set(key, []);
    byDate.get(key).push(e);
  }

  let html = "";
  // Leading empty cells
  for (let i = 0; i < firstDay; i++) {
    html += `<div class="cal-day-cell cal-day-empty"></div>`;
  }
  // Day cells
  for (let d = 1; d <= daysInMonth; d++) {
    const dayOfWeek = (firstDay + d - 1) % 7; // 0=Sun,6=Sat
    const isWeekend = dayOfWeek === 0 || dayOfWeek === 6;
    const isToday = todayY === year && todayM === month && todayD === d;
    const isPast = new Date(year, month, d) < new Date(todayY, todayM, todayD);
    const targetKey = `${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const evs = byDate.get(targetKey) || [];

    const chipsHtml = evs.map(e => {
      const hex = COLOR_HEX[e.color] || COLOR_HEX.indigo;
      const timeStr = e.all_day ? "" : formatTime(e.start_time);
      const timeHtml = timeStr ? `<span class="cal-chip-time">${escHtml(timeStr)}</span>` : "";
      return `<div class="cal-event-chip" data-id="${escHtml(e.event_id)}" style="--chip-color:${hex}" title="${escHtml(e.title)}">${timeHtml}${escHtml(e.title)}</div>`;
    }).join("");

    const progressHtml = isToday ? (() => {
      const pct = Math.round((now.getHours() * 60 + now.getMinutes()) / 14.4); // 0–100
      return `<div class="cal-day-today-bar" style="--progress:${pct}%"></div>`;
    })() : "";

    const classes = ["cal-day-cell",
      isToday   ? "cal-today"      : "",
      isPast    ? "cal-day-past"   : "",
      isWeekend ? "cal-day-weekend": "",
    ].filter(Boolean).join(" ");

    html += `<div class="${classes}">
      <span class="cal-day-num">${d}</span>
      <div class="cal-day-chips">${chipsHtml}</div>
      ${progressHtml}
    </div>`;
  }
  grid.innerHTML = html;

  // Attach click listeners to chips
  grid.querySelectorAll(".cal-event-chip").forEach(chip => {
    chip.addEventListener("click", e => {
      e.stopPropagation();
      openEditModal(chip.dataset.id);
    });
  });
}

// ─── Agenda view renderer ─────────────────────────────────────────────────────

function renderAgendaView() {
  const list = document.getElementById("cal-agenda-list");
  if (!list) return;
  document.getElementById("cal-title").textContent = monthTitle(calState.year, calState.month);

  // Show events for the current month
  const { year, month } = calState;
  const monthPrefix = `${year}-${String(month+1).padStart(2,"0")}`;
  const monthEvents = calState.events.filter(e => isoToDateKey(e.start_time).startsWith(monthPrefix));

  if (!monthEvents.length) {
    list.innerHTML = `<div class="cal-empty">No events this month.</div>`;
    return;
  }

  const now = new Date();
  const nowMs = now.getTime();
  const todayKey = isoToDateKey(now.toISOString());

  // Group by date (in configured timezone)
  const byDate = {};
  for (const e of monthEvents) {
    const dateKey = isoToDateKey(e.start_time);
    if (!byDate[dateKey]) byDate[dateKey] = [];
    byDate[dateKey].push(e);
  }

  let html = "";
  for (const dateKey of Object.keys(byDate).sort()) {
    const isToday = dateKey === todayKey;
    const label = new Date(dateKey + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    const todayBadge = isToday ? `<span class="cal-today-badge">TODAY</span>` : "";
    html += `<div class="cal-agenda-day">
      <div class="cal-agenda-date${isToday ? " cal-today-date" : ""}">${escHtml(label)}${todayBadge}</div>`;

    // Sort events by start_time within the day
    const sorted = [...byDate[dateKey]].sort((a, b) => (a.start_time || "").localeCompare(b.start_time || ""));
    let nowLineInserted = false;

    for (const e of sorted) {
      const endMs = e.all_day ? Infinity : new Date(e.end_time).getTime();
      const startMs = e.all_day ? 0 : new Date(e.start_time).getTime();
      const isPast = !e.all_day && endMs < nowMs;

      // Insert now-line before the first upcoming event in today's group
      if (isToday && !nowLineInserted && !e.all_day && startMs >= nowMs) {
        nowLineInserted = true;
        const timeLabel = now.toLocaleTimeString(undefined, {
          hour: "2-digit", minute: "2-digit",
          ...((_tz()) ? { timeZone: _tz() } : {}),
        });
        html += `<div class="cal-now-line">
          <div class="cal-now-dot"></div>
          <div class="cal-now-line-bar"></div>
          <span class="cal-now-label">${escHtml(timeLabel)}</span>
        </div>`;
      }

      const hex = COLOR_HEX[e.color] || COLOR_HEX.indigo;
      const timeStr = e.all_day ? "All day" : formatTime(e.start_time);
      html += `<div class="cal-agenda-event${isPast ? " cal-event-past" : ""}" data-id="${escHtml(e.event_id)}">
        <span class="cal-agenda-dot" style="background:${hex}"></span>
        <span class="cal-agenda-time">${escHtml(timeStr)}</span>
        <span class="cal-agenda-title">${escHtml(e.title)}</span>
        ${e.location ? `<span class="cal-agenda-loc">@ ${escHtml(e.location)}</span>` : ""}
        <div class="cal-agenda-actions">
          <button class="cal-edit-btn muted-btn small" data-id="${escHtml(e.event_id)}">Edit</button>
          <button class="cal-del-btn muted-btn small" data-id="${escHtml(e.event_id)}">✕</button>
        </div>
      </div>`;
    }

    // If all of today's events are past (no upcoming found), append now-line at end
    if (isToday && !nowLineInserted) {
      const timeLabel = now.toLocaleTimeString(undefined, {
        hour: "2-digit", minute: "2-digit",
        ...((_tz()) ? { timeZone: _tz() } : {}),
      });
      html += `<div class="cal-now-line">
        <div class="cal-now-dot"></div>
        <div class="cal-now-line-bar"></div>
        <span class="cal-now-label">${escHtml(timeLabel)}</span>
      </div>`;
    }

    html += `</div>`;
  }
  list.innerHTML = html;

  list.querySelectorAll(".cal-edit-btn").forEach(btn => {
    btn.addEventListener("click", () => openEditModal(btn.dataset.id));
  });
  list.querySelectorAll(".cal-del-btn").forEach(btn => {
    btn.addEventListener("click", () => confirmDeleteEvent(btn.dataset.id));
  });
  list.querySelectorAll(".cal-agenda-event").forEach(row => {
    row.addEventListener("click", e => {
      if (!e.target.closest("button")) openEditModal(row.dataset.id);
    });
  });

  // Auto-scroll now-line into view when showing current month
  if (year === now.getFullYear() && month === now.getMonth()) {
    setTimeout(() => list.querySelector(".cal-now-line")?.scrollIntoView({ block: "center", behavior: "smooth" }), 60);
  }
}

// ─── Render dispatcher ────────────────────────────────────────────────────────

function renderCalendar() {
  if (calState.view === "month") {
    renderMonthView();
  } else {
    renderAgendaView();
  }
}

// ─── Modal logic ──────────────────────────────────────────────────────────────

function openNewModal() {
  calState.editingId = null;
  calState.selectedColor = "indigo";

  const now = new Date();
  const later = new Date(now.getTime() + 60 * 60 * 1000);

  document.getElementById("cal-modal-title").textContent = "New Event";
  document.getElementById("cal-ev-title").value = "";
  document.getElementById("cal-ev-allday").checked = false;
  document.getElementById("cal-ev-start").value = isoToLocalInput(now.toISOString());
  document.getElementById("cal-ev-end").value = isoToLocalInput(later.toISOString());
  document.getElementById("cal-ev-location").value = "";
  document.getElementById("cal-ev-desc").value = "";
  document.getElementById("cal-modal-delete").classList.add("hidden");
  syncColorSwatches("indigo");
  document.getElementById("cal-modal").classList.remove("hidden");
  document.getElementById("cal-ev-title").focus();
}

function openEditModal(eventId) {
  const ev = calState.events.find(e => e.event_id === eventId);
  if (!ev) return;

  calState.editingId = eventId;
  calState.selectedColor = ev.color || "indigo";

  document.getElementById("cal-modal-title").textContent = "Edit Event";
  document.getElementById("cal-ev-title").value = ev.title || "";
  document.getElementById("cal-ev-allday").checked = !!ev.all_day;
  document.getElementById("cal-ev-start").value = isoToLocalInput(ev.start_time);
  document.getElementById("cal-ev-end").value = isoToLocalInput(ev.end_time);
  document.getElementById("cal-ev-location").value = ev.location || "";
  document.getElementById("cal-ev-desc").value = ev.description || "";
  document.getElementById("cal-modal-delete").classList.remove("hidden");
  syncColorSwatches(calState.selectedColor);
  document.getElementById("cal-modal").classList.remove("hidden");
  document.getElementById("cal-ev-title").focus();
}

function closeModal() {
  document.getElementById("cal-modal").classList.add("hidden");
  calState.editingId = null;
}

function syncColorSwatches(color) {
  document.querySelectorAll(".cal-color-swatch").forEach(s => {
    s.classList.toggle("active", s.dataset.color === color);
  });
  calState.selectedColor = color;
}

function saveModal() {
  const title = document.getElementById("cal-ev-title").value.trim();
  if (!title) {
    document.getElementById("cal-ev-title").focus();
    return;
  }
  const allDay = document.getElementById("cal-ev-allday").checked;
  const startRaw = document.getElementById("cal-ev-start").value;
  const endRaw = document.getElementById("cal-ev-end").value;
  const start_time = localInputToIso(startRaw);
  const end_time = localInputToIso(endRaw);
  if (!start_time || !end_time) {
    alert("Please set start and end times.");
    return;
  }
  const payload = {
    title,
    start_time,
    end_time,
    all_day: allDay,
    location: document.getElementById("cal-ev-location").value.trim(),
    description: document.getElementById("cal-ev-desc").value.trim(),
    color: calState.selectedColor,
  };

  if (calState.editingId) {
    send({ type: "update_calendar_event", event_id: calState.editingId, ...payload });
  } else {
    send({ type: "create_calendar_event", ...payload });
  }
  closeModal();
}

async function confirmDeleteEvent(eventId) {
  const ev = calState.events.find(e => e.event_id === eventId);
  const title = ev ? ev.title : eventId;
  const ok = await openConfirm({
    title: "Delete event",
    message: `Delete "${title}"?`,
    confirmText: "Delete",
    cancelText: "Cancel",
  });
  if (ok) {
    send({ type: "delete_calendar_event", event_id: eventId });
    closeModal();
  }
}

// ─── Public API (called by websocket.js) ──────────────────────────────────────

// Guard: only attempt the silent auto-detect save once per session.
let _tzAutoSaveDone = false;

/**
 * Show a banner if the configured timezone differs from the browser's current
 * system timezone. Called after config_status is received.
 */
export function checkCalendarTimezone() {
  const configTz = calendarCfg.timezone;
  const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const existing = document.getElementById("cal-tz-banner");
  if (existing) existing.remove();

  // First-time setup: no timezone configured yet — auto-detect and persist
  // via a minimal save_config (no wizard UI side-effects, once per session).
  if (!configTz) {
    calendarCfg.timezone = browserTz;
    if (!_tzAutoSaveDone) {
      _tzAutoSaveDone = true;
      send({
        type: "save_config",
        config: { calendar: { timezone: browserTz } },
        save_client_id: `tz_autodetect_${Date.now()}`,
      });
    }
    return;
  }

  if (configTz === browserTz) return;

  const banner = document.createElement("div");
  banner.id = "cal-tz-banner";
  banner.className = "cal-tz-banner";
  banner.innerHTML = `
    <span class="cal-tz-banner-msg">
      System timezone changed to <strong>${escHtml(browserTz)}</strong>
      (configured: ${escHtml(configTz)}).
    </span>
    <button class="cal-tz-banner-update secondary small">Update</button>
    <button class="cal-tz-banner-dismiss muted-btn small">Dismiss</button>
  `;

  const panel = document.getElementById("panel-calendar");
  if (panel) panel.insertBefore(banner, panel.firstChild);

  banner.querySelector(".cal-tz-banner-update").addEventListener("click", async () => {
    calendarCfg.timezone = browserTz;
    // saveSettings() reads calendarCfg; syncFormToState() skips the calendar
    // block when the integrations form is not open, so our update is preserved.
    const { saveSettings } = await import("./settings/save.js");
    saveSettings();
    banner.remove();
  });
  banner.querySelector(".cal-tz-banner-dismiss").addEventListener("click", () => banner.remove());
}

export function renderCalendarEvents(items) {
  calState.events = Array.isArray(items) ? items : [];
  renderCalendar();
}

/** Called on WebSocket disconnect — immediately re-enables sync buttons. */
export function resetCalSyncUi() {
  if (_syncTimeoutId !== null) {
    clearTimeout(_syncTimeoutId);
    _syncTimeoutId = null;
  }
  const btn = document.getElementById("cal-sync-btn");
  const resyncBtn = document.getElementById("cal-resync-btn");
  const status = document.getElementById("cal-sync-status");
  if (btn) btn.disabled = false;
  if (resyncBtn) resyncBtn.disabled = false;
  if (status && (status.textContent === "Syncing…" || status.textContent === "Clearing & re-syncing…")) {
    status.textContent = "Disconnected";
  }
}

export function onCalendarSyncDone(data) {
  const { count = 0, last_sync = 0, items, error } = data;
  if (Array.isArray(items)) {
    calState.events = items;
    renderCalendar();
  }
  const btn = document.getElementById("cal-sync-btn");
  const resyncBtn = document.getElementById("cal-resync-btn");
  const status = document.getElementById("cal-sync-status");
  if (btn) btn.disabled = false;
  if (resyncBtn) resyncBtn.disabled = false;
  if (_syncTimeoutId !== null) { clearTimeout(_syncTimeoutId); _syncTimeoutId = null; }
  if (status) {
    if (error) {
      status.textContent = `Sync error: ${error}`;
    } else {
      const ts = last_sync ? new Date(last_sync * 1000).toLocaleTimeString() : "";
      status.textContent = ts ? `Synced ${ts} (${count})` : `Synced (${count})`;
    }
  }
}

export function onCalendarEventCreated(item) {
  if (!item) return;
  calState.events = calState.events.filter(e => e.event_id !== item.event_id);
  calState.events.push(item);
  calState.events.sort((a, b) => (a.start_time || "").localeCompare(b.start_time || ""));
  renderCalendar();
}

export function onCalendarEventUpdated(item) {
  if (!item) return;
  const idx = calState.events.findIndex(e => e.event_id === item.event_id);
  if (idx !== -1) calState.events[idx] = item;
  else calState.events.push(item);
  calState.events.sort((a, b) => (a.start_time || "").localeCompare(b.start_time || ""));
  renderCalendar();
}

export function onCalendarEventDeleted(eventId) {
  calState.events = calState.events.filter(e => e.event_id !== eventId);
  renderCalendar();
}

// ─── Init (called once from events.js) ───────────────────────────────────────

export function initCalendar() {
  // Toolbar: prev / next / today
  document.getElementById("cal-prev")?.addEventListener("click", () => {
    calState.month--;
    if (calState.month < 0) { calState.month = 11; calState.year--; }
    renderCalendar();
  });
  document.getElementById("cal-next")?.addEventListener("click", () => {
    calState.month++;
    if (calState.month > 11) { calState.month = 0; calState.year++; }
    renderCalendar();
  });
  document.getElementById("cal-today")?.addEventListener("click", () => {
    const now = new Date();
    calState.year = now.getFullYear();
    calState.month = now.getMonth();
    renderCalendar();
  });

  // View toggle
  document.querySelectorAll(".cal-view-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      calState.view = btn.dataset.view;
      document.querySelectorAll(".cal-view-btn").forEach(b => b.classList.toggle("active", b === btn));
      document.getElementById("cal-month-view").classList.toggle("active", calState.view === "month");
      document.getElementById("cal-agenda-view").classList.toggle("active", calState.view === "agenda");
      renderCalendar();
    });
  });

  // New event button
  document.getElementById("cal-new-btn")?.addEventListener("click", openNewModal);

  // CalDAV sync button
  document.getElementById("cal-sync-btn")?.addEventListener("click", () => {
    const btn = document.getElementById("cal-sync-btn");
    const status = document.getElementById("cal-sync-status");
    if (btn) btn.disabled = true;
    if (status) status.textContent = "Syncing…";
    // Client-side timeout: re-enable the button if no response within 45s
    _syncTimeoutId = setTimeout(() => {
      if (btn) btn.disabled = false;
      if (status) status.textContent = "Sync timed out";
      _syncTimeoutId = null;
    }, 45_000);
    send({ type: "force_sync_caldav" });
  });

  // Re-sync button: clear all CalDAV events then pull fresh
  document.getElementById("cal-resync-btn")?.addEventListener("click", async () => {
    const ok = await openConfirm({
      title: "Full Re-sync",
      message: "This will delete all CalDAV-sourced events and re-import from scratch. Locally-created events are not affected.",
      confirmText: "Re-sync",
      cancelText: "Cancel",
    });
    if (!ok) return;
    const btn = document.getElementById("cal-resync-btn");
    const syncBtn = document.getElementById("cal-sync-btn");
    const status = document.getElementById("cal-sync-status");
    if (btn) btn.disabled = true;
    if (syncBtn) syncBtn.disabled = true;
    if (status) status.textContent = "Clearing & re-syncing…";
    _syncTimeoutId = setTimeout(() => {
      if (btn) btn.disabled = false;
      if (syncBtn) syncBtn.disabled = false;
      if (status) status.textContent = "Re-sync timed out";
      _syncTimeoutId = null;
    }, 100_000);
    send({ type: "full_resync_caldav" });
  });

  // Modal buttons
  document.getElementById("cal-modal-cancel")?.addEventListener("click", closeModal);
  document.getElementById("cal-modal-save")?.addEventListener("click", saveModal);
  document.getElementById("cal-modal-delete")?.addEventListener("click", () => {
    if (calState.editingId) confirmDeleteEvent(calState.editingId);
  });

  // Color swatches
  document.querySelectorAll(".cal-color-swatch").forEach(swatch => {
    swatch.addEventListener("click", () => syncColorSwatches(swatch.dataset.color));
  });

  // Close modal on backdrop click
  document.getElementById("cal-modal")?.addEventListener("click", e => {
    if (e.target === document.getElementById("cal-modal")) closeModal();
  });

  // Close modal on Escape
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !document.getElementById("cal-modal").classList.contains("hidden")) {
      closeModal();
    }
  });

  // All-day toggle: disable time inputs when checked
  document.getElementById("cal-ev-allday")?.addEventListener("change", e => {
    const timeRow = document.querySelector(".cal-time-row");
    if (timeRow) timeRow.classList.toggle("allday-mode", e.target.checked);
  });

  // Re-check timezone whenever the user returns to this tab (catches OS tz changes).
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkCalendarTimezone();
  });

  // Initial title render
  document.getElementById("cal-title").textContent = monthTitle(calState.year, calState.month);

  // Auto-refresh every minute to keep now-line and progress bar current
  setInterval(() => {
    const now = new Date();
    if (calState.year === now.getFullYear() && calState.month === now.getMonth()) {
      renderCalendar();
    }
  }, 60_000);
}
