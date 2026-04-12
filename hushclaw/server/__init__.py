# hushclaw/server/ — handler modules extracted from the monolithic server.py
# server.py delegates to these modules; they contain domain logic, not transport.

# Re-export HushClawServer so `from hushclaw.server import HushClawServer` still works.
from hushclaw.server_impl import HushClawServer  # noqa: F401
