import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const code = await readFile(new URL('../hushclaw/web/modules/chat/understanding.js', import.meta.url), 'utf8');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.events = {}; this.isConnected = true; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, fn) { this.events[name] = fn; }
  querySelector(selector) {
    for (const c of this.children) {
      if (selector.startsWith('.') ? c.className === selector.slice(1) : c.tag === selector) return c;
      const nested = c.querySelector(selector); if (nested) return nested;
    }
    return null;
  }
  set innerHTML(_) { throw Error('Unsafe HTML insertion'); }
}
async function harness() {
  const sent = [], confirmations = [];
  const context = vm.createContext({ document: { createElement: tag => new Element(tag) }, setTimeout: () => 1, clearTimeout() {}, Date });
  const mod = new vm.SourceTextModule(code, { context });
  const state = { ws: { readyState: 1, send: text => sent.push(JSON.parse(text)) } };
  await mod.link(async spec => {
    const values = spec.includes('state') ? { state, getCurrentSessionId: () => 's' } : {
      openConfirm: async options => { confirmations.push(options); return true; },
    };
    return new vm.SyntheticModule(Object.keys(values), function () { for (const [k, v] of Object.entries(values)) this.setExport(k, v); }, { context });
  });
  await mod.evaluate();
  const message = new Element('div'); message.dataset.messageId = 'a';
  const content = new Element('div'); content.className = 'msg-content'; message.append(content);
  return { api: mod.namespace, message, sent, confirmations };
}
const receipt = () => ({ question: '<img onerror=alert(1)>', status: 'ready', evidence: [
  { id: 'p', kind: 'preference', label: '风格', text: '<script>bad</script>', verification: 'unconfirmed' },
  { id: 'r', verification: 'rejected' }, { id: 'x', unavailable: true },
], analysis: { positions: [], links: [{ id: 'p' }, { id: 'p' }, { id: 'r' }, { id: 'x' }, { id: 'unknown' }] } });

test('counts distinguish retrieved, corresponding and confirmed records', async () => {
  const h = await harness();
  assert.deepEqual(JSON.parse(JSON.stringify(h.api.understandingCounts(receipt()))), { references: 1, correspondences: 1, confirmed: 0 });
});
test('receipt uses text nodes and routes feedback through the shared confirmation', async () => {
  const h = await harness(); h.api.attachUnderstanding(h.message, receipt(), 's');
  const card = h.message.querySelector('.understanding-card');
  assert.equal(card.querySelector('summary').textContent, '本次理解 · 参考 1 · 对应 1');
  const actions = card.querySelector('.understanding-actions');
  await actions.children[1].events.click();
  assert.equal(h.confirmations.length, 1);
  assert.equal(h.sent[0].type, 'understanding_feedback');
  assert.equal(h.sent[0].evidence_id, 'p');
  assert.equal(h.sent[0].session_id, 's');
});
test('stale or cross-session responses never update the visible receipt', async () => {
  const h = await harness(); h.api.attachUnderstanding(h.message, receipt(), 's');
  const summary = h.message.querySelector('summary');
  h.api.receiveUnderstanding({ message_id: 'a', session_id: 'other', ok: false });
  assert.equal(summary.textContent, '本次理解 · 参考 1 · 对应 1');
  h.api.receiveUnderstanding({ message_id: 'a', session_id: 's', ok: true, receipt: null });
  assert.equal(summary.textContent, '本次理解 · 暂无记录');
});
