"""Minimal structured rich-content model for outbound channel rendering."""
from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass(frozen=True)
class RichBlock:
    kind: str
    text: str = ""
    level: int = 0
    lang: str = ""
    items: tuple[str, ...] = ()


@dataclass(frozen=True)
class RichContentDocument:
    source_text: str
    blocks: tuple[RichBlock, ...] = field(default_factory=tuple)

    def has_kind(self, kind: str) -> bool:
        return any(block.kind == kind for block in self.blocks)


_RE_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_RE_LIST = re.compile(r"^\s*[-*+]\s+(.+)$")
_RE_QUOTE = re.compile(r"^\s*>\s?(.+)$")
_RE_FENCE_START = re.compile(r"^```([\w.+-]*)\s*$")


def parse_rich_content(text: str) -> RichContentDocument:
    lines = (text or "").splitlines()
    blocks: list[RichBlock] = []
    paragraph: list[str] = []
    i = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(RichBlock(kind="paragraph", text="\n".join(paragraph).strip()))
            paragraph = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            i += 1
            continue

        fence = _RE_FENCE_START.match(line)
        if fence:
            flush_paragraph()
            lang = fence.group(1).strip()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            blocks.append(RichBlock(kind="code_block", text="\n".join(body), lang=lang))
            i += 1
            continue

        heading = _RE_HEADING.match(line)
        if heading:
            flush_paragraph()
            blocks.append(
                RichBlock(kind="heading", text=heading.group(2).strip(), level=len(heading.group(1)))
            )
            i += 1
            continue

        list_item = _RE_LIST.match(line)
        if list_item:
            flush_paragraph()
            items = [list_item.group(1).strip()]
            i += 1
            while i < len(lines):
                nested = _RE_LIST.match(lines[i])
                if not nested:
                    break
                items.append(nested.group(1).strip())
                i += 1
            blocks.append(RichBlock(kind="list", items=tuple(items)))
            continue

        quote = _RE_QUOTE.match(line)
        if quote:
            flush_paragraph()
            items = [quote.group(1).strip()]
            i += 1
            while i < len(lines):
                nested = _RE_QUOTE.match(lines[i])
                if not nested:
                    break
                items.append(nested.group(1).strip())
                i += 1
            blocks.append(RichBlock(kind="quote", text="\n".join(items)))
            continue

        paragraph.append(line.rstrip())
        i += 1

    flush_paragraph()
    return RichContentDocument(source_text=text or "", blocks=tuple(blocks))
