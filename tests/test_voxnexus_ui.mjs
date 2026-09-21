// node --experimental-vm-modules --test tests/test_voxnexus_ui.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

async function harness() {
  const nodes = new Map();
  class Element { value = ''; style = {}; disabled = false; handlers = {}; addEventListener(name, fn) { this.handlers[name] = fn; } dispatchEvent(event) { this.handlers[event.type]?.(event); } }
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); } };
  const sent = [], timers = new Map(); let serial = 0;
  const body = { innerHTML: '', querySelectorAll() { return []; } };
  const state = { wizard: { open: true, tab: 'model', model: '', cheapModel: '' }, els: { wizardBody: body }, send: msg => sent.push(msg), escHtml: s => String(s ?? '').replaceAll('<', '&lt;').replaceAll('"', '&quot;') };
  const context = vm.createContext({ document, URL, setTimeout(fn, delay) { timers.set(++serial, { fn, delay }); return serial; }, clearTimeout(id) { timers.delete(id); } });
  const mod = new vm.SourceTextModule(await readFile(new URL('../hushclaw/web/modules/settings/voxnexus.js', import.meta.url), 'utf8'), { context });
  await mod.link(() => new vm.SyntheticModule(Object.keys(state), function() { for (const [key, value] of Object.entries(state)) this.setExport(key, value); }, { context }));
  await mod.evaluate();
  const api = mod.namespace;
  api.setVoxConfig({ gateway: 'https://gateway.example', client_id: 'app' });
  function reply(action, data) { const req = sent.filter(r => r.action === action).at(-1); assert.ok(req, action); api.handleVoxResult({ ...req, ok: true, ...data }); }
  return { api, state, body, sent, nodes, timers, reply };
}

test('profile has one gateway, no API key form, no invented percent', async () => {
  const h = await harness(); h.api.renderModelTab();
  assert.match(h.body.innerHTML, /个人中心/);
  assert.match(h.body.innerHTML, /可用额度/);
  assert.doesNotMatch(h.body.innerHTML, /provider-cards|wiz-apikey|wiz-baseurl|%/);
  assert.equal(h.api.voxReady(), false);
  assert.match(h.body.innerHTML, /id="wiz-model-select" disabled/);
});

test('positive balance with zero available cannot unlock models', async () => {
  const h = await harness(); h.api.renderModelTab();
  h.reply('status', { authed: true, pending: false });
  h.reply('account', { account: { status: 'active', balance: 100, available: 0, reserved: 100 } });
  assert.equal(h.api.voxReady(), false);
  assert.equal(h.sent.some(r => r.action === 'models'), false);
  h.reply('packages', { packages: { payments_enabled: false, data: [{ id: 'p1' }] } });
  assert.doesNotMatch(h.body.innerHTML, /data-package=/);
});

test('funded account loads metadata, preserves selection, escapes remote content', async () => {
  const h = await harness(); h.api.renderModelTab();
  h.reply('status', { authed: true });
  h.reply('account', { account: { email: '<img onerror=x>', status: 'active', available: 99 } });
  h.state.wizard.model = 'm1';
  h.reply('models', { models: [{ id: 'm1', context_window: 120000, max_output_tokens: 8000, pricing: { input: 2 }, tags: ['fast'] }] });
  assert.equal(h.api.voxReady(), true);
  assert.equal(h.state.wizard.model, 'm1');
  assert.match(h.body.innerHTML, /120,000/);
  assert.match(h.body.innerHTML, /input × 2/);
  assert.doesNotMatch(h.body.innerHTML, /<img/);
});

test('logout invalidates in-flight responses and clears account immediately', async () => {
  const h = await harness(); h.api.renderModelTab();
  h.reply('status', { authed: true });
  const stale = h.sent.find(r => r.action === 'account');
  h.nodes.get('vox-logout').dispatchEvent(new Event('click'));
  h.api.handleVoxResult({ ...stale, ok: true, account: { status: 'active', available: 999 } });
  assert.equal(h.api.voxReady(), false);
  assert.equal(h.api.vox.account, null);
});

test('login polls pending state and does not send bearer credentials', async () => {
  const h = await harness(); h.api.renderModelTab();
  h.nodes.get('vox-login').dispatchEvent(new Event('click'));
  h.reply('login', { authorize_url: 'https://auth.example/oauth/authorize?state=abc' });
  const poll = [...h.timers.values()].find(t => t.delay === 2000);
  assert.ok(poll); poll.fn();
  h.reply('status', { authed: true, pending: false });
  assert.ok(h.sent.some(r => r.action === 'account'));
  assert.doesNotMatch(JSON.stringify(h.sent), /access_token|refresh_token|api_key|base_url/);
});

test('401 locks model settings again', async () => {
  const h = await harness(); h.api.renderModelTab();
  h.reply('status', { authed: true });
  h.reply('account', { ok: false, status: 401, error: '重新登录' });
  assert.equal(h.api.voxReady(), false);
  assert.match(h.body.innerHTML, /重新登录/);
});
