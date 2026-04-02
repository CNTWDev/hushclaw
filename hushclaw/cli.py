"""HushClaw CLI: argparse REPL + subcommands."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Lazy imports — keep startup time minimal
# ---------------------------------------------------------------------------

def _make_agent():
    from hushclaw.agent import Agent
    return Agent()


# ---------------------------------------------------------------------------
# Thinking indicator (async spinner shown while waiting for LLM / tool)
# ---------------------------------------------------------------------------

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


async def _spin(label: str) -> None:
    """Animated spinner that keeps printing until cancelled."""
    i = 0
    t0 = time.monotonic()
    try:
        while True:
            elapsed = time.monotonic() - t0
            ch = _SPINNER[i % len(_SPINNER)]
            print(f"\r  {ch} {label} {elapsed:.0f}s ", end="", flush=True)
            i += 1
            await asyncio.sleep(0.12)
    except asyncio.CancelledError:
        print("\r" + " " * 36 + "\r", end="", flush=True)


async def _stop_spinner(task: "asyncio.Task | None") -> None:
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Shell confirmation (injected as _confirm_fn into run_shell)
# ---------------------------------------------------------------------------

def _make_shell_confirm() -> "callable":
    """Return a sync confirmation function for run_shell."""
    def _confirm(command: str) -> bool:
        print(f"\n  [run_shell] $ {command}")
        try:
            ans = input("  Allow? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        return ans == "y"
    return _confirm


# ---------------------------------------------------------------------------
# REPL event helpers
# ---------------------------------------------------------------------------

def _fmt_tool_input(inp: dict) -> str:
    parts = []
    for k, v in inp.items():
        sv = str(v)
        if len(sv) > 60:
            sv = sv[:57] + "..."
        parts.append(f'{k}="{sv}"' if isinstance(v, str) else f"{k}={sv}")
    return ", ".join(parts)


def _cost_str(in_tok: int, out_tok: int, config) -> str:
    ci = config.provider.cost_per_1k_input_tokens
    co = config.provider.cost_per_1k_output_tokens
    if not ci and not co:
        return ""
    cost = (in_tok / 1000 * ci) + (out_tok / 1000 * co)
    return f"  ~${cost:.4f}"


async def _run_events(loop_obj, user_input: str, config):
    """
    Consume event_stream(), print tool calls/results inline with a
    thinking spinner. Returns (response_text, input_tokens, output_tokens).
    """
    response_text = ""
    in_tok = out_tok = 0
    spinner: asyncio.Task | None = asyncio.create_task(_spin("thinking"))

    async for event in loop_obj.event_stream(user_input):
        etype = event["type"]

        # Every event means the LLM (or tool) responded — stop the spinner
        await _stop_spinner(spinner)
        spinner = None

        if etype == "tool_call":
            inp_str = _fmt_tool_input(event.get("input", {}))
            print(f"  [→ {event['tool']}({inp_str})]", flush=True)
            spinner = asyncio.create_task(_spin("executing"))

        elif etype == "tool_result":
            await _stop_spinner(spinner)
            spinner = None
            result = str(event.get("result", ""))
            if len(result) > 120:
                result = result[:117] + "..."
            is_err = "[Error]" in result or result.startswith("Error")
            prefix = "✗" if is_err else "✓"
            print(f"  [{prefix} {result}]", flush=True)
            # Restart spinner while waiting for next LLM round
            spinner = asyncio.create_task(_spin("thinking"))

        elif etype == "compaction":
            archived = event.get("archived", "?")
            kept = event.get("kept", "?")
            print(
                f"\n  [Context compacted: {archived} turns archived"
                f" → {kept} recent turns kept]\n",
                flush=True,
            )

        elif etype == "done":
            response_text = event.get("text", "")
            in_tok = event.get("input_tokens", 0)
            out_tok = event.get("output_tokens", 0)

    await _stop_spinner(spinner)
    return response_text, in_tok, out_tok


def _provider_env_var(provider_name: str) -> str:
    """Return the canonical env-var name for the given provider."""
    n = provider_name.lower()
    if "gemini" in n or "google" in n:
        return "GEMINI_API_KEY"
    if "minimax" in n:
        return "MINIMAX_API_KEY"
    if "openai" in n:
        return "OPENAI_API_KEY"
    if "aigocode" in n:
        return "AIGOCODE_API_KEY"
    return "ANTHROPIC_API_KEY"


def _current_provider_name() -> str:
    """Best-effort: read provider name from config without raising."""
    try:
        from hushclaw.config.loader import load_config
        return load_config().provider.name
    except Exception:
        return "anthropic-raw"


def _classify_error(e: Exception, provider_name: str = "") -> str:
    msg = str(e)
    msg_l = msg.lower()
    pname = provider_name or _current_provider_name()
    env_var = _provider_env_var(pname)
    if "401" in msg or "unauthorized" in msg or "api key" in msg_l or "api_key" in msg_l:
        return f"[API Error] 401 Unauthorized — check your {env_var}\n  detail: {msg}"
    if "429" in msg or "rate limit" in msg_l:
        return f"[API Error] 429 Rate Limited — please wait before retrying\n  detail: {msg}"
    if any(c in msg for c in ("500", "502", "503")):
        return f"[API Error] Server error — the API may be temporarily unavailable\n  detail: {msg}"
    if "timeout" in msg_l or "timed out" in msg_l:
        return f"[Network Error] Request timed out — check your connection\n  detail: {msg}"
    if "connection" in msg_l or "network" in msg_l or "socket" in msg_l:
        return f"[Network Error] Connection failed — check your internet connection\n  detail: {msg}"
    if "permission denied" in msg_l or "tool" in msg_l:
        return f"[Tool Error] {msg}"
    return f"[Error] {msg}"


def _handle_agent_init_error(e: Exception) -> None:
    """Print a helpful error message when agent initialization fails."""
    msg = str(e)
    msg_l = msg.lower()
    if "api key" in msg_l or "api_key" in msg_l or "not found" in msg_l and "key" in msg_l:
        pname = _current_provider_name()
        env_var = _provider_env_var(pname)
        print(
            f"\n[Setup Required] {pname} API key not found.\n"
            f"\n  Option 1 (recommended): hushclaw init"
            f"\n  Option 2: export {env_var}=<your-key>"
            f"\n  Option 3: add to config file — hushclaw config path\n",
            file=sys.stderr,
        )
    else:
        print(f"Failed to initialize agent: {e}", file=sys.stderr)


def _parse_tags(raw) -> list[str]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, list) else []
        except (ValueError, TypeError):
            return []
    return []


# ---------------------------------------------------------------------------
# Skill direct_tool dispatch
# ---------------------------------------------------------------------------

def _dispatch_direct_tool(loop_obj, user_input: str) -> bool:
    """If the user types /<skill-name> and the skill has a direct_tool configured,
    execute that tool directly (no LLM round-trip) and print the result.

    Returns True if the command was handled (caller should ``continue``).
    """
    skill_registry = getattr(loop_obj, "_skill_registry", None)
    # Also check executor context (set by agent during loop creation)
    if skill_registry is None:
        skill_registry = loop_obj.executor.get_context_value("_skill_registry")
    if skill_registry is None:
        return False

    cmd = user_input.lstrip("/").split()[0].lower()
    skill = skill_registry.get(cmd)
    if skill is None or not skill.get("direct_tool"):
        return False

    if not skill.get("available", True):
        print(
            f"\n  [Skill unavailable] '{cmd}': {skill.get('reason', 'requirements not met')}\n",
            file=sys.stderr,
        )
        return True

    tool_name = skill["direct_tool"]
    tool_def = loop_obj.registry.get(tool_name)
    if tool_def is None:
        print(
            f"\n  [Error] Skill '{cmd}' references tool '{tool_name}' which is not registered.\n",
            file=sys.stderr,
        )
        return True

    try:
        result = asyncio.run(loop_obj.executor.execute_single(tool_name, {}))
        print(f"\nhushclaw> {result}\n")
    except Exception as e:
        print(f"\n  [Tool Error] {e}\n", file=sys.stderr)
    return True


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

def _repl(agent, session_id: str | None = None) -> None:
    """Interactive REPL with readline history."""
    try:
        import readline
        history_file = agent.config.memory.data_dir / ".hushclaw_history"
        try:
            readline.read_history_file(str(history_file))
        except FileNotFoundError:
            pass
        import atexit
        atexit.register(readline.write_history_file, str(history_file))
    except ImportError:
        pass

    from hushclaw.gateway import Gateway
    gateway = Gateway(agent.config, agent)

    # --- Session continuity: offer to resume last session ---
    if session_id is None:
        sessions = agent.list_sessions()
        if sessions:
            last = sessions[0]
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(last["last_turn"]))
            turns = last["turn_count"]
            try:
                ans = input(
                    f"Resume last session {last['session_id'][:12]}..."
                    f" ({ts}, {turns} turns)? [Y/n] "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if ans in ("", "y"):
                session_id = last["session_id"]

    loop_obj = agent.new_loop(session_id, gateway=gateway)

    # Inject shell confirmation hook so run_shell asks before executing
    loop_obj.executor.set_context(_confirm_fn=_make_shell_confirm())

    print(f"\nHushClaw  (session: {loop_obj.session_id[:12]}...)  /help for commands\n")

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        # ---- REPL slash commands ----

        if user_input in ("/exit", "/quit"):
            print("Bye!")
            break

        if user_input == "/new":
            from hushclaw.util.ids import make_id
            loop_obj = agent.new_loop(make_id("s-"), gateway=gateway)
            loop_obj.executor.set_context(_confirm_fn=_make_shell_confirm())
            print(f"New session: {loop_obj.session_id[:12]}...")
            continue

        if user_input.startswith("/remember "):
            content = user_input[len("/remember "):].strip()
            nid = agent.remember(content)
            print(f"Saved: {nid[:8]}")
            continue

        if user_input.startswith("/search "):
            q = user_input[len("/search "):].strip()
            results = agent.search(q)
            if not results:
                print("  No results.")
            for r in results:
                print(f"  [{r['note_id'][:8]}] {r['title'][:60]} (score={r['score']:.2f})")
            continue

        if user_input.startswith("/memories"):
            # /memories [--auto] [--all]
            show_auto = "--auto" in user_input
            show_all = "--all" in user_input
            tag_filter = "_auto_extract" if show_auto else None
            notes = agent.list_memories(limit=20, tag=tag_filter)
            if not notes:
                print("  No memories stored.")
                continue
            shown = 0
            for n in notes:
                tags = _parse_tags(n.get("tags"))
                is_auto = "_auto_extract" in tags
                if not show_all and not show_auto and is_auto:
                    continue  # skip auto-extracted noise by default
                tag_disp = ""
                visible_tags = [t for t in tags if not t.startswith("_")]
                if visible_tags:
                    tag_disp = f" [{', '.join(visible_tags)}]"
                elif is_auto:
                    tag_disp = " [auto]"
                body = (n.get("body") or "")[:80].replace("\n", " ")
                print(f"  [{n['note_id'][:8]}] {n['title'][:50]}{tag_disp}")
                if body:
                    print(f"           {body}")
                shown += 1
            if shown == 0:
                print("  No user-saved memories. Use /memories --all to see auto-extracted.")
            continue

        if user_input.startswith("/forget "):
            partial = user_input[len("/forget "):].strip()
            notes = agent.list_memories(limit=200)
            matches = [n for n in notes if n["note_id"].startswith(partial)]
            if not matches:
                print(f"  No memory found matching '{partial}'")
            elif len(matches) > 1:
                print(f"  Ambiguous: {len(matches)} matches — use more characters")
                for m in matches[:5]:
                    print(f"    [{m['note_id'][:12]}] {m['title'][:50]}")
            else:
                n = matches[0]
                try:
                    ans = input(f"  Delete [{n['note_id'][:8]}] {n['title'][:60]}? [y/N] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    ans = ""
                if ans == "y":
                    ok = agent.forget(n["note_id"])
                    print(f"  {'Deleted' if ok else 'Not found'}: {n['note_id'][:8]}")
                else:
                    print("  Cancelled.")
            continue

        if user_input == "/sessions":
            for s in agent.list_sessions():
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["last_turn"]))
                itok = s.get("total_input_tokens") or 0
                otok = s.get("total_output_tokens") or 0
                print(f"  {s['session_id'][:12]}  turns={s['turn_count']}  last={ts}  in={itok}  out={otok}")
            continue

        if user_input == "/ctx":
            dbg = loop_obj.debug_state()
            policy = loop_obj._policy()
            history_tokens = dbg["history_tokens"]
            history_budget = policy.history_budget
            threshold_tokens = int(history_budget * policy.compact_threshold)
            pct = history_tokens / max(history_budget, 1) * 100
            thresh_pct = policy.compact_threshold * 100
            status = "⚠ COMPACT SOON" if pct >= thresh_pct * 0.9 else "OK"
            print("\n  ── Context Budget ──────────────────────────────")
            print(f"  stable_prefix budget : {policy.stable_budget:>8,} tokens  (config)")
            print(f"  dynamic_suffix budget: {policy.dynamic_budget:>8,} tokens  (config)")
            print(f"  memory_max_tokens    : {policy.memory_max_tokens:>8,} tokens  (config)")
            print(f"  history (in-context) : {history_tokens:>8,} tokens  ({pct:.1f}%  of budget)")
            print(f"  history budget       : {history_budget:>8,} tokens")
            print(f"  compact threshold    : {threshold_tokens:>8,} tokens  ({thresh_pct:.0f}%)")
            print(f"  compaction status    : {status}")
            print(f"  compact_strategy     : {policy.compact_strategy}")
            print(f"  turns in context     : {dbg['history_turns']}")
            print()
            continue

        if user_input == "/debug":
            state = loop_obj.debug_state()
            pct = state["history_tokens"] / max(state["history_budget"], 1) * 100
            thresh_pct = state["compact_threshold"] * 100
            ci = agent.config.provider.cost_per_1k_input_tokens
            co = agent.config.provider.cost_per_1k_output_tokens
            print(f"  session:   {state['session_id']}")
            print(
                f"  history:   {state['history_turns']} turns"
                f" / ~{state['history_tokens']:,} tokens"
                f" ({pct:.1f}%  budget={state['history_budget']:,}"
                f"  compacts at {thresh_pct:.0f}%)"
            )
            sit = state["session_input_tokens"]
            sot = state["session_output_tokens"]
            cost = _cost_str(sit, sot, agent.config)
            print(f"  session $: In={sit:,}  Out={sot:,}{cost}")
            if state["last_turn_input_tokens"]:
                lt_cost = _cost_str(
                    state["last_turn_input_tokens"],
                    state["last_turn_output_tokens"],
                    agent.config,
                )
                print(
                    f"  last turn: In={state['last_turn_input_tokens']:,}"
                    f"  Out={state['last_turn_output_tokens']:,}{lt_cost}"
                )
            print(f"  auto_extract: {'on' if agent.config.context.auto_extract else 'off'}")
            continue

        if user_input == "/help":
            print(
                "  /new            Start a new session\n"
                "  /ctx            Show context budget breakdown\n"
                "  /remember X     Save X to memory\n"
                "  /search X       Search memory\n"
                "  /memories       List user-saved memories\n"
                "  /memories --all Show all memories (incl. auto-extracted)\n"
                "  /memories --auto Show only auto-extracted memories\n"
                "  /forget <id>    Delete a memory by ID prefix\n"
                "  /sessions       List sessions with token usage\n"
                "  /debug          Show session/context/token state\n"
                "  /<skill>        Run a skill's direct_tool (if configured)\n"
                "  /help           Show this help\n"
                "  /exit           Quit"
            )
            continue

        # ---- Skill direct_tool dispatch ----
        if user_input.startswith("/") and _dispatch_direct_tool(loop_obj, user_input):
            continue

        # ---- LLM call with event stream ----
        _do_turn(loop_obj, user_input, agent.config)

    gateway.close()


def _do_turn(loop_obj, user_input: str, config, retry: bool = True) -> None:
    """Execute one turn, print response + token stats. Offers retry on error."""
    try:
        response, in_tok, out_tok = asyncio.run(
            _run_events(loop_obj, user_input, config)
        )
        cost = _cost_str(in_tok, out_tok, config)
        print(f"\nhushclaw> {response}")
        print(f"\n  ↳ In: {in_tok:,} / Out: {out_tok:,} tokens{cost}\n")
    except KeyboardInterrupt:
        print("\n  [Interrupted]")
    except Exception as e:
        print(f"\n{_classify_error(e)}\n", file=sys.stderr)
        if retry:
            try:
                ans = input("  Retry? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans == "y":
                _do_turn(loop_obj, user_input, config, retry=False)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_chat(args, agent) -> int:
    from hushclaw.gateway import Gateway
    gateway = Gateway(agent.config, agent)
    message = " ".join(args.message)
    try:
        if getattr(args, "stream", False):
            async def _stream():
                loop_obj = agent.new_loop(gateway=gateway)
                async for chunk in loop_obj.stream_run(message):
                    print(chunk, end="", flush=True)
                print()
            asyncio.run(_stream())
        else:
            loop_obj = agent.new_loop(gateway=gateway)
            response = asyncio.run(loop_obj.run(message))
            print(response)
        return 0
    except Exception as e:
        print(_classify_error(e), file=sys.stderr)
        return 1
    finally:
        gateway.close()


def cmd_remember(args, agent) -> int:
    content = " ".join(args.content)
    tags = args.tags.split(",") if args.tags else []
    nid = agent.remember(content, title=args.title or "", tags=tags)
    print(f"Saved: {nid[:8]}")
    return 0


def cmd_search(args, agent) -> int:
    results = agent.search(" ".join(args.query), limit=args.limit)
    if not results:
        print("No results found.")
        return 0
    for r in results:
        print(f"\n[{r['note_id'][:8]}] {r['title']} (score={r['score']:.3f})")
        print(f"  {r['body'][:200]}")
    return 0


def cmd_sessions(args, agent) -> int:
    sessions = agent.list_sessions()
    if not sessions:
        print("No sessions found.")
        return 0
    for s in sessions:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["last_turn"]))
        itok = s.get("total_input_tokens") or 0
        otok = s.get("total_output_tokens") or 0
        print(
            f"  {s['session_id']}  turns={s['turn_count']}  last={ts}"
            f"  in={itok} out={otok}"
        )
    return 0


def cmd_sessions_resume(args, agent) -> int:
    _repl(agent, session_id=args.session_id)
    return 0


def cmd_tools_list(args, agent) -> int:
    for td in agent.registry.list_tools():
        print(f"  {td.name:20} {td.description}")
    return 0


# ---------------------------------------------------------------------------
# Config commands (no agent needed)
# ---------------------------------------------------------------------------

def _config_file_path() -> Path:
    from hushclaw.config.loader import get_config_dir
    return get_config_dir() / "hushclaw.toml"


def _toml_value_show(v) -> str | None:
    """Format a value for TOML display (config show)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(v, list):
        if not v:
            return "[]"
        items = [_toml_value_show(i) for i in v]
        items = [i for i in items if i is not None]
        # Multi-line for readability when list is non-trivial
        if sum(len(i) for i in items) > 60:
            inner = ",\n    ".join(items)
            return f"[\n    {inner},\n]"
        return "[" + ", ".join(items) + "]"
    return f'"{v}"'


def _config_to_toml_str(config) -> str:
    """Render a Config dataclass as a TOML string for human display."""
    import dataclasses
    d = dataclasses.asdict(config)

    section_order = [
        "agent", "provider", "memory", "tools",
        "logging", "context", "gateway", "server",
    ]
    lines: list[str] = []

    for section in section_order:
        data = d.get(section)
        if not isinstance(data, dict):
            continue

        # Extract complex sub-structures from gateway before rendering scalars
        gateway_agents = []
        gateway_pipelines = {}
        if section == "gateway":
            gateway_agents = data.pop("agents", [])
            gateway_pipelines = data.pop("pipelines", {})

        section_lines = []
        for k, v in data.items():
            if isinstance(v, dict):
                continue  # skip nested tables in scalar pass
            sv = _toml_value_show(v)
            if sv is not None:
                section_lines.append(f"{k} = {sv}")

        if section_lines:
            lines.append(f"\n[{section}]")
            lines.extend(section_lines)

        # Render gateway sub-tables
        if section == "gateway":
            if gateway_pipelines:
                lines.append("")
                lines.append("[gateway.pipelines]")
                for name, agent_list in gateway_pipelines.items():
                    items = ", ".join(f'"{a}"' for a in agent_list)
                    lines.append(f"{name} = [{items}]")
            for ag in gateway_agents:
                lines.append("")
                lines.append("[[gateway.agents]]")
                for k, v in ag.items():
                    if v == "" or v == []:
                        continue
                    sv = _toml_value_show(v)
                    if sv is not None:
                        lines.append(f"{k} = {sv}")

    return "\n".join(lines).lstrip("\n") + "\n"


def cmd_config_show(args) -> int:
    from hushclaw.config.loader import load_config
    config = load_config()
    cfg_path = _config_file_path()

    if getattr(args, "json", False):
        import dataclasses
        print(json.dumps(dataclasses.asdict(config), indent=2, default=str))
        return 0

    exists = "exists" if cfg_path.exists() else "not found"
    print(f"# Active config  ({cfg_path})  [{exists}]")
    print(_config_to_toml_str(config))
    return 0


def cmd_config_path(args) -> int:
    from hushclaw.config.loader import get_config_dir, get_data_dir
    cfg_dir = get_config_dir()
    data_dir = get_data_dir()
    cfg_file = cfg_dir / "hushclaw.toml"
    plugin_dir = cfg_dir / "tools"

    def _status(p: Path) -> str:
        return "[exists]" if p.exists() else "[not found]"

    print(f"Config file: {cfg_file}  {_status(cfg_file)}")
    print(f"Data dir:    {data_dir}/  {_status(data_dir)}")
    print(f"Plugin dir:  {plugin_dir}/  {_status(plugin_dir)}")
    return 0


def cmd_config_set(args) -> int:
    from hushclaw.config.writer import set_config_value
    cfg_path = _config_file_path()
    try:
        set_config_value(cfg_path, args.key, args.value)
        print(f"Set {args.key} = {args.value!r}  ({cfg_path})")
        return 0
    except ValueError as e:
        print(f"[Error] {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# hushclaw init — interactive setup wizard
# ---------------------------------------------------------------------------

def cmd_init(args) -> int:
    from hushclaw.config.loader import get_config_dir
    from hushclaw.config.writer import write_config_toml

    cfg_dir = get_config_dir()
    cfg_path = cfg_dir / "hushclaw.toml"

    _hr = "─" * 42

    print(f"\nHushClaw Setup")
    print(_hr)

    # Check if config already exists
    if cfg_path.exists():
        try:
            ans = input(
                f"\nConfig already exists at:\n  {cfg_path}\n\nOverwrite? [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if ans != "y":
            print("Aborted — existing config unchanged.")
            return 0

    # Step 1: Provider
    print("\nStep 1/3: Provider\n")
    print("  1. Anthropic (Claude)  [recommended]")
    print("  2. Ollama (local, no API key needed)")
    print("  3. OpenAI-compatible")
    print("  4. AIGOCODE relay")
    try:
        raw = input("\nProvider [1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return 1

    provider_map = {
        "1": "anthropic-raw",
        "2": "ollama",
        "3": "openai-raw",
        "4": "aigocode-raw",
        "": "anthropic-raw",
    }
    provider_name = provider_map.get(raw)
    if provider_name is None:
        print(f"[Error] Invalid choice: {raw!r}")
        return 1

    # Step 2: API key (only for Anthropic / OpenAI)
    api_key = ""
    if provider_name in ("anthropic-raw", "openai-raw", "aigocode-raw"):
        import getpass
        provider_label = (
            "Anthropic" if "anthropic" in provider_name else
            "AIGOCODE" if "aigocode" in provider_name else
            "OpenAI"
        )
        print(f"\nStep 2/3: API Key\n")
        print(f"Enter your {provider_label} API key (input is hidden):")
        try:
            api_key = getpass.getpass("API key: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if not api_key:
            env_var = _provider_env_var(provider_name)
            print(f"[Warning] No API key entered — you can set it later via {env_var}")
    else:
        print("\nStep 2/3: API Key\n")
        print("  (Ollama runs locally — no API key required)")

    # Step 3: Model
    print("\nStep 3/3: Model\n")
    model_choices = {
        "anthropic-raw": [
            ("claude-sonnet-4-6", "recommended"),
            ("claude-haiku-4-5-20251001", "faster, cheaper"),
            ("claude-opus-4-6", "most capable"),
        ],
        "ollama": [
            ("llama3.2", "recommended for local use"),
            ("mistral", "alternative"),
            ("phi3", "lightweight"),
        ],
        "openai-raw": [
            ("gpt-4o", "recommended"),
            ("gpt-4o-mini", "faster, cheaper"),
            ("gpt-4-turbo", "previous generation"),
        ],
        "aigocode-raw": [
            ("gpt-4o-mini", "recommended"),
            ("gpt-4o", "higher quality"),
            ("gpt-4.1-mini", "balanced"),
        ],
    }
    choices = model_choices.get(provider_name, [("claude-sonnet-4-6", "default")])
    for i, (m, note) in enumerate(choices, 1):
        rec = "  [recommended]" if i == 1 else ""
        print(f"  {i}. {m}  ({note}){rec}")
    print(f"  4. Custom...")

    try:
        raw_model = input("\nModel [1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return 1

    if raw_model == "" or raw_model == "1":
        model = choices[0][0]
    elif raw_model == "2" and len(choices) >= 2:
        model = choices[1][0]
    elif raw_model == "3" and len(choices) >= 3:
        model = choices[2][0]
    elif raw_model == "4":
        try:
            model = input("Custom model name: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if not model:
            model = choices[0][0]
    else:
        # Treat as literal model name if it doesn't look like a digit choice
        model = raw_model if not raw_model.isdigit() else choices[0][0]

    # Build config sections
    sections: dict[str, dict] = {
        "agent": {"model": model},
        "provider": {"name": provider_name},
    }
    if api_key:
        sections["provider"]["api_key"] = api_key

    # Write config
    print(f"\n{_hr}")
    try:
        write_config_toml(cfg_path, sections)
    except OSError as e:
        print(f"[Error] Could not write config: {e}", file=sys.stderr)
        return 1

    print(f"Config written to:\n  {cfg_path}")

    # Optional connection test (any keyed provider)
    if api_key and provider_name not in ("ollama",):
        import os
        env_var = _provider_env_var(provider_name)
        os.environ[env_var] = api_key
        os.environ["HUSHCLAW_MODEL"] = model
        print("\nTesting connection...", end=" ", flush=True)
        try:
            from hushclaw.config.loader import load_config
            from hushclaw.providers.registry import get_provider
            cfg = load_config()
            provider = get_provider(cfg.provider)
            from hushclaw.providers.base import Message
            test_msg = [Message(role="user", content="hi")]
            resp = asyncio.run(provider.complete(
                messages=test_msg, model=model, max_tokens=10
            ))
            print("OK")
        except Exception as e:
            print(f"FAILED\n  [Warning] {e}")
            print("  You can still use hushclaw — check your API key if issues persist.")

    print(f"\nRun `hushclaw` to start chatting!\n")
    return 0


def cmd_doctor(args) -> int:
    """Check configuration, environment, and network health."""
    import socket
    import shutil as _shutil

    from hushclaw.config.loader import load_config, validate_config

    _hr = "─" * 42
    print(f"\nHushClaw Doctor\n{_hr}")

    # 1. Load config
    try:
        config = load_config()
        print("✓ Config loaded")
    except Exception as e:
        print(f"✗ Config error: {e}")
        return 1

    # 2. validate_config checks
    warnings = validate_config(config)
    for w in warnings:
        icon = "✗" if w.startswith("[ERROR]") else "⚠" if w.startswith("[WARN]") else "ℹ"
        print(f"{icon} {w}")
    if not warnings:
        print("✓ Config validation passed")

    # 3. Required binaries
    for b in ["git"]:
        if _shutil.which(b):
            print(f"✓ {b} found in PATH")
        else:
            print(f"⚠ {b} not found in PATH (needed for skill install)")

    # 4. Provider reachability (DNS check — no API call)
    provider = config.provider.name
    host_map = {
        "anthropic-raw": "api.anthropic.com",
        "anthropic-sdk": "api.anthropic.com",
        "openai-raw":    "api.openai.com",
        "openai-sdk":    "api.openai.com",
    }
    host = host_map.get(provider)
    if host:
        try:
            socket.setdefaulttimeout(5)
            socket.getaddrinfo(host, 443)
            print(f"✓ DNS resolved: {host}")
        except OSError:
            print(f"⚠ Cannot resolve {host} — check internet connection")
    else:
        print(f"ℹ Provider '{provider}' — skipping network check")

    # 5. data_dir writable
    data_dir = config.memory.data_dir
    if data_dir:
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            test_file = data_dir / ".doctor_write_test"
            test_file.touch()
            test_file.unlink()
            print(f"✓ data_dir writable: {data_dir}")
        except OSError as e:
            print(f"✗ data_dir not writable: {data_dir} — {e}")

    # 6. workspace_dir
    if config.agent.workspace_dir:
        if config.agent.workspace_dir.is_dir():
            print(f"✓ workspace_dir: {config.agent.workspace_dir}")
        else:
            print(f"⚠ workspace_dir set but does not exist: {config.agent.workspace_dir}")

    # 7. Summary
    print(f"\n{_hr}")
    error_count = sum(1 for w in warnings if w.startswith("[ERROR]"))
    if error_count:
        print(f"Found {error_count} error(s). Fix before using hushclaw.\n")
        return 1
    print("All checks passed.\n")
    return 0


def cmd_serve(args, agent) -> int:
    from hushclaw.gateway import Gateway
    from hushclaw.server import HushClawServer

    if hasattr(args, "host") and args.host:
        agent.config.server.host = args.host
    if hasattr(args, "port") and args.port:
        agent.config.server.port = args.port

    gateway = Gateway(agent.config, agent)
    server = HushClawServer(gateway, agent.config.server)

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        gateway.close()
    return 0


def cmd_agents_list(args, agent) -> int:
    from hushclaw.gateway import Gateway
    gateway = Gateway(agent.config, agent)
    for a in gateway.list_agents():
        desc = f"  {a['description']}" if a['description'] else ""
        print(f"  {a['name']}{desc}")
    gateway.close()
    return 0


def cmd_agents_run(args, agent) -> int:
    from hushclaw.gateway import Gateway
    gateway = Gateway(agent.config, agent)
    try:
        response = asyncio.run(gateway.execute(args.agent_name, args.message))
        print(response)
    except Exception as e:
        print(_classify_error(e), file=sys.stderr)
        return 1
    finally:
        gateway.close()
    return 0


def cmd_agents_pipeline(args, agent) -> int:
    from hushclaw.gateway import Gateway
    gateway = Gateway(agent.config, agent)
    names = [n.strip() for n in args.agent_names.split(",") if n.strip()]
    try:
        response = asyncio.run(gateway.pipeline(names, args.message))
        print(response)
    except Exception as e:
        print(_classify_error(e), file=sys.stderr)
        return 1
    finally:
        gateway.close()
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hushclaw",
        description="HushClaw — lightweight, token-first AI Agent with persistent memory",
    )
    p.add_argument("--version", action="version", version="hushclaw 0.2.0")
    p.add_argument(
        "--provider", metavar="NAME",
        help="Override provider (anthropic-raw, anthropic-sdk, ollama, openai-raw, aigocode-raw)",
    )
    p.add_argument("--model", metavar="MODEL", help="Override model name")
    p.add_argument("--session", metavar="ID", help="Session ID to resume")
    p.add_argument("--log-level", metavar="LEVEL", default=None,
                   help="Logging level (DEBUG, INFO, WARNING, ERROR)")

    sub = p.add_subparsers(dest="command")

    # init
    sub.add_parser("init", help="Interactive setup wizard (first-time configuration)")

    # doctor
    sub.add_parser("doctor", help="Check configuration and environment health")

    # chat
    chat_p = sub.add_parser("chat", help="Send a single message")
    chat_p.add_argument("message", nargs="+", help="Message text")
    chat_p.add_argument(
        "--stream", action="store_true",
        help="Stream output as it is generated",
    )

    # remember
    rem_p = sub.add_parser("remember", help="Save a note to memory")
    rem_p.add_argument("content", nargs="+")
    rem_p.add_argument("--title", default="")
    rem_p.add_argument("--tags", default="", help="Comma-separated tags")

    # search
    srch_p = sub.add_parser("search", help="Search memory")
    srch_p.add_argument("query", nargs="+")
    srch_p.add_argument("--limit", type=int, default=5)

    # sessions
    sess_p = sub.add_parser("sessions", help="List or resume sessions")
    sess_sub = sess_p.add_subparsers(dest="sessions_command")
    resume_p = sess_sub.add_parser("resume", help="Resume a session")
    resume_p.add_argument("session_id")

    # tools
    tools_p = sub.add_parser("tools", help="Tool management")
    tools_sub = tools_p.add_subparsers(dest="tools_command")
    tools_sub.add_parser("list", help="List available tools")

    # config
    cfg_p = sub.add_parser("config", help="Configuration management")
    cfg_sub = cfg_p.add_subparsers(dest="config_command")
    show_p = cfg_sub.add_parser("show", help="Show current configuration")
    show_p.add_argument(
        "--json", action="store_true",
        help="Output as JSON instead of TOML",
    )
    cfg_sub.add_parser("path", help="Show config and data directory paths")
    set_p = cfg_sub.add_parser("set", help="Set a config value (e.g. agent.model claude-haiku-4-5)")
    set_p.add_argument("key", help="Dotted key (e.g. agent.model, provider.api_key)")
    set_p.add_argument("value", help="New value")

    # serve
    serve_p = sub.add_parser("serve", help="Start the WebSocket server")
    serve_p.add_argument("--host", metavar="HOST")
    serve_p.add_argument("--port", type=int, metavar="PORT")

    # agents
    agents_p = sub.add_parser("agents", help="Multi-agent management")
    agents_sub = agents_p.add_subparsers(dest="agents_command")
    agents_sub.add_parser("list", help="List configured agents")
    agents_run_p = agents_sub.add_parser("run", help="Run a message through a named agent")
    agents_run_p.add_argument("agent_name")
    agents_run_p.add_argument("message")
    agents_pipeline_p = agents_sub.add_parser(
        "pipeline", help="Run a message through a sequence of agents"
    )
    agents_pipeline_p.add_argument(
        "agent_names", help="Comma-separated agent names (e.g. 'researcher,writer')"
    )
    agents_pipeline_p.add_argument("message")

    return p


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    import os
    if args.provider:
        os.environ["HUSHCLAW_PROVIDER"] = args.provider
    if args.model:
        os.environ["HUSHCLAW_MODEL"] = args.model
    if args.log_level:
        os.environ["HUSHCLAW_LOG_LEVEL"] = args.log_level

    # ---- Commands that don't need agent initialization ----

    if args.command == "init":
        sys.exit(cmd_init(args))

    if args.command == "doctor":
        sys.exit(cmd_doctor(args))

    if args.command == "config":
        if args.config_command == "path":
            sys.exit(cmd_config_path(args))
        elif args.config_command == "set":
            sys.exit(cmd_config_set(args))
        elif args.config_command == "show":
            sys.exit(cmd_config_show(args))
        else:
            parser.parse_args(["config", "--help"])
        return

    # ---- All other commands need the agent ----

    try:
        agent = _make_agent()
    except Exception as e:
        _handle_agent_init_error(e)
        sys.exit(1)

    try:
        if args.command is None:
            _repl(agent, session_id=args.session)

        elif args.command == "chat":
            sys.exit(cmd_chat(args, agent))

        elif args.command == "remember":
            sys.exit(cmd_remember(args, agent))

        elif args.command == "search":
            sys.exit(cmd_search(args, agent))

        elif args.command == "sessions":
            if args.sessions_command == "resume":
                sys.exit(cmd_sessions_resume(args, agent))
            else:
                sys.exit(cmd_sessions(args, agent))

        elif args.command == "tools":
            if args.tools_command == "list":
                sys.exit(cmd_tools_list(args, agent))
            else:
                parser.parse_args(["tools", "--help"])

        elif args.command == "serve":
            sys.exit(cmd_serve(args, agent))

        elif args.command == "agents":
            if args.agents_command == "list":
                sys.exit(cmd_agents_list(args, agent))
            elif args.agents_command == "run":
                sys.exit(cmd_agents_run(args, agent))
            elif args.agents_command == "pipeline":
                sys.exit(cmd_agents_pipeline(args, agent))
            else:
                parser.parse_args(["agents", "--help"])

    finally:
        agent.close()


if __name__ == "__main__":
    main()
