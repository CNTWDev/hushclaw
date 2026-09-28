"""Detect when the user's message corrects the assistant's previous answer.

A cheap lexical pre-filter decides whether a turn is worth an LLM check, so
ordinary turns cost nothing. With a model available the classifier confirms
the correction and names what went wrong; without one, only strong phrases
count.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Broad cues: worth asking the classifier. Chinese and English.
_CANDIDATE = re.compile(
    r"(不对|不是|错了|有误|搞错|弄错|理解错|误解|没理解|不准确|不正确|重新|再来|重做|我说的是|我的意思是|我要的是|"
    r"我问的是|你漏了|漏掉|忽略了|没按|不符合|不要这样|别这样|跑题|答非所问|"
    r"\bwrong\b|\bincorrect\b|\bnot what i\b|\bi meant\b|\bi said\b|\bthat's not\b|\bthats not\b|"
    r"\bno,|\bnope\b|\bmisunderst|\byou missed\b|\byou ignored\b|\btry again\b|\bredo\b)",
    re.I,
)

# Strong cues: accepted as corrections even without a classifier.
_STRONG = re.compile(
    r"(不对|错了|搞错|弄错|理解错|答非所问|不是我要的|不是我想要的|我说的是|我问的是|你漏了|没按我说的|"
    r"\bthat's wrong\b|\bthats wrong\b|\bnot what i (asked|meant|wanted)\b|\byou misunderstood\b|\bthat's incorrect\b)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class CorrectionVerdict:
    is_correction: bool
    what_was_wrong: str = ""
    expected: str = ""


def looks_like_correction(text: str) -> bool:
    return bool(_CANDIDATE.search(str(text or "")[:600]))


def strong_correction(text: str) -> bool:
    return bool(_STRONG.search(str(text or "")[:600]))


def parse_verdict(content: str) -> CorrectionVerdict:
    text = str(content or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in correction verdict")
    obj = json.loads(text[start:end + 1])
    return CorrectionVerdict(
        is_correction=bool(obj.get("is_correction")),
        what_was_wrong=str(obj.get("what_was_wrong") or "").strip()[:300],
        expected=str(obj.get("expected") or "").strip()[:300],
    )
