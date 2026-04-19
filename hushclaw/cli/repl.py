"""Interactive REPL — spinner, event stream rendering, slash commands."""
from __future__ import annotations

import asyncio
import json
import sys
import time


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


def _dispatch_direct_tool(loop_obj, user_input: str) -> bool:
    """If the user types /<skill-name> and the skill has a direct_tool configured,
    execute that tool directly (no LLM round-trip) and print the result.

    Returns True if the command was handled (caller should ``continue``).
    """
    skill_registry = getattr(loop_obj, "_skill_registry", None)
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


def _do_turn(loop_obj, user_input: str, config, classify_error, retry: bool = True) -> None:
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
        print(f"\n{classify_error(e)}\n", file=sys.stderr)
        if retry:
            try:
                ans = input("  Retry? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans == "y":
                _do_turn(loop_obj, user_input, config, classify_error, retry=False)


def repl(agent, classify_error, session_id: str | None = None) -> None:
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
                    continue
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
                "  /<skill>        Run a skill direct command (if configured)\n"
                "  /skills         List skills in Web chat; /<skill> runs direct_tool when configured\n"
                "  /help           Show this help\n"
                "  /exit           Quit"
            )
            continue

        if user_input.startswith("/") and _dispatch_direct_tool(loop_obj, user_input):
            continue

        _do_turn(loop_obj, user_input, agent.config, classify_error)

    gateway.close()
