# HushClaw Markdown design system

Markdown is a product content primitive, not a chat-only feature. Chat replies,
Markdown file previews, print/PDF exports, share images, forum posts, and tool
output all use the same semantic renderer and the same typography tokens.

## Architecture

- `web/modules/markdown.js` is the public rendering API. Consumers call
  `setMarkdownContent(container, raw, { surface })`; they do not assign local
  Markdown element styles.
- `web/react-src/react-islands.tsx` is the enhanced renderer. The native
  renderer in `markdown.js` remains the safe fallback; both emit the same
  semantic element contract.
- `web/styles/markdown-tight.css` defines element primitives such as headings,
  paragraphs, lists, quotes, tables, code, links, and diagrams.
- `web/styles/markdown-system.css` defines product typography and the supported
  surface densities. It is the only place a consumer-specific reading measure
  may change.

## Supported surfaces

| Surface | Use | Density |
| --- | --- | --- |
| `chat` | Assistant and user messages | Reading |
| `file` | Double-click Markdown document preview | Document |
| `share` | Unified share image | Export |
| `print` | Print and PDF output | Export |
| `forum` | Community posts | Reading |
| `tool` | Compact tool and runtime output | Compact |

Surfaces may tune font size, line height, spacing, and maximum reading width.
They may not define a different brand palette, card language, or theme.

## Content rules

- Body copy uses the product sans-serif stack; code and diagrams use the
  product monospace stack.
- Heading hierarchy is quiet and typographic. Only the first-level heading has
  a short blue signal rule.
- Quotes, tables, and code blocks use flat neutral layers and hairline borders.
- Links use the product blue and retain an underline. External links continue
  to use the existing safety prompt.
- Long URLs, wide tables, code, and box-drawing diagrams must stay inside their
  container and scroll horizontally where preserving alignment is necessary.
- Print uses the same surface contract with deterministic light tokens for
  paper legibility.

## Adding a Markdown consumer

1. Render through `setMarkdownContent`.
2. Choose one of the supported surfaces; do not create a local theme.
3. Add a new surface only when the reading context requires a materially
   different density, and register it in `MARKDOWN_SURFACES`.
4. Verify the native and React renderers, light and dark app modes, narrow
   widths, and print output when applicable.
