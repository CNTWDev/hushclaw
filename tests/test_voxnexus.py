"""Contract tests for VoxAuth sessions and the single-gateway account centre."""
import asyncio
import base64
import hashlib
import io
import json
import time
import urllib.error
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, patch

import pytest

from hushclaw.config.schema import ProviderConfig
from hushclaw.core.errors import classify_error
from hushclaw.exceptions import ProviderError
from hushclaw.providers.voxnexus import VoxNexusProvider
from hushclaw.providers.voxnexus_auth import VoxSession, http_error
from hushclaw.server.voxnexus_handler import handle_voxnexus, session_from_data


class Store:
    def __init__(self):
        self.data = {}
    def get(self, key):
        return self.data.get(key, '')
    def set(self, key, value):
        self.data[key] = value
    def delete(self, key):
        self.data.pop(key, None)


def session():
    return VoxSession('https://gateway.example/v1', 'https://auth.example', 'hushclaw', Store())


def tokens(expired=False):
    return {'access_token': 'old-access', 'refresh_token': 'old-refresh',
            'expires_at': time.time() + (-10 if expired else 900)}


def response(body):
    return io.BytesIO(json.dumps(body).encode())


@pytest.mark.asyncio
async def test_concurrent_refresh_and_late_401_use_one_rotated_token():
    s = session()
    s.tokens = tokens(True)
    s._token = AsyncMock(return_value={'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 900})
    assert await asyncio.gather(*[s.access_token() for _ in range(12)]) == ['new-access'] * 12
    assert await s.access_token(rejected='old-access') == 'new-access'
    assert s._token.await_count == 1
    saved = json.loads(s.store.get(s.key))
    assert saved['refresh_token'] == 'new-refresh'
    assert 'id_token' not in saved


@pytest.mark.asyncio
async def test_failed_refresh_clears_session_and_does_not_replay():
    s = session()
    s.tokens = tokens(True)
    s.store.set(s.key, json.dumps(s.tokens))
    s._token = AsyncMock(side_effect=urllib.error.HTTPError('', 400, '', {}, response({'error': 'invalid_grant'})))
    for _ in range(2):
        with pytest.raises(ProviderError) as error:
            await s.access_token()
        assert error.value.status_code == 401
    assert s._token.await_count == 1
    assert not s.store.get(s.key)


@pytest.mark.asyncio
async def test_401_refreshes_once_then_logs_out():
    s = session()
    s.tokens = tokens()
    s._token = AsyncMock(return_value={'access_token': 'new', 'refresh_token': 'rotated', 'expires_in': 900})
    with patch('hushclaw.providers.voxnexus_auth.open_request', side_effect=lambda *a: (_ for _ in ()).throw(urllib.error.HTTPError('', 401, '', {}, io.BytesIO()))) as op:
        with pytest.raises(ProviderError) as error:
            await s.request('/api/v1/me')
    assert error.value.status_code == 401
    assert op.call_count == 2
    assert s._token.await_count == 1
    assert not await s.signed_in()


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [402, 403])
async def test_quota_and_permission_never_retry_or_trust_body_code(code):
    s = session()
    s.tokens = tokens()
    err = urllib.error.HTTPError('', code, '', {'X-Request-Id': 'req-123'}, response({'error': {'code': '429'}}))
    with patch('hushclaw.providers.voxnexus_auth.open_request', side_effect=err) as op:
        with pytest.raises(ProviderError) as raised:
            await s.request('/api/v1/me')
    assert op.call_count == 1
    assert raised.value.status_code == code
    assert 'req-123' in str(raised.value)
    assert not classify_error(raised.value).retryable
    assert not classify_error(ProviderError('402 upstream says 429 timeout', status_code=402)).retryable


@pytest.mark.asyncio
async def test_429_exponential_backoff():
    s = session()
    s.tokens = tokens()
    errors = [urllib.error.HTTPError('', 429, '', {}, io.BytesIO()) for _ in range(3)]
    with patch('hushclaw.providers.voxnexus_auth.open_request', side_effect=[*errors, response({'available': 8})]) as op, patch('asyncio.sleep', new_callable=AsyncMock) as sleep:
        assert await s.request('/api/v1/me') == {'available': 8}
    assert [c.args[0] for c in sleep.await_args_list] == [1, 2, 4]
    assert op.call_count == 4


@pytest.mark.asyncio
async def test_pkce_fixed_port_fallback_wrong_state_single_use_and_logout():
    s = session()
    # Capture the loopback handler without depending on OS ports in the test.
    servers, handlers = [], []
    class Server:
        def close(self): self.closed = True
        async def wait_closed(self): pass
    async def bind(handler, host, port):
        assert host == '127.0.0.1'
        if port == 53682:
            raise OSError('busy')
        handlers.append(handler)
        server = Server(); servers.append(server)
        return server
    class Writer:
        def write(self, data): self.data = data
        async def drain(self): pass
        def close(self): pass
        async def wait_closed(self): pass
    async def callback(query):
        reader = asyncio.StreamReader()
        reader.feed_data(f'GET /callback?{query} HTTP/1.1\r\nHost: localhost\r\n\r\n'.encode())
        writer = Writer()
        await handlers[0](reader, writer)
        return writer.data
    s._token = AsyncMock(return_value={'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 900})
    with patch('asyncio.start_server', side_effect=bind):
        url = await s.start_login()
    q = parse_qs(urlsplit(url).query)
    assert q['redirect_uri'] == ['http://127.0.0.1:53683/callback']
    assert q['code_challenge_method'] == ['S256']
    assert 'aon-gateway' in q['scope'][0]
    assert b'400' in await callback('code=stolen&state=wrong')
    assert s._token.await_count == 0
    state = q['state'][0]
    assert b'200' in await callback(f'code=good&state={state}')
    assert b'400' in await callback(f'code=replayed&state={state}')
    await s.login_task
    form = s._token.await_args.args[0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'].encode()).digest()).rstrip(b'=').decode()
    assert challenge == q['code_challenge'][0]
    assert 'client_secret' not in form
    assert await s.signed_in()
    assert servers[0].closed
    await s.logout()
    assert not await s.signed_in()
    assert not s.store.get(s.key)


class WS:
    async def send(self, value): self.result = json.loads(value)


@pytest.mark.asyncio
async def test_disabled_payments_never_create_order():
    s = session(); s.request = AsyncMock(return_value={'payments_enabled': False, 'data': []})
    ws = WS()
    with patch('hushclaw.server.voxnexus_handler.session_from_data', return_value=s):
        await handle_voxnexus(ws, {'action': 'topup', 'package_id': 'pkg-1'}, None)
    assert not ws.result['ok']
    assert s.request.await_count == 1


@pytest.mark.asyncio
async def test_models_require_available_not_balance():
    s = session(); s.request = AsyncMock(return_value={'status': 'active', 'balance': 100, 'reserved': 100, 'available': 0})
    ws = WS()
    with patch('hushclaw.server.voxnexus_handler.session_from_data', return_value=s):
        await handle_voxnexus(ws, {'action': 'models'}, None)
    assert ws.result['status'] == 402
    assert s.request.await_count == 1


@pytest.mark.asyncio
async def test_checkout_url_with_fragment_and_order_polling():
    s = session()
    url = 'https://checkout.stripe.com/c/pay/cs_test#fid=test'
    s.request = AsyncMock(side_effect=[{'payments_enabled': True, 'data': [{'id': 'pkg-1'}]},
                                      {'topup': {'id': 'top-1', 'status': 'pending'}, 'checkout_url': url},
                                      {'id': 'top-1', 'status': 'paid'}])
    ws = WS()
    with patch('hushclaw.server.voxnexus_handler.session_from_data', return_value=s), patch('webbrowser.open') as browser:
        await handle_voxnexus(ws, {'action': 'topup', 'package_id': 'pkg-1'}, None)
        assert ws.result['ok']
        assert ws.result['order']['topup']['status'] == 'pending'
        browser.assert_called_once_with(url)
        await handle_voxnexus(ws, {'action': 'order', 'order_id': 'top-1'}, None)
    assert ws.result['order']['status'] == 'paid'
    assert s.request.await_args.args == ('/api/v1/topups/top-1',)


def test_browser_cannot_override_deployment_to_exfiltrate_tokens():
    config = ProviderConfig(name='voxnexus', base_url='https://gateway.example', voxauth_client_id='registered')
    gateway = SimpleNamespace(base_agent=SimpleNamespace(config=SimpleNamespace(provider=config)))
    with patch('hushclaw.server.voxnexus_handler.get_session', return_value='safe') as get:
        assert session_from_data({'base_url': 'https://evil.example', 'voxauth_issuer': 'https://evil.example'}, gateway) == 'safe'
        get.assert_called_once_with(config)


@pytest.mark.asyncio
async def test_stream_usage_tools_and_no_replay_after_output():
    chunks = [
        {'choices': [{'delta': {'content': 'Hello'}}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 't1', 'function': {'name': 'find', 'arguments': '{"q":'}}]}}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"x"}'}}]}, 'finish_reason': 'tool_calls'}]},
        {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 4}},
    ]
    stream = io.BytesIO(b''.join(b'data: ' + json.dumps(c).encode() + b'\n\n' for c in chunks) + b'data: [DONE]\n')
    s = session(); s.open = AsyncMock(return_value=stream)
    p = VoxNexusProvider(ProviderConfig())
    with patch('hushclaw.providers.voxnexus.get_session', return_value=s):
        events = [v async for v in p.stream_complete([], model='model-a')]
    assert events[0] == 'Hello'
    final = events[-1]
    assert final.input_tokens == 10 and final.output_tokens == 4
    assert final.tool_calls[0].input == {'q': 'x'}
    assert final.stop_reason == 'tool_use'
    assert stream.closed
    assert s.open.await_count == 1


@pytest.mark.asyncio
async def test_config_save_gate_runs_before_any_write(tmp_path):
    from hushclaw.server.config_handler import handle_save_config
    cfg = SimpleNamespace(provider=ProviderConfig(name='voxnexus', base_url='https://gateway.example', voxauth_client_id='app'))
    s = session(); s.request = AsyncMock(return_value={'status': 'active', 'available': 0})
    ws = WS()
    with patch('hushclaw.config.loader.load_config', return_value=cfg), patch('hushclaw.providers.voxnexus_auth.get_session', return_value=s), patch('hushclaw.config.loader.get_config_dir', return_value=tmp_path):
        await handle_save_config(ws, {'config': {'provider': {'name': 'voxnexus'}, 'agent': {'model': 'm'}}}, lambda: None)
    assert not ws.result['ok']
    assert not (tmp_path / 'hushclaw.toml').exists()


@pytest.mark.asyncio
async def test_valid_model_save_pins_deployment_and_never_persists_oauth(tmp_path):
    import tomllib
    from hushclaw.config.schema import Config
    from hushclaw.server.config_handler import handle_save_config
    cfg = Config(provider=ProviderConfig(name='voxnexus', base_url='https://gateway.example', voxauth_client_id='registered'))
    s = session()
    s.tokens = tokens()
    s.request = AsyncMock(side_effect=[{'status': 'active', 'available': 10}, {'data': [{'id': 'model-a'}]}])
    ws = WS(); applied = []
    with patch('hushclaw.config.loader.load_config', return_value=cfg), patch('hushclaw.providers.voxnexus_auth.get_session', return_value=s), patch('hushclaw.config.loader.get_config_dir', return_value=tmp_path):
        await handle_save_config(ws, {'config': {
            'provider': {'name': 'voxnexus', 'base_url': 'https://attacker.example', 'api_key': 'never-save-me'},
            'agent': {'model': 'model-a', 'cheap_model': ''},
        }}, lambda: applied.append(True))
    assert ws.result['ok'], ws.result
    raw = (tmp_path / 'hushclaw.toml').read_text()
    saved = tomllib.loads(raw)
    assert saved['provider']['base_url'] == 'https://gateway.example'
    assert saved['provider']['voxauth_client_id'] == 'registered'
    assert saved['agent']['model'] == 'model-a'
    assert applied == [True]
    assert all(value not in raw for value in ('never-save-me', 'old-access', 'old-refresh', 'api_key ='))


def test_plaintext_keyring_backend_is_rejected():
    import sys
    from hushclaw.providers.voxnexus_auth import KeychainStore
    PlaintextBackend = type('PlaintextBackend', (), {'__module__': 'keyrings.alt.file'})
    with patch.dict(sys.modules, {'keyring': SimpleNamespace(get_keyring=lambda: PlaintextBackend())}):
        with pytest.raises(ProviderError, match='系统钥匙串'):
            KeychainStore().get('account')


def test_unconfigured_provider_can_start_without_api_key_or_keyring():
    from hushclaw.providers.registry import get_provider
    assert isinstance(get_provider(ProviderConfig()), VoxNexusProvider)
