"""Isolated browser coverage for navigation and live English/Chinese switching."""
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
    output = Path(tempfile.mkdtemp(prefix='hushclaw-sidebar-locale-'))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome')
            page = browser.new_page(viewport={'width':1280, 'height':900}, device_scale_factor=2)
            errors=[]
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.route(url, lambda route: route.fulfill(body=html, content_type='text/html'))
            page.goto(url)
            page.evaluate("""async () => {
              window.i18n=await import('/modules/i18n.js');
              await import('/modules/shell.js');
              window.store=await import('/modules/state.js');
              store.state.ws={readyState:1,send:()=>{}};
              window.settings=await import('/modules/settings.js');
              window.calendar=await import('/modules/calendar.js');
              window.connections=await import('/modules/panels/app_connectors.js');
              document.getElementById('startup-overlay').classList.add('hidden');
              document.documentElement.dataset.theme='vector';
              document.documentElement.dataset.mode='light';
              document.body.classList.add('app-rail-expanded');
              document.querySelectorAll('.panel').forEach(e=>e.classList.remove('active'));
              document.getElementById('panel-calendar').classList.add('active');
              document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('active',e.dataset.tab==='calendar'));
              i18n.initLocale(); i18n.setLocale('zh');
              calendar.initCalendar();
            }""")
            assert page.locator('[data-tab="app-connectors"]').inner_text()=='连接'
            assert page.locator('.rail-locale-value').inner_text()=='中文'
            page.locator('.rail-utilities').screenshot(path=str(output/'utilities-zh.png'))
            page.locator('#lang-toggle').click()
            assert page.locator('html').get_attribute('lang')=='en'
            page.locator('.rail-utilities').screenshot(path=str(output/'utilities-en.png'))
            assert page.locator('[data-tab="app-connectors"]').inner_text()=='Connections'
            assert page.locator('.it-page-title').inner_text()=='My itinerary'
            assert page.locator('#cal-summary').inner_text().startswith('0 events')
            page.locator('[data-view="month"]').click()
            assert page.locator('.it-month-head').inner_text().startswith('Mon')
            assert page.locator('#lang-toggle svg').count()==1
            assert page.evaluate("localStorage.getItem('hushclaw.ui.locale')")=='en'
            # Newly inserted UI labels translate, while identical user text does not.
            page.evaluate("""() => {
              document.body.insertAdjacentHTML('beforeend', '<div id="translation-fixture"><span data-i18n="ui:Save">Save</span><span id="user-text">Save</span><input id="draft" value="Save"></div>');
              i18n.setLocale('zh');
            }""")
            assert page.locator('#translation-fixture [data-i18n]').inner_text()=='保存'
            assert page.locator('#user-text').inner_text()=='Save'
            assert page.locator('#draft').input_value()=='Save'
            page.evaluate("document.getElementById('translation-fixture').remove()")
            # An open settings form keeps its DOM node and unsaved data.
            page.evaluate("""() => {
              settings.openWizard(); store.wizard.tab='system'; settings.renderSettingsModal();
              window.draftNode=document.getElementById('sys-max-tokens'); draftNode.value='1234';
              i18n.setLocale('en');
            }""")
            assert page.locator('#sys-max-tokens').input_value()=='1234'
            assert page.evaluate("draftNode===document.getElementById('sys-max-tokens')")
            page.evaluate("i18n.setLocale('zh')")
            assert page.locator('#sys-max-tokens').input_value()=='1234'
            assert '生成' in page.locator('#wizard-body').inner_text()
            page.evaluate("store.wizard.tab='model'; settings.renderSettingsModal(); i18n.setLocale('en')")
            page.wait_for_function("document.getElementById('vox-login')?.textContent==='Sign in to VoxNexus'")
            assert page.locator('.vox-badge').inner_text()=='Signed out'
            page.evaluate("settings.closeWizard(); connections.renderAppConnectorsPanel(); i18n.setLocale('zh')")
            assert page.locator('.app-connectors-header h1').text_content()=='连接', (page.locator('.app-connectors-header h1').text_content(), errors)
            # Geometry: both utilities share icons/labels, remain visible on short screens.
            for width,height,mode in [(1280,900,'light'),(1280,460,'light'),(1280,900,'dark'),(390,844,'light')]:
                page.set_viewport_size({'width':width,'height':height})
                page.evaluate("mode => {document.documentElement.dataset.mode=mode; document.body.classList.add('app-rail-expanded')}",mode)
                page.wait_for_timeout(280)
                sidebar=page.locator('header.app-rail').bounding_box()
                language=page.locator('#lang-toggle').bounding_box()
                settings_box=page.locator('.tab-settings').bounding_box()
                assert language['y']+language['height']<=height+1,(width,language)
                assert abs(language['x']-settings_box['x'])<1
                assert language['width']==settings_box['width']
                assert page.locator('header.app-rail').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
                page.locator('header.app-rail').screenshot(path=str(output/f'{width}-{height}-{mode}.png'))
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown();server.server_close()
    print(json.dumps({'screenshots':str(output),'result':'passed'},ensure_ascii=False))


if __name__=='__main__':
    main()
