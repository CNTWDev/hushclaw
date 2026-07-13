import json
from pathlib import Path
from types import SimpleNamespace

from hushclaw.artifacts.html import (
    ARTIFACT_MANIFEST_NAME,
    build_html_artifact_manifest,
    validate_html_artifact,
)
from hushclaw.config.schema import ToolsConfig
from hushclaw.loop import _coerce_artifact_meta
from hushclaw.server.http_mixin import _html_artifact_security_headers
from hushclaw.skills.loader import SkillRegistry
from hushclaw.skills.validator import SkillValidator
from hushclaw.tools.builtins.html_artifact_tools import (
    inspect_html_artifact,
    publish_html_artifact,
)
from hushclaw.tools.registry import ToolRegistry


ROOT = Path(__file__).parent.parent
BUILTIN_SKILL = ROOT / "hushclaw" / "skills" / "builtins" / "html-artifact"


def _write_report(root: Path, *, scripts: bool = False, remote: bool = False) -> Path:
    root.mkdir(parents=True)
    script = '<script src="app.js"></script>' if scripts else ""
    remote_style = '<link rel="stylesheet" href="https://cdn.example.com/theme.css">' if remote else ""
    (root / "index.html").write_text(
        "<!doctype html><html lang='en'><head><meta name='viewport' content='width=device-width'>"
        f"<title>Quarterly report</title><link rel='stylesheet' href='styles.css'>{remote_style}</head>"
        f"<body><main><h1>Quarterly report</h1></main>{script}</body></html>",
        encoding="utf-8",
    )
    (root / "styles.css").write_text("@media print { body { color: #000; } }", encoding="utf-8")
    if scripts:
        (root / "app.js").write_text("document.body.dataset.ready = 'true';", encoding="utf-8")
    return root


def test_builtin_html_artifact_skill_is_valid_and_discoverable():
    result = SkillValidator().validate(BUILTIN_SKILL / "SKILL.md")
    assert result.ok
    assert result.name == "html-artifact"

    registry = SkillRegistry([])
    skill = registry.get("html-artifact")
    assert skill is not None
    assert "publish_html_artifact" in skill["content"]
    assert str(BUILTIN_SKILL / "references" / "quality-gates.md") in skill["content"]


def test_html_artifact_tools_are_registered_and_enabled_by_default():
    registry = ToolRegistry()
    registry.load_builtins()
    names = {item.name for item in registry.list_tools()}
    assert {"inspect_html_artifact", "publish_html_artifact"} <= names
    assert {"inspect_html_artifact", "publish_html_artifact"} <= set(ToolsConfig().enabled)


def test_html_artifact_metadata_survives_agent_event_normalization():
    meta = _coerce_artifact_meta({
        "url": "/files/artifacts/art-1/index.html",
        "preview_url": "/files/artifacts/art-1/index.html",
        "manifest_url": "/files/artifacts/art-1/artifact-manifest.json",
        "name": "Operating review",
        "kind": "directory",
        "artifact_type": "static-report",
        "trust_level": "generated",
        "quality": {"ok": True, "score": 95},
        "capabilities": {"scripts": False},
    })
    assert meta is not None
    assert meta["artifact_type"] == "static-report"
    assert meta["quality"]["score"] == 95
    assert meta["capabilities"]["scripts"] is False


def test_static_report_validation_passes_for_local_semantic_bundle(tmp_path):
    report = _write_report(tmp_path / "report")
    result = validate_html_artifact(report)
    assert result["ok"] is True
    assert result["score"] == 100
    assert result["metrics"]["remote_resources"] == 0

    tool_result = inspect_html_artifact(str(report))
    assert not tool_result.is_error
    assert json.loads(tool_result.content)["status"] == "passed"


def test_static_report_rejects_scripts_remote_assets_and_missing_files(tmp_path):
    report = _write_report(tmp_path / "report", scripts=True, remote=True)
    (report / "styles.css").unlink()
    result = validate_html_artifact(report)
    assert result["ok"] is False
    joined = "\n".join(result["errors"])
    assert "must not contain JavaScript" in joined
    assert "Remote resources are not allowed" in joined
    assert "Referenced resource is missing" in joined


def test_interactive_report_allows_local_scripts(tmp_path):
    report = _write_report(tmp_path / "report", scripts=True)
    result = validate_html_artifact(report, artifact_type="interactive-report")
    assert result["ok"] is True
    assert result["metrics"]["scripts"] == 1


def test_single_html_file_must_not_reference_unpublished_siblings(tmp_path):
    report = _write_report(tmp_path / "report")
    result = validate_html_artifact(report / "index.html")
    assert result["ok"] is False
    assert "Single-file artifacts must inline local dependencies" in result["errors"][0]


def test_publish_html_artifact_persists_manifest_and_structured_metadata(tmp_path):
    report = _write_report(tmp_path / "report")
    cfg = SimpleNamespace(server=SimpleNamespace(upload_dir=tmp_path / "uploads", public_base_url=""))
    result = publish_html_artifact(str(report), title="Q2 operating review", _config=cfg)
    assert not result.is_error
    payload = json.loads(result.content)
    artifact = payload["artifact"]
    assert artifact["artifact_type"] == "static-report"
    assert artifact["trust_level"] == "generated"
    assert artifact["quality"]["score"] == 100
    assert artifact["preview_url"].endswith("/index.html")
    assert result.metadata["artifacts"][0]["manifest_url"].endswith(ARTIFACT_MANIFEST_NAME)

    artifact_root = cfg.server.upload_dir / "artifacts" / artifact["artifact_id"]
    manifest = json.loads((artifact_root / ARTIFACT_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["title"] == "Q2 operating review"
    assert manifest["security"]["network_policy"] == "deny"
    assert manifest["capabilities"]["scripts"] is False


def test_managed_artifact_security_headers_follow_manifest_capabilities(tmp_path):
    root = tmp_path / "artifact"
    root.mkdir()
    quality = {"ok": True, "score": 100, "title": "Report"}
    static_manifest = build_html_artifact_manifest(
        title="Report", artifact_type="static-report", entrypoint="index.html", quality=quality,
    )
    (root / ARTIFACT_MANIFEST_NAME).write_text(json.dumps(static_manifest), encoding="utf-8")
    static_headers = dict(_html_artifact_security_headers(root))
    assert "script-src 'none'" in static_headers["Content-Security-Policy"]
    assert "https:" not in static_headers["Content-Security-Policy"]
    assert static_headers["Content-Security-Policy"].endswith("sandbox")
    assert "allow-same-origin" not in static_headers["Content-Security-Policy"]

    interactive_manifest = build_html_artifact_manifest(
        title="Dashboard", artifact_type="interactive-report", entrypoint="index.html", quality=quality,
    )
    (root / ARTIFACT_MANIFEST_NAME).write_text(json.dumps(interactive_manifest), encoding="utf-8")
    interactive_headers = dict(_html_artifact_security_headers(root))
    assert "script-src 'self' 'unsafe-inline'" in interactive_headers["Content-Security-Policy"]
    assert interactive_headers["Content-Security-Policy"].endswith("sandbox allow-scripts")


def test_html_preview_does_not_grant_same_origin_access():
    source = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "files.js").read_text(encoding="utf-8")
    assert 'sandbox="allow-scripts"' in source
    assert "allow-scripts allow-same-origin" not in source
    assert 'referrerpolicy="no-referrer"' in source
