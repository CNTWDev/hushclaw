"""Lightweight file ratings and deterministic tag metadata helpers."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterable

MAX_FILE_TAGS = 12
MAX_FILE_TAG_LENGTH = 32

_KIND_TAGS = {
    ".md": "文档", ".markdown": "文档", ".txt": "文档", ".doc": "文档", ".docx": "文档",
    ".pdf": "文档", ".rtf": "文档",
    ".xls": "表格", ".xlsx": "表格", ".csv": "表格", ".tsv": "表格",
    ".ppt": "演示", ".pptx": "演示", ".key": "演示",
    ".png": "图片", ".jpg": "图片", ".jpeg": "图片", ".gif": "图片", ".webp": "图片", ".svg": "图片",
    ".mp3": "音频", ".wav": "音频", ".m4a": "音频", ".ogg": "音频",
    ".mp4": "视频", ".mov": "视频", ".mkv": "视频", ".webm": "视频",
    ".json": "数据", ".jsonl": "数据", ".yaml": "数据", ".yml": "数据", ".xml": "数据",
    ".py": "代码", ".js": "代码", ".ts": "代码", ".tsx": "代码", ".jsx": "代码", ".html": "代码", ".css": "代码",
}

_TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("战略", ("战略", "strategy", "strategic")),
    ("市场", ("市场", "market", "marketing", "gtm")),
    ("产品", ("产品", "product", "roadmap")),
    ("研究", ("研究", "调研", "分析", "research", "analysis")),
    ("报告", ("报告", "汇报", "report")),
    ("会议", ("会议", "纪要", "meeting", "minutes")),
    ("财务", ("财务", "预算", "营收", "finance", "financial", "budget", "revenue")),
    ("组织", ("组织", "人力", "人才", "organization", "organisational", "talent", "hr")),
    ("技术", ("技术", "架构", "算法", "architecture", "technical", "technology", "algorithm", "api")),
    ("规划", ("规划", "计划", "plan", "planning")),
)


def normalize_file_tag(value: object) -> tuple[str, str] | None:
    """Return a safe display tag and case-insensitive key."""
    display = re.sub(r"\s+", " ", str(value or "").strip().lstrip("#")).strip()
    if not display or len(display) > MAX_FILE_TAG_LENGTH:
        return None
    if any(ord(ch) < 32 for ch in display):
        return None
    return display, display.casefold()


def normalize_file_tags(values: Iterable[object] | object) -> list[tuple[str, str]]:
    if isinstance(values, str):
        values = re.split(r"[,，\n]", values)
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_file_tag(value)
        if normalized is None:
            continue
        display, key = normalized
        if key in seen:
            continue
        seen.add(key)
        result.append((display, key))
        if len(result) >= MAX_FILE_TAGS:
            break
    return result


def suggest_file_tags(name: str, *, mime_type: str = "") -> list[str]:
    """Suggest stable local tags without invoking a model or blocking a request."""
    filename = str(name or "").strip()
    lowered = filename.casefold()
    tokens = set(filter(None, re.split(r"[^a-z0-9]+", lowered)))
    suggestions: list[str] = []

    kind = _KIND_TAGS.get(Path(filename).suffix.casefold())
    if not kind:
        major = str(mime_type or "").split("/", 1)[0].casefold()
        kind = {"image": "图片", "audio": "音频", "video": "视频", "text": "文档"}.get(major)
    if kind:
        suggestions.append(kind)

    for tag, needles in _TOPIC_RULES:
        matched = any(
            needle in lowered if any("\u4e00" <= ch <= "\u9fff" for ch in needle)
            else needle in tokens
            for needle in needles
        )
        if matched and tag not in suggestions:
            suggestions.append(tag)
        if len(suggestions) >= 5:
            break
    return suggestions


def ensure_auto_file_tags(conn, file_id: str, name: str, *, mime_type: str = "") -> list[str]:
    """Add missing deterministic tags while preserving all user decisions."""
    now = int(time.time())
    suggestions = suggest_file_tags(name, mime_type=mime_type)
    for display, key in normalize_file_tags(suggestions):
        conn.execute(
            """
            INSERT INTO file_tags(file_id, tag, normalized_tag, source, confidence, created, updated)
            VALUES (?, ?, ?, 'auto', 1.0, ?, ?)
            ON CONFLICT(file_id, normalized_tag) DO NOTHING
            """,
            (file_id, display, key, now, now),
        )
    return suggestions


def replace_manual_file_tags(conn, file_id: str, values: Iterable[object] | object) -> list[str]:
    """Replace manual tags atomically; matching auto tags become user-owned."""
    normalized = normalize_file_tags(values)
    now = int(time.time())
    conn.execute("DELETE FROM file_tags WHERE file_id=? AND source='manual'", (file_id,))
    for display, key in normalized:
        conn.execute(
            """
            INSERT INTO file_tags(file_id, tag, normalized_tag, source, confidence, created, updated)
            VALUES (?, ?, ?, 'manual', 1.0, ?, ?)
            ON CONFLICT(file_id, normalized_tag) DO UPDATE SET
                tag=excluded.tag,
                source='manual',
                confidence=1.0,
                updated=excluded.updated
            """,
            (file_id, display, key, now, now),
        )
    return [display for display, _key in normalized]
