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
    assert "width: 36px;" in markdown_css
    assert "background: color-mix(in srgb, var(--md-section-rule) 68%, transparent);" in markdown_css
    assert "background: color-mix(in srgb, var(--md-callout-bg) 12%, transparent);" in markdown_css
    assert "background: color-mix(in srgb, var(--surface2) 9%, transparent);" in markdown_css
    assert "background: var(--md-table-head-bg);" in markdown_css
    assert "background: var(--md-code-bg);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--md-accent) 14%, transparent);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--md-table-border) 42%, transparent);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--md-code-border) 42%, transparent);" in markdown_css
    assert "border-left: 1px solid color-mix(in srgb, var(--border) 50%, transparent);" in base_css
    assert "border-bottom: 1px solid color-mix(in srgb, var(--border2) 58%, transparent);" in base_css
    assert "--md-section-rule: color-mix(in srgb, var(--md-h2-to) 22%, transparent);" in theme_css
    assert "--md-section-rule: color-mix(in srgb, var(--md-h2-to) 24%, transparent);" in theme_css


def test_chat_markdown_headings_use_single_rule_hierarchy():
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")

    assert "border-bottom: 0;" in markdown_css
    assert ".markdown-surface-rich h1::after" in markdown_css
    assert "height: 1px;" in markdown_css
    assert ".markdown-surface-rich h2::after" not in markdown_css
    assert ".markdown-surface-rich h3::after" not in markdown_css
    assert ':root[data-theme="vector"] .markdown-surface-rich h1::after' in markdown_css
    assert ':root[data-theme="vector"] .markdown-surface-rich h2::after' not in markdown_css


def test_chat_markdown_hr_is_weaker_and_avoids_heading_double_rules():
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")

    assert "background: color-mix(in srgb, var(--md-section-rule) 30%, transparent);" in markdown_css
    assert "opacity: 0.34;" in markdown_css
    assert ".markdown-surface :where(h1, h2) + hr," in markdown_css
    assert ".markdown-surface hr + :where(h1, h2) {" in markdown_css


def test_chat_markdown_longform_reading_density_is_tighter():
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")

    assert "--md-body-leading: 1.68;" in markdown_css
    assert "--md-list-leading: 1.6;" in markdown_css
    assert "--md-gap-md: 13px;" in markdown_css
    assert "--md-measure: 74ch;" in markdown_css
    assert "max-width: min(100%, calc(var(--md-measure) + 4ch));" in markdown_css
    assert "color: color-mix(in srgb, var(--md-accent) 44%, var(--text));" in markdown_css
    assert "margin-top: calc(var(--md-gap-sm) - 1px);" in markdown_css
    assert "margin-bottom: calc(var(--md-gap-sm) - 1px);" in markdown_css


def test_chat_markdown_inline_code_and_tables_are_quieter_for_longform_reading():
    markdown_css = (ROOT / "hushclaw" / "web" / "styles" / "markdown-tight.css").read_text(encoding="utf-8")

    assert "padding: 1px 4px;" in markdown_css
    assert "border: 1px solid color-mix(in srgb, var(--md-inline-code-border) 74%, var(--border));" in markdown_css
    assert "background: color-mix(in srgb, var(--md-inline-code-bg) 70%, var(--surface2) 30%);" in markdown_css
    assert "color: color-mix(in srgb, var(--md-inline-code-color) 72%, var(--text));" in markdown_css
    assert "padding: 7px 10px;" in markdown_css
    assert "font: 740 11px/1.45 var(--sans);" in markdown_css
    assert "background: color-mix(in srgb, var(--md-table-row-alt) 78%, transparent);" in markdown_css


def test_share_card_uses_single_primary_datetime_and_light_footer_branding():
    export_js = (ROOT / "hushclaw" / "web" / "modules" / "chat" / "export.js").read_text(encoding="utf-8")
    share_css = (ROOT / "hushclaw" / "web" / "styles" / "share-card.css").read_text(encoding="utf-8")

    assert '<span>${escHtml(templateMeta[2])}</span>' in export_js
    assert 'datetime.split(" ")[0] || datetime' not in export_js
    assert 'const fDatetime = _mk("span", "cimg-footer-datetime", datetime);' not in export_js
    assert "fRightInner.appendChild(fBrand);" in export_js
    assert "opacity: 0.52;" in share_css
    assert ".cimg-footer-brand {" in share_css
    assert ".cimg-footer-datetime {" not in share_css


def test_share_card_background_is_paper_like_without_top_to_bottom_wash():
    share_css = (ROOT / "hushclaw" / "web" / "styles" / "share-card.css").read_text(encoding="utf-8")

    assert "background: color-mix(in srgb, var(--ci-bg) 90%, var(--ci-bg-soft) 10%);" in share_css
    assert ".cimg-card::before {\n  content: \"\";\n  display: none;" in share_css
    assert "linear-gradient(180deg, rgba(255, 255, 255, 0.028), transparent 24%)" not in share_css
    assert "linear-gradient(180deg, rgba(255, 255, 255, 0.07), transparent 28%)" not in share_css
    assert "linear-gradient(180deg, rgba(255, 255, 255, 0.025), transparent 24%)" not in share_css


def test_connections_panel_unifies_apps_channels_and_sync_sources():
    panel_js = (ROOT / "hushclaw" / "web" / "modules" / "panels" / "app_connectors.js").read_text(encoding="utf-8")
    index_html = (ROOT / "hushclaw" / "web" / "index.html").read_text(encoding="utf-8")
    panel_css = (ROOT / "hushclaw" / "web" / "styles" / "panels-app-connectors.css").read_text(encoding="utf-8")

    assert 'const CONNECTION_KIND_ORDER = ["app", "channel", "sync_source"];' in panel_js
    assert 'const CONNECTION_KIND_LABELS = {' in panel_js
    assert 'Manage apps, channels, and sync sources from one directory.' in panel_js
    assert '_renderConnectionDetailsModal(item)' in panel_js
    assert 'const CHANNEL_PROVIDER_IDS = new Set(CHANNELS.map((channel) => channel.id));' in panel_js
    assert 'function _isChannelConnection(item) {' in panel_js
    assert '_renderChannelConfigModal(item)' in panel_js
    assert '_saveChannelConfig(provider)' in panel_js
    assert 'wizard.tab = "integrations";' in panel_js
    assert 'No Settings or Wizard hand-off is required.' in panel_js
    assert 'title: `${isAppPanel || _isChannelConnection(item) ? "Configure" : "View"} ${item.name}`' in panel_js
    assert 'Open Integrations' in panel_js
    assert '<span>Connections</span>' in index_html
    assert 'data-desc="Manage apps, channels, and sync sources"' in index_html
    assert '.app-connector-kind-chip {' in panel_css
    assert '.app-connector-card-telegram {' in panel_css
    assert '.app-connector-card-email {' in panel_css
