import { send, state } from './state.js';
import { openDialog, openConfirm } from './modal.js';
const pending = new Map();
let serial = 0;
function request(type, extra = {}) {
  if (state.ws?.readyState !== 1) return Promise.reject(Error('连接已断开，请重连后重试。'));
  const id = `native-${Date.now()}-${++serial}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(Error('操作超时，请重新查看状态。'));
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
  reply.ok ? p.resolve(reply) : p.reject(Error(reply.error || '读取失败'));
}
export function resetNativeRequests() {
  for (const p of pending.values()) {
    clearTimeout(p.timer);
    p.reject(Error('连接已断开，请重连后核对状态。'));
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
    title: '日历来源',
    cardClass: 'it-sources-dialog',
    html: '<div id="it-source-editor">正在读取来源状态…</div>',
    actions: [{
      label: '完成',
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
    host.append(node('p', '选择显示的日历。同一账号若已由本机同步，请隐藏重复的 Google / CalDAV 来源。'));
    const known = new Map(data.events.map(e => [`${e.source || 'local'}:${e.remote_calendar || ''}`, e]));
    for (const [key, e] of known) {
      const name = {
        macos: '本机日历',
        google: 'Google',
        caldav: 'CalDAV',
        local: 'HushClaw'
      }[e.source || 'local'] || e.source;
      checkbox(name + (e.remote_calendar ? ' · ' + (e.source === 'macos' ? e.remote_etag : e.remote_calendar) : ''), !data.hidden.has(key), checked => {
        checked ? data.hidden.delete(key) : data.hidden.add(key);
        onChange();
      });
    }
    host.append(node('h4', '本机日历 · 只读'));
    if (!native.supported) {
      host.append(node('p', '本机日历要求 HushClaw 服务运行在 macOS 上。其他平台仍可使用本地行程、Google 或 CalDAV。'));
      return;
    }
    const labels = {
      not_installed: '尚未安装日历辅助程序',
      not_determined: '尚未授权',
      denied: '权限被拒绝，请在系统设置 → 隐私与安全性 → 日历中允许访问',
      restricted: '系统限制了日历访问',
      write_only: '需要读取权限才能展示行程',
      authorized: '已获得系统日历读取权限'
    };
    host.append(node('p', labels[native.permission] || native.permission));
    host.append(node('p', native.enabled ? '每 5 分钟刷新，范围为过去 90 天到未来 275 天。' : '当前未启用自动读取。关闭同步会清理已导入的副本，不影响系统日历。'));
    if (native.last_sync) host.append(node('p', '上次成功：' + new Date(native.last_sync * 1000).toLocaleString()));
    if (native.last_error) host.append(node('p', '上次错误：' + native.last_error));
    if (native.enabled) host.append(button('关闭本机同步并清理', async () => {
      const ok = await openConfirm({
        title: '关闭本机日历同步？',
        message: '将停止读取并清理所有本机日历导入的日程副本。不删除系统日历中的原始日程；重新启用后可重新同步。',
        confirmText: '关闭并清理', cancelText: '取消', dangerConfirm: true,
      });
      openCalendarSources(data, onChange, false, ok ? {enabled:false, calendar_ids:native.calendar_ids || []} : null);
    }));
    if (native.permission !== 'authorized') {
      host.append(button('授权读取本机日历', async () => {
        const ok = await openConfirm({
          title: '允许读取本机日历？',
          message: '辅助程序将请求 macOS 日历权限（系统可能称为“完整访问”）。HushClaw 仅实现读取；授权后你再选择要导入的日历。不修改外部日程，不自动发送给模型。',
          confirmText: '继续系统授权',
          cancelText: '取消'
        });
        openCalendarSources(data, onChange, ok);
      }));
      return;
    }
    const selected = new Set(native.calendar_ids || []);
    for (const c of native.calendars || []) checkbox(`${c.title} · ${c.account}`, selected.has(c.id), checked => checked ? selected.add(c.id) : selected.delete(c.id));
    host.append(button('保存选择并刷新', () => operation('configure_native_calendar', {
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
      host.append(button('重试', () => openCalendarSources(data, onChange)));
    }
  }
}
