from hushclaw.loop import AgentLoop
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
    assert "未确认完成" in sanitized


def test_shell_rejects_unmanaged_background_processes():
    result = __import__("asyncio").run(run_shell("python worker.py &"))

    assert result.is_error
    assert "Unmanaged background process blocked" in result.content
