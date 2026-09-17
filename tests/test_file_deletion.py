"""Deletion must remove bytes without breaking shared files or hiding failures."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hushclaw.memory.store import MemoryStore
from hushclaw.server import HushClawServer
from hushclaw.tools.builtins.file_tools import _register_generated_file


@pytest.fixture
def files(tmp_path):
    memory = MemoryStore(tmp_path / "memory")
    server = HushClawServer.__new__(HushClawServer)
    server._gateway = SimpleNamespace(base_agent=SimpleNamespace(memory=memory))
    server._upload_dir = tmp_path / "uploads"
    server._upload_dir.mkdir()
    server._upload_index_backfilled = True
    server._file_url = lambda value: f"/files/{value}"
    yield server, memory
    memory.close()


def delete(server, file_id):
    import asyncio
    result = []

    class WS:
        async def send(self, value):
            result.append(json.loads(value))

    asyncio.run(server._handle_delete_file(WS(), {"file_id": file_id}))
    return result[-1]


def upload(server, name="report.md"):
    return server._save_or_reuse_uploaded_file(file_bytes=b"important content", original_name=name)


def test_delete_uploaded_bytes_index_and_reupload(files):
    server, memory = files
    item = upload(server)
    row = server._lookup_uploaded_file(item["file_id"])
    target = Path(row["storage_path"])
    note = memory.remember("important content", persist_to_disk=False)
    memory.conn.execute("INSERT INTO kb_file_index VALUES (?, 'test', ?, 1, 1, 1)", (row["blob_id"], note))
    assert delete(server, item["file_id"])["ok"]
    assert not target.exists()
    assert server._lookup_uploaded_file(item["file_id"]) is None
    assert not memory.conn.execute("SELECT 1 FROM notes WHERE note_id=?", (note,)).fetchone()
    assert not memory.conn.execute("SELECT 1 FROM notes_fts WHERE note_id=?", (note,)).fetchone()
    assert not memory.conn.execute("SELECT 1 FROM kb_file_index").fetchone()
    recreated = upload(server)
    assert Path(server._lookup_uploaded_file(recreated["file_id"])["storage_path"]).read_bytes() == b"important content"


def test_delete_shared_upload_removes_original_path_and_preserves_other(files):
    server, memory = files
    first, second = upload(server, "first.md"), upload(server, "second.md")
    target = Path(server._lookup_uploaded_file(first["file_id"])["storage_path"])
    assert delete(server, first["file_id"])["ok"]
    assert not target.exists()
    other = Path(server._lookup_uploaded_file(second["file_id"])["storage_path"])
    assert other != target
    assert other.read_bytes() == b"important content"
    assert delete(server, second["file_id"])["ok"]
    assert not other.exists()


def test_generated_file_deleted_from_its_actual_directory_and_can_be_regenerated(files, tmp_path):
    server, memory = files
    target = tmp_path / "generated.md"
    target.write_text("generated content")
    file_id = _register_generated_file(target, memory)
    assert delete(server, file_id)["ok"]
    assert not target.exists()
    target.write_text("generated again")
    assert _register_generated_file(target, memory) == file_id
    assert server._lookup_uploaded_file(file_id) is not None


def test_unlink_failure_restores_file_and_visible_record(files):
    server, _memory = files
    item = upload(server)
    target = Path(server._lookup_uploaded_file(item["file_id"])["storage_path"])
    with patch.object(Path, "unlink", side_effect=PermissionError("access denied")):
        result = delete(server, item["file_id"])
    assert not result["ok"]
    assert target.read_bytes() == b"important content"
    assert server._lookup_uploaded_file(item["file_id"]) is not None


def test_identical_generated_content_keeps_independent_local_paths(files, tmp_path):
    server, memory = files
    first, second = tmp_path / "first.md", tmp_path / "second.md"
    first.write_text("same content")
    second.write_text("same content")
    first_id = _register_generated_file(first, memory)
    second_id = _register_generated_file(second, memory)
    assert Path(server._lookup_uploaded_file(first_id)["storage_path"]) == first
    assert Path(server._lookup_uploaded_file(second_id)["storage_path"]) == second
    assert delete(server, first_id)["ok"]
    assert not first.exists()
    assert second.read_text() == "same content"
    assert delete(server, second_id)["ok"]
    assert not second.exists()


def test_missing_file_and_unknown_id(files):
    server, _memory = files
    item = upload(server)
    Path(server._lookup_uploaded_file(item["file_id"])["storage_path"]).unlink()
    assert delete(server, item["file_id"])["ok"]
    assert not delete(server, "../../not-registered")["ok"]


def test_symlink_target_is_not_deleted(files, tmp_path):
    server, memory = files
    item = upload(server)
    target = Path(server._lookup_uploaded_file(item["file_id"])["storage_path"])
    external = tmp_path / "external.md"
    external.write_text("keep")
    target.unlink()
    target.symlink_to(external)
    assert not delete(server, item["file_id"])["ok"]
    assert external.read_text() == "keep"
