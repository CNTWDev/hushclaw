from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256


@dataclass(slots=True)
class ShareRenderRequest:
    session_id: str
    assistant_turn_id: str
    template: str = "auto"
    theme: str = "auto"
    size: str = "standard"
    include_question: bool = True


@dataclass(slots=True)
class ShareRenderPayload:
    browser_payload: dict
    css_width: int
    css_height: int
    scale: float
    cache_key: str


def _normalize_template(template: str) -> str:
    allowed = {"auto", "dark", "ink", "folio", "blueprint", "halo"}
    value = str(template or "auto").strip().lower()
    return value if value in allowed else "auto"


def _normalize_theme(theme: str) -> str:
    value = str(theme or "auto").strip().lower()
    return value if value in {"auto", "dark", "light"} else "auto"


def _normalize_size(size: str) -> tuple[int, int, float, str]:
    value = str(size or "standard").strip().lower()
    if value == "hq":
      return 900, 1273, 2.5, "hq"
    return 900, 1273, 2.0, "standard"


def build_share_render_payload(req: ShareRenderRequest, *, memory) -> ShareRenderPayload:
    turns = memory.load_session_turns(req.session_id)
    if not turns:
        raise ValueError("session not found")

    target_idx = -1
    target_turn: dict | None = None
    for idx, turn in enumerate(turns):
        if str(turn.get("turn_id") or "") == req.assistant_turn_id:
            target_idx = idx
            target_turn = turn
            break
    if target_turn is None:
        raise ValueError("assistant turn not found")
    if str(target_turn.get("role") or "") != "assistant":
        raise ValueError("target turn is not an assistant response")

    question = ""
    if req.include_question:
        for idx in range(target_idx - 1, -1, -1):
            turn = turns[idx]
            if str(turn.get("role") or "") == "user":
                question = str(turn.get("content") or "").strip()
                break

    css_width, css_height, scale, size_name = _normalize_size(req.size)
    template = _normalize_template(req.template)
    theme = _normalize_theme(req.theme)
    content = str(target_turn.get("content") or "")
    ts = int(target_turn.get("ts") or 0)
    dt = datetime.fromtimestamp(ts) if ts > 0 else datetime.now()
    datetime_text = dt.strftime("%Y-%m-%d %H:%M")

    browser_payload = {
        "content": content,
        "question": question,
        "datetime": datetime_text,
        "template": template,
        "theme": theme,
        "size": size_name,
    }
    digest = sha256(
        (
            req.session_id
            + "|"
            + req.assistant_turn_id
            + "|"
            + template
            + "|"
            + theme
            + "|"
            + size_name
            + "|"
            + ("1" if req.include_question else "0")
            + "|"
            + content
            + "|"
            + question
        ).encode("utf-8")
    ).hexdigest()
    return ShareRenderPayload(
        browser_payload=browser_payload,
        css_width=css_width,
        css_height=css_height,
        scale=scale,
        cache_key=digest,
    )
