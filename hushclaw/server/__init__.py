"""hushclaw/server/ — handler modules and mixin classes extracted from server_impl.py.

server_impl.py delegates to these modules; they contain domain logic, not transport.
"""


def __getattr__(name: str):
    """Lazy re-export: avoids a circular import between server_impl and server.*."""
    if name == "HushClawServer":
        from hushclaw.server_impl import HushClawServer  # noqa: F401
        return HushClawServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
