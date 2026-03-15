"""PPT editing tools powered by python-pptx.

All functions are synchronous (python-pptx is a sync library).
Installed automatically when the skill is loaded via HushClaw's bundled-tool mechanism.
"""
from __future__ import annotations

from pathlib import Path

from hushclaw.tools.base import ToolResult, tool


@tool(description="Get slide count and title list from a PPTX file.")
def pptx_info(path: str) -> ToolResult:
    """Return basic info about the presentation: slide count and per-slide titles."""
    try:
        from pptx import Presentation  # type: ignore
    except ImportError:
        return ToolResult(error="python-pptx is not installed. Run: pip install python-pptx")

    p = Path(path).expanduser()
    if not p.exists():
        return ToolResult(error=f"File not found: {path}")

    prs = Presentation(str(p))
    titles = []
    for i, slide in enumerate(prs.slides):
        title_shape = slide.shapes.title
        titles.append({
            "slide": i,
            "title": title_shape.text.strip() if title_shape and title_shape.has_text_frame else "",
        })

    return ToolResult(output={
        "path": str(p),
        "slide_count": len(prs.slides),
        "slides": titles,
    })


@tool(description="Read all text from a specific slide (0-based index).")
def pptx_read_slide(path: str, slide_index: int) -> ToolResult:
    """Extract all text from the specified slide."""
    try:
        from pptx import Presentation  # type: ignore
    except ImportError:
        return ToolResult(error="python-pptx is not installed. Run: pip install python-pptx")

    p = Path(path).expanduser()
    if not p.exists():
        return ToolResult(error=f"File not found: {path}")

    prs = Presentation(str(p))
    if slide_index < 0 or slide_index >= len(prs.slides):
        return ToolResult(error=f"slide_index {slide_index} out of range (0–{len(prs.slides)-1})")

    slide = prs.slides[slide_index]
    texts = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                line = para.text.strip()
                if line:
                    texts.append(line)

    return ToolResult(output={
        "slide": slide_index,
        "texts": texts,
        "full_text": "\n".join(texts),
    })


@tool(description="Extract all text from every slide in a PPTX file.")
def pptx_extract_all_text(path: str) -> ToolResult:
    """Return a list of {slide, texts} dicts covering all slides."""
    try:
        from pptx import Presentation  # type: ignore
    except ImportError:
        return ToolResult(error="python-pptx is not installed. Run: pip install python-pptx")

    p = Path(path).expanduser()
    if not p.exists():
        return ToolResult(error=f"File not found: {path}")

    prs = Presentation(str(p))
    slides_out = []
    for i, slide in enumerate(prs.slides):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    line = para.text.strip()
                    if line:
                        texts.append(line)
        slides_out.append({"slide": i, "texts": texts})

    return ToolResult(output={"path": str(p), "slides": slides_out})


@tool(description="Add a title slide (with title and subtitle) to an existing PPTX file.")
def pptx_add_title_slide(path: str, title: str, subtitle: str = "") -> ToolResult:
    """Append a title slide layout page to the presentation."""
    try:
        from pptx import Presentation  # type: ignore
        from pptx.util import Pt  # type: ignore  # noqa: F401
    except ImportError:
        return ToolResult(error="python-pptx is not installed. Run: pip install python-pptx")

    p = Path(path).expanduser()
    if not p.exists():
        return ToolResult(error=f"File not found: {path}")

    prs = Presentation(str(p))
    layout = prs.slide_layouts[0]  # Title Slide layout
    slide = prs.slides.add_slide(layout)

    placeholders = {ph.placeholder_format.idx: ph for ph in slide.placeholders}
    if 0 in placeholders:
        placeholders[0].text = title
    if 1 in placeholders:
        placeholders[1].text = subtitle

    prs.save(str(p))
    return ToolResult(output={
        "added_slide": len(prs.slides) - 1,
        "title": title,
        "subtitle": subtitle,
    })


@tool(description="Add a text content slide (title + body text) to an existing PPTX file.")
def pptx_add_text_slide(path: str, title: str, content: str) -> ToolResult:
    """Append a title+content layout slide. Use newlines in content for bullet points."""
    try:
        from pptx import Presentation  # type: ignore
    except ImportError:
        return ToolResult(error="python-pptx is not installed. Run: pip install python-pptx")

    p = Path(path).expanduser()
    if not p.exists():
        return ToolResult(error=f"File not found: {path}")

    prs = Presentation(str(p))
    layout = prs.slide_layouts[1]  # Title and Content layout
    slide = prs.slides.add_slide(layout)

    placeholders = {ph.placeholder_format.idx: ph for ph in slide.placeholders}
    if 0 in placeholders:
        placeholders[0].text = title
    if 1 in placeholders:
        tf = placeholders[1].text_frame
        tf.clear()
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if i == 0:
                tf.paragraphs[0].text = line
            else:
                tf.add_paragraph().text = line

    prs.save(str(p))
    return ToolResult(output={
        "added_slide": len(prs.slides) - 1,
        "title": title,
    })


@tool(description="Set the text of a specific placeholder on a slide (0-based slide_index and placeholder_index).")
def pptx_set_slide_text(path: str, slide_index: int, placeholder_index: int, text: str) -> ToolResult:
    """Modify the text of one placeholder on the specified slide."""
    try:
        from pptx import Presentation  # type: ignore
    except ImportError:
        return ToolResult(error="python-pptx is not installed. Run: pip install python-pptx")

    p = Path(path).expanduser()
    if not p.exists():
        return ToolResult(error=f"File not found: {path}")

    prs = Presentation(str(p))
    if slide_index < 0 or slide_index >= len(prs.slides):
        return ToolResult(error=f"slide_index {slide_index} out of range (0–{len(prs.slides)-1})")

    slide = prs.slides[slide_index]
    placeholders = {ph.placeholder_format.idx: ph for ph in slide.placeholders}
    if placeholder_index not in placeholders:
        available = sorted(placeholders.keys())
        return ToolResult(error=f"placeholder_index {placeholder_index} not found. Available: {available}")

    placeholders[placeholder_index].text = text
    prs.save(str(p))
    return ToolResult(output={
        "slide": slide_index,
        "placeholder": placeholder_index,
        "text": text,
    })


@tool(description="Delete a slide from a PPTX file by 0-based slide_index.")
def pptx_delete_slide(path: str, slide_index: int) -> ToolResult:
    """Remove the slide at slide_index from the presentation."""
    try:
        from pptx import Presentation  # type: ignore
        from lxml import etree  # type: ignore  # noqa: F401
    except ImportError as e:
        return ToolResult(error=f"Missing dependency: {e}. Run: pip install python-pptx lxml")

    p = Path(path).expanduser()
    if not p.exists():
        return ToolResult(error=f"File not found: {path}")

    prs = Presentation(str(p))
    total = len(prs.slides)
    if slide_index < 0 or slide_index >= total:
        return ToolResult(error=f"slide_index {slide_index} out of range (0–{total-1})")

    xml_slides = prs.slides._sldIdLst
    xml_slides.remove(xml_slides[slide_index])
    prs.save(str(p))
    return ToolResult(output={
        "deleted_slide": slide_index,
        "remaining_slides": total - 1,
    })


@tool(description="Create a new blank PPTX file at the given path. Overwrites if exists.")
def pptx_create(path: str) -> ToolResult:
    """Create an empty PowerPoint presentation."""
    try:
        from pptx import Presentation  # type: ignore
    except ImportError:
        return ToolResult(error="python-pptx is not installed. Run: pip install python-pptx")

    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.save(str(p))
    return ToolResult(output={"created": str(p)})
