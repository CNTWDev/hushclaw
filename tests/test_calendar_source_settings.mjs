import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../hushclaw/web/modules/settings/tab-misc.js', import.meta.url), 'utf8');
const handlers = await readFile(new URL('../hushclaw/web/modules/settings/handlers.js', import.meta.url), 'utf8');
const start = source.indexOf('  document.querySelectorAll("[data-calendar-del]")');
const end = source.indexOf('  document.querySelectorAll("[data-cal-preset]")', start);

function harness() {
  const account = { label: 'Only calendar', enabled: true };
  const calendarAccounts = [account];
  let confirm, click, change, renders = 0;
  const input = { checked: false, isConnected: true, addEventListener: (_, fn) => change = fn };
  const button = { dataset: { calendarDel: '0' }, addEventListener: (_, fn) => click = fn };
  const context = {
    calendarAccounts, currentCalendarTab: 0,
    setCurrentCalendarTab: () => {},
    _syncCalendarFormToAccount: () => { account.enabled = input.checked; },
    renderIntegrationsTab: () => renders++,
    openConfirm: options => {
      assert.equal(options.dangerConfirm, true);
      assert.match(options.message, /保存/);
      return new Promise(resolve => confirm = resolve);
    },
    document: { querySelectorAll: () => [button], getElementById: () => input },
  };
  vm.runInNewContext(source.slice(start, end), context);
  return { account, calendarAccounts, input, remove: () => click({stopPropagation(){}}),
    disable: () => change({target:input}), confirm: ok => confirm(ok), renders: () => renders };
}

test('the last calendar has an accessible delete button; empty accounts stay empty', () => {
  const code = source.match(/function _renderAccountTabBar\([^]*?\n\}/)[0];
  const render = vm.runInNewContext(`(${code})`, {escHtml: value => value});
  assert.match(render([{label:'Only'}], 0, 'calendar'), /<button[^>]+data-calendar-del="0"[^>]+aria-label="Delete account"/);
  assert.doesNotMatch(render([], 0, 'calendar'), /data-calendar-del/);
  assert.match(render([], 0, 'calendar'), /btn-calendar-add-account/);
  assert.doesNotMatch(handlers, /calendarAccounts.length === 0\) calendarAccounts.push/);
});

test('deleting the last source waits for confirmation and leaves a genuinely empty list', async () => {
  const h = harness();
  const pending = h.remove();
  assert.equal(h.calendarAccounts.length, 1);
  h.confirm(true);
  await pending;
  assert.equal(h.calendarAccounts.length, 0);
  assert.equal(h.renders(), 1);
});

test('cancelling deletion keeps the source', async () => {
  const h = harness();
  const pending = h.remove();
  h.confirm(false);
  await pending;
  assert.equal(h.calendarAccounts.length, 1);
  assert.equal(h.renders(), 0);
});

test('disabling is not reflected in saveable state until confirmed', async () => {
  const h = harness();
  let pending = h.disable();
  assert.equal(h.input.checked, true);
  assert.equal(h.account.enabled, true);
  h.confirm(false);
  await pending;
  assert.equal(h.input.checked, true);
  h.input.checked = false;
  pending = h.disable();
  h.confirm(true);
  await pending;
  assert.equal(h.input.checked, false);
  assert.equal(h.account.enabled, false);
});
