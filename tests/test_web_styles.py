from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_chat_markdown_long_links_wrap_inside_message_bubbles():
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")

    assert ".msg-inner {\n  display: flex;\n  align-items: flex-start;\n  gap: 8px;\n  min-width: 0;" in chat_css
    assert "flex: 1 1 auto;" in chat_css
    assert "overflow-x: hidden;" in chat_css
    assert "word-break: break-word;" in chat_css
    assert ".markdown-surface :where(p, li, blockquote, td, th, a, code)" in markdown_css
    assert '.markdown-surface button[data-streamdown="link"]' in markdown_css
    assert '.markdown-surface :is(a:not(.dl-link), button[data-streamdown="link"])' in markdown_css
    assert "appearance: none;" in markdown_css
    assert "background: transparent;" in markdown_css
    assert "padding: 0;" in markdown_css
    assert "overflow-wrap: anywhere;" in markdown_css
