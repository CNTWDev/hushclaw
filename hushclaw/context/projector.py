"""Post-turn lightweight projection and auto-extraction service."""
from __future__ import annotations

import re

from hushclaw.memory.kinds import DECISION, PROJECT_KNOWLEDGE, TELEMETRY, USER_MODEL
from hushclaw.util.logging import get_logger

log = get_logger("context.projector")

_AUTO_EXTRACT_USER_PATTERNS = [
    r"(?:我叫|名字是|my name is|I(?:'m| am) called)\s+(\S+)",
    r"(?:项目名|项目|project(?:\s+name)?)\s*[：:=]\s*(.+?)(?:\s*[,，\n]|$)",
    r"(?:技术栈|约定|规范|环境|stack|convention|workflow)\s*[：:=]\s*(.{8,100}?)(?:[。\n]|$)",
]
_AUTO_EXTRACT_ASSISTANT_PATTERNS = [
    r"https?://[^\s\"'>]+",
    r"(?:^|\s|[：:=\(])(~?/(?:[\w.% -]+/)+[\w.% -]+\.[\w]+)",
    r"\bv\d+\.\d+(?:\.\d+)?(?:-[\w.]+)?\b",
]
_INTEREST_PATTERNS = [
    r"(?:为什么|怎么|如何|什么是|有没有|是否|能否|会不会)\s*(.{6,80}?)(?:[?？。\n]|$)",
    r"(?:why (?:does|is|would|did)|how (?:does|can|to|do)|what is|is there)\s+(.{8,100}?)(?:[?.\n]|$)",
]
_PREFERENCE_PATTERNS = [
    r"(?:用户偏好|偏好|习惯|我喜欢|我不喜欢|风格)\s*[：:]?\s*([^\n。！？!?]{4,80})(?:[。！？!?\n]|$)",
    r"(?:I prefer|I like|I don't like|my preference is|my usual workflow is)\s+(.{8,100}?)(?:[.\n]|$)",
]
_BELIEF_PATTERNS = [
    r"(?:我认为|我觉得|我感觉|应该|不应该|这应该|这不应该)\s+(.{8,80}?)(?:[。\n]|$)",
    r"(?:I think|I believe|I feel|should(?:n't| not)|this should(?:n't| not))\s+(.{8,100}?)(?:[.\n]|$)",
]
_DECISION_PATTERNS = [
    r"(?:决定|结论|方案|选择了|最终|我们采用|确定使用)\s*[：:]?\s*([^\n。！？!?]{4,80})(?:[。！？!?\n]|$)",
    r"(?:decided to|we chose|the approach is|final decision|we settled on)\s*[：:\s]?\s*(.{8,100}?)(?:[.\n]|$)",
]
_ACTION_LOG_RE = re.compile(
    r"^(?:帮我|帮助我|已(?:修复|完成|创建|删除|更改|修改)|完成了|修改了|创建了|删除了|"
    r"updated|fixed|completed|created|deleted|I(?:'ve| have) (?:fixed|updated|created|deleted|completed))\s+.{5,}",
    re.IGNORECASE,
)
_REQUEST_LIKE_RE = re.compile(
    r"^(?:帮我|帮助我|请帮|我想要|我需要|目标|需求|任务|please |can you |could you |help me )",
    re.IGNORECASE,
)
_AUTO_EXTRACT_MAX_PER_TURN = 3
_AUTO_EXTRACT_STOP_PHRASES = (
    "保存到记忆",
    "并保存到记忆",
    "已保存到记忆",
    "save to memory",
    "saved to memory",
)
_CORRECTION_RE = re.compile(
    r"不对|不是这样|理解错了|你弄错了|重新来|再来一次|不是我要的|不是我想要|"
    r"错了|搞错了|没理解|不符合|不对劲|不是这个意思|"
    r"that'?s (not right|wrong|incorrect)|you misunderstood|"
    r"try again|not what I (want|meant|asked)|"
    r"incorrect|you got it wrong|wrong answer",
    re.IGNORECASE,
)
_AUTO_EXTRACT_FRAGMENT_PREFIXES = (
    "并", "以及", "并且", "另外", "然后", "且", "并将",
    "and ", "then ",
)
_AUTO_EXTRACT_PATH_RE = re.compile(r"^~?/(?:[\w.% -]+/)+[\w.% -]+\.[\w]+$")


def _strip_markdown_noise(s: str) -> str:
    t = s.strip()
    t = re.sub(r"\*+", "", t)
    t = re.sub(r'^[`#>\s》」』"\']+|[`#>\s》」』"\']+$', "", t)
    return t.strip(" \t\r\n。，、,.;；:：\"'（）()[]【】「」『』-*_")


def _auto_extract_fact_ok(fact: str) -> bool:
    t = _strip_markdown_noise(fact)
    lower_t = t.lower()
    if any(p in t or p in lower_t for p in _AUTO_EXTRACT_STOP_PHRASES):
        return False
    if len(t) < 6:
        return False
    substantive = re.findall(r"[\w\u4e00-\u9fff]", t)
    if len(substantive) < 4:
        return False
    if len(substantive) / max(len(t), 1) < 0.45:
        return False
    if t.startswith(_AUTO_EXTRACT_FRAGMENT_PREFIXES):
        return False
    if t.endswith((",", "，", ";", "；", ":", "：", '"', "'")):
        return False
    return True


def _extract_from_text(
    text: str,
    patterns: list[str],
    *,
    artifact_only: bool,
    seen: set[str],
    out: list[str],
    limit: int,
) -> None:
    if not text or len(out) >= limit:
        return
    for pattern in patterns:
        if len(out) >= limit:
            break
        for match in re.findall(pattern, text, re.IGNORECASE | re.MULTILINE):
            if len(out) >= limit:
                break
            if isinstance(match, tuple):
                fact = " ".join(m.strip() for m in match if m.strip())
            else:
                fact = match.strip()
            if not fact or len(fact) < 6:
                continue
            clean = _strip_markdown_noise(fact)
            store = clean if clean else fact
            if not store or store in seen:
                continue
            if artifact_only:
                if re.match(r"^https?://", store, re.I):
                    pass
                elif _AUTO_EXTRACT_PATH_RE.match(store):
                    pass
                elif re.match(r"^v\d+\.\d+", store, re.I):
                    pass
                else:
                    continue
            elif not _auto_extract_fact_ok(store):
                continue
            seen.add(store)
            out.append(store)


class TurnProjectionService:
    """Lightweight, zero-LLM post-turn projection service."""

    def __init__(self, auto_extract: bool = True) -> None:
        self.auto_extract = auto_extract

    async def after_turn(
        self,
        session_id: str,
        user_input: str,
        assistant_response: str,
        memory,
    ) -> None:
        if not self.auto_extract:
            return

        seen: set[str] = set()
        typed_extractions: list[tuple[str, str]] = []

        def _collect_typed(text: str, patterns: list[str], note_type: str, *, artifact_only: bool = False) -> None:
            if not text or len(typed_extractions) >= _AUTO_EXTRACT_MAX_PER_TURN:
                return
            tmp: list[str] = []
            _extract_from_text(
                text,
                patterns,
                artifact_only=artifact_only,
                seen=seen,
                out=tmp,
                limit=_AUTO_EXTRACT_MAX_PER_TURN - len(typed_extractions),
            )
            for fact in tmp:
                if _ACTION_LOG_RE.search(fact):
                    log.debug("auto_extract: suppressed action_log fragment %r", fact[:40])
                    continue
                if note_type == "fact" and _REQUEST_LIKE_RE.search(fact):
                    log.debug("auto_extract: suppressed request-like fact %r", fact[:40])
                    continue
                typed_extractions.append((fact, note_type))

        _collect_typed(user_input, _INTEREST_PATTERNS, "interest")
        _collect_typed(user_input, _PREFERENCE_PATTERNS, "preference")
        _collect_typed(user_input, _BELIEF_PATTERNS, "belief")
        _collect_typed(user_input, _DECISION_PATTERNS, "decision")
        _collect_typed(user_input, _AUTO_EXTRACT_USER_PATTERNS, "fact")
        _collect_typed(assistant_response, _AUTO_EXTRACT_ASSISTANT_PATTERNS, "fact", artifact_only=True)

        for fact, note_type in typed_extractions[:_AUTO_EXTRACT_MAX_PER_TURN]:
            try:
                note_title = f"Auto: {fact[:60]}"
                if memory.note_exists_with_title(note_title):
                    log.debug("auto_extract: skipping duplicate title %r", note_title[:40])
                    continue
                memory.remember(
                    fact,
                    title=note_title,
                    tags=["_auto_extract"],
                    note_type=note_type,
                    memory_kind=(
                        USER_MODEL if note_type in {"interest", "belief", "preference"}
                        else DECISION if note_type == "decision"
                        else PROJECT_KNOWLEDGE
                    ),
                    persist_to_disk=False,
                )
            except Exception as e:
                log.debug("auto_extract save failed: %s", e)

        if user_input and _CORRECTION_RE.search(user_input):
            try:
                snippet = user_input[:120].replace("\n", " ")
                memory.remember(
                    f"correction in session {session_id[:8]}: {snippet}",
                    title=f"Correction: {snippet[:60]}",
                    tags=["_correction", session_id],
                    memory_kind=TELEMETRY,
                    persist_to_disk=False,
                )
                log.debug("auto_extract: correction signal recorded for session %s", session_id[:8])
            except Exception as e:
                log.debug("correction signal save failed: %s", e)
