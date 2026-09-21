"""Authentication constraints for the password-based CalDAV integration."""
from urllib.parse import urlsplit


def validate_caldav_password_auth(url: str) -> None:
    """Reject Google endpoints before sending unsupported password credentials."""
    normalized = url.strip()
    if "://" not in normalized:
        normalized = "https://" + normalized.lstrip("/")
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/")
    google_endpoint = (
        host in {"google.com", "www.google.com", "calendar.google.com"}
        and (path == "/calendar/dav" or path.startswith("/calendar/dav/"))
    ) or (
        host == "apidata.googleusercontent.com"
        and (path == "/caldav" or path.startswith("/caldav/"))
    )
    if google_endpoint:
        raise ValueError(
            "Google Calendar requires OAuth 2.0; account passwords and app passwords "
            "are not supported by Google's CalDAV API. The legacy "
            "www.google.com/calendar/dav endpoint is deprecated. "
            "This CalDAV integration only supports username/password authentication; "
            "Mac 用户无需开发者 ID：在系统互联网账户登录 Google，再进入 HushClaw 日历 → 日历来源授权读取本机日历。"
            "应用内 OAuth 必须由发布方配置真实的已注册应用或授权服务；不能使用邮箱应用密码代替。"
        )
    if parsed.scheme != 'https' or not host or parsed.username or parsed.password:
        raise ValueError('CalDAV 必须使用不含嵌入凭据的 HTTPS 服务地址，不会通过明文 HTTP 发送密码。')
