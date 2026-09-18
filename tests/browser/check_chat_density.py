"""python3 tests/browser/check_chat_density.py — Chrome fixture-only visual QA."""
import json
import re
import tempfile
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright

from check_files_layout import QuietHandler, ROOT


def check_icons(page):
    for selector in ['#btn-attach']:
        metrics = page.locator(selector).evaluate("""el => {
          const b=el.getBoundingClientRect(), s=el.querySelector('svg').getBoundingClientRect();
          return {dx:Math.abs(b.x+b.width/2-s.x-s.width/2),dy:Math.abs(b.y+b.height/2-s.y-s.height/2),width:b.width,height:b.height};
        }""")
        assert metrics['dx'] < 1 and metrics['dy'] < 1, (selector, metrics)
        assert metrics['width'] == metrics['height'], metrics


def main():
    web = ROOT / 'hushclaw' / 'web'
    html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '', (web / 'index.html').read_text())
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(web)))
    Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/'
    output = Path(tempfile.mkdtemp(prefix='hushclaw-chat-density-'))
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome')
            page = browser.new_page(viewport={'width':1440,'height':960},device_scale_factor=2)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.route(url, lambda r: r.fulfill(body=html,content_type='text/html'))
            page.goto(url)
            page.add_script_tag(url=url+'react-dist/react-islands.js', type='module')
            page.wait_for_function('window.HushClawReactMarkdown?.ready')
            page.evaluate("""async () => {
              localStorage.setItem('hushclaw.ui.app-rail-expanded','1');
              await import('/modules/shell.js');
              const {state, setCurrentSessionId, setWorkbenchPanelVisible} = await import('/modules/state.js');
              const {renderSessions,renderWorkspaceSelector,initSessionsSidebarState,toggleSessionsSidebar} = await import('/modules/panels/sessions.js');
              initSessionsSidebarState();
              window.toggleSessionsSidebar=toggleSessionsSidebar;
              window.chat=await import('/modules/chat.js');
              const {initComposerMenu}=await import('/modules/ui/composer-menu.js');
              window.sent=[];
              state.ws={readyState:1,send:value=>sent.push(JSON.parse(value))};
              setCurrentSessionId('fixture-0');
              document.documentElement.dataset.theme='vector';
              document.documentElement.dataset.mode='light';
              document.documentElement.style.colorScheme='light';
              document.getElementById('startup-overlay').classList.add('hidden');
              state.activeWorkspace='VoxNexus';
              renderWorkspaceSelector([{name:'VoxNexus',path:'/fixture'},{name:'Transsion',path:'/fixture'}]);
              renderSessions(Array.from({length:30},(_,i)=>({session_id:'fixture-'+i,
                title:['未来董事会会议框架','这是我们的融资 BP','我们在 10 月 15 号之前','系统搜一下这次苹果发布会关于苹果手机的新消息','越来越多的个人助理公司','AISO设计讨论'][i%6],
                turn_count:i+4,last_turn:Date.now()/1000-i*86400,workspace:'VoxNexus',
                runtime:i===0?{status:'running',summary:'正在生成文档…'}:{status:'idle'}})));
              chat.insertUserMsg('请把董事会会议框架整理得更清晰，方便阅读和比较。');
              chat.appendChunk('## 会议框架\\n\\n让董事会围绕关键决策展开讨论，把背景资料放在附录中。\\n\\n| 议题 | 需要决定的事项 |\\n| --- | --- |\\n| 市场与产品 | 明确目标用户和产品优先级 |\\n| 财务与组织 | 审核预算，确认负责人 |\\n\\n每个议题保留结论、依据和下一步，便于会后跟进。');
              chat.finalizeAiMsgNow();
              chat.insertThinkingMsg(Date.now()-15000);
              chat.showAiProgress('正在生成或更新文件…');
              setWorkbenchPanelVisible('files',false);
              setWorkbenchPanelVisible('runtime',false);
              initComposerMenu({button:document.getElementById('btn-attach'),input:document.getElementById('input')});
            }""")
            check_icons(page)
            page.locator('.msg.ai .bubble h2').wait_for()
            page.wait_for_function("Math.abs(document.querySelector('header.app-rail').getBoundingClientRect().width-192)<0.1")
            metrics=page.evaluate("""() => {
              const list=document.querySelector('#sessions-list').getBoundingClientRect();
              const row=document.querySelectorAll('.sidebar-session')[1].getBoundingClientRect();
              return {rowHeight:row.height,visibleRows:[...document.querySelectorAll('.sidebar-session')].filter(el=>el.getBoundingClientRect().bottom<=list.bottom).length,
                composerHeight:document.querySelector('.input-wrap').getBoundingClientRect().height,
                navWidth:document.querySelector('header.app-rail').getBoundingClientRect().width,
                threadsWidth:document.querySelector('#sessions-sidebar').getBoundingClientRect().width};
            }""")
            assert metrics['rowHeight'] <= 46, metrics
            assert metrics['visibleRows'] >= 17, metrics
            assert metrics['composerHeight'] <= 96, metrics
            assert abs(metrics['navWidth'] - 192) < 1, metrics
            assert metrics['threadsWidth'] == 260, metrics
            typography = page.evaluate("""() => {
              const read=selector=>{const s=getComputedStyle(document.querySelector(selector));
                return {size:s.fontSize,line:s.lineHeight,weight:s.fontWeight,tracking:s.letterSpacing,family:s.fontFamily};};
              return {body:read('.msg.ai .bubble p'),heading:read('.msg.ai .bubble h2'),
                session:read('.sidebar-session:not(.active) .sidebar-session-title'),
                activeSession:read('.sidebar-session.active .sidebar-session-title'),
                meta:read('.sidebar-session-meta'),input:read('#input')};
            }""")
            assert typography['body']['size'] == '13.5px', typography
            assert abs(float(typography['body']['line'][:-2])-22) < .1, typography
            assert typography['heading']['size'] == '16px', typography
            assert typography['heading']['weight'] == '600', typography
            assert page.locator('.msg.ai .bubble h2').evaluate('el=>getComputedStyle(el).marginTop') == '0px'
            assert typography['session']['size'] == '12px', typography
            assert typography['session']['weight'] == '500', typography
            assert typography['activeSession']['weight'] == '600', typography
            assert typography['meta']['size'] == '11px', typography
            assert typography['input']['size'] == '13px', typography
            # Navigation is a compact, stronger scan label; prose is a calmer
            # reading texture. Shared font family must not flatten these roles.
            assert float(typography['session']['size'][:-2]) < float(typography['body']['size'][:-2]), typography
            assert int(typography['session']['weight']) > int(typography['body']['weight']), typography
            assert all(s['tracking'] in ('normal','0px') for s in typography.values()), typography
            assert all('Inter' not in s['family'] for s in typography.values()), typography
            metrics['typography'] = typography
            native_headings = page.evaluate("""async () => {
              const {renderMarkdown}=await import('/modules/markdown.js');
              const root=document.createElement('div');
              root.innerHTML=renderMarkdown('# A\\n\\n## B\\n\\n### C\\n\\n#### D\\n\\n##### E\\n\\n###### F');
              return [...root.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(el=>el.tagName);
            }""")
            assert native_headings == ['H1','H2','H3','H4','H5','H6'], native_headings
            assert page.locator('#btn-new-session').count() == 0
            page.locator('#btn-attach').click()
            assert page.get_by_role('menu',name='Add context or action').is_visible()
            page.keyboard.press('Escape')
            page.locator('.sidebar-session').first.hover()
            page.locator('.session-delete-btn').first.click()
            assert page.get_by_role('heading',name='Delete session').is_visible()
            page.get_by_role('button',name='Cancel',exact=True).click()
            assert not [v for v in page.evaluate('sent') if v['type']=='delete_session']
            page.locator('#app-modal-overlay').wait_for(state='hidden')
            page.mouse.move(0,0)
            page.locator('#input').focus()
            page.wait_for_timeout(350)  # Capture settled paint, not theme/rail transitions.
            page.screenshot(path=str(output/'desktop-light.png'))
            page.evaluate("document.documentElement.dataset.mode='dark'; document.documentElement.style.colorScheme='dark'")
            page.wait_for_timeout(350)
            page.screenshot(path=str(output/'desktop-dark.png'))
            page.evaluate("""() => {
              chat.removeThinkingMsg(); chat.appendChunk('补充：记录每项决策的负责人和完成时间。'); chat.finalizeAiMsgNow();
            }""")
            authors=page.locator('.msg.ai .msg-meta')
            assert authors.count() == 2
            assert authors.nth(0).is_visible() and not authors.nth(1).is_visible()
            # Narrowest resizable thread rail: actions remain within the header.
            page.evaluate("document.documentElement.style.setProperty('--threads-drawer-w','220px')")
            assert page.locator('.sidebar-header').evaluate("""el=>{
              const b=el.getBoundingClientRect();return [...el.querySelectorAll('button')].every(btn=>btn.getBoundingClientRect().right<=b.right);
            }""")
            page.locator('#btn-toggle-app-rail').click()
            check_icons(page)
            page.evaluate('toggleSessionsSidebar(true);toggleSessionsSidebar(false)')
            assert page.locator('#btn-toggle-sessions svg').count() == 1
            results.append(metrics)
            page.set_viewport_size({'width':390,'height':844})
            page.evaluate("document.body.classList.add('sessions-collapsed'); document.documentElement.dataset.mode='light'; document.documentElement.style.colorScheme='light'")
            page.wait_for_timeout(350)
            check_icons(page)
            assert page.evaluate('document.documentElement.scrollWidth') <= 390
            composer=page.locator('.input-wrap').bounding_box()
            assert composer['y']+composer['height'] <= 844, composer
            assert composer['height'] <= 104, composer
            assert abs(page.locator('#btn-attach').bounding_box()['y']-page.locator('#btn-send').bounding_box()['y']) < 1
            page.screenshot(path=str(output/'mobile.png'))
            # Exercise the production event binding, not a synthetic forwarding click.
            boot = browser.new_page(viewport={'width':1440,'height':960})
            boot.route(url, lambda r: r.fulfill(body=html,content_type='text/html'))
            boot.on('pageerror', lambda e: errors.append(str(e)))
            boot.goto(url)
            boot.evaluate("""async () => {
              window.WebSocket=class {static OPEN=1;readyState=0;send(){}close(){}};
              await import('/modules/events.js');
              const {setCurrentSessionId,getCurrentSessionId}=await import('/modules/state.js');
              setCurrentSessionId('fixture-old'); window.getSessionId=getCurrentSessionId;
              document.getElementById('startup-overlay').classList.add('hidden');
            }""")
            boot.locator('#btn-new-session-sidebar').focus()
            boot.keyboard.press('Enter')
            assert not boot.evaluate('getSessionId()')
            assert boot.locator('#input').evaluate('el=>el===document.activeElement')
            assert boot.locator('#messages').inner_text().count('New session started.') == 1
            boot.close()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(json.dumps({'screenshots':str(output),'results':results},ensure_ascii=False))


if __name__ == '__main__':
    main()
