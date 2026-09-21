"""Transport/setup regressions: no real credentials or external connections."""
import json
import threading
import tomllib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hushclaw.config.schema import Config, EmailConfig, CalendarConfig
from hushclaw.util import mail_auth as auth
from hushclaw.server.integration_handler import handle_test_email, handle_test_calendar


def email(**kw):
    return EmailConfig(**dict(dict(imap_host='imap.qq.com', smtp_host='smtp.qq.com',
        smtp_port=465, username='me@qq.com', password='client-secret'), **kw))


def test_gmail_normalizes_only_grouping_spaces_and_rejects_login_password():
    cfg = email(imap_host='imap.gmail.com', password='abcd efgh ijkl mnop')
    assert auth.mail_credentials(cfg, cfg.imap_host) == ('me@qq.com', 'abcdefghijklmnop')
    cfg.password = 'my-google-login-password'
    with pytest.raises(ValueError, match='应用专用密码'):
        auth.mail_credentials(cfg, cfg.imap_host)
    cfg.imap_host = 'imap.qq.com'
    cfg.password = ' spaces matter '
    assert auth.mail_credentials(cfg, cfg.imap_host)[1] == ' spaces matter '


def test_outlook_is_rejected_before_any_connection(monkeypatch):
    constructor = MagicMock()
    monkeypatch.setattr(auth.imaplib, 'IMAP4_SSL', constructor)
    with pytest.raises(ValueError, match='OAuth'):
        auth.imap_connection(email(imap_host='outlook.office365.com'))
    constructor.assert_not_called()


@pytest.mark.parametrize('port,implicit', [(465, True), (587, False)])
def test_smtp_transport_requires_tls_before_auth(monkeypatch, port, implicit):
    ssl_conn, plain_conn = MagicMock(), MagicMock()
    monkeypatch.setattr(auth.smtplib, 'SMTP_SSL', ssl_conn)
    monkeypatch.setattr(auth.smtplib, 'SMTP', plain_conn)
    selected = ssl_conn if implicit else plain_conn
    with auth.smtp_connection(email(smtp_port=port)) as server:
        assert server is selected.return_value
    selected.assert_called_once()
    (plain_conn if implicit else ssl_conn).assert_not_called()
    server.login.assert_called_once_with('me@qq.com', 'client-secret')
    if implicit:
        server.starttls.assert_not_called()
    else:
        assert [c[0] for c in server.method_calls] == ['ehlo', 'starttls', 'ehlo', 'login', 'quit']
    server.send_message.assert_not_called()


def test_no_plaintext_fallback_when_starttls_fails(monkeypatch):
    constructor = MagicMock()
    constructor.return_value.starttls.side_effect = auth.smtplib.SMTPNotSupportedError('no TLS')
    monkeypatch.setattr(auth.smtplib, 'SMTP', constructor)
    with pytest.raises(auth.smtplib.SMTPNotSupportedError):
        with auth.smtp_connection(email(smtp_port=587)):
            pytest.fail('must not enter')
    constructor.return_value.login.assert_not_called()
    constructor.return_value.quit.assert_called_once()
    with pytest.raises(ValueError, match='TLS'):
        with auth.smtp_connection(email(smtp_port=587, use_tls=False)):
            pytest.fail('must not enter')


def test_multiaccount_test_matches_identity_not_index():
    first = email()
    second = email(username='second@qq.com', password='second-secret')
    cfg = Config(emails=[first, second])
    assert auth.resolve_test_account(cfg, {'account':1}, 'email').password == 'second-secret'
    assert auth.resolve_test_account(cfg, {'account':0, 'username':second.username}, 'email').password == 'second-secret'
    for changes in ({'username':'new@qq.com'}, {'imap_host':'imap.other.test'}, {'smtp_host':'smtp.other.test'}):
        with pytest.raises(ValueError, match='不会使用其他账号'):
            auth.resolve_test_account(cfg, changes, 'email')
    with pytest.raises(ValueError):
        auth.resolve_test_account(Config(emails=[first, first]), {}, 'email')


@pytest.mark.asyncio
async def test_email_check_is_readonly_runs_off_loop_and_redacts_failure(monkeypatch):
    constructor = MagicMock()
    constructor.return_value.select.return_value = ('OK', [])
    main_thread = threading.get_ident()
    def login(*_):
        assert threading.get_ident() != main_thread
    constructor.return_value.login.side_effect = login
    smtp = MagicMock()
    monkeypatch.setattr(auth.imaplib, 'IMAP4_SSL', constructor)
    monkeypatch.setattr(auth.smtplib, 'SMTP_SSL', smtp)
    ws = SimpleNamespace(send=AsyncMock())
    gateway = SimpleNamespace(base_agent=SimpleNamespace(config=Config(emails=[email()])))
    await handle_test_email(ws, {}, gateway)
    constructor.return_value.select.assert_called_once_with('INBOX', readonly=True)
    smtp.return_value.send_message.assert_not_called()
    assert json.loads(ws.send.call_args.args[0])['ok'] is True
    constructor.return_value.login.side_effect = auth.imaplib.IMAP4.error('client-secret refused')
    ws.send.reset_mock()
    await handle_test_email(ws, {}, gateway)
    payloads = [json.loads(c.args[0]) for c in ws.send.call_args_list]
    assert payloads[-1]['ok'] is False
    assert '授权码' in payloads[-1]['message']
    assert 'client-secret' not in json.dumps(payloads)
    constructor.return_value.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_calendar_test_uses_selected_account_not_first(monkeypatch):
    import sys
    caldav = MagicMock()
    caldav.DAVClient.return_value.principal.return_value.calendars.return_value = []
    monkeypatch.setitem(sys.modules, 'caldav', caldav)
    cfg = Config(calendars=[CalendarConfig(url='https://first.test', username='first', password='one'),
        CalendarConfig(url='https://second.test', username='second', password='two')])
    ws = SimpleNamespace(send=AsyncMock())
    await handle_test_calendar(ws, {'account':1}, SimpleNamespace(base_agent=SimpleNamespace(config=cfg)))
    caldav.DAVClient.assert_called_once_with(url='https://second.test', username='second', password='two', timeout=20)
    assert json.loads(ws.send.call_args.args[0])['ok'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_save_never_moves_password_to_a_different_endpoint(monkeypatch, tmp_path, legacy):
    from hushclaw.config import loader
    from hushclaw.server.config_handler import handle_save_config
    monkeypatch.setattr(loader, 'get_config_dir', lambda: tmp_path)
    config_path = tmp_path / 'hushclaw.toml'
    config_path.write_text('[email]\nusername="me@qq.com"\nimap_host="imap.qq.com"\nsmtp_host="smtp.qq.com"\npassword="private"\n')
    payload = {'username':'me@qq.com', 'imap_host':'imap.changed.test', 'smtp_host':'smtp.qq.com'}
    ws = SimpleNamespace(send=AsyncMock())
    await handle_save_config(ws, {'config':{'email':payload if legacy else [payload]}}, lambda:None)
    saved = tomllib.loads(config_path.read_text())
    assert not saved['email'][0].get('password')
    assert json.loads(ws.send.call_args.args[0])['ok'] is True


def test_managed_oauth_has_no_fake_default_and_never_contacts_placeholder(monkeypatch):
    from hushclaw.app_connectors import oauth
    cfg = Config()
    assert cfg.app_connectors.broker_base_url == ''
    assert cfg.app_connectors.google_workspace.scopes == ['https://www.googleapis.com/auth/calendar.readonly']
    request = MagicMock()
    monkeypatch.setattr(oauth, '_json_request', request)
    for url in ['', 'https://bus-ie.aibotplatform.com/hushclaw/app-connectors/oauth', 'http://broker.test', 'https://user:pass@broker.test']:
        cfg.app_connectors.broker_base_url = url
        assert oauth.managed_broker_url(cfg) == ''
        with pytest.raises(oauth.OAuthError, match='未配置'):
            oauth.begin_oauth('google_workspace', cfg, MagicMock(), 'http://localhost:8765')
    request.assert_not_called()
    from hushclaw.config.loader import _dict_to_config
    loaded = _dict_to_config({'app_connectors':{'broker_base_url':'https://broker.test/oauth'}})
    assert oauth.managed_broker_url(loaded) == 'https://broker.test/oauth'
