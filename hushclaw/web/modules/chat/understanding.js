/** A quiet, inspectable receipt. Counts are evidence counts, never an IQ/fit score. */
import { state, getCurrentSessionId } from '../state.js';
import { openConfirm } from '../modal.js';

const cards = new Map();
function node(tag, text, className = '') {
  const el = document.createElement(tag);
  el.textContent = text;
  el.className = className;
  return el;
}
function send(card, extra = {}) {
  if (state.ws?.readyState !== 1) return false;
  state.ws.send(JSON.stringify({ type: 'get_understanding', message_id: card.dataset.messageId,
    session_id: card.dataset.sessionId, ...extra }));
  return true;
}
export function understandingCounts(receipt) {
  const evidence = receipt?.evidence || [];
  const active = evidence.filter(e => e.verification !== 'rejected' && !e.unavailable);
  const ids = new Set(active.map(e => e.id));
  return { references: active.length,
    correspondences: new Set((receipt?.analysis?.links || []).filter(l => ids.has(l.id)).map(l => l.id)).size,
    confirmed: active.filter(e => e.verification === 'confirmed').length };
}

function paint(card, receipt) {
  const summary = card.querySelector('summary');
  const body = card.querySelector('.understanding-body');
  body.replaceChildren();
  if (!receipt) {
    summary.textContent = '本次理解 · 暂无记录';
    body.append(node('p', '较早的对话没有保存这份依据记录；不会事后虚构当时使用过的记忆。'));
    return;
  }
  const counts = understandingCounts(receipt);
  summary.textContent = `本次理解 · 参考 ${counts.references}` + (receipt.status === 'ready' ? ` · 对应 ${counts.correspondences}` : ' · 待核对');
  body.append(node('p', '参考＝提供给模型的个人记忆；对应＝回复中可核对的内容关联，不代表已证明模型的内部思考。', 'understanding-note'));
  const analysis = receipt.analysis;
  body.append(node('h4', '这次的问题'));
  body.append(node('p', analysis?.question_summary || receipt.question));
  if (analysis) {
    body.append(node('h4', '问题里的观点 · 系统解读'));
    if (!analysis.positions?.length) body.append(node('p', '未识别到明确表达的个人立场；提问不自动视为赞同。', 'understanding-note'));
    for (const position of analysis.positions || []) {
      body.append(node('blockquote', position.quote));
      body.append(node('p', position.interpretation));
    }
  } else {
    body.append(node('p', receipt.status === 'failed' ? '后台核对暂未完成；以下仅展示实际提供的参考，不推断是否沿用。' : '后台正在核对观点与回复，不影响继续聊天。', 'understanding-note'));
  }
  body.append(node('h4', `历史参考 · ${counts.references} 条，其中 ${counts.confirmed} 条经你确认`));
  if (!receipt.evidence?.length) body.append(node('p', '这次没有选中合适的个人记忆，不会为了展示而强行关联。', 'understanding-note'));
  for (const evidence of receipt.evidence || []) {
    const item = node('section', '', 'understanding-evidence');
    const labels = { preference: '偏好与习惯', viewpoint: '历史观点', memory: '历史记录' };
    item.append(node('div', `${labels[evidence.kind] || '参考'} · ${evidence.label}`, 'understanding-evidence-title'));
    item.append(node('p', evidence.text));
    item.append(node('span', evidence.unavailable ? '来源已不可用' : evidence.verification === 'confirmed' ? '你已确认' : evidence.verification === 'rejected' ? '已停用 · 不再作为个人参考' : '历史归纳 · 未经你确认', 'understanding-note'));
    const link = analysis?.links?.find(l => l.id === evidence.id);
    if (link && !evidence.unavailable && evidence.verification !== 'rejected') {
      item.append(node('div', link.relation === 'changed' ? '回复中讨论了变化' : '回复中的对应内容', 'understanding-evidence-title'));
      item.append(node('blockquote', link.answer_quote));
      item.append(node('p', link.reason));
    }
    if (evidence.source_excerpt) {
      const source = node('details', '', 'understanding-source');
      source.append(node('summary', '查看原始依据' + (evidence.updated ? ` · ${new Date(evidence.updated * 1000).toLocaleDateString()}` : '')));
      source.append(node('blockquote', evidence.source_excerpt));
      item.append(source);
    }
    for (const change of evidence.evolution || []) {
      if (change.type === 'new') continue;
      item.append(node('p', `观点记录：${change.text}${change.reason ? `；原因：${change.reason}` : ''}`, 'understanding-note'));
    }
    if (!evidence.unavailable) {
      const actions = node('div', '', 'understanding-actions');
      for (const [label, verdict] of evidence.verification === 'rejected' ? [['恢复参考', 'unconfirmed']] : [['符合我', 'confirmed'], ['不准确', 'rejected']]) {
        const button = node('button', label);
        button.type = 'button';
        button.disabled = evidence.verification === verdict;
        button.addEventListener('click', async () => {
          if (verdict === 'rejected' && !await openConfirm({ title: '纠正个人理解', message: '将停止把这条归纳作为后续个人参考。原始对话保留，你可以恢复。', confirmText: '停止参考', cancelText: '取消' })) return;
          if (send(card, { type: 'understanding_feedback', evidence_id: evidence.id, verdict })) button.disabled = true;
        });
        actions.append(button);
      }
      item.append(actions);
    }
    body.append(item);
  }
  for (const uncertainty of analysis?.uncertainties || []) body.append(node('p', `待确认：${uncertainty}`, 'understanding-note'));
  if (receipt.status !== 'ready') {
    const refresh = node('button', '重新检查');
    refresh.type = 'button';
    refresh.addEventListener('click', () => send(card));
    body.append(refresh);
  }
  clearTimeout(card._pollTimer);
  if (card.isConnected && ['pending', 'running'].includes(receipt.status) && (card._pollCount || 0) < 30) {
    card._pollCount = (card._pollCount || 0) + 1;
    card._pollTimer = setTimeout(() => { if (card.isConnected) send(card); }, 4000);
  }
}

export function attachUnderstanding(message, receipt = undefined, sessionId = getCurrentSessionId()) {
  if (!message?.dataset.messageId) return;
  let card = message.querySelector('.understanding-card');
  if (!card) {
    card = node('details', '', 'understanding-card');
    card.dataset.messageId = message.dataset.messageId;
    card.dataset.sessionId = sessionId || '';
    card.append(node('summary', '本次理解 · 查看关联依据'));
    card.append(node('div', '', 'understanding-body'));
    card.addEventListener('toggle', () => {
      if (card.open) { cards.set(card.dataset.messageId, card); card._pollCount = 0; send(card); }
    });
    (message.querySelector('.msg-content') || message).append(card);
  }
  cards.set(card.dataset.messageId, card);
  for (const [id, previous] of cards) if (cards.size > 250 && !previous.isConnected) cards.delete(id);
  if (receipt !== undefined) paint(card, receipt);
}

export function receiveUnderstanding(data) {
  const card = cards.get(data.message_id);
  if (!card || !card.isConnected || card.dataset.sessionId !== data.session_id) return;
  paint(card, data.ok ? data.receipt : null);
}
