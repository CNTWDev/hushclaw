"""Validation and manifest contracts for generated HTML artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


ARTIFACT_MANIFEST_NAME = "artifact-manifest.json"
HTML_ARTIFACT_TYPES = {"static-report", "interactive-report", "mini-app"}
_REMOTE_SCHEMES = {"http", "https", "//"}
_RESOURCE_ATTRS = {
    "img": ("src",),
    "script": ("src",),
    "link": ("href",),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "iframe": ("src",),
}


class _ArtifactHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.has_viewport = False
        self.has_main = False
        self.h1_count = 0
        self.title_parts: list[str] = []
        self._in_title = False
        self.inline_scripts = 0
        self.script_sources: list[str] = []
        self.resources: list[str] = []
        self.images_missing_alt = 0
        self.forms = 0
        self.event_handlers = 0
        self.javascript_urls = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {str(k).lower(): str(v or "") for k, v in attrs}
        self.event_handlers += sum(1 for name in values if name.startswith("on"))
        self.javascript_urls += sum(
            1 for name, value in values.items()
            if name in {"href", "src", "action", "formaction"}
            and value.strip().lower().startswith("javascript:")
        )
        if tag == "html":
            self.lang = values.get("lang", "").strip()
        elif tag == "meta" and values.get("name", "").lower() == "viewport":
            self.has_viewport = bool(values.get("content", "").strip())
        elif tag == "main" or values.get("role", "").lower() == "main":
            self.has_main = True
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "script":
            src = values.get("src", "").strip()
            if src:
                self.script_sources.append(src)
            else:
                self.inline_scripts += 1
        elif tag == "img" and "alt" not in values:
            self.images_missing_alt += 1
        elif tag == "form":
            self.forms += 1

        for attr in _RESOURCE_ATTRS.get(tag, ()):
            raw = values.get(attr, "").strip()
            if not raw:
                continue
            if attr == "srcset":
                self.resources.extend(part.strip().split(" ", 1)[0] for part in raw.split(",") if part.strip())
            else:
                self.resources.append(raw)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join("".join(self.title_parts).split())


def _artifact_root_and_entry(path: str | Path, entrypoint: str) -> tuple[Path, Path, str]:
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"HTML artifact path not found: {source}")
    if source.is_file():
        if source.suffix.lower() not in {".html", ".htm"}:
            raise ValueError("A single-file HTML artifact must end in .html or .htm")
        return source.parent, source, source.name
    if not source.is_dir():
        raise ValueError(f"HTML artifact path is not a file or directory: {source}")

    rel = Path(entrypoint or "index.html")
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Invalid HTML artifact entrypoint: {entrypoint}")
    entry = (source / rel).resolve()
    try:
        entry.relative_to(source)
    except ValueError as exc:
        raise ValueError(f"HTML artifact entrypoint escapes its root: {entrypoint}") from exc
    if not entry.is_file():
        raise FileNotFoundError(f"HTML artifact entrypoint not found: {entry}")
    if entry.suffix.lower() not in {".html", ".htm"}:
        raise ValueError("HTML artifact entrypoint must end in .html or .htm")
    return source, entry, rel.as_posix()


def _is_remote_resource(value: str) -> bool:
    raw = value.strip()
    if raw.startswith("//"):
        return True
    return urlparse(raw).scheme.lower() in _REMOTE_SCHEMES


def _is_ignorable_resource(value: str) -> bool:
    raw = value.strip().lower()
    return not raw or raw.startswith(("#", "data:", "blob:", "mailto:", "tel:", "javascript:"))


def _local_resource_error(root: Path, entry: Path, value: str) -> str:
    clean = value.split("#", 1)[0].split("?", 1)[0].strip()
    if not clean or clean.startswith("/"):
        return ""
    candidate = (entry.parent / clean).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return f"Resource escapes artifact root: {value}"
    if not candidate.exists():
        return f"Referenced resource is missing: {value}"
    return ""


def validate_html_artifact(
    path: str | Path,
    *,
    entrypoint: str = "index.html",
    artifact_type: str = "static-report",
) -> dict:
    """Validate one generated HTML file or directory without executing it."""
    if artifact_type not in HTML_ARTIFACT_TYPES:
        raise ValueError(
            f"Unsupported HTML artifact type {artifact_type!r}; "
            f"expected one of {sorted(HTML_ARTIFACT_TYPES)}"
        )
    root, entry, normalized_entrypoint = _artifact_root_and_entry(path, entrypoint)
    single_file = Path(path).expanduser().resolve().is_file()
    raw = entry.read_text(encoding="utf-8", errors="replace")
    parser = _ArtifactHTMLParser()
    parser.feed(raw)

    errors: list[str] = []
    warnings: list[str] = []
    remote_resources = sorted({item for item in parser.resources if _is_remote_resource(item)})

    for resource in sorted(set(parser.resources)):
        if _is_ignorable_resource(resource) or _is_remote_resource(resource):
            continue
        if single_file:
            errors.append(
                f"Single-file artifacts must inline local dependencies or be published as a directory: {resource}"
            )
            continue
        problem = _local_resource_error(root, entry, resource)
        if problem:
            errors.append(problem)

    if remote_resources:
        errors.append(
            "Remote resources are not allowed in managed HTML artifacts: "
            + ", ".join(remote_resources[:5])
        )
    script_count = parser.inline_scripts + len(parser.script_sources)
    if artifact_type == "static-report" and script_count:
        errors.append("Static reports must not contain JavaScript; use interactive-report when scripts are required")
    if artifact_type == "static-report" and parser.event_handlers:
        errors.append("Static reports must not contain inline event handlers")
    if parser.javascript_urls:
        errors.append("javascript: URLs are not allowed in managed HTML artifacts")
    if not parser.title:
        warnings.append("Add a concise <title> for browser tabs and exports")
    if not parser.lang:
        warnings.append("Add a language to <html lang=...>")
    if not parser.has_viewport:
        warnings.append("Add a responsive viewport meta tag")
    if not parser.has_main:
        warnings.append("Add a <main> landmark")
    if parser.h1_count != 1:
        warnings.append(f"Use exactly one <h1>; found {parser.h1_count}")
    if parser.images_missing_alt:
        warnings.append(f"Add alt text to {parser.images_missing_alt} image(s)")
    if parser.forms and artifact_type != "mini-app":
        warnings.append("Forms are intended for mini-app artifacts and may be blocked by the runtime")

    css_text = "" if single_file else "\n".join(
        file.read_text(encoding="utf-8", errors="ignore")
        for file in root.rglob("*.css")
        if file.is_file() and file.stat().st_size <= 2_000_000
    )
    if artifact_type.endswith("report") and "@media print" not in f"{raw}\n{css_text}".lower():
        warnings.append("Add @media print styles for report export")

    artifact_files = [entry] if single_file else [item for item in root.rglob("*") if item.is_file()]
    file_count = len(artifact_files)
    total_bytes = sum(item.stat().st_size for item in artifact_files)
    score = max(0, 100 - 25 * len(errors) - 5 * len(warnings))
    return {
        "ok": not errors,
        "status": "passed" if not errors else "failed",
        "score": score,
        "artifact_type": artifact_type,
        "entrypoint": normalized_entrypoint,
        "title": parser.title,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "files": file_count,
            "bytes": total_bytes,
            "scripts": script_count,
            "event_handlers": parser.event_handlers,
            "remote_resources": len(remote_resources),
            "missing_alt": parser.images_missing_alt,
        },
    }


def build_html_artifact_manifest(
    *,
    title: str,
    artifact_type: str,
    entrypoint: str,
    quality: dict,
) -> dict:
    """Build the versioned manifest persisted beside a published artifact."""
    if artifact_type not in HTML_ARTIFACT_TYPES:
        raise ValueError(f"Unsupported HTML artifact type: {artifact_type}")
    scripts = artifact_type in {"interactive-report", "mini-app"}
    return {
        "schema_version": 1,
        "kind": "html-artifact",
        "type": artifact_type,
        "title": str(title or quality.get("title") or "HTML artifact").strip(),
        "entrypoint": entrypoint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "capabilities": {
            "scripts": scripts,
            "forms": artifact_type == "mini-app",
            "network": False,
            "print": artifact_type.endswith("report"),
        },
        "security": {
            "trust_level": "generated",
            "network_policy": "deny",
            "sandbox": True,
        },
        "quality": quality,
    }


def read_html_artifact_manifest(artifact_root: Path) -> dict:
    """Read a managed artifact manifest, returning an empty dict if invalid."""
    path = artifact_root / ARTIFACT_MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
