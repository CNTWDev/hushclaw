from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_chat_markdown_long_links_wrap_inside_message_bubbles():
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")
    react_source = (ROOT / "hushclaw" / "web" / "react-src" / "react-islands.tsx").read_text(encoding="utf-8")
    markdown_native = (ROOT / "hushclaw" / "web" / "modules" / "markdown.js").read_text(encoding="utf-8")
    markdown_preprocess = (ROOT / "hushclaw" / "web" / "shared" / "markdown-preprocess.js").read_text(encoding="utf-8")

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
    assert "white-space: normal;" in markdown_css
    assert "overflow-wrap: anywhere;" in markdown_css
    assert '[data-md-link="compact"]' in markdown_css
    assert "md-link-modal-url" in markdown_css
    assert 'pre[data-md-diagram="true"] > code' in markdown_css
    assert 'font-variant-ligatures: none;' in markdown_css
    assert 'text-wrap: nowrap;' in markdown_css
    assert 'components={{ a: CompactMarkdownLink, pre: MarkdownPre, code: MarkdownCode }}' in react_source
    assert "compactUrlLabel" in react_source
    assert 'data-md-diagram={isDiagram ? "true" : undefined}' in react_source
    assert 'const BOX_DRAWING_GLOBAL_RE' in markdown_preprocess
    assert 'const ALIGNMENT_GAP_RE' in markdown_preprocess
    assert 'function shouldFenceAsPreformattedBlock' in markdown_preprocess
    assert 'out.push("```");' in markdown_preprocess
    assert 'const isDiagram = langNorm === "box" || isBoxDrawingCodeBlock(inner);' in markdown_native
    assert 'data-md-diagram="true"' in markdown_native


def test_chat_markdown_blocks_use_softer_line_based_surfaces():
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")
    base_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")
    theme_css = (ROOT / "hushclaw" / "web" / "styles" / "theme-modes.css").read_text(encoding="utf-8")

    assert 'content: "";' in markdown_css
    assert "width: 88px;" in markdown_css
    assert "background: color-mix(in srgb, var(--md-accent) 22%, transparent);" in markdown_css
    assert "background: var(--md-section-rule);" in markdown_css
    assert "background: color-mix(in srgb, var(--md-callout-bg) 28%, transparent);" in markdown_css
    assert "background: color-mix(in srgb, var(--surface2) 18%, transparent);" in markdown_css
    assert "background: var(--md-table-head-bg);" in markdown_css
    assert "background: var(--md-code-bg);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--md-accent) 28%, transparent);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--md-table-border) 72%, transparent);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--md-code-border) 72%, transparent);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--border) 50%, transparent);" in base_css
    assert "border-bottom: 1px solid color-mix(in srgb, var(--border2) 58%, transparent);" in base_css
    assert "--md-section-rule: color-mix(in srgb, var(--md-h2-to) 30%, transparent);" in theme_css
