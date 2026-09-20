import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {Streamdown, defaultRemarkPlugins} from 'streamdown';
import {remarkArtifactLinks} from '../hushclaw/web/shared/artifact-links.js';

function render(raw, mode='static') {
  return renderToStaticMarkup(React.createElement(Streamdown, {
    mode, remarkPlugins:[...Object.values(defaultRemarkPlugins), remarkArtifactLinks],
    components:{a:({node, ...props})=>React.createElement('a',props)},
  },raw));
}

test('real board report link tolerates destination whitespace in static and streaming modes',()=>{
  for(const mode of ['static','streaming']) for(const padding of ['', ' ', '\n']) {
    const html=render(`[下载《董事会经营汇报框架》](${padding}/files/e3c67191717b${padding})`,mode);
    assert.match(html,/href="\/files\/e3c67191717b"/);
    assert.doesNotMatch(html,/\[blocked\]/);
    assert.equal((html.match(/<a /g)||[]).length,1);
  }
});
test('existing links, reference definitions, images and titles are not rewritten',()=>{
  for(const raw of ['[文件](/files/id "说明 /files/other")', '[文件](</files/id>)',
    '[文件][report]\n\n[report]: /files/id', '[**文件**](/files/id)']) {
    const html=render(raw);
    assert.match(html,/href="\/files\/id"/);
    assert.equal((html.match(/<a /g)||[]).length,1);
    assert.doesNotMatch(html,/\[blocked\]/);
  }
  assert.match(render('![图片](/files/picture.png)'),/src="\/files\/picture.png"/);
});
test('plain paths still become links without linking code examples',()=>{
  const html=render('下载 /files/id_report.md 或 (/files/second)\n\n`/files/example`\n\n```text\n/files/code\n```');
  assert.match(html,/href="\/files\/id_report.md"/);
  assert.match(html,/href="\/files\/second"/);
  assert.doesNotMatch(html,/href="\/files\/(?:example|code)"/);
});
test('linkifier is idempotent and leaves unsafe URLs to the existing sanitizer',()=>{
  const tree={type:'root',children:[{type:'paragraph',children:[{type:'text',value:'/files/id'}]}]};
  const transform=remarkArtifactLinks();transform(tree);
  const once=JSON.stringify(tree);transform(tree);assert.equal(JSON.stringify(tree),once);
  const html=render('[bad](javascript:alert%281%29) [bad2](file:///etc/passwd)');
  assert.doesNotMatch(html,/href="(?:javascript:|file:)/);
});
