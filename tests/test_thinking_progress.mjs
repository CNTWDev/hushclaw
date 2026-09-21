import { setLocale } from "../hushclaw/web/modules/i18n.js";
setLocale("zh");
// Run: node --test tests/test_thinking_progress.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createAgentActivity, runtimeActivityLabel, thinkingActivityDetail } from '../hushclaw/web/modules/ui/ai-primitives.js';

test('public labels represent actual preparation, compaction, model and tool phases', () => {
  const labels = ['preparing', 'compacting', 'waiting_model', 'retrying_model', 'streaming'].map(phase => runtimeActivityLabel({phase}));
  assert.equal(new Set(labels).size, 5);
  assert.equal(labels[1], '正在整理上下文…');
  assert.equal(labels[2], '正在等待模型回复…');
  assert.equal(runtimeActivityLabel({ phase: 'tool_call', active_step: { meta: { tool: 'web_search' } } }), '正在查找资料…');
  assert.equal(runtimeActivityLabel({ phase: 'thinking', summary: 'private model thoughts' }), '正在等待执行进度…');
});

test('long waits produce one truthful hint rather than repeated animated text', () => {
  const detail = runtimeActivityLabel({phase:'waiting_model'});
  assert.equal(thinkingActivityDetail(detail, 'waiting_model', 1000, 30999), detail);
  const slow = thinkingActivityDetail(detail, 'waiting_model', 1000, 31000);
  assert.match(slow, /等待模型回复.*耗时较长/);
  assert.equal(thinkingActivityDetail(detail, 'waiting_model', 1000, 99000), slow);
  assert.equal(thinkingActivityDetail(detail, 'waiting_model', 99000, 99500), detail);
  assert.equal(thinkingActivityDetail('正在查找资料…', 'tool_call', 1000, 99000), '正在查找资料…');
});

function mockDom(t, reducedMotion = false) {
  const originalDocument = globalThis.document;
  const originalWindow = globalThis.window;
  const animations = [];
  class Element {
    className = ''; textContent = ''; dataset = {}; children = []; attributes = {};
    set innerHTML(_) {
      this.children = ['ai-activity-copy', 'ai-activity-detail', 'ai-activity-elapsed'].map(name => {
        const el = new Element(); el.className = name; el.parent = this; return el;
      });
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    querySelector(selector) {
      const match = selector.match(/^\.([\w-]+)/)?.[1];
      return this.children.find(el => el.className.split(' ').includes(match) &&
        (!selector.includes(':not') || !el.className.includes('is-outgoing')));
    }
    getAnimations() { return []; }
    replaceChildren() { this.children = []; }
    append(el) { this.children.push(el); el.parent = this; }
    remove() { this.parent.children = this.parent.children.filter(el => el !== this); }
    animate(frames, options) { animations.push({frames, options}); return {finished: Promise.resolve()}; }
  }
  globalThis.document = { createElement: () => new Element() };
  globalThis.window = { matchMedia: () => ({ matches: reducedMotion }) };
  t.after(() => { globalThis.document = originalDocument; globalThis.window = originalWindow; });
  return animations;
}

test('real phase change enters from below and exits upward without replacing the activity', async t => {
  const animations = mockDom(t);
  const root = createAgentActivity({label:'Thinking', detail:'正在整理上下文…'});
  assert.equal(animations.length, 0);
  root.updateActivity({detail:'正在等待模型回复…'});
  assert.equal(animations.length, 2);
  assert.equal(animations[0].frames[0].transform, 'translateY(100%)');
  assert.equal(animations[1].frames[1].transform, 'translateY(-100%)');
  await Promise.resolve();
  assert.equal(root.querySelector('.ai-activity-detail').children.length, 1);
  root.updateActivity({detail:'正在等待模型回复…', startedAt:Date.now()-1000});
  assert.equal(animations.length, 2, 'elapsed-time ticks must not restart the animation');
});

test('reduced motion still updates stage text without animation', t => {
  const animations = mockDom(t, true);
  const root = createAgentActivity({detail:'正在整理上下文…'});
  root.updateActivity({detail:'正在等待模型回复…'});
  assert.equal(animations.length, 0);
  assert.equal(root.querySelector('.ai-activity-detail').children[0].textContent, '正在等待模型回复…');
});

test('runtime updates retain precise stage and backend start time across reconnect', async () => {
  const websocket = await readFile(new URL('../hushclaw/web/modules/websocket.js', import.meta.url), 'utf8');
  const chat = await readFile(new URL('../hushclaw/web/modules/chat.js', import.meta.url), 'utf8');
  assert.match(websocket, /state\._sessionRunState\[sid\]\?\.phase/);
  assert.match(websocket, /startedAt: runtime\.phase_started_at/);
  assert.match(chat, /startedAt: runtime\.phase_started_at/);
  assert.match(chat, /state\._thinkingStageKey !== key/);
});
