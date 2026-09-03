import asyncio
import tempfile
from pathlib import Path

from hushclaw.config.schema import AgentConfig, Config, MemoryConfig, ToolsConfig
from hushclaw.loop import AgentLoop
from hushclaw.memory.store import MemoryStore
from hushclaw.providers.base import LLMResponse
from hushclaw.runtime.threat_patterns import unwrap_untrusted_context, wrap_untrusted_context
from hushclaw.tools.registry import ToolRegistry
from hushclaw.tools.builtins.shell_tools import run_shell


def test_background_claim_requires_runtime_tracking():
    text = "评论已抓取 71/234 条，剩余部分正在后台继续。"

    assert AgentLoop._claims_untracked_background_work(text)
    assert not AgentLoop._has_tracked_background_work(AgentLoop.__new__(AgentLoop))


def test_untracked_background_claim_is_sanitized_without_losing_progress():
    text = "抓取完成 338 条视频，评论已抓取 71/234 条（剩余部分正在后台继续）。"

    sanitized = AgentLoop._sanitize_untracked_background_claim(text)

    assert "338 条视频" in sanitized
    assert "71/234" in sanitized
    assert "后台继续" not in sanitized
    assert "剩余部分尚未执行，也不会自动继续" in sanitized
    assert sanitized.count(AgentLoop._UNTRACKED_BACKGROUND_NOTICE) == 1


def test_background_claim_sanitizer_is_idempotent():
    text = "我会在后台继续整理，完成后通知你。"

    once = AgentLoop._sanitize_untracked_background_claim(text)
    twice = AgentLoop._sanitize_untracked_background_claim(once)

    assert twice == once
    assert once.count(AgentLoop._UNTRACKED_BACKGROUND_NOTICE) == 1


def test_background_guard_ignores_product_and_negative_statements():
    assert not AgentLoop._claims_untracked_background_work(
        "后台服务完成请求后返回结果，客户端负责渲染。"
    )
    assert not AgentLoop._claims_untracked_background_work(
        "我不会在后台继续处理，也不会稍后返回结果。"
    )
    assert not AgentLoop._claims_untracked_background_work(
        AgentLoop._UNTRACKED_BACKGROUND_NOTICE
    )


def test_background_guard_detects_explicit_first_person_promise():
    assert AgentLoop._claims_untracked_background_work(
        "正文已经完成一半，我会在后台继续整理，稍后通知你。"
    )


def test_background_guard_keeps_full_answer_without_retrying_provider():
    class _Provider:
        stream_complete = None

        def __init__(self):
            self.calls = 0

        async def complete(self, **_kwargs):
            self.calls += 1
            return LLMResponse(
                content="# 完整提案\n核心正文保留。剩余部分正在后台继续。",
                stop_reason="end_turn",
                input_tokens=10,
                output_tokens=20,
            )

    async def _run():
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            memory = MemoryStore(data_dir)
            provider = _Provider()
            config = Config(
                agent=AgentConfig(model="test-model", stream_mode="off"),
                memory=MemoryConfig(data_dir=data_dir),
                tools=ToolsConfig(enabled=[]),
            )
            loop = AgentLoop(
                config,
                provider,
                memory,
                ToolRegistry(),
                session_id="s-background-guard",
            )
            try:
                events = [event async for event in loop.event_stream("请写提案")]
                persisted = memory.load_session_history("s-background-guard")
            finally:
                memory.close()
            return provider.calls, events, persisted, loop._context

    calls, events, persisted, context = asyncio.run(_run())

    final_text = events[-1]["text"]
    assert calls == 1
    assert "# 完整提案" in final_text
    assert "核心正文保留" in final_text
    assert "后台继续" not in final_text
    assert events[-1]["rounds_used"] == 0
    assert events[-1]["perf"]["background_claim_sanitized"] == 1
    assert persisted[-1]["content"] == final_text
    assert [message.role for message in context] == ["user", "assistant"]


def test_shell_rejects_unmanaged_background_processes():
    result = __import__("asyncio").run(run_shell("python worker.py &"))

    assert result.is_error
    assert "Unmanaged background process blocked" in result.content


def test_tool_result_display_unwraps_security_boundary_and_keeps_suffix():
    wrapped, _scan = wrap_untrusted_context(
        "Written 10 chars\nDownload: /files/file-1",
        source="tool:write_file",
        kind="tool_result",
    )
    result = f"{wrapped}\nVerification failed (missing: report.md)."

    assert unwrap_untrusted_context(result) == (
        "Written 10 chars\nDownload: /files/file-1\n"
        "Verification failed (missing: report.md)."
    )
