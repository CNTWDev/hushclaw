import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { UI_MESSAGES } from '../hushclaw/web/modules/i18n-catalog.js';
import { setLocale, uiText, t, localeTag } from '../hushclaw/web/modules/i18n.js';

async function files(dir) {
  const paths=[];
  for (const entry of await readdir(dir, {withFileTypes:true})) {
    const url=new URL(entry.name+(entry.isDirectory()?'/':''),dir);
    if (entry.isDirectory()) paths.push(...await files(url));
    else if (entry.name.endsWith('.js')) paths.push(url);
  }
  return paths;
}

test('every annotated product message has English and Chinese translations', async () => {
  const root=new URL('../hushclaw/web/',import.meta.url);
  const paths=[new URL('index.html',root),...await files(new URL('modules/',root))];
  let checked=0;
  for (const path of paths) {
    const source=await readFile(path,'utf8');
    for (const [,raw] of source.matchAll(/data-i18n(?:-ph|-title|-aria|-desc)?="ui:([^"$]+)"/g)) {
      const key=raw.replaceAll('&amp;','&').replaceAll('&quot;','"').replaceAll('&#x27;',"'");
      assert.ok(UI_MESSAGES[key]?.en, `${path.pathname}: ${key}`);
      assert.ok(UI_MESSAGES[key]?.zh, `${path.pathname}: ${key}`);
      checked++;
    }
  }
  assert.ok(checked>500,checked);
});

test('UI copy and parameterized dates/counts follow the selected locale', () => {
  setLocale('zh');
  assert.equal(uiText('Connections'),'连接');
  assert.equal(uiText('{n} events',{n:3}),'3 项行程');
  assert.equal(localeTag(),'zh-CN');
  setLocale('en');
  assert.equal(uiText('个人中心'),'Account centre');
  assert.equal(uiText('{n} events',{n:3}),'3 events');
  assert.equal(t('save'),'Save');
  assert.equal(localeTag(),'en-US');
  assert.equal(uiText(null),'');
});
