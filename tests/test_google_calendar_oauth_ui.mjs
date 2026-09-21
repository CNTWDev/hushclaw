// Run: node --experimental-vm-modules --test tests/test_google_calendar_oauth_ui.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

async function harness({ saveId = 'save-1' } = {}) {
  class Element extends EventTarget {
    value = ''; checked = false; disabled = false; innerHTML = '';
    textContent = ''; style = {};
  }
  const nodes = new Map();
  const document = new EventTarget();
  document.getElementById = id => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  const popups = [];
  const timers = new Map();
  let timerId = 0;
  let saved = 0;
  let rendered = '';
  const window = {
    open() {
      const popup = { opener: {}, closed: false, navigations: [], close() { this.closed = true; } };
      popup.location = { replace(url) { popup.navigations.push(url); } };
      popups.push(popup);
      return popup;
    },
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
    clearTimeout(id) { timers.delete(id); },
  };
  const state = {
    appConnectors: { google_workspace: { enabled: true, auth_mode: 'custom', calendar_sync_enabled: true, scopes: [] } },
    appConnectorsPanel: {}, connectionsView: { items: [{id:'google_workspace', name:'Google Workspace', kind:'app', manage_target:'panel', manage_id:'google_workspace', auth:'OAuth 2.0'}] }, connectors: {}, wizard: {},
    els: { wstatus: { textContent: 'Invalid configuration' } },
    escHtml: value => String(value ?? ''), send() {},
  };
  const imports = {
    '../state.js': state,
    '../settings/save.js': { syncFormToState() {}, saveSettings() { saved++; return saveId; } },
    '../modal.js': { openDialog(options) { rendered = options.html; }, closeModal() {}, openConfirm: async () => false },
    '../http.js': { withApiKey: (url, key) => url + '?api_key=' + key },
    '../settings/providers.js': { CHANNELS: [] },
    '../i18n.js': { t: key => key },
  };
  const context = vm.createContext({ document, window, URLSearchParams, location: { search: '?api_key=local-key', origin: 'http://localhost:8765' } });
  const source = await readFile(new URL('../hushclaw/web/modules/panels/app_connectors.js', import.meta.url), 'utf8');
  const module = new vm.SourceTextModule(source, { context });
  await module.link(specifier => {
    const bindings = imports[specifier];
    assert.ok(bindings, specifier);
    return new vm.SyntheticModule(Object.keys(bindings), function () {
      for (const [key, value] of Object.entries(bindings)) this.setExport(key, value);
    }, { context });
  });
  await module.evaluate();
  module.namespace.openGoogleCalendarSettings();
  function result(detail) {
    const event = new Event('config-saved');
    event.detail = detail;
    document.dispatchEvent(event);
  }
  return { document, popups, timers, result, get rendered() { return rendered; }, get saved() { return saved; } };
}

test('Google setup renders sync controls and enables OAuth after custom credentials are entered', async () => {
  const h = await harness();
  assert.match(h.rendered, /app-google-workspace-calendar-sync/);
  assert.doesNotMatch(h.rendered, /undefined/);
  assert.match(h.rendered, /http:\/\/localhost:8765\/oauth\/app-connectors\/google_workspace\/callback/);
  const get = id => h.document.getElementById('app-google-workspace-' + id);
  get('auth-mode').value = 'custom';
  get('client-id').value = 'client';
  get('client-id').dispatchEvent(new Event('input'));
  assert.equal(h.document.getElementById('btn-oauth-app-google_workspace').disabled, true);
  get('client-secret').value = 'secret';
  get('client-secret').dispatchEvent(new Event('input'));
  assert.equal(h.document.getElementById('btn-oauth-app-google_workspace').disabled, false);
});

test('OAuth starts only after its own successful config save', async () => {
  const h = await harness();
  h.document.getElementById('btn-oauth-app-google_workspace').dispatchEvent(new Event('click'));
  assert.equal(h.saved, 1);
  const popup = h.popups[0];
  assert.equal(popup.opener, null);
  assert.deepEqual(popup.navigations, []);
  h.result({ save_client_id: 'unrelated', ok: true });
  assert.deepEqual(popup.navigations, []);
  h.result({ save_client_id: 'save-1', ok: true });
  assert.deepEqual(popup.navigations, ['/oauth/app-connectors/google_workspace/start?api_key=local-key']);
  assert.equal(h.timers.size, 0);
});

test('switching to managed mode without a configured broker does not enable Connect', async () => {
  const h = await harness();
  const mode = h.document.getElementById('app-google-workspace-auth-mode');
  mode.value = 'managed';
  mode.dispatchEvent(new Event('input'));
  assert.equal(h.document.getElementById('btn-oauth-app-google_workspace').disabled, true);
  assert.match(h.rendered, /无需你申请开发者 ID/);
});

test('failed or invalid settings cannot start OAuth', async () => {
  for (const saveId of ['save-1', undefined]) {
    const h = await harness({ saveId: saveId ?? null });
    h.document.getElementById('btn-oauth-app-google_workspace').dispatchEvent(new Event('click'));
    if (saveId) h.result({ save_client_id: saveId, ok: false, error: 'Save failed' });
    assert.equal(h.popups[0].closed, true);
    assert.deepEqual(h.popups[0].navigations, []);
    assert.equal(h.timers.size, 0);
  }
});
