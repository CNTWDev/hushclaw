"""python3 tests/browser/check_chat_progress.py (Playwright + Chrome).

Uses real source modules and mocked server events; no model or user data access.
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


def cursor_position(page, expected=""):
    if expected:
        page.wait_for_function("text => document.querySelector('.msg-streaming .bubble')?.textContent.includes(text)", arg=expected)
    page.wait_for_function("document.querySelector('.msg-streaming .bubble[data-stream-tail]')")
    # Independently compare the cursor to the final visible text's Range.
    page.wait_for_function("""() => {
      const b = document.querySelector('.msg-streaming .bubble');
      const walker = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
      let node, last;
      while (node = walker.nextNode()) {
        if (node.textContent.trim() && !node.parentElement.closest('button, [aria-hidden="true"]')) last = node;
      }
      const r = document.createRange();
      const n = last.textContent.trimEnd().length;
      r.setStart(last, n-1); r.setEnd(last, n);
      const tail = [...r.getClientRects()].at(-1), box = b.getBoundingClientRect();
      const css = getComputedStyle(b, '::after');
      return css.display === 'block' && Math.abs(parseFloat(css.top) + box.top + parseFloat(css.height) - tail.bottom) < 2
        && Math.abs(parseFloat(css.left) + box.left - Math.min(tail.right + 2, box.right - 3)) < 2;
    }""")
    return page.locator('.msg-streaming .bubble').evaluate("""b => {
      const css = getComputedStyle(b, '::after');
      return {top:css.top,left:css.left,height:css.height,shadow:css.boxShadow,renderer:b.dataset.renderer};
    }""")


def main():
    web = ROOT / 'hushclaw' / 'web'
    html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '', (web / 'index.html').read_text())
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(web)))
    Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/'
    output = Path(tempfile.mkdtemp(prefix='hushclaw-chat-progress-'))
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome')
            for renderer in ['native', 'react']:
                page = browser.new_page(viewport={'width':1280, 'height':900}, device_scale_factor=2)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route(url, lambda route: route.fulfill(body=html, content_type='text/html'))
                page.goto(url)
                if renderer == 'react':
                    page.add_script_tag(url=url+'react-dist/react-islands.js', type='module')
                    page.wait_for_function('window.HushClawReactMarkdown?.ready')
                page.evaluate("""async () => {
                  const {state, setCurrentSessionId} = await import('/modules/state.js');
                  window.chat = await import('/modules/chat.js');
                  window.primitives = await import('/modules/ui/ai-primitives.js');
                  const {handleMessage} = await import('/modules/websocket.js');
                  window.state = state;
                  state.ws = {readyState:1,send:() => {}};
                  setCurrentSessionId('fixture');
                  window.event = data => handleMessage({session_id:'fixture', ...data});
                  document.documentElement.dataset.theme='vector';
                  document.documentElement.dataset.mode='light';
                  document.documentElement.style.colorScheme='light';
                  document.getElementById('startup-overlay').classList.add('hidden');
                  document.querySelectorAll('.panel').forEach(el => el.classList.remove('active'));
                  document.getElementById('panel-chat').classList.add('active');
                  chat.insertThinkingMsg(Date.now()-5000);
                  window.thinkingNode = state._thinkingEl;
                  event({type:'round_info',round:1,max_rounds:10});
                  event({type:'tool_call',tool:'fetch_url',call_id:'1',input:{}});
                }""")
                assert page.evaluate('state._thinkingEl === thinkingNode')
                assert page.locator('.ai-activity-detail').get_attribute('data-detail') == '正在查找资料…'
                transitions = page.evaluate("""() => {
                  const el = document.querySelector('.ai-activity-detail');
                  const animations = el.getAnimations({subtree:true});
                  animations.forEach(a => {a.pause();a.currentTime=120;});
                  return animations.map(a => a.effect.getKeyframes().map(k => k.transform));
                }""")
                assert ['translateY(0px)', 'translateY(-100%)'] in transitions, transitions
                page.locator('.thinking-msg').screenshot(path=str(output / f'{renderer}-step-transition.png'))
                page.evaluate("""() => {
                  document.querySelector('.ai-activity-detail').getAnimations({subtree:true}).forEach(a => a.finish());
                  event({type:'session_runtime',runtime:{status:'running',phase:'tool_call',
                    active_step:{step_type:'tool',meta:{tool:'read_file'}}}});
                }""")
                assert page.locator('.ai-activity-detail').get_attribute('data-detail') == '正在阅读文件…'
                page.evaluate("event({type:'tool_result',tool:'read_file',call_id:'1',result:'ok'}); event({type:'round_info',round:2,max_rounds:10})")
                assert page.locator('.ai-activity-detail').get_attribute('data-detail') == '正在分析 · 第 2 轮'
                assert page.evaluate('state._thinkingEl === thinkingNode')
                page.evaluate("event({type:'tool_call',tool:'write_file',call_id:'2',input:{}})")
                page.wait_for_function("!document.querySelector('.ai-activity-line.is-outgoing')")
                assert page.locator('.ai-activity-detail').get_attribute('data-detail') == '正在生成或更新文件…'
                page.evaluate("chat.showAiProgress('正在生成或更新文件…')")
                assert page.locator('.ai-activity-detail').evaluate('(el) => el.getAnimations({subtree:true}).length') == 0
                # Background events must not replace the current session's step.
                page.evaluate("event({type:'tool_call',session_id:'other',tool:'fetch_url'})")
                assert page.locator('.ai-activity-detail').get_attribute('data-detail') == '正在生成或更新文件…'
                page.emulate_media(reduced_motion='reduce')
                page.evaluate("chat.showAiProgress('正在阅读文件…')")
                assert page.locator('.ai-activity-detail').evaluate('(el) => el.getAnimations({subtree:true}).length') == 0
                assert page.locator('.ai-activity-line.is-outgoing').count() == 0
                page.evaluate("event({type:'chunk',text:'这是第一段回复。\\n\\n'})")
                first = cursor_position(page, '这是第一段回复。')
                assert page.locator('.thinking-msg').count() == 0
                page.evaluate("event({type:'chunk',text:'## 结论\\n\\n- 第一项内容\\n- 第二项 **加粗结尾**'})")
                nested = cursor_position(page, '加粗结尾')
                assert float(nested['top'][:-2]) > float(first['top'][:-2]) + 30
                page.evaluate("event({type:'chunk',text:'\\n\\n| 项目 | 结果 |\\n| --- | --- |\\n| 校验 | 通过 |\\n\\n```python\\nprint(42)\\n```\\n\\n' + '这一段用于检查换行后光标的位置。'.repeat(20) + '最后一个字。'})")
                final = cursor_position(page, '最后一个字。')
                assert final['shadow'] == 'none', final
                assert page.locator('.msg-streaming .bubble').evaluate("b => getComputedStyle(b,'::after').animationName") == 'none'
                page.locator('#chat-area').screenshot(path=str(output / f'{renderer}-stream.png'))
                page.set_viewport_size({'width':390,'height':844})
                page.evaluate("document.body.classList.add('sessions-collapsed'); document.documentElement.dataset.mode='dark'")
                cursor_position(page, '最后一个字。')
                page.evaluate("chat.setChunkText('```python\\nprint(42)\\n```')")
                cursor_position(page, 'print(42)')
                page.evaluate("chat.setChunkText('| 项目 | 结果 |\\n| --- | --- |\\n| 校验 | 通过 |')")
                cursor_position(page, '通过')
                page.evaluate('window.previousBubble=state._aiBubbleEl; chat.finalizeAiMsgNow()')
                assert page.locator('[data-stream-tail]').count() == 0
                assert page.evaluate('previousBubble._stopFollowingTail === null')
                # Session replay and reset must also clear Thinking and observers.
                page.evaluate("chat.insertThinkingMsg(); chat.setChunkText('恢复中的正文。')")
                cursor_position(page, '恢复中的正文。')
                assert page.locator('.thinking-msg').count() == 0
                page.evaluate('window.previousBubble=state._aiBubbleEl; chat.resetChatSessionUiState()')
                assert page.evaluate("!previousBubble.hasAttribute('data-stream-tail')")
                assert not errors, errors
                results.append({'renderer':renderer,'first':first,'nested':nested,'final':final})
                page.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print(json.dumps({'screenshots':str(output),'results':results},ensure_ascii=False))


if __name__ == '__main__':
    main()
