"""Verify the workbench frame with real modules and isolated fixture data.

Run: python3 tests/browser/check_workbench_frame.py
No live account, service, or user files are accessed.
"""
import json
import re
import tempfile
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright
from check_files_layout import ROOT, QuietHandler


def main():
    web = ROOT / 'hushclaw/web'
    html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '', (web / 'index.html').read_text())
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(web)))
    Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/'
    output = Path(tempfile.mkdtemp(prefix='hushclaw-workbench-frame-'))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome')
            page = browser.new_page(viewport={'width':1600, 'height':1000}, device_scale_factor=1)
            errors=[]
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.route(url, lambda route: route.fulfill(body=html, content_type='text/html'))
            page.goto(url)
            page.evaluate("""async () => {
              window.i18n = await import('/modules/i18n.js');
              await import('/modules/shell.js');
              window.store = await import('/modules/state.js');
              window.sessions = await import('/modules/panels/sessions.js');
              window.files = await import('/modules/panels/files.js');
              window.autocomplete = await import('/modules/events/autocomplete.js');
              window.chat = await import('/modules/chat.js');
              window.sent = [];
              store.state.ws = {readyState:1, send:data => sent.push(JSON.parse(data))};
              document.getElementById('startup-overlay').classList.add('hidden');
              document.documentElement.dataset.theme = 'vector';
              document.documentElement.dataset.mode = 'light';
              i18n.initLocale(); i18n.setLocale('zh');
              sessions.initSessionsSidebarState();
              files.initFilesSidebar();
              store.state.activeWorkspace = 'VoxNexus';
              sessions.renderWorkspaceSelector([{name:'VoxNexus'}, {name:'Long workspace name with many words'}, {name:'个人项目'}]);
              store.setCurrentSessionId('fixture-0');
              sessions.renderSessions(Array.from({length:35}, (_,i)=>({session_id:'fixture-'+i,
                title:['产品战略与长期记忆','整理这周的项目进展','下一代智能终端设计方案'][i%3],
                turns:8, updated:Date.now()/1000-i*3600})));
              store.state.agents = [{name:'default'}, {name:'exec-ai-ceo'}];
              store.skills.catalog = [{name:'company-due-diligence'}, {name:'product-idea'}];
              autocomplete.refreshComposerRecommendations();
              window.fileData = {total:223, items:Array.from({length:20}, (_,i)=>({
                file_id:'file-'+i, filename:'file-'+i+'.md', url:'/files/file-'+i,
                name:['端侧智能战略与产品价值.md', 'VoxNexus_Product_Strategy_2026_September.md', '项目评审与下一步计划.md'][i%3],
                size:21000, modified:Date.now()/1000-i*3000, source:'upload', rating:i%5,
                tags:[{name:'战略',source:'manual'}]}))};
              files.renderFiles(fileData);
              chat.insertUserMsg('帮我整理产品方向，并总结接下来的三个重点。');
              chat.completeAiMsgWithAuthoritativeText('产品的核心是让不同模型共享持续的个人上下文。\\n\\n## 三个重点\\n\\n1. **本地优先**：记忆保存在自己的设备上。\\n2. **自由切换**：在同一个工作台中选择合适的模型。\\n3. **持续理解**：跨会话保留偏好、项目背景和重要决定。\\n\\n接下来可以先完善最常用的工作路径：开始对话、查看资料，再将结果保存到本地。');
              chat.finalizeAiMsgNow();
            }""")
            assert not page.locator('#chat-workbench').is_visible(), 'Files should start closed'
            page.locator('#btn-toggle-files-inline').click()
            assert page.locator('#chat-workbench').is_visible()
            for width,height,mode,expanded in [(1600,1000,'light',True),(1440,900,'dark',False),(1180,760,'light',False)]:
                page.set_viewport_size({'width':width,'height':height})
                page.evaluate("([mode,expanded])=>{document.documentElement.dataset.mode=mode;document.body.classList.toggle('app-rail-expanded',expanded)}",[mode,expanded])
                page.wait_for_timeout(300)
                boxes=[page.locator(s).bounding_box() for s in ['.sidebar-header','#chat-stats-strip','.files-sidebar-header']]
                assert max(b['y'] for b in boxes)-min(b['y'] for b in boxes)<1,boxes
                assert all(b['height']==52 for b in boxes), boxes
                assert page.locator('#input').bounding_box()['width']>300
                answer = page.locator('.msg.ai').last.bounding_box()
                composer = page.locator('#chat-composer .input-wrap').bounding_box()
                assert abs(answer['x']-composer['x'])<=1, (answer,composer)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert page.locator('#files-list').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
                page.screenshot(path=str(output/f'frame-{width}-{mode}.png'))
            # Filters stay collapsed until requested and preserve their active state.
            assert not page.locator('#files-filter-bar').is_visible()
            page.locator('#files-filters-toggle').click()
            page.locator('#files-important-filter').click()
            assert page.evaluate('sent.at(-1).min_rating')==4
            page.evaluate('files.renderFiles(fileData)')
            page.locator('#files-filters-toggle').click()
            assert not page.locator('#files-filter-bar').is_visible()
            assert page.locator('.filter-count').inner_text()=='1'
            page.locator('#files-filters-toggle').click()
            assert page.locator('#files-important-filter').get_attribute('aria-pressed')=='true'
            page.locator('#files-sort').select_option('rating')
            assert page.evaluate('sent.at(-1).sort')=='rating'
            page.locator('#files-filters-toggle').click()
            # Shortcuts are operable with keyboard, wrap, and insert into the draft.
            assert not page.locator('.composer-recommendation').first.is_visible()
            page.locator('#composer-recommendations summary').focus()
            page.keyboard.press('Enter')
            assert page.locator('.composer-recommendation').first.is_visible()
            page.locator('.composer-recommendation').first.click()
            assert page.locator('#input').input_value().startswith('@')
            page.evaluate("store.els.input.value='';autocomplete.refreshComposerRecommendations()")
            # Workspace switching retains routing, including the default workspace.
            page.locator('#workspace-select').select_option('个人项目')
            assert page.evaluate('store.state.activeWorkspace')=='个人项目'
            assert page.evaluate("sent.some(s=>s.type==='list_sessions' && s.workspace==='个人项目')")
            page.locator('#workspace-select').select_option('')
            assert page.evaluate('store.state.activeWorkspace') is None
            # Narrow windows overlay files without squeezing the composer.
            for width in [1024,768,390,320]:
                page.set_viewport_size({'width':width,'height':844})
                page.evaluate("document.body.classList.toggle('sessions-collapsed',innerWidth<=760)")
                page.evaluate('files.toggleFilesSidebar(false)')
                page.wait_for_timeout(300)
                panel=page.locator('#chat-workbench').bounding_box()
                assert panel['x']>=0 and panel['x']+panel['width']<=width+1,panel
                assert page.locator('#chat-workbench').evaluate('(el)=>getComputedStyle(el).position')=='absolute'
                assert page.locator('#files-list').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
                page.screenshot(path=str(output/f'drawer-{width}.png'))
                page.keyboard.press('Escape')
                assert not page.locator('#chat-workbench').is_visible()
                assert page.locator('#input').bounding_box()['width']>170
            # Mobile retains the workspace switcher when sessions are opened.
            page.evaluate('sessions.toggleSessionsSidebar(false)')
            assert page.locator('#workspace-select').is_visible()
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown(); server.server_close()
    print(json.dumps({'screenshots':str(output),'result':'passed'},ensure_ascii=False))


if __name__=='__main__':
    main()
