from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


def test_process_attachments_db_miss_emits_actionable_read_file_fallback():
    from hushclaw.server_impl import HushClawServer

    server = HushClawServer.__new__(HushClawServer)
    server._lookup_uploaded_file = lambda _fid: None

    text, images = server._process_attachments(
        "Please review this attachment",
        [{"file_id": "file-123", "name": "notes.md", "url": "/files/file-123"}],
    )

    assert images == []
    assert "Please review this attachment" in text
    assert "read_file('/files/file-123')" in text
    assert "content not accessible" not in text


def test_read_file_resolves_artifact_url_to_local_file(tmp_path):
    from hushclaw.tools.builtins.file_tools import make_download_url, read_file

    cfg = SimpleNamespace(
        agent=SimpleNamespace(workspace_dir=tmp_path / "workspace"),
        server=SimpleNamespace(upload_dir=tmp_path / "uploads"),
    )
    src = tmp_path / "report.md"
    src.write_text("# Report\n\nArtifact body", encoding="utf-8")
    artifact = json.loads(make_download_url(str(src), _config=cfg).content)

    result = read_file(artifact["url"], _config=cfg)

    assert not result.is_error
    assert "Artifact body" in result.content


def test_edit_document_resolves_artifact_url_to_local_file(tmp_path):
    from hushclaw.tools.builtins.file_tools import edit_document, make_download_url, read_file

    cfg = SimpleNamespace(
        agent=SimpleNamespace(workspace_dir=tmp_path / "workspace"),
        server=SimpleNamespace(upload_dir=tmp_path / "uploads"),
    )
    src = tmp_path / "notes.md"
    src.write_text("Before\nTarget line\nAfter", encoding="utf-8")
    artifact = json.loads(make_download_url(str(src), _config=cfg).content)

    edited = edit_document(
        artifact["url"],
        operations=[{"type": "replace", "anchor": "Target line", "content": "Updated line"}],
        _config=cfg,
        create_backup=False,
    )

    assert not edited.is_error
    reread = read_file(artifact["url"], _config=cfg)
    assert not reread.is_error
    assert "Updated line" in reread.content


def test_artifact_url_rejects_traversal_attempts(tmp_path):
    from hushclaw.tools.builtins.file_tools import make_download_url, read_file

    cfg = SimpleNamespace(
        agent=SimpleNamespace(workspace_dir=tmp_path / "workspace"),
        server=SimpleNamespace(upload_dir=tmp_path / "uploads"),
    )
    src = tmp_path / "report.md"
    src.write_text("# Report", encoding="utf-8")
    artifact = json.loads(make_download_url(str(src), _config=cfg).content)
    artifact_id = artifact["artifact_id"]

    result = read_file(f"/files/artifacts/{artifact_id}/../escape.txt", _config=cfg)

    assert result.is_error
    assert "File not found" in result.content
