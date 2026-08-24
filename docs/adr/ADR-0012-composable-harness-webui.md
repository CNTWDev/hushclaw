# ADR-0012: Composable harness shell and conversation workbench

Status: accepted

## Context

DeepSeek Harness demonstrates a coherent product model around three ideas:

1. durable session events are the source of truth;
2. capabilities join the runtime through explicit seams instead of patching a privileged loop;
3. the browser client is a composable three-column workbench whose sidebar and details column concede space before the conversation does.

HushClaw already has the matching kernel foundations: `event_stream()` as its primary API, the append-only `events` table, projection checkpoints, tool and skill registries, distro assembly, and an Agent OS facade. Replacing these with a second plugin runtime would duplicate mature behavior and weaken the zero-mandatory-dependency contract.

The WebUI did have a real mismatch: several site-wide override sheets competed for ownership, extensions could only add full navigation panels, and the conversation/sidebar/workbench geometry was fixed.

## Decision

### Runtime boundary

HushClaw keeps a small privileged kernel. Providers, tools, skills, connectors, agents, domains, and product shells remain replaceable at their existing seams. New features must attach to one of those seams or add a documented seam; they must not add feature-specific branches to `AgentLoop` or transport handlers.

This deliberately adapts, rather than copies, the “everything is a plugin” idea: kernel invariants stay explicit while product capabilities remain composable.

### Durable state

The current SQLite schema already contains the required event model:

- `events` is the durable replay log;
- `turns` and `sessions` are query projections used by existing clients;
- `projections` stores consumer checkpoints;
- `threads` and `runs` hold execution topology;
- `artifacts` and `uploaded_files` keep generated output separate from message payloads.

No destructive database migration is required for this upgrade. Existing data stays in place. New model-visible facts must be appended as events and projected into query tables; code must not introduce another conversation store or dual-write compatibility database.

### Web composition

The product shell owns one semantic design layer, `styles/harness-shell.css`. Feature panels continue to own their colocated styles. Removed site-wide override layers must not be reintroduced.

The chat surface is a stable three-column composition:

```text
navigation | workspaces + conversations | conversation | details
```

- The navigation rail can expand without remounting panels.
- Conversation and details widths are user-resizable and keyboard adjustable.
- Narrow screens concede the details column first, then turn conversations into a drawer.
- The composer and transcript share one readable width axis.

The WebUI plugin host exposes two contribution types:

- `registerSidePlugin(...)` for a complete product panel;
- `registerUiSlot(...)` for ordered contributions to named shell surfaces.

Every registration returns a disposer. Disposing a contribution removes its DOM and runs cleanup callbacks, giving browser extensions the same reversible-effect property as runtime extensions.

Initial named slots are:

- `rail.footer`
- `conversation.header.actions`
- `composer.leading`
- `composer.trailing`
- `workbench.panel`

## Consequences

- The interface has one source of truth for shared layout and chrome.
- Existing conversations, memories, tasks, artifacts, connectors, and runtime events require no data copy.
- Extensions can add focused UI without owning navigation or importing chat internals.
- Future shell changes must preserve slot identity and disposer behavior.
- A future database redesign is justified only by a missing invariant, not by visual or module refactoring alone.

## Verification

- Web asset tests assert one shell stylesheet and the absence of removed override layers.
- Plugin-host tests assert ordered slots and reversible registrations.
- Browser verification covers desktop, resized columns, details-panel activation, and the narrow-screen concession path.
