from __future__ import annotations

from hushclaw.rich_content import parse_rich_content, render_channel_message


def test_parse_rich_content_preserves_structured_blocks():
    doc = parse_rich_content(
        "# Title\n\n"
        "Paragraph line 1\nParagraph line 2\n\n"
        "- one\n- two\n\n"
        "> note\n> more\n\n"
        "```python\nprint('x')\n```"
    )
    assert [block.kind for block in doc.blocks] == [
        "heading",
        "paragraph",
        "list",
        "quote",
        "code_block",
    ]
    assert doc.blocks[0].level == 1
    assert doc.blocks[2].items == ("one", "two")
    assert doc.blocks[4].lang == "python"


def test_render_channel_message_telegram_prefers_html_but_keeps_plain_fallback():
    rendered = render_channel_message(
        "telegram",
        "# Title\n\nUse **bold** and [docs](https://example.com).",
        prefer_rich=True,
    )
    assert rendered.format == "telegram_html"
    assert "<b>Title</b>" in rendered.body
    assert '<a href="https://example.com">docs</a>' in rendered.body
    assert "docs (https://example.com)" in rendered.plain_text


def test_render_channel_message_slack_and_plain_channels_diverge():
    slack = render_channel_message("slack", "Use **bold**", prefer_rich=True)
    feishu = render_channel_message("feishu", "Use **bold**", prefer_rich=True)
    assert slack.format == "slack_mrkdwn"
    assert slack.body == "Use **bold**"
    assert feishu.format == "plain"
    assert feishu.body == "Use bold"
