"""Shared runtime contracts injected into skill execution."""
from __future__ import annotations


SKILL_OUTPUT_CONTRACT = (
    "## Runtime Output Contract\n"
    "- For simple generated files, call `write_file(\"name.ext\", content)` with a relative path; "
    "do not write new files to `/files/...` because `/files/` is a WebUI URL prefix.\n"
    "- Existing `/files/{file_id}` URLs may be passed to `read_file`, `patch_document`, or "
    "`update_document` when editing an already registered file.\n"
    "- When making local edits to an existing Markdown, HTML, or text document, call "
    "`patch_document(path, operations, change_summary)`; use `update_document(path, content, change_summary)` "
    "only for full-document rewrites.\n"
    "- `write_file` returns the downloadable `/files/` URL after the framework registers the artifact.\n"
    "- For existing local files or directories, call `make_download_url(path)` or "
    "`make_download_bundle(path)` instead of hand-writing `/files/...` links.\n"
    "- Bundled Python tools should declare `_output_dir: Path | None = None` and write final "
    "outputs under that injected directory."
)
