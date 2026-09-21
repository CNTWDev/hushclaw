import { uiText } from "./i18n.js";
import { send, state } from './state.js';
import { openDialog, openConfirm } from './modal.js';
const pending = new Map();
let serial = 0;
function request(type, extra = {}) {
  if (state.ws?.readyState !== 1) return Promise.reject(Error(uiText("Disconnected. Reconnect and try again.")));
  const id = `native-${Date.now()}-${++serial}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(Error(uiText("Operation timed out. Check status again.")));
    }, 150000);
    pending.set(id, {
      resolve,
      reject,
      timer
    });
    send({
      type,
      request_id: id,
      ...extra
    });
  });
}
export function receiveNativeStatus(reply) {
  const p = pending.get(reply.request_id);
  if (!p) return;
  clearTimeout(p.timer);
  pending.delete(reply.request_id);
  reply.ok ? p.resolve(reply) : p.reject(Error(reply.error || uiText("Read failed")));
}
export function resetNativeRequests() {
  for (const p of pending.values()) {
    clearTimeout(p.timer);
    p.reject(Error(uiText("Disconnected. Reconnect and check status.")));
  }
  pending.clear();
}
export async function openCalendarSources(data, onChange, authorize = false, configure = null) {
  let close, host, native;
  const node = (tag, text) => {
    const n = document.createElement(tag);
    n.textContent = text;
    return n;
  };
  const button = (text, fn) => {
    const b = node('button', text);
    b.type = 'button';
    b.addEventListener('click', fn);
    return b;
  };
  close = openDialog({
    title: uiText("Calendar sources"),
    cardClass: 'it-sources-dialog',
    html: '<div id="it-source-editor" data-i18n="ui:Reading source status…">正在读取来源状态…</div>',
    actions: [{
      label: uiText("Done"),
      onClick: () => close()
    }],
    onOpen: () => host = document.getElementById('it-source-editor')
  });
  const alive = () => host.isConnected && document.getElementById('it-source-editor') === host;
  async function operation(type, extra) {
    host.querySelectorAll('button,input').forEach(b => b.disabled = true);
    try {
      native = await request(type, extra);
      if (alive()) paint();
    } catch (error) {
      if (alive()) {
        host.append(node('p', error.message));
        host.querySelectorAll('button,input').forEach(b => b.disabled = false);
      }
    }
  }
  function checkbox(text, checked, fn) {
    const label = node('label', '');
    label.className = 'it-check';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = checked;
    input.addEventListener('change', () => fn(input.checked));
    label.append(input, document.createTextNode(text));
    host.append(label);
  }
  function paint() {
    host.replaceChildren();
    host.append(node('p', uiText("Choose visible calendars. If an account already syncs through the device, hide duplicate Google / CalDAV sources.")));
    const known = new Map(data.events.map(e => [`${e.source || 'local'}:${e.remote_calendar || ''}`, e]));
    for (const [key, e] of known) {
      const name = {
        macos: uiText("On device"),
        google: 'Google',
        caldav: 'CalDAV',
        local: 'HushClaw'
      }[e.source || 'local'] || e.source;
      checkbox(name + (e.remote_calendar ? ' · ' + (e.source === 'macos' ? e.remote_etag : e.remote_calendar) : ''), !data.hidden.has(key), checked => {
        checked ? data.hidden.delete(key) : data.hidden.add(key);
        onChange();
      });
    }
    host.append(node('h4', uiText("Device calendars \u00b7 read-only")));
    if (!native.supported) {
      host.append(node('p', uiText("Device calendars require HushClaw to run on macOS. Other platforms can use local events, Google or CalDAV.")));
      return;
    }
    const labels = {
      not_installed: uiText("Calendar helper is not installed"),
      not_determined: uiText("Not authorized"),
      denied: uiText("Permission denied. Allow calendar access in System Settings \u2192 Privacy & Security \u2192 Calendars."),
      restricted: uiText("Calendar access is restricted by the system"),
      write_only: uiText("Read permission is required to show events"),
      authorized: uiText("System calendar read access granted")
    };
    host.append(node('p', labels[native.permission] || native.permission));
    host.append(node('p', native.enabled ? uiText("Refreshes every 5 minutes, covering 90 days back and 275 days ahead.") : uiText("Automatic reading is disabled. Disabling sync removes imported copies, not system calendars.")));
    if (native.last_sync) host.append(node('p', uiText("Last success:") + new Date(native.last_sync * 1000).toLocaleString()));
    if (native.last_error) host.append(node('p', uiText("Last error:") + native.last_error));
    if (native.enabled) host.append(button(uiText("Disable device sync and clear imports"), async () => {
      const ok = await openConfirm({
        title: uiText("Disable device calendar sync?"),
        message: uiText("Stop reading and remove imported device-calendar copies. Original system events are untouched; re-enable to import again."),
        confirmText: uiText("Disable and clear"), cancelText: uiText("Cancel"), dangerConfirm: true,
      });
      openCalendarSources(data, onChange, false, ok ? {enabled:false, calendar_ids:native.calendar_ids || []} : null);
    }));
    if (native.permission !== 'authorized') {
      host.append(button(uiText("Authorize device calendar access"), async () => {
        const ok = await openConfirm({
          title: uiText("Allow access to device calendars?"),
          message: uiText("The helper requests macOS calendar access (the system may call it full access). HushClaw only reads calendars you select after authorization. It does not modify external events or automatically send them to models."),
          confirmText: uiText("Continue to system authorization"),
          cancelText: uiText("Cancel")
        });
        openCalendarSources(data, onChange, ok);
      }));
      return;
    }
    const selected = new Set(native.calendar_ids || []);
    for (const c of native.calendars || []) checkbox(`${c.title} · ${c.account}`, selected.has(c.id), checked => checked ? selected.add(c.id) : selected.delete(c.id));
    host.append(button(uiText("Save selection and refresh"), () => operation('configure_native_calendar', {
      enabled: true,
      calendar_ids: [...selected]
    })));
  }
  try {
    native = configure
      ? await request('configure_native_calendar', configure)
      : await request(authorize ? 'authorize_native_calendar' : 'get_native_calendar_status');
    if (alive()) paint();
  } catch (error) {
    if (alive()) {
      host.textContent = error.message;
      host.append(button(uiText("Retry"), () => openCalendarSources(data, onChange)));
    }
  }
}
