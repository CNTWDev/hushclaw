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
            "Google Calendar sync is not implemented here. The Google Workspace "
            "connector in Connections supports OAuth authorization, but does not "
            "sync events into the Calendar view."
        )
