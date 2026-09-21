"""VoxAuth public-client PKCE and shared, rotating sessions (no file secrets)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from hushclaw.exceptions import ProviderError

CALLBACK_PORTS = (53682, 53683, 53684)
ISSUER = "https://auth.voxnexus.ai"


def https_url(value: str) -> str:
    value = value.strip().rstrip("/")
    p = urllib.parse.urlsplit(value)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ProviderError("VoxNexus 地址必须是 HTTPS URL，且不能包含凭证、查询参数或片段。")
    return value


class KeychainStore:
    """Only OS-backed keyring backends; never fall back to plaintext files."""
    def _backend(self):
        try:
            import keyring
            backend = keyring.get_keyring()
            module = type(backend).__module__
            if not module.startswith(("keyring.backends.macOS", "keyring.backends.Windows",
                                      "keyring.backends.SecretService", "keyring.backends.libsecret",
                                      "keyring.backends.kwallet")):
                raise RuntimeError("no OS keyring")
            return backend
        except Exception:
            raise ProviderError("VoxNexus 需要系统钥匙串：请安装 hushclaw[voxnexus] 并启用系统 keyring。") from None

    def get(self, key):
        return self._backend().get_password("hushclaw.voxnexus", key) or ""

    def set(self, key, value):
        self._backend().set_password("hushclaw.voxnexus", key, value)

    def delete(self, key):
        backend = self._backend()
        if backend.get_password("hushclaw.voxnexus", key):
            backend.delete_password("hushclaw.voxnexus", key)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a bearer token or OAuth form to a redirect.


def open_request(request, timeout=30):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def http_error(status: int, request_id="") -> ProviderError:
    message = {
        401: "登录已失效，请在 Settings → VoxNexus 重新登录。",
        402: "可用额度不足，请在 Settings → VoxNexus 充值，或降低最大输出 token 数。",
        403: "账号不可用或缺少 aon-gateway 权限，请联系支持。",
        429: "请求过于频繁，请稍后再试。",
        502: "网关或支付服务暂时不可用，请稍后重试；持续失败请联系运维。",
    }.get(status, "请求失败，请稍后重试或联系支持。")
    suffix = f" Request ID: {request_id}" if request_id else ""
    return ProviderError(f"VoxNexus HTTP {status}: {message}{suffix}", status_code=status)


class VoxSession:
    def __init__(self, gateway: str, issuer: str, client_id: str, store=None):
        self.gateway = https_url(gateway).removesuffix("/v1")
        self.issuer = https_url(issuer)
        self.client_id = client_id.strip()
        if not self.client_id:
            raise ProviderError("请填写已在 VoxAuth 注册的公开客户端 client_id。")
        self.key = hashlib.sha256(f"{self.gateway}|{self.issuer}|{self.client_id}".encode()).hexdigest()
        self.store = store or KeychainStore()
        self.lock = asyncio.Lock()
        self.tokens = None
        self.login_task = None
        self.login_error = ""

    async def _load(self):
        if self.tokens is None:
            raw = await asyncio.to_thread(self.store.get, self.key)
            self.tokens = json.loads(raw) if raw else {}
        return self.tokens

    async def signed_in(self):
        async with self.lock:
            return bool((await self._load()).get("refresh_token"))

    async def _store_call(self, method, *args):
        # A cancelled coroutine must wait for a keychain mutation to finish;
        # otherwise logout could race a still-running write in the thread pool.
        task = asyncio.create_task(asyncio.to_thread(method, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _save(self, value):
        if not value.get("access_token") or not value.get("refresh_token"):
            raise ProviderError("VoxAuth 未返回完整会话，请重新登录。", status_code=401)
        value = {"access_token": value["access_token"], "refresh_token": value["refresh_token"],
                 "expires_at": time.time() + float(value.get("expires_in", 900))}
        await self._store_call(self.store.set, self.key, json.dumps(value))
        self.tokens = value

    async def logout(self):
        if self.login_task:
            self.login_task.cancel()
            try:
                await self.login_task
            except asyncio.CancelledError:
                pass
        async with self.lock:
            await self._store_call(self.store.delete, self.key)
            self.tokens = {}
        self.login_error = ""

    async def _token(self, form):
        req = urllib.request.Request(self.issuer + "/oauth/token",
            data=urllib.parse.urlencode({"client_id": self.client_id, **form}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        def exchange():
            with open_request(req) as resp:
                return json.load(resp)
        return await asyncio.to_thread(exchange)

    async def access_token(self, rejected=None):
        # All provider instances share this lock, including requests rejected with 401.
        async with self.lock:
            tokens = await self._load()
            current = tokens.get("access_token")
            if current and tokens.get("expires_at", 0) > time.time() + 60 and current != rejected:
                return current
            if not tokens.get("refresh_token"):
                raise http_error(401)
            try:
                fresh = await self._token({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
                await self._save(fresh)
            except (Exception, asyncio.CancelledError) as exc:
                # Refresh rotation may have happened even if the response was lost.
                # Never replay an uncertain refresh token.
                self.tokens = {}
                await self._store_call(self.store.delete, self.key)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise http_error(401) from None
            return self.tokens["access_token"]

    async def open(self, path, payload=None, timeout=30):
        token = await self.access_token()
        refreshed = False
        retries = 0
        while True:
            req = urllib.request.Request(self.gateway + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            try:
                return await asyncio.to_thread(open_request, req, timeout)
            except urllib.error.HTTPError as exc:
                status, request_id = exc.code, exc.headers.get("X-Request-Id", "")
                exc.close()
                if status == 401 and not refreshed:
                    token = await self.access_token(rejected=token)
                    refreshed = True
                    continue
                if status == 429 and retries < 3:
                    await asyncio.sleep(2 ** retries)
                    retries += 1
                    continue
                if status == 401:
                    await self.logout()
                raise http_error(status, request_id) from None
            except (OSError, urllib.error.URLError):
                raise ProviderError("VoxNexus 网络连接失败，请检查网络后重试。") from None

    async def request(self, path, payload=None):
        response = await self.open(path, payload)
        def read():
            with response:
                return json.load(response)
        return await asyncio.to_thread(read)

    async def start_login(self):
        if self.login_task and not self.login_task.done():
            raise ProviderError("登录正在进行，请完成浏览器登录或先取消。")
        await asyncio.to_thread(self.store.get, self.key)  # Fail before browser if keyring unavailable.
        verifier = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(32)
        callback = asyncio.get_running_loop().create_future()
        self.login_error = ""

        async def accept(reader, writer):
            status, message = "400 Bad Request", "Invalid callback."
            try:
                raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
                method, target, _ = raw.decode("ascii").split("\r\n", 1)[0].split(" ", 2)
                url = urllib.parse.urlsplit(target)
                query = urllib.parse.parse_qs(url.query)
                if method == "GET" and url.path == "/callback" and query.get("state") == [state] and not callback.done():
                    if query.get("error"):
                        callback.set_exception(ProviderError("VoxAuth 登录已取消或未获授权。"))
                        status, message = "200 OK", "Login cancelled. Return to HushClaw."
                    elif len(query.get("code", [])) == 1:
                        callback.set_result(query["code"][0])
                        status, message = "200 OK", "Authorization received. Return to HushClaw to finish signing in."
            except (ValueError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                pass
            finally:
                body = message.encode()
                writer.write(f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
                await writer.drain()
                writer.close()
                await writer.wait_closed()

        server = None
        for port in CALLBACK_PORTS:
            try:
                server = await asyncio.start_server(accept, "127.0.0.1", port)
                break
            except OSError:
                continue
        if server is None:
            raise ProviderError("VoxAuth 回调端口 53682–53684 均被占用，请关闭其他登录窗口后重试。")
        redirect = f"http://127.0.0.1:{port}/callback"

        async def finish():
            try:
                code = await asyncio.wait_for(callback, 300)
                async with self.lock:
                    result = await self._token({"grant_type": "authorization_code", "code": code,
                                               "code_verifier": verifier, "redirect_uri": redirect})
                    await self._save(result)
            except asyncio.TimeoutError:
                self.login_error = "登录超时，请重试。"
            except Exception:
                self.login_error = "登录失败，请检查 client_id、已注册回调地址和 aon-gateway 权限后重试。"
            finally:
                server.close()
                await server.wait_closed()
        self.login_task = asyncio.create_task(finish())
        return self.issuer + "/oauth/authorize?" + urllib.parse.urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": redirect,
            "scope": "openid profile email aon-gateway", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256"})


_sessions = {}


def get_session(config):
    key = (config.base_url or "", config.voxauth_issuer, config.voxauth_client_id)
    if key not in _sessions:
        _sessions[key] = VoxSession(*key)
    return _sessions[key]
