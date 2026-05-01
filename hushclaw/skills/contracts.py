"""Shared runtime contracts injected into skill execution."""
from __future__ import annotations


SKILL_OUTPUT_CONTRACT = (
    "## Runtime Output Contract\n"
    "- For simple generated files, call `write_file(\"name.ext\", content)` with a relative path; "
    "do not write to `/files/...` because `/files/` is a read-only URL prefix.\n"
    "- `write_file` returns the downloadable `/files/` URL after the framework registers the artifact.\n"
    "- For existing local files or directories, call `make_download_url(path)` or "
    "`make_download_bundle(path)` instead of hand-writing `/files/...` links.\n"
    "- Bundled Python tools should declare `_output_dir: Path | None = None` and write final "
    "outputs under that injected directory."
)
