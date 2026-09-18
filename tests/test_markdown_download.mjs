// Run: node --experimental-vm-modules --test tests/test_markdown_download.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const sample = '# 标题\n\n> Question\n\n公式：\\[900 \\times 80\\% \\times 50\\]\n\n```js\nconst x = 1;\n```\n';

async function harness({ picker, clickError } = {}) {
  const clicks = [], blobs = [], revoked = [], timers = [], warnings = [];
  const anchor = {
    remove() { this.removed = true; },
    click() {
      assert.equal(this.attached, true);
      assert.equal(revoked.length, 0);
      if (clickError) throw clickError;
      clicks.push({ href: this.href, filename: this.download });
    },
  };
  const context = vm.createContext({
    Blob, window: { showSaveFilePicker: picker },
    document: { createElement: () => anchor, body: { appendChild(a) { a.attached = true; } } },
    URL: { createObjectURL(blob) { blobs.push(blob); return "blob:download"; }, revokeObjectURL(url) { revoked.push(url); } },
    setTimeout(callback, delay) { timers.push({ callback, delay }); },
    console: { warn(...args) { warnings.push(args); } },
  });
  const module = new vm.SourceTextModule(await readFile(new URL('../hushclaw/web/modules/download.js', import.meta.url), 'utf8'), { context });
  await module.link(() => { throw new Error('unexpected dependency'); });
  await module.evaluate();
  return { save: module.namespace.saveMarkdownFile, clicks, blobs, revoked, timers, warnings, anchor };
}

test('native save preserves Unicode and raw Markdown without duplicate download', async () => {
  let options, written, closed = false;
  const h = await harness({ picker: async value => {
    options = value;
    return { createWritable: async () => ({ write: async text => { written = text; }, close: async () => { closed = true; } }) };
  } });
  assert.equal(await h.save(sample, 'chat.md'), 'saved');
  assert.equal(options.suggestedName, 'chat.md');
  assert.equal(written, sample);
  assert.equal(closed, true);
  assert.equal(h.clicks.length, 0);
});

for (const name of ['SecurityError', 'NotAllowedError', 'NotSupportedError', 'TypeError']) {
  test(`exposed picker rejecting with ${name} falls back to download`, async () => {
    const h = await harness({ picker: async () => { throw Object.assign(new Error('blocked'), { name }); } });
    assert.equal(await h.save(sample, 'chat.md'), 'downloaded');
    assert.deepEqual(h.clicks, [{ href: 'blob:download', filename: 'chat.md' }]);
    assert.equal(await h.blobs[0].text(), sample);
    assert.equal(h.blobs[0].type, 'text/markdown;charset=utf-8');
    assert.equal(h.warnings.length, 1);
  });
}

test('missing picker uses ordinary download and defers URL cleanup', async () => {
  const h = await harness();
  assert.equal(await h.save(sample, 'chat.md'), 'downloaded');
  assert.equal(h.revoked.length, 0);
  assert.equal(h.anchor.removed, true);
  assert.equal(h.timers.length, 1);
  assert.ok(h.timers[0].delay >= 1000);
  h.timers[0].callback();
  assert.deepEqual(h.revoked, ['blob:download']);
});

test('user cancellation does not download or report failure', async () => {
  const h = await harness({ picker: async () => { throw Object.assign(new Error('cancelled'), { name: 'AbortError' }); } });
  assert.equal(await h.save(sample, 'chat.md'), 'cancelled');
  assert.equal(h.clicks.length, 0);
  assert.equal(h.warnings.length, 0);
});

for (const phase of ['createWritable', 'write', 'close']) {
  test(`${phase} failure falls back and aborts any open writer`, async () => {
    let aborted = false;
    const writer = {
      async write() { if (phase === 'write') throw new Error('write failed'); },
      async close() { if (phase === 'close') throw new Error('close failed'); },
      async abort() { aborted = true; },
    };
    const h = await harness({ picker: async () => ({ async createWritable() {
      if (phase === 'createWritable') throw new Error('unsupported');
      return writer;
    } }) });
    assert.equal(await h.save(sample, 'chat.md'), 'downloaded');
    assert.equal(aborted, phase !== 'createWritable');
    assert.equal(await h.blobs[0].text(), sample);
  });
}

test('download failure propagates for actionable UI feedback and still cleans up', async () => {
  const h = await harness({ clickError: new Error('download blocked') });
  await assert.rejects(h.save(sample, 'chat.md'), /download blocked/);
  assert.equal(h.anchor.removed, true);
  assert.equal(h.timers.length, 1);
});

test('chat button uses the tested helper and catches document-building failures', async () => {
  const source = await readFile(new URL('../hushclaw/web/modules/chat/export.js', import.meta.url), 'utf8');
  const handler = source.slice(source.indexOf('mdBtn.addEventListener'), source.indexOf('const imgBtn ='));
  assert.ok(handler.indexOf('try {') < handler.indexOf('_buildShareMarkdown('));
  assert.match(handler, /await saveMarkdownFile\(text,/);
  assert.match(handler, /result !== "cancelled"/);
  assert.match(handler, /finally\s*\{\s*mdBtn.disabled = false/);
  assert.doesNotMatch(handler, /showSaveFilePicker|revokeObjectURL/);
});
