"""Real-browser checks for compact progress and trace-free conversation history.

Run with python3 tests/browser/check_chat_activity_layout.py.
Uses a local fixture and real UI modules, without live model calls or user data.
"""
import json
import re
import tempfile
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright
from check_files_layout import QuietHandler, ROOT


def main():
    web = ROOT / 'hushclaw/web'
    html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '', (web / 'index.html').read_text())
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(web)))
    Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/'
    output = Path(tempfile.mkdtemp(prefix='hushclaw-chat-activity-'))
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome')
            for width, mode in [(1280, 'light'), (390, 'light'), (390, 'dark')]:
                page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=2)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route(url, lambda route: route.fulfill(body=html, content_type='text/html'))
                page.goto(url)
                page.evaluate("""async mode => {
                  const {state, setCurrentSessionId} = await import('/modules/state.js');
                  window.chat = await import('/modules/chat.js');
                  const {handleMessage} = await import('/modules/websocket.js');
                  window.state = state;
                  state.ws = {readyState:1,send:() => {}};
                  setCurrentSessionId('fixture');
                  window.event = data => handleMessage({session_id:'fixture', ...data});
                  document.documentElement.dataset.theme='vector';
                  document.documentElement.dataset.mode=mode;
                  document.getElementById('startup-overlay').classList.add('hidden');
                  document.querySelectorAll('.panel').forEach(el => el.classList.remove('active'));
                  document.getElementById('panel-chat').classList.add('active');
                  document.body.classList.add('sessions-collapsed');
                  chat.setDevMode(false);
                  await chat.renderSessionHistory('fixture', [{role:'user',content:'帮我阅读这份文档，整理关键结论并更新文件。'}]);
                  chat.insertThinkingMsg();
                  event({type:'tool_call',tool:'read_file',input:{}});
                }""", mode)
                assert page.locator('.thinking-msg').count() == 1
                assert not page.locator('.chat-progress-details').is_visible()
                assert not page.locator('.thinking-bubble .ai-activity-elapsed').is_visible()
                before = page.locator('.thinking-msg').bounding_box()
                page.evaluate("""() => {
                  state._thinkingStart = Date.now() - 125000;
                  for (let i=0; i<80; i++) {
                    event({type:'tool_call',tool:'read_file',call_id:String(i),input:{}});
                    event({type:'tool_result',tool:'read_file',call_id:String(i),result:'Done'});
                  }
                  chat.showAiProgress('正在生成或更新文件…');
                }""")
                assert page.locator('#messages .tool-line, #messages .tool-round').count() == 0
                assert page.locator('.thinking-msg').count() == 1
                assert page.locator('.thinking-bubble .ai-activity-elapsed').inner_text().startswith('2m')
                after = page.locator('.thinking-msg').bounding_box()
                assert abs(before['height'] - after['height']) < 2, (before, after)
                assert after['height'] < 60, after
                page.locator('#chat-area').screenshot(path=str(output / f'{width}-{mode}-working.png'))
                page.evaluate("chat.setDevMode(true); chat.showAiProgress('正在生成或更新文件…')")
                page.locator('.chat-progress-details').click()
                assert page.locator('#runtime-monitor').is_visible()
                page.evaluate("chat.setDevMode(false); chat.showAiProgress('正在整理回复…')")
                assert not page.locator('.chat-progress-details').is_visible()
                page.evaluate("""async () => {
                  const {setWorkbenchPanelVisible} = await import('/modules/state.js');
                  setWorkbenchPanelVisible('runtime', false);
                  event({type:'chunk', text:'已更新文档，并保留原有结构。你可以在文件面板查看最新版本。'});
                  chat.finalizeAiMsgNow();
                }""")
                assert page.locator('.thinking-msg').count() == 0
                answer_left = page.locator('#messages .msg.ai .bubble').bounding_box()['x']
                assert abs(after['x'] - answer_left) < 2, (after, answer_left)
                page.evaluate("""async () => {
                  await chat.renderSessionHistory('fixture', [
                    {role:'user',content:'请更新这份文档。'},
                    ...Array.from({length:150}, () => ({role:'tool',tool_name:'read_file',content:'Done'})),
                    {role:'assistant',content:'文档已更新。主要结论分为三个部分：核心能力、产品拓展与商业价值。'},
                  ]);
                }""")
                assert page.locator('#messages .msg').count() == 2
                assert page.locator('#messages .tool-line, #messages .history-window-spacer').count() == 0
                assert page.locator('#messages').evaluate('(el) => el.scrollWidth <= el.clientWidth')
                page.locator('#chat-area').screenshot(path=str(output / f'{width}-{mode}-complete.png'))
                assert not errors, errors
                results.append({'width':width,'mode':mode,'progressHeight':after['height']})
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(json.dumps({'screenshots':str(output),'results':results}, ensure_ascii=False))


if __name__ == '__main__':
    main()
