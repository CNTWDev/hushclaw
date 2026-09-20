import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';

const code = await readFile(new URL('../hushclaw/web/modules/chat/feedback.js', import.meta.url), 'utf8');
test('shared modal does not dismiss when a body action replaces its own DOM', async () => {
  const modal = await readFile(new URL('../hushclaw/web/modules/modal.js', import.meta.url), 'utf8');
  const handler = modal.match(/const onOverlayClick = (\(ev\) => \{[\s\S]*?\n  \});/)[1];
  const overlay = {}, detachedButton = {};
  let closed = 0;
  const fn = vm.runInNewContext(handler, {overlay, closeOnBackdrop:true, _closeCurrent: () => closed++});
  fn({target:detachedButton}); assert.equal(closed,0);
  fn({target:overlay}); assert.equal(closed,1);
});
class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.dataset = {}; this.events = {}; this.isConnected = true;
    this.style = {}; this.classList = {add(){}, toggle(){}};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, fn) { this.events[name] = fn; }
  setAttribute() {}
  querySelectorAll() { return []; }
  set innerHTML(_) { throw Error('Unsafe HTML'); }
}
async function harness() {
  const sent = [], dialogs = [], toasts = [], observations = [];
  let observerCallback;
  const host = new Element('div');
  const document = {createElement: tag => new Element(tag), createTextNode: text => ({textContent:text}),
    addEventListener(){}, getElementById: () => host};
  class Observer { constructor(fn) {observerCallback = fn;} observe(el) {observations.push(el);} unobserve() {} }
  const context = vm.createContext({document, IntersectionObserver: Observer, setTimeout: () => 1, clearTimeout(){}, Date});
  const state = {ws:{readyState:1, send: text => sent.push(JSON.parse(text))}};
  const mod = new vm.SourceTextModule(code, {context});
  await mod.link(async spec => {
    const values = spec.includes('state') ? {state, els:{}, getCurrentSessionId: () => 's', showToast: m => toasts.push(m), setComposerDraft(){}}
      : spec.includes('modal') ? {openDialog: opts => {dialogs.push(opts); opts.onOpen(); return () => {};}}
      : {addMessageReference(){}};
    return new vm.SyntheticModule(Object.keys(values), function() {for (const [k,v] of Object.entries(values)) this.setExport(k,v);}, {context});
  });
  await mod.evaluate();
  const message = new Element('div'), bubble = new Element('div'), footer = new Element('div');
  message.dataset = {role:'ai', messageId:'a'};
  return {api:mod.namespace, state, sent, dialogs, host, toasts, message, bubble, footer, observations,
    visible: el => observerCallback([{target:el,isIntersecting:true}])};
}
test('summary separates reply helpfulness from endorsement and reference counts', async () => {
  const h = await harness();
  assert.equal(h.api.feedbackSummary(), '评价 · 摘记');
  assert.equal(h.api.feedbackSummary({rating:5,saved:1}), '5 星 · 记住 1');
  assert.equal(h.api.feedbackSummary({endorsed:2,inspiring:1}), '认可 2 · 启发 1');
});
test('only visible assistant messages hydrate, matched responses update persisted counts', async () => {
  const h = await harness(); h.api.attachMessageFeedback(h.message,h.bubble,h.footer);
  assert.equal(h.sent.length,0);
  h.visible(h.footer.children[0]); assert.equal(h.sent[0].type,'get_message_feedback');
  const reply = {...h.sent[0],ok:true,items:[],counts:{rating:4,saved:1}};
  h.api.receiveMessageFeedback({...reply,session_id:'other'});
  assert.equal(h.footer.children[0].textContent,'评价 · 摘记');
  h.api.receiveMessageFeedback(reply);
  assert.equal(h.footer.children[0].textContent,'4 星 · 记住 1');
  h.message.dataset.role = 'user'; h.api.attachMessageFeedback(h.message,h.bubble,h.footer);
  assert.equal(h.footer.children.length,1);
});
test('editor uses shared dialog and failed save never changes counters optimistically', async () => {
  const h = await harness(); h.api.attachMessageFeedback(h.message,h.bubble,h.footer);
  const opening = h.footer.children[0].events.click();
  assert.equal(h.dialogs[0].cardClass,'message-feedback-dialog');
  h.api.receiveMessageFeedback({...h.sent[0],ok:true,items:[],counts:{}}); await opening;
  const stars = h.host.children.find(el => el.className === 'feedback-stars');
  stars.children[4].events.click();
  const saving = h.host.children.find(el => el.textContent === '保存回复评价').events.click();
  assert.equal(h.sent[1].type,'save_message_feedback');
  assert.equal(h.sent[1].feedback.rating,5);
  assert.equal(h.footer.children[0].textContent,'评价 · 摘记');
  h.api.receiveMessageFeedback({...h.sent[1],ok:false,error:'版本冲突'}); await saving;
  assert.equal(h.toasts[0],'版本冲突');
  assert.equal(h.footer.children[0].textContent,'评价 · 摘记');
});
test('disconnected editor offers retry without sending or hanging', async () => {
  const h = await harness(); h.state.ws.readyState = 3;
  h.api.attachMessageFeedback(h.message,h.bubble,h.footer);
  await h.footer.children[0].events.click();
  assert.equal(h.sent.length,0);
  assert.match(h.host.textContent,/连接已断开/);
  assert.equal(h.host.children[0].textContent,'重试');
});
