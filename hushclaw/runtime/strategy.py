"""Deterministic per-turn execution strategy.

The model remains responsible for language understanding and tool arguments, but
the runtime should decide the broad execution envelope before calling it.  This
module intentionally uses only small, explainable signals so it is cheap,
predictable, and safe to run on every turn.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class TaskStrategy:
    """Execution envelope selected for one user turn."""

    intent: str = "general"
    max_tool_rounds: int | None = None
    allowed_tools: frozenset[str] | None = None
    reason: str = ""

    def reflection_fingerprint(self) -> str:
        """Map the runtime intent to the existing reflection taxonomy."""
        return {
            "research": "web_research",
            "code_change": "code_change",
        }.get(self.intent, "general_assistance")


_RESEARCH = re.compile(
    r"(?:latest|recent|news|research|search|look\s*up|compare|benchmark|price|regulation|最新|最近|新闻|研究|搜索|查一下|查找|对比|比较|价格|法规|资料)",
    re.I,
)
_CODE = re.compile(
    r"(?:code|coding|bug|debug|fix|refactor|patch|test|pytest|repository|repo|代码|报错|修复|重构|修改|测试|项目|仓库)",
    re.I,
)
_FILE = re.compile(
    r"(?:file|document|report|export|write|create|generate|save|文件|文档|报告|导出|生成|写入|保存)",
    re.I,
)
_MEMORY = re.compile(
    r"(?:remember|recall|memory|preference|remember this|记住|记忆|回忆|之前|偏好)",
    re.I,
)
_EXTERNAL = re.compile(
    r"(?:send|post|publish|reply|delete|cancel|schedule|email|message|发送|发布|回复|删除|取消|安排|邮件|消息)",
    re.I,
)
_PLANNING = re.compile(
    r"(?:plan|roadmap|steps|workflow|strategy|规划|方案|路线图|步骤|流程|策略)",
    re.I,
)
_OPERATIONAL = re.compile(
    r"(?:use\s+(?:a\s+)?tool|call\s+the\s+tool|run|execute|invoke|skill|技能|执行|调用|运行)",
    re.I,
)
_CONTINUATION = re.compile(
    r"^(?:继续|接着|然后呢|再来|继续做|继续执行|继续处理|继续查|继续分析|go\s+on|continue|proceed)\s*(?:啊|吧|呀|呢|一下|下去)?[。！!？?…]*$",
    re.I,
)


def classify_task(
    user_input: str,
    *,
    has_images: bool = False,
    has_references: bool = False,
) -> TaskStrategy:
    """Select a conservative execution envelope from explicit user signals.

    The order matters: external side effects and memory requests must not be
    accidentally treated as ordinary conversation.  Ambiguous requests retain
    the existing general ReAct behavior for compatibility.
    """
    text = " ".join(str(user_input or "").split())
    if not text:
        return TaskStrategy(reason="empty_input")

    if _EXTERNAL.search(text):
        return TaskStrategy(
            intent="external_side_effect",
            max_tool_rounds=6,
            reason="explicit external side effect signal",
        )
    if _MEMORY.search(text):
        return TaskStrategy(
            intent="memory_operation",
            max_tool_rounds=3,
            reason="explicit memory signal",
        )
    if _RESEARCH.search(text):
        return TaskStrategy(
            intent="research",
            max_tool_rounds=8,
            reason="research/current-information signal",
        )
    if _CODE.search(text):
        return TaskStrategy(
            intent="code_change",
            max_tool_rounds=8,
            reason="code or verification signal",
        )
    if _FILE.search(text) or has_images or has_references:
        return TaskStrategy(
            intent="file_or_artifact",
            max_tool_rounds=6,
            reason="file, artifact, or explicit reference signal",
        )
    if _PLANNING.search(text):
        return TaskStrategy(
            intent="planning",
            max_tool_rounds=4,
            reason="planning signal",
        )

    # A short continuation is still part of the previous task. Do this before
    # the conversational fast path so "继续啊" does not hide every tool and
    # turn a pending task continuation into a draft-only answer.
    if _CONTINUATION.search(text):
        return TaskStrategy(
            intent="continuation",
            max_tool_rounds=8,
            reason="explicit continuation signal",
        )

    # Short conversational turns should not expose the entire tool registry.
    if len(text) <= 80 and not _OPERATIONAL.search(text):
        return TaskStrategy(
            intent="conversation",
            max_tool_rounds=0,
            allowed_tools=frozenset(),
            reason="short non-operational turn",
        )
    return TaskStrategy(reason="no decisive signal; preserve general behavior")
