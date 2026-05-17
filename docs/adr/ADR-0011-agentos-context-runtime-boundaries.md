# ADR-0011: AgentOS Context Runtime Boundaries

## Status

Accepted

## Context

Recent AgentOS work added several kernel-level seams:

- prompt blocks for kernel/distro/domain prompt composition
- skill progressive disclosure
- tool output budgeting with artifact offload
- session recall separate from long-term memory recall
- context assembly tracing
- domain manifest validation

These are useful, but they also introduce a project governance risk: future
changes could keep adding logic to `ContextAssembler`, `ToolExecutor`, or distro
classes without clear ownership. This ADR records the boundary rules so the
runtime remains maintainable.

## Decision

### Prompt Blocks

`PromptBlockRegistry` is the only supported way for distro/domain code to add
stable prompt content.

Allowed:
- kernel default prompt blocks
- distro prompt blocks such as enterprise boundary instructions
- domain prompt blocks returned through a distro-mediated domain runtime

Not allowed:
- importing CRM/HR/Finance modules from AgentOS kernel code
- patching prompt constants at runtime
- putting business-domain instructions directly into `ContextAssembler`

### Skills

Skills use progressive disclosure.

The system prompt may include a compact skill index. Full `SKILL.md` content is
loaded only through `use_skill` or its alias `skill_view`.

Do not add full skill bodies to the stable system prompt. Do not convert memory
notes directly into skills. Memory and skills are different runtime assets.

### Tool Output Budget

Large tool outputs must not be pushed directly into model context.

`ToolExecutor` applies `ToolOutputBudget` after every tool call:
- small output stays inline
- large output is stored in `MemoryStore.artifacts` when available
- the model receives a preview plus an artifact id
- full content can be retrieved through `read_artifact`

Do not add new ad hoc truncation logic inside individual tools unless the tool
has domain-specific summarization semantics. Generic context protection belongs
in `runtime/tool_output_budget.py`.

### Memory vs Session Recall

Long-term semantic memory and session history are separate:

- `memory.recall_with_budget()` retrieves durable semantic memory
- `SessionRecall` retrieves prior conversation/task evidence
- both are injected as reference material, not active instructions

Do not use long-term memory as an action log. Do not store completed task logs
as durable user memory just to recover previous context.

### Context Trace

`ContextTrace` records what was injected during the latest context assembly:

- source
- tier
- hit/miss
- character count
- budget
- elapsed time
- small metadata

Trace data is observability. It must not influence prompt behavior, tool policy,
or memory ranking.

### Domain Manifests

Domain packages are installable business capability units. AgentOS validates
their manifest shape before registration, but it must not validate
business-specific semantics.

Allowed:
- stable `id` and `name`
- generic `module_type` and `status` values
- declared datasets, workflows, policies, and UI facets with stable `id`
- tool and agent names as strings

Not allowed:
- registering duplicate domain ids
- adding CRM/HR/Finance-specific validation to the kernel
- letting a domain runtime enter the registry with a malformed manifest

Domain packages should keep declarative assets separate from runtime wiring:

```
domain/package.py   manifest, datasets, workflows, policies, agent definitions
domain/runtime.py   bind storage, expose tools, list records/events, execute actions
domain/store.py     domain-owned data access
domain/tools.py     domain-owned tool functions
```

This layout keeps the runtime thin and makes future dynamic install/load work
possible without requiring AgentOS to inspect domain internals.

## Governance Rules

1. Before adding a new context source, decide whether it is:
   - stable prompt content (`PromptBlock`)
   - dynamic turn context (`ContextAssembler`)
   - long-term semantic memory (`MemoryStore`)
   - prior-session evidence (`SessionRecall`)
   - procedural knowledge (`SkillRegistry`)

2. Prefer closing an existing half-built loop over adding a new capability.
   Example: `ToolOutputBudget` required `read_artifact` to complete the loop.

3. Keep personal mode first-class. Enterprise/domain extensions must degrade to
   empty/no-op behavior when not installed.

4. Do not create a broad registry or framework layer until at least two concrete
   call sites need it. The current `ContextAssembler` can stay explicit while
   the source list is small and readable.

5. When a new seam is added, update this ADR or the relevant boundary ADR in the
   same change.

## Consequences

Positive:
- The AgentOS kernel can evolve without importing business domains.
- Personal mode remains local-first and easy to debug.
- Large tool results and recalled context are governed centrally.
- Future contributors have clear places to add or remove logic.

Trade-offs:
- `ContextAssembler` remains somewhat explicit instead of fully registry-driven.
- Some small no-op methods exist on distro/domain protocols to preserve a stable
  contract.
- Developers must keep ADRs updated when adding new extension surfaces.
