"""Tools for validating and publishing managed HTML artifacts."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from hushclaw.artifacts.html import (
    ARTIFACT_MANIFEST_NAME,
    build_html_artifact_manifest,
    validate_html_artifact,
)
from hushclaw.tools.base import ToolResult, tool
from hushclaw.tools.builtins.file_tools import register_download_bundle


def _json_error(message: str, *, quality: dict | None = None) -> ToolResult:
    payload = {"ok": False, "error": message}
    if quality is not None:
        payload["quality"] = quality
    return ToolResult.error(json.dumps(payload, ensure_ascii=False))


@tool(
    name="inspect_html_artifact",
    description=(
        "Statically inspect a generated HTML file or directory before publishing. "
        "Checks structure, local resources, accessibility basics, print readiness, "
        "remote dependencies, and script policy without executing the page."
    ),
    parallel_safe=True,
)
def inspect_html_artifact(
    path: str,
    entrypoint: str = "index.html",
    artifact_type: str = "static-report",
) -> ToolResult:
    try:
        result = validate_html_artifact(
            path,
            entrypoint=entrypoint,
            artifact_type=artifact_type,
        )
    except Exception as exc:
        return _json_error(str(exc))
    return ToolResult.ok(json.dumps(result, ensure_ascii=False))


@tool(
    name="publish_html_artifact",
    description=(
        "Validate and publish a generated HTML file or directory as a managed, "
        "previewable artifact. Adds an artifact manifest and returns structured "
        "preview, quality, capability, and security metadata."
    ),
    mutating=True,
)
def publish_html_artifact(
    path: str,
    title: str = "",
    entrypoint: str = "index.html",
    artifact_type: str = "static-report",
    _config=None,
) -> ToolResult:
    try:
        source = Path(path).expanduser().resolve()
        quality = validate_html_artifact(
            source,
            entrypoint=entrypoint,
            artifact_type=artifact_type,
        )
    except Exception as exc:
        return _json_error(str(exc))
    if not quality.get("ok"):
        return _json_error("HTML artifact failed validation", quality=quality)

    normalized_entrypoint = str(quality["entrypoint"])
    manifest = build_html_artifact_manifest(
        title=title,
        artifact_type=artifact_type,
        entrypoint=normalized_entrypoint,
        quality=quality,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="hushclaw-html-artifact-") as temp_dir:
            staging = Path(temp_dir) / "artifact"
            if source.is_dir():
                shutil.copytree(source, staging)
            else:
                staging.mkdir(parents=True)
                normalized_entrypoint = "index.html"
                shutil.copy2(source, staging / normalized_entrypoint)
                manifest["entrypoint"] = normalized_entrypoint
            (staging / ARTIFACT_MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            artifact = register_download_bundle(
                staging,
                _config=_config,
                entrypoint=normalized_entrypoint,
                display_name=manifest["title"],
            )
    except Exception as exc:
        return _json_error(f"Failed to publish HTML artifact: {exc}", quality=quality)

    root_url = str(artifact.get("root_url") or "")
    artifact.update({
        "title": manifest["title"],
        "artifact_type": artifact_type,
        "media_type": "text/html",
        "preview_url": artifact.get("entry_url") or artifact.get("url"),
        "manifest_url": f"{root_url}{ARTIFACT_MANIFEST_NAME}" if root_url else "",
        "trust_level": "generated",
        "capabilities": manifest["capabilities"],
        "quality": quality,
    })
    payload = {"ok": True, "artifact": artifact, "quality": quality}
    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False),
        artifact_id=str(artifact.get("artifact_id") or ""),
        metadata={"artifact": artifact, "artifacts": [artifact]},
    )
