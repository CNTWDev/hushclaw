"""server/chat_mixin.py — chat/pipeline/orchestrate flows, attachment processing,
skill slash-command routing, and session lifecycle helpers.

Extracted from server_impl.py. All methods are accessed via self (mixin pattern).
"""
from __future__ import annotations

import asyncio
import json
import time

from hushclaw.server.session import _SessionEntry
from hushclaw.util.ids import make_id
from hushclaw.util.logging import get_logger

log = get_logger("server")


def _extract_attachment_text(local_path: str, raw: bytes, max_chars: int = 32768) -> tuple[str, bool]:
    """Extract readable text from a non-image attachment. Returns (text, truncated)."""
    from pathlib import Path as _Path
    p = _Path(local_path)
    ext = p.suffix.lower()
    try:
        if ext == ".pdf":
            from hushclaw.tools.builtins.file_tools import _read_pdf
            return _read_pdf(p, max_chars)
        if ext in (".docx", ".doc"):
            from hushclaw.tools.builtins.file_tools import _read_word
            return _read_word(p, max_chars)
        if ext in (".xlsx", ".xls"):
            from hushclaw.tools.builtins.file_tools import _read_excel
            return _read_excel(p, max_chars)
        text = raw.decode("utf-8", errors="replace")
        return text[:max_chars], len(text) > max_chars
    except Exception:
        return "", False


class ChatMixin:
    """Mixin for HushClawServer: chat flows, attachments, skills, session lifecycle."""

    # ── Session lifecycle ──────────────────────────────────────────────────────

    def _get_or_create_session_entry(self, session_id: str) -> _SessionEntry:
        """Return (or create) the server-level entry for *session_id*.

        If an entry exists with a running task, cancel that task before
        resetting state — a new chat message implies a fresh run.
        """
        memory = getattr(self, "_gateway", None) and self._gateway.memory or None
        entry = getattr(self, "_session_tasks", {}).get(session_id)
        if entry is None:
            entry = _SessionEntry(session_id=session_id, memory=memory)
            self._session_tasks[session_id] = entry
        else:
            if entry.task and not entry.task.done():
                entry.task.cancel()
            flush_task = getattr(entry, "wire_flush_task", None)
            if flush_task is not None and not flush_task.done():
                flush_task.cancel()
            entry.task = None
            entry.text = ""
            entry.buffer.clear()
            entry.pending_wire_events.clear()
            entry.wire_flush_task = None
            entry.finished_at = None
            entry.pending_amendments.clear()
            entry.applied_amendment = None
            entry.active_run_id = ""
            entry.current_request = None
            entry.runtime_run = type(entry.runtime_run)()
        return entry

    def _normalize_chat_request(self, data: dict) -> dict:
        """Normalize a chat payload into a runtime-ready request dict."""
        text = str(data.get("text", "") or "").strip()
        attachments = data.get("attachments") or []
        text, images = self._process_attachments(text, attachments)
        workspace = (data.get("workspace") or "").strip() or ""
        if workspace:
            known = {ws_entry.name for ws_entry in self._gateway.base_agent.config.workspaces.list}
            if workspace not in known:
                log.warning("chat: unknown workspace=%r, ignoring (known=%s)", workspace, known)
                workspace = ""
        references = data.get("references") or []
        if not isinstance(references, list):
            references = []
        return {
            "agent": str(data.get("agent", "default") or "default").strip() or "default",
            "text": text,
            "images": images,
            "workspace": workspace,
            "client_now": str(data.get("client_now") or "").strip(),
            "references": references,
        }

    async def _subscribe_session(self, ws, session_id: str) -> None:
        """Attach *ws* as subscriber for a running session and replay its buffer."""
        entry = getattr(self, "_session_tasks", {}).get(session_id)
        if entry is None or not entry.is_running():
            await ws.send(json.dumps({
                "type": "session_not_running",
                "session_id": session_id,
                "expired": entry is None,
            }))
            return

        entry.subscriber = ws
        # Prefer durable event log; fall back to in-memory hot-cache buffer.
        mem = getattr(entry, "memory", None)
        if mem is not None:
            replay_items = mem.session_log.session_wire_events(session_id)
        else:
            replay_items = list(entry.buffer)
        try:
            await ws.send(json.dumps({
                "type": "replay_start",
                "session_id": session_id,
                "count": len(replay_items),
            }))
            for raw in replay_items:
                await ws.send(raw)
            await ws.send(json.dumps({
                "type": "replay_end",
                "session_id": session_id,
            }))
        except Exception:
            entry.subscriber = None

    async def _emit_session_status(self, ws, session_id: str, status: str, reason: str) -> None:
        await self._emit_session_runtime(ws, session_id, status=status, reason=reason)

    def _runtime_defaults_for_status(self, status: str, reason: str = "") -> tuple[str, str, bool]:
        if status == "queued":
            return "queued", "Queued", False
        if status == "running":
            return "thinking", "Thinking", False
        if status == "waiting_user":
            return "waiting_user", "Waiting for you", True
        if status == "completed":
            return "done", "Completed", False
        if status == "failed":
            return "failed", "Failed", False
        if status == "stopped":
            return "stopped", "Stopped", False
        if status in {"offline", "stale"}:
            return status, "Syncing", False
        if reason == "done":
            return "done", "Completed", False
        if reason == "awaiting_user":
            return "waiting_user", "Waiting for you", True
        if reason == "error":
            return "failed", "Failed", False
        return "idle", "Idle", False

    async def _emit_session_runtime(
        self,
        ws,
        session_id: str,
        *,
        status: str,
        reason: str = "",
        phase: str | None = None,
        summary: str | None = None,
        agent: str | None = None,
        last_error: str = "",
        requires_user: bool | None = None,
    ) -> None:
        if not session_id:
            return
        now = int(time.time() * 1000)
        if status == "idle":
            if reason == "done":
                runtime_status = "completed"
            elif reason == "awaiting_user":
                runtime_status = "waiting_user"
            elif reason == "error":
                runtime_status = "failed"
            elif reason == "stopped":
                runtime_status = "stopped"
            else:
                runtime_status = "idle"
        else:
            runtime_status = status
        default_phase, default_summary, default_requires_user = self._runtime_defaults_for_status(runtime_status, reason)
        registry = getattr(self, "_session_runtime", None)
        if registry is None:
            self._session_runtime = {}
            registry = self._session_runtime
        prev = registry.get(session_id) or {}
        entry = getattr(self, "_session_tasks", {}).get(session_id)
        runtime_meta = entry.runtime_meta() if entry is not None and hasattr(entry, "runtime_meta") else {}
        reset_started_at = runtime_status in {"queued", "running"} and reason == "start"
        runtime = {
            "session_id": session_id,
            "status": runtime_status,
            "phase": phase or default_phase,
            "summary": summary or default_summary,
            "agent": agent if agent is not None else prev.get("agent", ""),
            "thread_id": runtime_meta.get("thread_id") or prev.get("thread_id", ""),
            "thread_state": runtime_meta.get("thread_state") or prev.get("thread_state", ""),
            "thread_agent": runtime_meta.get("thread_agent") or prev.get("thread_agent", ""),
            "run_id": runtime_meta.get("run_id") or prev.get("run_id", ""),
            "run_seq": runtime_meta.get("run_seq") or prev.get("run_seq", 0),
            "run_state": runtime_meta.get("run_state") or prev.get("run_state", ""),
            "trigger_type": runtime_meta.get("trigger_type") or prev.get("trigger_type", "user"),
            "pending_amendments": runtime_meta.get("pending_amendments", 0),
            "last_completed_run_id": runtime_meta.get("last_completed_run_id") or prev.get("last_completed_run_id", ""),
            "last_superseded_run_id": runtime_meta.get("last_superseded_run_id") or prev.get("last_superseded_run_id", ""),
            "last_amendment_id": runtime_meta.get("last_amendment_id") or prev.get("last_amendment_id", ""),
            "active_step": runtime_meta.get("active_step") or prev.get("active_step", {}),
            "started_at": (
                now
                if reset_started_at or (runtime_status in {"queued", "running"} and prev.get("started_at") is None)
                else prev.get("started_at")
            ),
            "updated_at": now,
            "last_error": last_error or (prev.get("last_error", "") if runtime_status != "failed" else ""),
            "requires_user": default_requires_user if requires_user is None else bool(requires_user),
            "reason": reason,
            "display_state": entry.effective_display_status(runtime_status) if entry is not None else runtime_status,
        }
        registry[session_id] = runtime

        if runtime_status in {"queued", "running"}:
            self._running_sessions.add(session_id)
        elif runtime_status in {"idle", "waiting_user", "completed", "failed", "stopped", "offline", "stale"}:
            self._running_sessions.discard(session_id)
        await ws.send(json.dumps({
            "type": "session_status",
            "session_id": session_id,
            "status": "running" if runtime_status in {"queued", "running"} else "idle",
            "reason": reason,
            "ts": int(time.time() * 1000),
        }))
        await ws.send(json.dumps({
            "type": "session_runtime",
            "session_id": session_id,
            "runtime": runtime,
        }))

    async def _emit_session_phase(
        self,
        ws,
        session_id: str,
        phase: str,
        summary: str,
        *,
        agent: str | None = None,
    ) -> None:
        await self._emit_session_runtime(
            ws,
            session_id,
            status="running",
            reason=phase,
            phase=phase,
            summary=summary,
            agent=agent,
        )

    async def _emit_run_state(
        self,
        ws,
        session_id: str,
        *,
        run_id: str,
        state: str,
        agent: str = "",
        reason: str = "",
        amendment_id: str = "",
        safe_point: str = "",
    ) -> None:
        if not session_id or not run_id:
            return
        await ws.send(json.dumps({
            "type": "run_state_changed",
            "session_id": session_id,
            "run_id": run_id,
            "state": state,
            "agent": agent,
            "reason": reason,
            "amendment_id": amendment_id,
            "safe_point": safe_point,
            "ts": int(time.time() * 1000),
        }))

    async def _emit_thread_state(
        self,
        ws,
        session_id: str,
        *,
        thread_id: str,
        state: str,
        agent: str = "",
    ) -> None:
        if not session_id or not thread_id:
            return
        await ws.send(json.dumps({
            "type": "thread_state_changed",
            "session_id": session_id,
            "thread_id": thread_id,
            "state": state,
            "agent": agent,
            "ts": int(time.time() * 1000),
        }))

    async def _emit_step_state(
        self,
        ws,
        session_id: str,
        *,
        run_id: str,
        step_id: str,
        step_type: str,
        state: str,
        summary: str,
        meta: dict | None = None,
    ) -> None:
        if not session_id or not run_id or not step_id:
            return
        await ws.send(json.dumps({
            "type": "step_state_changed",
            "session_id": session_id,
            "run_id": run_id,
            "step_id": step_id,
            "step_type": step_type,
            "state": state,
            "summary": summary,
            "meta": dict(meta or {}),
            "ts": int(time.time() * 1000),
        }))

    # ── Attachment processing (multimodal) ────────────────────────────────────

    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    _IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}

    @staticmethod
    def _detect_mime(path_str: str, data: bytes) -> str | None:
        """Detect image MIME type from magic bytes or file extension."""
        import os
        sig = data[:16]
        if sig[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if sig[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if sig[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if sig[:4] in (b"RIFF", b"WEBP") or b"WEBP" in sig[:12]:
            return "image/webp"
        ext = os.path.splitext(path_str)[1].lower()
        return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"
                }.get(ext.lstrip("."))

    def _process_attachments(
        self, text: str, attachments: list[dict]
    ) -> tuple[str, list[str]]:
        """Split attachments into (augmented_text, image_data_uris).

        Image attachments are read from disk and returned as base64 data URIs
        for direct LLM vision input.  Non-image attachments are appended to the
        text as local paths so the agent can use read_file().
        """
        import base64
        if not attachments:
            return text, []

        images: list[str] = []
        file_lines: list[str] = []

        for att in attachments:
            name = att.get("name", "file")
            file_id = att.get("file_id", "")
            local_path = ""
            if file_id:
                lookup = getattr(self, "_lookup_uploaded_file", None)
                if callable(lookup):
                    row = lookup(file_id)
                    if row is not None:
                        local_path = str(row["storage_path"])
                # Glob fallback omitted: disk filenames use blob_id prefix (b_…),
                # not file_id, so glob(f"{file_id}_*") never matches. When DB
                # lookup misses, fall through to the URL path so read_file can
                # resolve /files/{file_id} via its own DB query.

            if local_path:
                try:
                    with open(local_path, "rb") as _fh:
                        raw = _fh.read()
                    mime = self._detect_mime(local_path, raw)
                    if mime and mime in self._IMAGE_MIMES:
                        b64 = base64.b64encode(raw).decode()
                        images.append(f"data:{mime};base64,{b64}")
                        file_lines.append(f"- {name} (image; path: {local_path})")
                        log.debug("multimodal: encoded image %s (%d bytes)", name, len(raw))
                        continue
                except Exception as e:
                    log.warning("multimodal: failed to read %s: %s", local_path, e)
                    file_lines.append(f"- {name} (unreadable)")
                    continue
                # Non-image: extract and inject text content inline.
                content, truncated = _extract_attachment_text(local_path, raw)
                if content:
                    trunc_note = " [truncated]" if truncated else ""
                    ref = f" [path: {local_path}]" if local_path else ""
                    file_lines.append(f"- {name}{ref}{trunc_note}:\n{content}")
                    log.debug("attachment: injected %d chars from %s", len(content), name)
                else:
                    file_lines.append(f"- {name} (path: {local_path})")
            else:
                file_lines.append(f"- {name} (uploaded; content not accessible)")

        if file_lines:
            lines = [text] if text else []
            lines.append("\n[Attached files]")
            lines.extend(file_lines)
            text = "\n".join(lines).strip()

        return text, images

    # ── Skill slash-command routing ────────────────────────────────────────────

    def _get_skill_registry_for_agent(self, agent_name: str):
        """Return the skill registry for a routed agent (fallback to base agent)."""
        pool = self._gateway.get_pool(agent_name)
        reg = getattr(pool._agent, "_skill_registry", None)
        if reg is not None:
            return reg
        return getattr(self._gateway.base_agent, "_skill_registry", None)

    @staticmethod
    def _rewrite_prompt_skill_text(skill_name: str, skill_desc: str, task: str) -> str:
        """Encode '/<skill>' intent into plain text for prompt-only skills."""
        desc = (skill_desc or "").strip()
        body = task.strip() if task.strip() else (desc or f"Run skill '{skill_name}'.")
        return (
            f"[SkillCommand /{skill_name}] {body}\n"
            f"Please apply the '/{skill_name}' skill instructions for this request."
        )

    async def _try_handle_slash_command(
        self,
        ws,
        agent_name: str,
        session_id: str,
        text: str,
    ) -> tuple[bool, bool, str]:
        """Handle slash commands before LLM routing.

        Returns:
          (handled, ok, next_text)
          - handled=False: caller should continue normal chat flow
          - handled=True: command already responded; caller should return
        """
        if not text.startswith("/"):
            return False, True, text
        raw = text[1:].strip()
        if not raw:
            return False, True, text

        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        cmd_args = parts[1].strip() if len(parts) > 1 else ""
        if not cmd:
            return False, True, text

        skill_registry = self._get_skill_registry_for_agent(agent_name)
        if skill_registry is None:
            await ws.send(json.dumps({"type": "error", "message": "Skill registry unavailable."}))
            return True, False, text

        if cmd == "skills":
            items = skill_registry.list_all() or []
            items = sorted(items, key=lambda s: (s.get("available") is not True, s.get("name", "")))
            lines = [f"Available skills ({len(items)}):"]
            if not items:
                lines.append("- (none)")
            for s in items:
                name = s.get("name", "")
                desc = s.get("description", "") or "No description."
                if s.get("available", True):
                    lines.append(f"- /{name}: {desc}")
                else:
                    reason = s.get("reason", "requirements not met")
                    lines.append(f"- /{name}: {desc} [unavailable: {reason}]")
            await ws.send(json.dumps({"type": "done", "text": "\n".join(lines)}))
            log.info("slash command handled: /skills session=%s", session_id[:12])
            return True, True, text

        skill = skill_registry.get(cmd)
        if skill is None:
            # Keep compatibility: unknown slash command falls back to normal chat.
            return False, True, text

        if not skill.get("available", True):
            reason = skill.get("reason", "requirements not met")
            await ws.send(json.dumps({"type": "error", "message": f"Skill '/{cmd}' unavailable: {reason}"}))
            log.info("slash command unavailable: /%s session=%s", cmd, session_id[:12])
            return True, False, text

        tool_name = skill.get("direct_tool", "")
        if not tool_name:
            # Fallback for prompt-only skills: route as normal chat with an
            # explicit skill intent so "/<skill>" remains usable in WebUI.
            desc = (skill.get("description", "") or "").strip()
            if not cmd_args:
                self._pending_skill_prompts[session_id] = {"skill": cmd, "description": desc}
                await ws.send(json.dumps({
                    "type": "done",
                    "text": (
                        f"Using '/{cmd}'. Please add one short requirement "
                        "(e.g. time range or focus), then send."
                    ),
                }))
                log.info("slash command prompt-skill awaiting details: /%s session=%s", cmd, session_id[:12])
                return True, True, text
            task = cmd_args or desc or f"Run skill '{cmd}'."
            rewritten = self._rewrite_prompt_skill_text(cmd, desc, task)
            log.info("slash command prompt-skill fallback: /%s session=%s", cmd, session_id[:12])
            return False, True, rewritten

        try:
            pool = self._gateway.get_pool(agent_name)
            loop_obj = pool._get_or_create_loop(session_id, gateway=self._gateway)
            result = await loop_obj.executor.execute_single(tool_name, {})
            if getattr(result, "is_error", False):
                await ws.send(json.dumps({
                    "type": "error",
                    "message": f"Skill '/{cmd}' tool '{tool_name}' failed: {result.content}",
                }))
                log.info("slash command tool error: /%s tool=%s session=%s", cmd, tool_name, session_id[:12])
                return True, False, text
            await ws.send(json.dumps({"type": "done", "text": result.content}))
            log.info("slash command tool executed: /%s tool=%s session=%s", cmd, tool_name, session_id[:12])
            return True, True, text
        except Exception as e:
            log.error("slash command execution error: /%s err=%s", cmd, e, exc_info=True)
            await ws.send(json.dumps({"type": "error", "message": str(e)}))
            return True, False, text

    # ── Chat / pipeline handlers ───────────────────────────────────────────────

    @staticmethod
    def _with_session_id(event: dict, session_id: str) -> dict:
        """Return a wire event annotated with its owning session."""
        if not isinstance(event, dict):
            return {"type": "error", "message": "Invalid event", "session_id": session_id}
        if event.get("session_id") == session_id:
            return event
        return {**event, "session_id": session_id}

    async def _handle_chat(self, ws, data: dict) -> None:
        import time as _time
        _t_recv = _time.monotonic()

        req = self._normalize_chat_request(data)
        agent = req["agent"]
        text = req["text"]
        workspace = req["workspace"] or None
        client_now = req["client_now"]
        references = req["references"]
        images = list(req["images"] or [])

        log.info(
            "chat recv: agent=%s input=%r workspace=%r",
            agent, text[:80], workspace,
        )
        _t_attach = _time.monotonic()
        if data.get("attachments"):
            log.info(
                "chat attachments: n=%d elapsed=%.0fms",
                len(data.get("attachments") or []), (_t_attach - _t_recv) * 1000,
            )

        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return

        session_id = data.get("session_id") or make_id("s-")
        pending = self._pending_skill_prompts.pop(session_id, None)
        if pending and text and not text.startswith("/"):
            text = self._rewrite_prompt_skill_text(
                pending.get("skill", "").strip() or "skill",
                pending.get("description", ""),
                text,
            )
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))
        await self._emit_session_status(ws, session_id, "running", "start")

        handled, ok, text = await self._try_handle_slash_command(ws, agent, session_id, text)
        if handled:
            await self._emit_session_status(ws, session_id, "idle", "done" if ok else "error")
            return

        _t_dispatch = _time.monotonic()
        log.info(
            "chat dispatch: agent=%s session=%s workspace=%r pre_dispatch=%.0fms",
            agent, session_id[:12], workspace, (_t_dispatch - _t_recv) * 1000,
        )

        current_req = {
            "agent": agent,
            "text": text,
            "images": images,
            "workspace": workspace or "",
            "client_now": client_now,
            "references": references,
        }
        _first_event = True
        _last_phase = ""
        entry = getattr(self, "_session_tasks", {}).get(session_id)
        try:
            while current_req:
                next_req = None
                run_id = ""
                thread_id = ""
                async for event in self._gateway.event_stream(
                    current_req["agent"],
                    current_req["text"],
                    session_id,
                    images=current_req.get("images") or [],
                    workspace=current_req.get("workspace") or None,
                    client_now=current_req.get("client_now") or "",
                    references=current_req.get("references") or [],
                    session_entry=entry,
                ):
                    if _first_event:
                        _first_event = False
                        log.info(
                            "chat first_event: session=%s type=%s elapsed=%.0fms",
                            session_id[:12], event.get("type"), (_time.monotonic() - _t_recv) * 1000,
                        )
                    event_type = event.get("type")
                    if event_type == "thread_run_bound":
                        thread_id = str(event.get("thread_id") or "")
                        run_id = str(event.get("run_id") or "")
                        if entry is not None:
                            if hasattr(entry, "bind_thread"):
                                entry.bind_thread(thread_id, agent_name=current_req["agent"])
                            if hasattr(entry, "begin_run") and getattr(entry, "active_run_id", "") != run_id:
                                entry.begin_run(
                                    current_req,
                                    run_id=run_id,
                                    trigger_type=str(event.get("trigger_type") or "user"),
                                )
                            if hasattr(entry, "set_step"):
                                entry.set_step(
                                    step_type="model",
                                    step_id=f"model:{run_id}:0",
                                    state="running",
                                    summary="Thinking",
                                    meta={"round": 0},
                                )
                        await self._emit_thread_state(
                            ws,
                            session_id,
                            thread_id=thread_id,
                            state="active",
                            agent=current_req["agent"],
                        )
                        await self._emit_session_runtime(
                            ws,
                            session_id,
                            status="running",
                            reason="start",
                            phase="thinking",
                            summary="Thinking",
                            agent=current_req["agent"],
                        )
                        await self._emit_run_state(
                            ws,
                            session_id,
                            run_id=run_id,
                            state="started",
                            agent=current_req["agent"],
                            reason="start",
                        )
                        await self._emit_step_state(
                            ws,
                            session_id,
                            run_id=run_id,
                            step_id=f"model:{run_id}:0",
                            step_type="model",
                            state="started",
                            summary="Thinking",
                            meta={"round": 0},
                        )
                        continue
                    if event_type == "chunk" and _last_phase != "streaming":
                        _last_phase = "streaming"
                        await self._emit_session_phase(ws, session_id, "streaming", "Writing response", agent=agent)
                    elif event_type == "round_info":
                        _last_phase = "thinking"
                        round_no = event.get("round")
                        max_rounds = event.get("max_rounds")
                        summary = "Thinking"
                        if round_no and max_rounds:
                            summary = f"Thinking · round {round_no}/{max_rounds}"
                        if entry is not None and hasattr(entry, "set_step") and run_id:
                            entry.set_step(
                                step_type="model",
                                step_id=f"model:{run_id}:{round_no or 0}",
                                state="running",
                                summary=summary,
                                meta={"round": round_no or 0, "max_rounds": max_rounds or 0},
                            )
                        await self._emit_session_phase(ws, session_id, "thinking", summary, agent=agent)
                        if run_id:
                            await self._emit_step_state(
                                ws,
                                session_id,
                                run_id=run_id,
                                step_id=f"model:{run_id}:{round_no or 0}",
                                step_type="model",
                                state="started",
                                summary=summary,
                                meta={"round": round_no or 0, "max_rounds": max_rounds or 0},
                            )
                    elif event_type == "tool_call":
                        _last_phase = "tool_call"
                        tool = str(event.get("tool") or "tool")
                        call_id = str(event.get("call_id") or "")
                        if entry is not None and hasattr(entry, "set_step") and run_id and call_id:
                            entry.set_step(
                                step_type="tool",
                                step_id=call_id,
                                state="running",
                                summary=f"Using {tool}",
                                meta={"tool": tool},
                            )
                        await self._emit_session_phase(ws, session_id, "tool_call", f"Using {tool}", agent=agent)
                        if run_id and call_id:
                            await self._emit_step_state(
                                ws,
                                session_id,
                                run_id=run_id,
                                step_id=call_id,
                                step_type="tool",
                                state="started",
                                summary=f"Using {tool}",
                                meta={"tool": tool},
                            )
                    await ws.send(json.dumps(self._with_session_id(event, session_id)))
                    if event_type == "done":
                        log.info(
                            "chat done: session=%s total=%.0fms",
                            session_id[:12], (_time.monotonic() - _t_recv) * 1000,
                        )
                        stop_reason = str(event.get("stop_reason") or "")
                        if stop_reason == "user_amendment" and entry is not None:
                            if hasattr(entry, "complete_run"):
                                entry.complete_run(run_id, superseded=True, state="superseded")
                            await self._emit_run_state(
                                ws,
                                session_id,
                                run_id=run_id,
                                state="superseded",
                                agent=current_req["agent"],
                                reason="user_amendment",
                                amendment_id=str((entry.applied_amendment or {}).get("amendment_id") or ""),
                                safe_point=str(event.get("safe_point") or ""),
                            )
                            next_req = entry.applied_amendment
                            if next_req:
                                await self._emit_session_phase(
                                    ws,
                                    session_id,
                                    "thinking",
                                    "Applying your latest update",
                                    agent=str(next_req.get("agent") or current_req["agent"]),
                                )
                        elif stop_reason != "awaiting_user_confirmation":
                            if entry is not None and hasattr(entry, "complete_run"):
                                entry.complete_run(run_id, superseded=False, state="completed")
                            await self._emit_run_state(
                                ws,
                                session_id,
                                run_id=run_id,
                                state="completed",
                                agent=current_req["agent"],
                                reason=stop_reason or "done",
                            )
                            await self._emit_session_status(ws, session_id, "idle", "done")
                    elif event_type == "tool_result" and event.get("tool") == "remember_skill":
                        # Push refreshed skills list so the Skills panel updates without a tab switch
                        await self._handle_list_skills(ws)
                    elif event_type == "tool_result":
                        call_id = str(event.get("call_id") or "")
                        tool = str(event.get("tool") or "tool")
                        if entry is not None and hasattr(entry, "clear_step"):
                            entry.clear_step(step_id=call_id)
                        if run_id and call_id:
                            await self._emit_step_state(
                                ws,
                                session_id,
                                run_id=run_id,
                                step_id=call_id,
                                step_type="tool",
                                state="completed",
                                summary=f"Finished {tool}",
                                meta={"tool": tool},
                            )
                    elif event_type == "awaiting_user":
                        if entry is not None and hasattr(entry, "runtime_run"):
                            entry.runtime_run.state = "paused"
                        if entry is not None and hasattr(entry, "set_step") and run_id:
                            entry.set_step(
                                step_type="approval",
                                step_id=f"approval:{run_id}",
                                state="waiting",
                                summary="Waiting for you",
                                meta={"pending_tools": list(event.get("pending_tools") or [])},
                            )
                        await self._emit_run_state(
                            ws,
                            session_id,
                            run_id=run_id,
                            state="paused",
                            agent=current_req["agent"],
                            reason="awaiting_user_confirmation",
                        )
                        if run_id:
                            await self._emit_step_state(
                                ws,
                                session_id,
                                run_id=run_id,
                                step_id=f"approval:{run_id}",
                                step_type="approval",
                                state="waiting",
                                summary="Waiting for you",
                                meta={"pending_tools": list(event.get("pending_tools") or [])},
                            )
                        await self._emit_session_runtime(
                            ws,
                            session_id,
                            status="waiting_user",
                            reason="awaiting_user",
                            phase="waiting_user",
                            summary="Waiting for you",
                            agent=current_req["agent"],
                            requires_user=True,
                        )
                    elif event_type == "user_amendment_queued":
                        await self._emit_session_phase(
                            ws,
                            session_id,
                            "thinking",
                            "Queued your latest update",
                            agent=current_req["agent"],
                        )
                    elif event_type == "user_amendment_applied":
                        if entry is not None and hasattr(entry, "set_step") and run_id:
                            entry.set_step(
                                step_type="amendment",
                                step_id=str(event.get("amendment_id") or f"amendment:{run_id}"),
                                state="applied",
                                summary="Applying your latest update",
                                meta={"safe_point": str(event.get("safe_point") or "")},
                            )
                        await self._emit_session_phase(
                            ws,
                            session_id,
                            "thinking",
                            "Replanning with your latest update",
                            agent=str(event.get("agent") or current_req["agent"]),
                        )
                        if run_id:
                            await self._emit_step_state(
                                ws,
                                session_id,
                                run_id=run_id,
                                step_id=str(event.get("amendment_id") or f"amendment:{run_id}"),
                                step_type="amendment",
                                state="applied",
                                summary="Applying your latest update",
                                meta={"safe_point": str(event.get("safe_point") or "")},
                            )
                    elif event_type == "error":
                        await self._emit_session_runtime(
                            ws,
                            session_id,
                            status="failed",
                            reason="error",
                            phase="failed",
                            summary="Failed",
                            agent=current_req["agent"],
                            last_error=str(event.get("message") or ""),
                        )
                if next_req:
                    current_req = {
                        "agent": str(next_req.get("agent") or current_req["agent"]),
                        "text": str(next_req.get("text") or "").strip(),
                        "images": list(next_req.get("images") or []),
                        "workspace": str(next_req.get("workspace") or current_req.get("workspace") or ""),
                        "client_now": str(next_req.get("client_now") or current_req.get("client_now") or ""),
                        "references": list(next_req.get("references") or []),
                    }
                    if entry is not None:
                        entry.applied_amendment = None
                    continue
                break
        except Exception as e:
            if entry is not None and hasattr(entry, "complete_run"):
                entry.complete_run(entry.active_run_id, superseded=False)
            log.error("event_stream error: %s", e, exc_info=True)
            await self._emit_session_runtime(
                ws,
                session_id,
                status="failed",
                reason="error",
                phase="failed",
                summary="Failed",
                agent=agent,
                last_error=str(e),
            )
            await ws.send(json.dumps({"type": "error", "message": str(e), "session_id": session_id}))

    async def _handle_test_agent(self, ws, data: dict) -> None:
        agent = str(data.get("agent") or "default").strip() or "default"
        text = str(data.get("text") or "").strip()
        request_id = str(data.get("request_id") or "")
        if not text:
            await ws.send(json.dumps({
                "type": "agent_test_result",
                "ok": False,
                "agent": agent,
                "request_id": request_id,
                "error": "Test prompt is required.",
            }))
            return
        if self._gateway.get_agent_def(agent) is None:
            await ws.send(json.dumps({
                "type": "agent_test_result",
                "ok": False,
                "agent": agent,
                "request_id": request_id,
                "error": f"Unknown agent: {agent}",
            }))
            return
        session_id = data.get("session_id") or f"agent-test-{agent}"
        try:
            result = await self._gateway.execute(agent, text, session_id=session_id)
            await ws.send(json.dumps({
                "type": "agent_test_result",
                "ok": True,
                "agent": agent,
                "request_id": request_id,
                "text": result,
            }))
        except Exception as e:
            log.error("agent test error: %s", e, exc_info=True)
            await ws.send(json.dumps({
                "type": "agent_test_result",
                "ok": False,
                "agent": agent,
                "request_id": request_id,
                "error": str(e),
            }))

    async def _handle_broadcast_mention(self, ws, data: dict) -> None:
        text = data.get("text", "").strip()
        agents_raw = data.get("agents", [])
        if isinstance(agents_raw, str):
            agent_names = [a.strip() for a in agents_raw.split(",") if a.strip()]
        elif isinstance(agents_raw, list):
            agent_names = [str(a).strip() for a in agents_raw if str(a).strip()]
        else:
            await ws.send(json.dumps({"type": "error", "message": "agents must be a list or string"}))
            return
        agent_names = list(dict.fromkeys(agent_names))

        # Split attachments: images → vision, others → path text
        attachments = data.get("attachments") or []
        text, _images = self._process_attachments(text, attachments)

        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return
        if not agent_names:
            await ws.send(json.dumps({"type": "error", "message": "agents is required"}))
            return

        unknown = [name for name in agent_names if self._gateway.get_agent_def(name) is None]
        if unknown:
            await ws.send(json.dumps({"type": "error", "message": f"Unknown agents: {', '.join(unknown)}"}))
            return

        session_id = data.get("session_id") or make_id("s-")
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))
        await self._emit_session_runtime(
            ws,
            session_id,
            status="running",
            reason="start",
            phase="broadcast",
            summary=f"Broadcasting to {len(agent_names)} agents",
        )
        log.info(
            "mention routing: mode=broadcast agents=%s fallback=default session=%s",
            agent_names,
            session_id[:12],
        )
        try:
            results = await self._gateway.broadcast(agent_names, text, images=_images)
            merged = "\n\n".join(
                f"### @{name}\n{(results.get(name) or '').strip()}" for name in agent_names
            ).strip()
            await self._emit_session_status(ws, session_id, "idle", "done")
            await ws.send(json.dumps({
                "type": "done",
                "text": merged or "(empty broadcast response)",
                "session_id": session_id,
            }))
        except Exception as e:
            log.error("broadcast_mention error: %s", e, exc_info=True)
            await self._emit_session_runtime(
                ws,
                session_id,
                status="failed",
                reason="error",
                phase="failed",
                summary="Failed",
                last_error=str(e),
            )
            await ws.send(json.dumps({"type": "error", "message": str(e), "session_id": session_id}))

    async def _handle_pipeline(self, ws, data: dict) -> None:
        text = data.get("text", "").strip()
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return

        agents_raw = data.get("agents", [])
        if isinstance(agents_raw, str):
            agent_names = self._gateway.resolve_pipeline(agents_raw)
        elif isinstance(agents_raw, list):
            agent_names = agents_raw
        else:
            await ws.send(json.dumps({"type": "error", "message": "agents must be a list or string"}))
            return

        if not agent_names:
            await ws.send(json.dumps({"type": "error", "message": "No agents specified for pipeline"}))
            return

        session_id = data.get("session_id") or make_id("s-")
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))
        await self._emit_session_runtime(
            ws,
            session_id,
            status="running",
            reason="start",
            phase="pipeline",
            summary=f"Running pipeline · {len(agent_names)} agents",
        )

        try:
            async for event in self._gateway.pipeline_stream(agent_names, text, session_id):
                if event.get("type") == "pipeline_step":
                    step_agent = str(event.get("agent") or "agent")
                    await self._emit_session_phase(ws, session_id, "pipeline", f"Pipeline · {step_agent}")
                await ws.send(json.dumps(self._with_session_id(event, session_id)))
                if event.get("type") == "done":
                    await self._emit_session_status(ws, session_id, "idle", "done")
                elif event.get("type") == "error":
                    await self._emit_session_runtime(
                        ws,
                        session_id,
                        status="failed",
                        reason="error",
                        phase="failed",
                        summary="Failed",
                        last_error=str(event.get("message") or ""),
                    )
        except Exception as e:
            log.error("pipeline_stream error: %s", e, exc_info=True)
            await self._emit_session_runtime(
                ws,
                session_id,
                status="failed",
                reason="error",
                phase="failed",
                summary="Failed",
                last_error=str(e),
            )
            await ws.send(json.dumps({"type": "error", "message": str(e), "session_id": session_id}))

    async def _handle_orchestrate(self, ws, data: dict) -> None:
        text = data.get("text", "").strip()
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return

        session_id = data.get("session_id") or make_id("s-")
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))
        await self._emit_session_runtime(
            ws,
            session_id,
            status="running",
            reason="start",
            phase="orchestrating",
            summary="Orchestrating",
        )

        try:
            result = await self._gateway.orchestrate(text, session_id)
            await self._emit_session_status(ws, session_id, "idle", "done")
            await ws.send(json.dumps({"type": "done", "text": result, "session_id": session_id}))
        except Exception as e:
            log.error("orchestrate error: %s", e, exc_info=True)
            await self._emit_session_runtime(
                ws,
                session_id,
                status="failed",
                reason="error",
                phase="failed",
                summary="Failed",
                last_error=str(e),
            )
            await ws.send(json.dumps({"type": "error", "message": str(e), "session_id": session_id}))
