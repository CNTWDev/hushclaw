// Run: node --test tests/test_chat_process_visibility.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const read = path => readFile(new URL(`../hushclaw/web/modules/${path}`, import.meta.url), 'utf8');
const chat = await read('chat.js');
const tools = await read('chat/tools.js');
const websocket = await read('websocket.js');
const select = vm.runInNewContext(`(${chat.match(/function _conversationTurns\(turns\) \{[\s\S]*?\n\}/)[0]})`);

test('restored history hides tool traces without modifying stored conversation or downloads', () => {
  const user = { role: 'user', content: '请生成文件' };
  const reply = { role: 'assistant', content: '[下载](/files/example)' };
  const turns = Object.freeze([user, ...Array.from({ length: 150 }, () =>
    Object.freeze({ role: 'tool', content: 'Done', tool_name: 'web_search' })), reply]);
  const visible = select(turns);
  assert.equal(visible.length, 2);
  assert.equal(visible[0], user);
  assert.equal(visible[1], reply);
  assert.equal(turns.length, 152);
  assert.equal(select(null).length, 0);
  assert.match(chat, /const turnList = _conversationTurns\(turns\);/);
  const render = chat.match(/function _renderOneTurn\([\s\S]*?\n\}/)[0];
  assert.doesNotMatch(render, /renderToolResult|role === "tool"/);
});

test('live, replay and legacy rounds do not append tool rows, even in developer mode', () => {
  for (const name of ['insertToolBubble', 'updateToolBubble', 'createToolRound', 'insertRoundLine']) {
    const body = tools.match(new RegExp(`export function ${name}\\([^]*?\\n\\}`))[0];
    assert.doesNotMatch(body, /appendChild|createElement|createProcessDisclosure|isDevMode/);
  }
  assert.match(websocket, /pushSessionRuntimeEvent/);
  assert.match(websocket, /showAiProgress\(toolActivityLabel\(data.tool\)\)/);
  assert.match(websocket, /noteGeneratedArtifacts\(data.artifacts\)/);
  assert.match(websocket, /case "awaiting_user":/);
  assert.match(websocket, /case "error":/);
});
