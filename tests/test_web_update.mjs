// Run: node --test tests/test_web_update.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const html = await readFile(new URL('../hushclaw/web/index.html', import.meta.url), 'utf8');
const updateScript = html.match(/<script type="module">\s*import \{ openConfirm \}[^\n]+\n([\s\S]*?)<\/script>/)[1];
const workerScript = await readFile(new URL('../hushclaw/web/sw.js', import.meta.url), 'utf8');
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

async function updateHarness() {
  const posted = [];
  let reloads = 0, confirmations = 0, confirm;
  const worker = { state: 'installed', postMessage: msg => posted.push(msg.type) };
  const reg = { waiting: worker, active: {}, update() {}, addEventListener() {} };
  const listeners = {};
  vm.runInNewContext(updateScript, {
    navigator: { serviceWorker: {
      controller: {},
      addEventListener: (name, fn) => { listeners[name] = fn; },
      register: async (path, options) => {
        assert.equal(path, '/sw.js');
        assert.equal(options.updateViaCache, 'none');
        return reg;
      },
    } },
    window: { location: { reload: () => reloads++ } },
    openConfirm: () => { confirmations++; return new Promise(resolve => { confirm = resolve; }); },
    setInterval() {},
  });
  await flush();
  return { worker, reg, listeners, posted, confirm: value => confirm(value),
    reloads: () => reloads, confirmations: () => confirmations };
}

test('an update waits for confirmation and reloads only after controller change', async () => {
  const h = await updateHarness();
  assert.equal(h.confirmations(), 1);
  assert.deepEqual(h.posted, []);
  h.listeners.controllerchange();
  assert.equal(h.reloads(), 0);
  h.confirm(true);
  await flush();
  assert.deepEqual(h.posted, ['SKIP_WAITING']);
  assert.equal(h.reloads(), 0);
  h.listeners.controllerchange();
  assert.equal(h.reloads(), 1);
});

test('activation while the dialog is open does not dereference null waiting worker', async () => {
  const h = await updateHarness();
  h.reg.waiting = null;
  h.reg.active = h.worker;
  h.worker.state = 'activated';
  h.listeners.controllerchange();
  assert.equal(h.reloads(), 0, 'never reload before consent');
  h.confirm(true);
  await flush();
  assert.equal(h.reloads(), 1);
  assert.deepEqual(h.posted, []);
});

test('Later leaves the current page and its draft untouched', async () => {
  const h = await updateHarness();
  h.confirm(false);
  await flush();
  h.listeners.controllerchange();
  assert.equal(h.reloads(), 0);
  assert.deepEqual(h.posted, []);
});

test('installation does not auto-activate; activation finishes cache cleanup and claim', async () => {
  const handlers = {}, deleted = [];
  let skips = 0, claims = 0, cached = false;
  vm.runInNewContext(workerScript, {
    self: {
      addEventListener: (name, fn) => { handlers[name] = fn; },
      skipWaiting: () => skips++,
      clients: { claim: async () => claims++ },
    },
    caches: {
      open: async () => ({ addAll: async () => { cached = true; } }),
      keys: async () => ['hushclaw-v47', 'hushclaw-v48'],
      delete: async key => deleted.push(key),
    },
  });
  let pending;
  handlers.install({ waitUntil: promise => { pending = promise; } });
  await pending;
  assert.equal(cached, true);
  assert.equal(skips, 0);
  handlers.message({ data: { type: 'SKIP_WAITING' } });
  assert.equal(skips, 1);
  handlers.activate({ waitUntil: promise => { pending = promise; } });
  await pending;
  assert.deepEqual(deleted, ['hushclaw-v47']);
  assert.equal(claims, 1);
});
