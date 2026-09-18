# HushClaw UI Design System

The site-wide component layer is split by responsibility:

- `styles/theme-modes.css` owns the single HushClaw palette in light and dark.
- `styles/ui-foundations.css` is the sole owner of product font stacks, type
  scale, weights and shared control tokens. Shell/feature styles consume them.
- `styles/harness-shell.css` owns navigation, chat, composer, controls, and
  shared chrome.
- `styles/product-pages.css` owns the page-level header, metric, filter, card,
  row, state, and empty-state contract used by Agents, Skills, Connections,
  Memories, Tasks, Calendar, Logs, Settings, and document dialogs.
- `styles/markdown-system.css` owns content typography across every Markdown
  surface. See [Markdown design system](./markdown-design-system.md).
- `styles/ai-primitives.css` is the final AI interaction contract. It owns
  state-driven motion and the seven reusable controls below; feature styles
  must not override their state semantics.

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
3. **Compact, not cramped.** Reading text is 13.5px; scan labels are smaller and
   stronger, not a copy of the prose style. Standard controls are 32px,
   compact controls and segmented tabs 28px, inline icon actions 24px.
   Coarse pointers expand inline actions to 32px; mobile standard controls are 36px.
   Standard gaps follow a 4px base rhythm.
4. **Meaningful color.** Green means success, orange means warning, and red
   means error or destructive action. These colors never decorate neutral UI.
5. **One component grammar.** Cards use 10px radii, controls 8px, chips 6px,
   and windows 14px. Feature modules do not introduce new radius systems.
6. **Motion explains state.** Use 150–240ms transitions for hover, focus, open,
   and completion. Reduced-motion preferences remain authoritative.

## AI interaction contract

Every AI operation maps backend-specific values onto the same public states:

`idle → queued → running → waiting_user → streaming → completed | failed | cancelled`

The browser stores the normalized state in `data-ai-state`. Features may
choose user-facing copy, but they must not invent parallel colors, spinners,
or completion semantics. Raw tool names and payloads belong in the Runtime
monitor; the default conversation shows a human-readable summary first.

The component set is intentionally small:

| Primitive | Responsibility |
| --- | --- |
| `AgentActivity` | One compact active-work signal with elapsed time |
| `ProcessDisclosure` | Collapsed reasoning/tool summary and optional details |
| `StreamingMessage` | Stable incremental answer rendering |
| `PromptComposer` | Files, commands, skills, agents, and sending |
| `ApprovalCard` | One explicit decision with one dominant action |
| `TaskRow` | Background work using the shared state language |
| `ContextCard` | Evidence and sources adjacent to supported content |

Object-local generative actions are an interaction behavior rather than an
eighth visual primitive. Selecting assistant text may expose Explain, Improve,
and Shorten actions beside the selection; the action prepares a prompt and
keeps the user in control.

### Motion levels

| Level | Duration | Use |
| --- | --- | --- |
| Instant | 120ms | Press and direct manipulation |
| Fast | 160ms | Hover, focus, selection |
| Standard | 240ms | Menus, source cards, state changes |
| Reveal | 380ms | Process expansion and progressive disclosure |

Continuous motion is allowed only while an operation is active. A settled
component must become still. `prefers-reduced-motion` disables every loop and
nonessential transition.

The executable reference is available at `/ui-lab.html`; it is also linked
from System → Developer Mode. New AI-facing controls should be demonstrated
there in light and dark mode before shipping.

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

## Conversation density

New installations start with a narrow icon rail and a single text Threads column.
The expanded app rail is 192px and Threads defaults to 260px; saved user-resized
column widths remain authoritative. Workspace tabs and the conversation header
share a 44px chrome height. Threads has one header for its title, compact New
action, refresh and collapse controls, followed by a 28px search field. Ordinary
session rows are 46px, with extra space only for genuine runtime activity; no
separator between every row. The New action is only in Threads (also accessible
in the mobile drawer), not repeated in the composer. Source, stop and send use
centered square 32px controls (36px on coarse pointers). The empty composer is
about 90px high and still grows for multiline input.

Messages and the composer share their reading width and horizontal gutter.
Assistant identity remains a quiet text label; consecutive assistant messages
do not repeat it. User alignment and bubble treatment identify user turns.
Repeated avatar logos and the redundant You label are omitted; timestamps and
keyboard-accessible message actions remain.

## Typography

Use the native system stack, without a network font dependency: SF/PingFang SC
on macOS, Segoe UI/Microsoft YaHei UI on Windows, platform sans fallback on
Linux. We do not claim identical glyphs across operating systems. Do not declare
unbundled Inter or Plus Jakarta Sans as if they were installed product fonts.

| Role | Size / line height | Weight |
| --- | --- | --- |
| Page heading | 18 / 26px | 600 |
| Content section heading | 16 / 24px | 600 |
| Chat prose | 13.5 / 22px | 400, full text contrast |
| Composer text | 13 / 20px | 400 |
| Navigation, session/file title | 12 / 18px | 500; selected 600 |
| Controls and filter labels | 12 / 18px | 500 |
| Metadata and timestamps | 11 / 16px | 400 |

Consistency means a shared role hierarchy, not identical typography everywhere.
Lists need smaller, stronger scan labels; prose needs a distinct reading rhythm.
Do not enlarge metadata to the control size or make all session titles regular
weight: that flattens the hierarchy and makes the shell compete with content.

Only 400, 500 and 600 weights are used by the product contract. Chinese and
mixed-language UI use normal tracking; no compressed negative letter spacing.
Monospace is for code, not prose or ordinary timestamps. Tabular numerals are
opt-in for aligned metrics, not every field or Markdown paragraph. Export/print
surfaces may keep their own document sizing. Compactness comes from 4/8/12/16px
spacing, fewer repeated labels and restrained surfaces, not miniature text.

Regression checks: `python3 -m pytest -q`, plus the fixture-only Chrome scripts
`tests/browser/check_chat_density.py`, `check_chat_progress.py` and
`check_files_layout.py` for light/dark, narrow rails, mobile, keyboard actions,
confirmation, reduced motion and streaming behavior. These scripts use mock data,
never the user's conversations or files.

## Files and live progress

The right rail prioritizes Files. File rows use intrinsic heights (about 58px
on desktop), a single title and metadata line, and the shared color/control
tokens. Rows must not stretch to fill an empty list. Search, source, rating and
tag filters remain available. Attach, tag editing and delete use the shared
`ui-icon-action` primitive, visible without hovering in a reserved, in-flow
column. Names ellipsize with full-name tooltips; list tracks must use
`minmax(0, 1fr)` so unbroken filenames cannot displace actions. The second
metadata line spans below the actions to retain room for ratings and tags. Activity
and Recent results no longer occupy this rail. The runtime monitor is opt-in.

All file deletion uses `openConfirm` with destructive styling and explicit
local-file wording. Cancel, Escape and backdrop dismissal never send deletion.
Rapidly reopening a dialog must cancel the previous close animation callback.
After confirmation, the server removes the registered local file and its
unshared knowledge index. Shared uploads retain a separate managed copy for
the remaining entries. Schema v8 records generated file locations independently
of content hashes, so identical outputs cannot redirect one another's deletion.
Ambiguous legacy generated paths are rejected rather than guessed.

During a run, the shared AgentActivity component keeps “Thinking” stable and
shows one short, event-derived phase beside it. Only a changed phase animates
(240ms upward transition: old step exits, new step enters); elapsed-time ticks
never restart the animation. Keep the same activity node across tools and rounds,
and restore friendly labels from `active_step.meta` on reconnect. Tool
arguments, raw results and internal reasoning are not status copy. Reduced
motion disables phase transitions. This is an activity signal, not a claimed
percentage of completion.

The streaming cursor follows the final rendered glyph, never the author/avatar.
A frame-coalesced content/resize observer handles native and React Markdown
without changing their DOM. Completion, cancellation and session switches remove
the cursor and disconnect observers. Reduced motion uses a steady cursor.

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
