"""Password-based mail transport policy shared by setup tests and agent tools.

No OAuth client impersonation, plaintext fallback, or cross-account credentials.
"""
from contextlib import contextmanager
import imaplib
import re
import smtplib
import ssl

from hushclaw.util.ssl_context import make_ssl_context


def mail_provider(host: str) -> str:
    host = str(host or '').strip().lower().rstrip('.')
    if host in {'imap.gmail.com', 'smtp.gmail.com'}:
        return 'gmail'
    if host in {'outlook.office365.com', 'imap-mail.outlook.com', 'smtp-mail.outlook.com', 'smtp.office365.com'}:
        return 'outlook'
    if host in {'imap.mail.me.com', 'smtp.mail.me.com'}:
        return 'icloud'
    if host in {'imap.qq.com', 'smtp.qq.com'}:
        return 'qq'
    if host in {'imap.feishu.cn', 'smtp.feishu.cn'}:
        return 'feishu'
    if host in {'imap.163.com', 'smtp.163.com', 'imap.126.com', 'smtp.126.com', 'imap.yeah.net', 'smtp.yeah.net'}:
        return 'netease'
    if re.fullmatch(r'(?:imap|smtp)(?:pro)?\.zoho\.(?:com|eu|in|com.au|jp|ca|com.cn)', host):
        return 'zoho'
    return 'custom'


def credential_hint(host: str) -> str:
    return {
        'gmail': 'Gmail 需开启两步验证并生成 16 位应用专用密码，不是网页登录密码；组织策略可能禁止此方式。无需开发者 ID。',
        'outlook': 'Outlook / Microsoft 365 要求 OAuth，当前 IMAP 表单不支持它的密码登录。Mac 用户请通过系统“互联网账户”登录后使用 Mail；应用内 OAuth 需由发布方注册应用，不能借用其他软件的 Client ID。',
        'icloud': 'iCloud 请使用 Apple 账户生成的 App 专用密码和完整 iCloud 邮箱地址，不是 Apple 账户登录密码。',
        'qq': 'QQ 邮箱请先开启 IMAP/SMTP，填入邮箱生成的授权码，不是 QQ 登录密码。',
        'netease': '网易邮箱请开启 IMAP/SMTP，填入客户端授权密码（授权码），并按服务商要求批准客户端登录。',
        'zoho': 'Zoho 请核对账户所属数据中心和套餐是否支持 IMAP，开启 IMAP；启用 MFA / SAML 时使用应用专用密码。',
        'feishu': '飞书邮箱需要管理员允许第三方客户端，并在飞书设置 → 邮箱生成第三方客户端专用密码。',
    }.get(mail_provider(host), '请核对完整邮箱、服务商提供的客户端凭据和 IMAP/SMTP 权限。')


def mail_credentials(config, host: str) -> tuple[str, str]:
    provider = mail_provider(host)
    if provider == 'outlook':
        raise ValueError(credential_hint(host))
    username = str(config.username or '').strip()
    password = str(config.password or '')
    if not username or not password:
        raise ValueError('缺少当前邮箱的用户名或客户端凭据。' + credential_hint(host))
    if provider == 'gmail':
        # Google displays app passwords in groups of four; only normalize that
        # documented format, never strip arbitrary whitespace from other secrets.
        password = password.replace(' ', '')
        if not re.fullmatch(r'[A-Za-z]{16}', password):
            raise ValueError(credential_hint(host))
    return username, password


def mail_error_message(exc: Exception, host: str) -> str:
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, ssl.SSLError):
        return 'TLS 证书校验失败，请检查服务商地址和系统证书；不会关闭证书验证。'
    if isinstance(exc, (TimeoutError, OSError)) and not isinstance(exc, smtplib.SMTPException):
        return '连接超时或网络不可达，请检查服务器地址、端口及网络。'
    if isinstance(exc, (imaplib.IMAP4.error, smtplib.SMTPAuthenticationError)):
        return '服务商拒绝登录或邮箱访问。' + credential_hint(host)
    return '邮件连接失败，请核对服务器和 TLS 端口设置。' + credential_hint(host)


def imap_connection(config, timeout=20):
    username, password = mail_credentials(config, config.imap_host)
    conn = imaplib.IMAP4_SSL(config.imap_host, int(config.imap_port),
                          ssl_context=make_ssl_context(), timeout=timeout)
    try:
        conn.login(username, password)
        return conn
    except Exception as exc:
        try:
            conn.shutdown()
        except Exception:
            pass
        raise ValueError(mail_error_message(exc, config.imap_host)) from None


@contextmanager
def smtp_connection(config, timeout=20):
    username, password = mail_credentials(config, config.smtp_host)
    port = int(config.smtp_port)
    if port != 465 and not getattr(config, 'use_tls', True):
        raise ValueError('SMTP 必须使用 TLS：465 为隐式 TLS，587 为 STARTTLS；不支持明文发送凭据。')
    server = None
    try:
        ctx = make_ssl_context()
        server = (smtplib.SMTP_SSL(config.smtp_host, port, timeout=timeout, context=ctx)
                  if port == 465 else smtplib.SMTP(config.smtp_host, port, timeout=timeout))
        server.ehlo()
        if port != 465:
            server.starttls(context=ctx)
            server.ehlo()
        server.login(username, password)
        yield server
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                server.close()


def account_identity(account, kind: str) -> tuple:
    get = account.get if isinstance(account, dict) else lambda key, default='': getattr(account, key, default)
    keys = ('imap_host', 'smtp_host', 'username') if kind == 'email' else ('url', 'username')
    return tuple(str(get(key) or '').strip().lower() if key.endswith('host') else str(get(key) or '').strip() for key in keys)


def resolve_test_account(config, data: dict, kind: str):
    """Merge a test form, keeping a saved secret only for the exact endpoint/user."""
    from dataclasses import asdict
    from hushclaw.config.schema import EmailConfig, CalendarConfig
    cls = EmailConfig if kind == 'email' else CalendarConfig
    accounts = getattr(config, 'emails' if kind == 'email' else 'calendars', [])
    index = data.get('account', 0)
    if type(index) is not int or index < 0:
        raise ValueError('无效的账号选择。')
    base = accounts[index] if index < len(accounts) else cls()
    fields = asdict(base)
    fields['password'] = ''
    for key in fields:
        if key in data and key != 'password':
            fields[key] = data[key].strip() if isinstance(data[key], str) else data[key]
    supplied = data.get('password')
    if supplied:
        fields['password'] = supplied
    else:
        matches = [a for a in accounts if account_identity(a, kind) == account_identity(fields, kind)]
        if len(matches) == 1:
            fields['password'] = matches[0].password
    if not fields['password']:
        raise ValueError('当前账号没有可用的已保存凭据，请重新输入此账号的应用专用密码 / 授权码；不会使用其他账号的密码。')
    for key in ('imap_port', 'smtp_port') if kind == 'email' else ():
        fields[key] = int(fields[key])
        if not 1 <= fields[key] <= 65535:
            raise ValueError('端口必须在 1–65535 之间。')
    return cls(**fields)
