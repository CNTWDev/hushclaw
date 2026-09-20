/** Shared-modal itinerary detail/edit interactions. External events never mutate. */
import { send, els, getCurrentSessionId, setComposerDraft, state, showToast } from './state.js';
import { openDialog, openConfirm } from './modal.js';
import { wallInput, wallToISO, shiftDay } from './calendar_math.js';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
})[c]);
const readonly = e => e.source && e.source !== 'local';
let editor = null;
let editorError = null;
export function onCalendarError(reply) {
  if (editorError) editorError(reply.message);else showToast(reply.message || '行程操作失败');
}
export function closeEventEditor() {
  editor?.();
  editor = null;
}
export function eventDetails(e, date, zone) {
  let close;
  const actions = [{
    label: '完成',
    secondary: true,
    onClick: () => close()
  }];
  if (!readonly(e)) {
    actions.push({
      label: '编辑',
      onClick: () => {
        close();
        eventEditor(e, date, zone);
      }
    });
    actions.push({
      label: '删除',
      danger: true,
      onClick: async () => {
        close();
        if (await openConfirm({
          title: '删除行程',
          message: `确认删除「${e.title}」？此操作无法撤销。`,
          confirmText: '删除',
          cancelText: '取消',
          dangerConfirm: true
        })) send({
          type: 'delete_calendar_event',
          event_id: e.event_id
        });
      }
    });
  }
  actions.push({
    label: '准备这场行程',
    onClick: async () => {
      close();
      const {
        switchTab
      } = await import('./panels.js');
      switchTab('chat');
      const draft = `请帮我准备这场行程：${e.title}\n时间：${e.start_time} 至 ${e.end_time}\n地点：${e.location || '未设置'}\n请先明确目标、待确认的问题及准备材料；不要自动修改日历、发送消息或邀请参会人。`;
      els.input.value = [els.input.value.trim(), draft].filter(Boolean).join('\n\n');
      setComposerDraft(getCurrentSessionId(), els.input.value);
      els.input.dispatchEvent(new Event('input', {
        bubbles: true
      }));
      els.input.focus();
    }
  });
  const meeting = (e.location || '').match(/https:\/\/[^\s<>"']+/)?.[0],
    label = {
      macos: '本机日历',
      google: 'Google',
      caldav: 'CalDAV'
    }[e.source] || 'HushClaw';
  close = openDialog({
    title: e.title,
    cardClass: 'it-detail-dialog',
    html: `<div class="it-detail"><p>${esc(e.all_day ? e.start_time + ' 至 ' + e.end_time + '（结束日期不含当天）' : wallInput(e.start_time, zone).replace('T', ' ') + ' — ' + wallInput(e.end_time, zone).replace('T', ' '))}</p><p>${esc(zone)}</p><p>${esc(e.location || '未设置地点')}</p><p>${esc(label)}${e.source === 'macos' ? ' · ' + esc(e.remote_etag || '') : ''} · ${readonly(e) ? '只读；请回原日历修改' : '本地行程，可编辑'}</p>${meeting ? `<a href="${esc(meeting)}" target="_blank" rel="noopener noreferrer">打开会议链接 ↗</a>` : ''}<p class="it-description">${esc(e.description || '')}</p><p class="it-muted">准备行程只填入对话草稿，由你确认发送。</p></div>`,
    actions
  });
}
export function eventEditor(e, date, zone, initial = '') {
  if (e && readonly(e)) return eventDetails(e, date, zone);
  const start = initial || (e ? wallInput(e.start_time, zone) : date + 'T09:00'),
    end = e ? wallInput(e.end_time, zone) : wallInput(new Date(Date.parse(wallToISO(start, zone)) + 3600000).toISOString(), zone);
  let close,
    busy = false;
  const field = (name, label, value, type = 'text') => `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${name === 'title' ? 'maxlength="500" required' : ''}></label>`;
  close = openDialog({
    title: e ? '编辑行程' : '新建行程',
    cardClass: 'it-edit-dialog',
    html: `<form id="it-event-form"><p class="it-muted">保存在 HushClaw · ${esc(zone)}</p>${field('title', '行程名称', e?.title || '')}<label class="it-check"><input name="all_day" type="checkbox" ${e?.all_day ? 'checked' : ''}>全天</label><div class="it-form-times">${field('start', '开始', start, 'datetime-local')}${field('end', '结束', end, 'datetime-local')}</div>${field('location', '地点或会议链接', e?.location || '')}<label>备注<textarea name="description" rows="3" maxlength="5000">${esc(e?.description || '')}</textarea></label><p id="it-form-error" role="alert"></p></form>`,
    actions: [{
      label: '取消',
      secondary: true,
      onClick: () => close()
    }, {
      label: '保存',
      onClick: save
    }],
    onOpen: () => {
      const form = document.getElementById('it-event-form');
      const all = form.elements.all_day;
      const toggle = () => {
        for (const name of ['start', 'end']) {
          const input = form.elements[name],
            v = input.value;
          input.type = all.checked ? 'date' : 'datetime-local';
          input.value = all.checked ? v.slice(0, 10) : v.length === 10 ? v + 'T00:00' : v;
        }
        if (all.checked && form.elements.start.value >= form.elements.end.value) form.elements.end.value = shiftDay(form.elements.start.value, 1);
      };
      all.addEventListener('change', toggle);
      if (all.checked) toggle();
      form.addEventListener('submit', ev => {
        ev.preventDefault();
        save();
      });
      form.elements.title.focus();
    },
    onClose: () => {
      editor = null;
      editorError = null;
    }
  });
  editor = close;
  editorError = message => {
    busy = false;
    document.getElementById('it-form-error').textContent = message;
  };
  function save() {
    const form = document.getElementById('it-event-form');
    if (busy || !form?.reportValidity()) return;
    try {
      if (state.ws?.readyState !== 1) throw Error('连接已断开，请重连后保存。');
      const get = n => form.elements[n].value,
        all = form.elements.all_day.checked;
      const a = all ? get('start') : wallToISO(get('start'), zone),
        b = all ? get('end') : wallToISO(get('end'), zone);
      if (!a || !b || Date.parse(b) <= Date.parse(a)) throw Error('结束时间必须晚于开始时间；全天行程的结束日期不包含当天。');
      if (!get('title').trim()) throw Error('请输入行程名称。');
      busy = true;
      send({
        type: e ? 'update_calendar_event' : 'create_calendar_event',
        ...(e ? {
          event_id: e.event_id
        } : {}),
        title: get('title').trim(),
        start_time: a,
        end_time: b,
        all_day: all,
        location: get('location').trim(),
        description: get('description').trim(),
        color: e?.color || 'indigo'
      });
      document.getElementById('it-form-error').textContent = '正在保存…';
      setTimeout(() => {
        busy = false;
        const error = document.getElementById('it-form-error');
        if (error?.textContent === '正在保存…') error.textContent = '尚未收到保存确认，请关闭后刷新行程，核对是否已保存。';
      }, 15000);
    } catch (error) {
      document.getElementById('it-form-error').textContent = error.message;
    }
  }
}
