"""Real-browser regression: python3 tests/browser/check_files_layout.py.

Requires Playwright and installed Chrome. Serves source assets on an ephemeral
loopback port; all file data and WebSocket sends are mocked. No user files or
running HushClaw service are accessed. Screenshots are written to a temp folder.
"""

import json
import re
import tempfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def check_layout(page):
    metrics = page.evaluate("""() => {
      const list = document.querySelector('#files-list');
      const bounds = list.getBoundingClientRect();
      const row = document.querySelector('.file-item').getBoundingClientRect();
      const name = document.querySelector('.file-item-name').getBoundingClientRect();
      const actions = [...document.querySelectorAll('.file-item:first-child .ui-icon-action')].map(el => {
        const r = el.getBoundingClientRect();
        const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        return {left:r.left, right:r.right, width:r.width, height:r.height,
          hittable: el === hit || el.contains(hit)};
      });
      return {listWidth:list.clientWidth, scrollWidth:list.scrollWidth,
        rowHeight:row.height, rowRight:row.right, listRight:bounds.right,
        nameRight:name.right, actions,
        visibleRows:[...list.children].filter(el => el.getBoundingClientRect().bottom <= bounds.bottom).length};
    }""")
    assert metrics['scrollWidth'] <= metrics['listWidth'], metrics
    assert metrics['rowRight'] <= metrics['listRight'], metrics
    assert len(metrics['actions']) == 3
    assert metrics['nameRight'] <= metrics['actions'][0]['left'], metrics
    for action in metrics['actions']:
        assert action['hittable'], metrics
        assert action['right'] <= metrics['listRight'], metrics
        assert action['width'] >= 24 and action['height'] >= 24, metrics
    return metrics


def main():
    web = ROOT / 'hushclaw' / 'web'
    html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '', (web / 'index.html').read_text())
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(web)))
    Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/'
    output = Path(tempfile.mkdtemp(prefix='hushclaw-files-layout-'))
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome')
            for width, height, touch in [(1440, 900, False), (1280, 800, False), (390, 844, True), (320, 740, True)]:
                page = browser.new_page(viewport={'width':width, 'height':height},
                                        device_scale_factor=2, has_touch=touch)
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.route(url, lambda route: route.fulfill(body=html, content_type='text/html'))
                page.goto(url)
                page.evaluate("""async () => {
                  const {state, setWorkbenchPanelVisible} = await import('/modules/state.js');
                  const {renderFiles} = await import('/modules/panels/files.js');
                  window.sent = [];
                  window.testState = state;
                  state.ws = {readyState:1, send:value => window.sent.push(JSON.parse(value))};
                  document.documentElement.dataset.theme = 'vector';
                  document.documentElement.dataset.mode = 'light';
                  document.documentElement.style.colorScheme = 'light';
                  document.getElementById('startup-overlay').classList.add('hidden');
                  if (innerWidth < 760) document.body.classList.add('sessions-collapsed');
                  document.querySelectorAll('.panel').forEach(el => el.classList.remove('active'));
                  document.getElementById('panel-chat').classList.add('active');
                  setWorkbenchPanelVisible('files', true);
                  document.getElementById('files-sidebar').classList.remove('hidden');
                  window.renderFixture = count => renderFiles({total:221, items:Array.from({length:count}, (_, i) => ({
                    file_id:'fixture-'+i, filename:'fixture-'+i+'.md', url:'/files/fixture-'+i,
                    name:[('VoxNexus_' + 'VeryLongFileName'.repeat(12) + '.md'),
                      '产品战略提案.md', '"quoted" & <not markup>.md'][i%3],
                    size:12340, modified:Date.now()/1000-200, source:i%2 ? 'upload':'generated',
                    rating:i%3+2, tags:[{name:'产品', source:'manual'}, {name:'长标签'.repeat(10), source:'auto'}]
                  }))});
                  window.renderFixture(20);
                }""")
                page.mouse.move(0, 0)  # Actions must work without hover.
                assert page.locator('#btn-toggle-files-sidebar svg').count() == 1
                metrics = check_layout(page)
                if not touch:
                    assert metrics['rowHeight'] <= 60, metrics
                    assert metrics['visibleRows'] >= 8, metrics
                else:
                    assert all(a['width'] >= 32 for a in metrics['actions'])
                attach = page.locator('.file-item-attach').first
                attach.focus()
                page.keyboard.press('Enter')
                assert page.evaluate('window.testState._attachments.length') == 1
                page.locator('.file-item-del').first.click()
                assert page.get_by_text('删除文件？', exact=True).is_visible()
                assert not [v for v in page.evaluate('sent') if v['type'] == 'delete_file']
                page.get_by_role('button', name='取消', exact=True).click()
                assert not [v for v in page.evaluate('sent') if v['type'] == 'delete_file']
                page.locator('.file-item-del').first.click()
                page.get_by_role('button', name='删除文件', exact=True).click()
                assert [v for v in page.evaluate('sent') if v['type'] == 'delete_file'] == [
                    {'type':'delete_file', 'file_id':'fixture-0'}]
                page.locator('#app-modal-overlay').wait_for(state='hidden')
                page.mouse.move(0, 0)
                page.locator('.toast').wait_for(state='hidden')
                page.locator('#files-sidebar').screenshot(path=str(output / f'files-{width}-light.png'))
                page.evaluate("document.documentElement.dataset.mode='dark'; document.documentElement.style.colorScheme='dark'")
                check_layout(page)
                page.locator('#files-sidebar').screenshot(path=str(output / f'files-{width}-dark.png'))
                page.evaluate('renderFixture(1)')
                check_layout(page)
                assert not errors, errors
                results.append({'viewport':width, 'touch':touch, **metrics})
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(json.dumps({'screenshots':str(output), 'results':results}, ensure_ascii=False))


if __name__ == '__main__':
    main()
