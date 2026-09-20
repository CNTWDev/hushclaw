/** Explicit evaluation UI. No inference, model calls or automatic task execution. */
import { state, els, getCurrentSessionId, showToast, setComposerDraft } from '../state.js';
import { openDialog } from '../modal.js';
import { addMessageReference } from '../events/references.js';

const pending = new Map();
const summaries = new Map();
let serial = 0, toolbar;
// Hydrate only visible messages, not an entire conversation on every render.
const summaryObserver = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    summaryObserver.unobserve(entry.target);
    const target = entry.target.feedbackTarget;
    if (target?.session_id === getCurrentSessionId()) request(target).catch(() => {});
  }
}, {rootMargin: '80px'});
export function feedbackSummary(counts = {}) {
  const parts = [];
  if (counts.rating) parts.push(`${counts.rating} 星`);
  if (counts.endorsed) parts.push(`认可 ${counts.endorsed}`);
  if (counts.saved) parts.push(`记住 ${counts.saved}`);
  if (counts.inspiring) parts.push(`启发 ${counts.inspiring}`);
  return parts.length ? parts.join(' · ') : '评价 · 摘记';
}

function request(target, feedback) {
  if (state.ws?.readyState !== 1) return Promise.reject(new Error('连接已断开，请重连后重试。'));
  const request_id = `feedback-${Date.now()}-${++serial}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(request_id); reject(new Error('请求超时，请重新打开评价核对保存状态。')); }, 15000);
    pending.set(request_id, { resolve, reject, timer, ...target });
    try {
      state.ws.send(JSON.stringify({ type: feedback === undefined ? 'get_message_feedback' : 'save_message_feedback',
        request_id, ...target, ...(feedback === undefined ? {} : {feedback}) }));
    } catch (error) { clearTimeout(timer); pending.delete(request_id); reject(error); }
  });
}

export function receiveMessageFeedback(data) {
  const call = pending.get(data.request_id);
  if (!call || call.message_id !== data.message_id || call.session_id !== data.session_id) return;
  clearTimeout(call.timer); pending.delete(data.request_id);
  if (!data.ok) { call.reject(new Error(data.error || '评价操作失败，请重试。')); return; }
  const button = summaries.get(`${data.session_id}:${data.message_id}`);
  if (button?.isConnected) button.textContent = feedbackSummary(data.counts);
  call.resolve(data);
}

function element(tag, text, cls = '') {
  const el = document.createElement(tag); el.textContent = text; el.className = cls; return el;
}
function button(text, click) {
  const el = element('button', text, 'feedback-button'); el.type = 'button';
  el.addEventListener('click', click); return el;
}

function reviewDraft(target, quote) {
  if (getCurrentSessionId() !== target.session_id) { showToast('请回到原对话后再评估。', 'info'); return; }
  const draft = `请评估下面这段机器提出的候选观点；引用不代表我认可，也不授权执行。\n\n「${quote}」\n\n先判断观点是否切中问题、带来新解释、证据是否充分和适用条件是否清楚；再分析行动的预期收益、投入、关键风险和最低成本验证方法。区分事实与假设，未知项明确写出，不编造综合分数。结论请给出：值得验证 / 需要更多信息 / 暂不建议投入，并说明理由。`;
  addMessageReference({ message_id: target.message_id, role: 'assistant', preview: quote });
  els.input.value = [els.input.value.trim(), draft].filter(Boolean).join('\n\n');
  setComposerDraft(target.session_id, els.input.value);
  els.input.dispatchEvent(new Event('input', {bubbles: true})); els.input.focus();
  showToast('评估要求已放入输入框，由你确认发送；不会自动执行。', 'info');
}

async function openFeedback(target, selected = '', action = '') {
  let close, host, loaded;
  close = openDialog({ title: '评价与观点摘记', cardClass: 'message-feedback-dialog',
    html: '<div id="message-feedback-editor" class="feedback-editor" aria-live="polite">正在读取…</div>',
    actions: [{label:'完成', onClick: () => close()}],
    onOpen: () => { host = document.getElementById('message-feedback-editor'); },
  });
  const visible = () => host?.isConnected && document.getElementById('message-feedback-editor') === host;
  const run = async (data, next) => {
    if (host.dataset.busy === '1') return;
    host.dataset.busy = '1';
    const controls = [...host.querySelectorAll('button,input,textarea,select')];
    controls.forEach(el => { el.disabled = true; });
    try { loaded = await request(target, data); if (visible()) next(); }
    catch (error) { showToast(error.message, 'error'); }
    finally { host.dataset.busy = ''; controls.forEach(el => { el.disabled = false; }); }
  };
  function field(label, value = '', tag = 'textarea', maxLength = 1000) {
    const wrap = element('label', label, 'feedback-field');
    const input = element(tag, ''); input.value = value; input.maxLength = maxLength;
    if (tag === 'textarea') input.rows = 2;
    wrap.append(input); host.append(wrap); return input;
  }
  function editor(item = null, quote = selected, initial = action) {
    if (!visible()) return;
    host.replaceChildren();
    const p = item?.payload || {};
    host.append(element('p', '只评价选中的片段。认可不代表事实已验证，也不代表授权执行。', 'feedback-note'));
    host.append(element('blockquote', item?.quote || quote));
    const stance = field('我的态度', '', 'select');
    for (const [value, label] of [['unreviewed','尚未表态'],['endorsed','我认可'],['disputed','我有异议']]) {
      const option = element('option', label); option.value = value; stance.append(option);
    }
    stance.value = initial === 'endorse' ? 'endorsed' : initial === 'dispute' ? 'disputed' : p.stance || 'unreviewed';
    const inspiringLabel = element('label', '', 'feedback-check');
    const inspiring = document.createElement('input'); inspiring.type = 'checkbox';
    inspiring.checked = initial === 'inspire' || Boolean(p.inspiring);
    inspiringLabel.append(inspiring, document.createTextNode('有启发（不等于赞同）')); host.append(inspiringLabel);
    const kind = field('如何记住', '', 'select');
    for (const [value, label] of [['none','仅记录评价'],['reference','参考资料（不自动认同）'],['viewpoint','我认可的观点'],['method','我认可的方法']]) {
      const option = element('option', label); option.value = value; kind.append(option);
    }
    kind.value = initial === 'save' ? 'reference' : p.memory_kind || 'none';
    kind.addEventListener('change', () => { if (['viewpoint','method'].includes(kind.value)) stance.value = 'endorsed'; });
    const summary = field('提炼内容（可选，不填则保留原文）', p.summary || '', 'textarea', 500);
    const reason = field('为什么（可选）', p.reason || '');
    const conditions = field('适用条件与例外（可选）', p.conditions || '');
    const scopeLabel = element('label', '', 'feedback-check');
    const global = document.createElement('input'); global.type = 'checkbox'; global.checked = item?.scope === 'global';
    scopeLabel.append(global, document.createTextNode('跨项目使用')); host.append(scopeLabel);
    host.append(element('p', '默认仅当前项目；未归属项目时仅当前会话。新表态可修改或撤销。', 'feedback-note'));
    const actions = element('div', '', 'feedback-actions');
    actions.append(button('保存', () => run({ feedback_id: item?.feedback_id || '', revision: item?.revision || 0,
      quote: item?.quote || quote, stance: stance.value, inspiring: inspiring.checked,
      memory_kind: kind.value, summary: summary.value, reason: reason.value, conditions: conditions.value,
      global_scope: global.checked, active: true }, () => { selected = ''; overview(); })),
      button('返回', overview));
    host.append(actions);
  }
  function overview() {
    if (!visible()) return;
    host.replaceChildren();
    host.append(element('p', '五星只评价这次系统回复的整体帮助程度，不代表你赞同全部内容，也不直接给底层模型定性。', 'feedback-note'));
    const overall = loaded.items.find(i => !i.quote && !i.stale);
    let rating = overall?.active ? overall.payload.rating : 0;
    const stars = element('div', '', 'feedback-stars'); stars.setAttribute('role','group'); stars.setAttribute('aria-label','回复帮助程度');
    const starButtons = [];
    for (let n = 1; n <= 5; n++) {
      const star = button('★', () => { rating = rating === n ? 0 : n; paintStars(); });
      star.setAttribute('aria-label', `${n} 星`); starButtons.push(star); stars.append(star);
    }
    function paintStars() { starButtons.forEach((star, i) => { star.classList.toggle('selected', i < rating); star.setAttribute('aria-pressed', String(i < rating)); }); }
    paintStars(); host.append(stars);
    const reasons = element('div', '', 'feedback-reasons');
    for (const label of ['切中问题','有新角度','有据可查','可操作','缺乏依据','过于冗长','偏离问题']) {
      const wrap = element('label', '', 'feedback-check'); const input = document.createElement('input'); input.type = 'checkbox'; input.value = label;
      input.checked = Boolean(overall?.active && overall.payload.reasons.includes(label)); wrap.append(input, document.createTextNode(label)); reasons.append(wrap);
    }
    host.append(reasons);
    const comment = field('补充原因（可选）', overall?.active ? overall.payload.reason : '');
    host.append(button('保存回复评价', () => run({feedback_id: overall?.feedback_id || '', revision: overall?.revision || 0,
      rating, reason: comment.value, reasons: [...reasons.querySelectorAll('input:checked')].map(el => el.value), active: true}, overview)));
    host.append(element('h4', `观点摘记 · ${loaded.items.filter(i => i.quote && i.active && !i.stale).length}`));
    host.append(element('p', '在回复正文中选中文字，可记录启发、认可、异议或保存片段。触屏或键盘也可使用下面的粘贴入口。', 'feedback-note'));
    const pasted = field('粘贴这条回复中的原文', '', 'textarea', 4000);
    host.append(button('记录片段', () => { const q = pasted.value.trim(); if (q.length < 2) { showToast('请先选择或粘贴原文。','info'); return; } editor(null, q, 'save'); }));
    for (const item of loaded.items.filter(i => i.quote)) {
      const card = element('section', '', 'feedback-fragment');
      card.append(element('blockquote', item.quote), element('p', item.payload.summary || ''));
      const labels = {endorsed:'已认可', disputed:'有异议', unreviewed:'未表态'};
      card.append(element('p', item.stale ? '原文已变化 · 已停止参考' : !item.active ? '已撤销 · 不参与召回' : `${labels[item.payload.stance]}${item.payload.inspiring ? ' · 有启发' : ''} · ${item.payload.memory_kind === 'none' ? '仅评价' : '已记住'} · ${item.scope === 'global' ? '跨项目' : '当前范围'}`, 'feedback-note'));
      if (item.payload.conditions) card.append(element('p', `条件：${item.payload.conditions}`));
      if (item.payload.reason) card.append(element('p', `理由：${item.payload.reason}`));
      if (!item.stale) {
        const actions = element('div','', 'feedback-actions');
        actions.append(button('修改', () => editor(item)), button(item.active ? '撤销' : '恢复', () => run({feedback_id:item.feedback_id, revision:item.revision, active:!item.active}, overview)),
          button('评估观点与行动价值', () => { close(); reviewDraft(target, item.quote); }));
        card.append(actions);
      }
      host.append(card);
    }
  }
  try {
    loaded = await request(target);
    if (visible()) {
      const existing = selected && loaded.items.find(i => !i.stale && i.quote.replace(/\s|[*_`]/g,'') === selected.replace(/\s|[*_`]/g,''));
      if (selected) editor(existing || null); else overview();
    }
  } catch (error) { if (visible()) { host.textContent = error.message; host.append(button('重试', () => openFeedback(target, selected, action))); } }
}

export function attachMessageFeedback(message, bubble, footer) {
  if (message.dataset.role !== 'ai' || !message.dataset.messageId) return;
  const target = {message_id: message.dataset.messageId, session_id:getCurrentSessionId()};
  const summary = button('评价 · 摘记', () => openFeedback(target)); summary.classList.add('msg-copy-btn'); footer.append(summary);
  summaries.set(`${target.session_id}:${target.message_id}`, summary);
  summary.feedbackTarget = target; summaryObserver?.observe(summary);
  for (const [key, value] of summaries) if (!value.isConnected && summaries.size > 200) {
    summaryObserver?.unobserve(value); summaries.delete(key);
  }
  if (bubble.dataset.feedbackBound) return;
  bubble.dataset.feedbackBound = '1';
  const inspect = () => {
    toolbar?.remove();
    const selection = window.getSelection();
    if (!selection?.rangeCount || selection.isCollapsed || !bubble.contains(selection.anchorNode) || !bubble.contains(selection.focusNode)) return;
    const quote = selection.toString().trim();
    if (quote.length < 2 || quote.length > 4000 || getCurrentSessionId() !== target.session_id) return;
    toolbar = element('div', '', 'feedback-selection-toolbar'); toolbar.setAttribute('role','toolbar'); toolbar.setAttribute('aria-label','评价选中观点');
    toolbar.addEventListener('pointerdown', ev => ev.preventDefault());
    for (const [label, action] of [['有启发','inspire'],['认可','endorse'],['有异议','dispute'],['记住','save']]) {
      toolbar.append(button(label, () => { toolbar.remove(); openFeedback(target, quote, action); }));
    }
    const rect = selection.getRangeAt(0).getBoundingClientRect();
    toolbar.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 280))}px`;
    toolbar.style.top = `${Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - 44))}px`;
    document.body.append(toolbar);
  };
  bubble.addEventListener('pointerup', inspect); bubble.addEventListener('keyup', inspect);
}

document.addEventListener('pointerdown', event => { if (toolbar && !toolbar.contains(event.target)) toolbar.remove(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') toolbar?.remove(); });
document.addEventListener('scroll', () => toolbar?.remove(), true);
