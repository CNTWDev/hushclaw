import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../hushclaw/web/modules/settings/integration-guides.js', import.meta.url), 'utf8');
const module = new vm.SourceTextModule(source, { context:vm.createContext({URL}) });
await module.link(() => { throw Error('pure module'); });
await module.evaluate();
const {emailGuide, calendarGuide, syncIntegrationAccount} = module.namespace;

test('provider setup distinguishes app passwords, auth codes, and OAuth-only mail', () => {
  assert.match(emailGuide('imap.gmail.com').credential, /16 位/);
  assert.equal(emailGuide('outlook.office365.com').blocked, true);
  assert.equal(emailGuide('imap.qq.com').smtp_port, 465);
  assert.equal(emailGuide('imap.163.com').smtp_port, 465);
  assert.match(emailGuide('imappro.zoho.eu').hint, /数据中心/);
  assert.match(emailGuide('imap.feishu.cn').hint, /管理员/);
  assert.equal(emailGuide('imap.gmail.com.evil.test').label, 'Custom');
  assert.match(calendarGuide('https://www.google.com/calendar/dav'), /不接受邮箱应用密码/);
});

function form(overrides = {}) {
  const nodes = {
    'email-imap-host':{value:'imap.qq.com'}, 'email-smtp-host':{value:'smtp.qq.com'},
    'email-username':{value:'me@qq.com'}, 'email-password':{value:''},
    ...overrides,
  };
  return {getElementById:id => nodes[id]};
}
test('draft secret survives account switching but is cleared on endpoint or user changes', () => {
  const acct = {imap_host:'imap.qq.com', smtp_host:'smtp.qq.com', username:'me@qq.com', password:'draft', password_set:true};
  syncIntegrationAccount(acct, 'email', form());
  assert.equal(acct.password, 'draft');
  syncIntegrationAccount(acct, 'email', form({'email-username':{value:'other@qq.com'}}));
  assert.equal(acct.password, '');
  assert.equal(acct.password_set, false);
  syncIntegrationAccount(acct, 'email', form({'email-username':{value:'other@qq.com'}, 'email-password':{value:' new secret '}}));
  assert.equal(acct.password, ' new secret ');
});

test('calendar form uses its actual name input and isolates pending credentials', () => {
  const acct = {url:'https://old.test', username:'me', password:'old'};
  const values = {'calendar-url':'https://new.test', 'calendar-username':'me', 'calendar-name':'Work'};
  syncIntegrationAccount(acct, 'calendar', {getElementById:id => ({value:values[id] || ''})});
  assert.equal(acct.calendar_name, 'Work');
  assert.equal(acct.password, '');
});
