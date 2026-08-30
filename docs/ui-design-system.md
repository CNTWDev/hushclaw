# HushClaw UI Design System

The site-wide component layer is split by responsibility:

- `styles/theme-modes.css` owns the single HushClaw palette in light and dark.
- `styles/harness-shell.css` owns navigation, chat, composer, controls, and
  shared chrome.
- `styles/product-pages.css` owns the page-level header, metric, filter, card,
  row, state, and empty-state contract used by Agents, Skills, Connections,
  Memories, Tasks, Calendar, Logs, Settings, and document dialogs.
- `styles/markdown-system.css` owns content typography across every Markdown
  surface. See [Markdown design system](./markdown-design-system.md).

HushClaw uses one product design language. Historical Vector, Pearl, and Steel
theme choices are retired. Light and dark are brightness modes of the same
system, not separate themes.

## Principles

1. **Quiet canvas, clear signal.** Neutral page, canvas, surface, inset, and
   field layers carry structure. Blue is reserved for focus, selection, links,
   and the primary action.
2. **Hairlines before shadows.** Components use a one-pixel neutral outline.
   Shadows explain elevation only for cards, popovers, the composer, and modal
   windows.
3. **Compact, not cramped.** Body text is 14px. Controls are 32–36px high.
   Standard gaps follow a 4px base rhythm.
4. **Meaningful color.** Green means success, orange means warning, and red
   means error or destructive action. These colors never decorate neutral UI.
5. **One component grammar.** Cards use 10px radii, controls 8px, chips 6px,
   and windows 14px. Feature modules do not introduce new radius systems.
6. **Motion explains state.** Use 150–240ms transitions for hover, focus, open,
   and completion. Reduced-motion preferences remain authoritative.

## Core tokens

The authoritative color tokens live at the end of
`hushclaw/web/styles/theme-modes.css`. The final component and shell contract
lives in `hushclaw/web/styles/harness-shell.css`.

| Role | Dark | Light |
| --- | --- | --- |
| Page | `oklch(20.9% .004 264.477)` | `oklch(98.5% .001 286.376)` |
| Canvas | `oklch(23.1% .004 264.487)` | `oklch(100% 0 0)` |
| Surface | `oklch(26% .006 271.191)` | `oklch(96.1% .002 247.84)` |
| Line | `oklch(30.8% .006 258.354)` | `oklch(94.6% .003 264.542)` |
| Text | `oklch(96.4% .002 247.839)` | `oklch(24.7% .006 258.361)` |
| Accent | `oklch(68% .173 253.301)` | `oklch(62.6% .205 254.947)` |

## Sharing

Share images are a product surface, not a theme gallery. The image action
generates the single `hushclaw-unified` card directly. It follows the active
brightness mode while keeping the same spacing, type, border, accent, and
content hierarchy as the main interface.

## Migration

On first load after the upgrade, `hushclaw.ui.theme` is removed from browser
storage. `hushclaw.ui.mode` remains so an existing Auto, Light, or Dark choice
continues to work. No SQLite migration is required because theme choice was
never stored in the application database.
