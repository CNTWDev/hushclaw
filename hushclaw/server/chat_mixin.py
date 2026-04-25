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


class ChatMixin:
    """Mixin for HushClawServer: chat flows, attachments, skills, session lifecycle."""

    # ── Session lifecycle ──────────────────────────────────────────────────────

    def _get_or_create_session_entry(self, session_id: str) -> _SessionEntry:
        """Return (or create) the server-level entry for *session_id*.

        If an entry exists with a running task, cancel that task before
        resetting state — a new chat message implies a fresh run.
        """
        memory = getattr(getattr(self, "_agent", None), "memory", None)
        entry = self._session_tasks.get(session_id)
        if entry is None:
            entry = _SessionEntry(session_id=session_id, memory=memory)
            self._session_tasks[session_id] = entry
        else:
            if entry.task and not entry.task.done():
                entry.task.cancel()
            entry.task = None
            entry.text = ""
            entry.buffer.clear()
            entry.finished_at = None
        return entry

    async def _subscribe_session(self, ws, session_id: str) -> None:
        """Attach *ws* as subscriber for a running session and replay its buffer."""
        entry = self._session_tasks.get(session_id)
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
            replay_items = mem.events.session_wire_events(session_id)
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
            # Send accumulated partial text so the client can display stream progress.
            if entry.text:
                await ws.send(json.dumps({
                    "type": "chunk",
                    "text": entry.text,
                    "_replay": True,
                }))
            await ws.send(json.dumps({
                "type": "replay_end",
                "session_id": session_id,
            }))
        except Exception:
            entry.subscriber = None

    async def _emit_session_status(self, ws, session_id: str, status: str, reason: str) -> None:
        if not session_id:
            return
        if status == "running":
            self._running_sessions.add(session_id)
        elif status in {"idle", "offline", "stale"}:
            self._running_sessions.discard(session_id)
        await ws.send(json.dumps({
            "type": "session_status",
            "session_id": session_id,
            "status": status,
            "reason": reason,
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
                if not local_path:
                    matches = list(self._upload_dir.glob(f"{file_id}_*"))
                    local_path = str(matches[0]) if matches else ""

            if local_path:
                try:
                    with open(local_path, "rb") as _fh:
                        raw = _fh.read()
                    mime = self._detect_mime(local_path, raw)
                    if mime and mime in self._IMAGE_MIMES:
                        b64 = base64.b64encode(raw).decode()
                        images.append(f"data:{mime};base64,{b64}")
                        log.debug("multimodal: encoded image %s (%d bytes)", name, len(raw))
                        continue
                except Exception as e:
                    log.warning("multimodal: failed to read %s: %s", local_path, e)
                # Non-image or read error — inject path as text
                file_lines.append(f"- {name} (local path: {local_path})")
            else:
                url = att.get("url", "")
                file_lines.append(f"- {name} (url: {url})" if url else f"- {name}")

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

    async def _handle_chat(self, ws, data: dict) -> None:
        import time as _time
        _t_recv = _time.monotonic()

        agent = data.get("agent", "default")
        text = data.get("text", "").strip()
        workspace = (data.get("workspace") or "").strip() or None
        client_now = (data.get("client_now") or "").strip()

        log.info(
            "chat recv: agent=%s input=%r workspace=%r",
            agent, text[:80], workspace,
        )

        # Split attachments: images → vision content blocks, others → path text
        attachments = data.get("attachments") or []
        text, images = self._process_attachments(text, attachments)
        _t_attach = _time.monotonic()
        if attachments:
            log.info(
                "chat attachments: n=%d elapsed=%.0fms",
                len(attachments), (_t_attach - _t_recv) * 1000,
            )

        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return

        # Validate workspace name against registry (unknown names are silently dropped)
        if workspace:
            known = {ws_entry.name for ws_entry in self._gateway.base_agent.config.workspaces.list}
            if workspace not in known:
                log.warning("chat: unknown workspace=%r, ignoring (known=%s)", workspace, known)
                workspace = None

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

        _first_event = True
        try:
            async for event in self._gateway.event_stream(agent, text, session_id, images=images, workspace=workspace, client_now=client_now):
                if _first_event:
                    _first_event = False
                    log.info(
                        "chat first_event: session=%s type=%s elapsed=%.0fms",
                        session_id[:12], event.get("type"), (_time.monotonic() - _t_recv) * 1000,
                    )
                await ws.send(json.dumps(event))
                if event.get("type") == "done":
                    log.info(
                        "chat done: session=%s total=%.0fms",
                        session_id[:12], (_time.monotonic() - _t_recv) * 1000,
                    )
                    await self._emit_session_status(ws, session_id, "idle", "done")
                elif event.get("type") == "tool_result" and event.get("tool") == "remember_skill":
                    # Push refreshed skills list so the Skills panel updates without a tab switch
                    await self._handle_list_skills(ws)
                elif event.get("type") == "error":
                    await self._emit_session_status(ws, session_id, "idle", "error")
        except Exception as e:
            log.error("event_stream error: %s", e, exc_info=True)
            await self._emit_session_status(ws, session_id, "idle", "error")
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

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
        await self._emit_session_status(ws, session_id, "running", "start")
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
            await ws.send(json.dumps({"type": "done", "text": merged or "(empty broadcast response)"}))
        except Exception as e:
            log.error("broadcast_mention error: %s", e, exc_info=True)
            await self._emit_session_status(ws, session_id, "idle", "error")
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

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
        await self._emit_session_status(ws, session_id, "running", "start")

        try:
            async for event in self._gateway.pipeline_stream(agent_names, text, session_id):
                await ws.send(json.dumps(event))
                if event.get("type") == "done":
                    await self._emit_session_status(ws, session_id, "idle", "done")
                elif event.get("type") == "error":
                    await self._emit_session_status(ws, session_id, "idle", "error")
        except Exception as e:
            log.error("pipeline_stream error: %s", e, exc_info=True)
            await self._emit_session_status(ws, session_id, "idle", "error")
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

    async def _handle_orchestrate(self, ws, data: dict) -> None:
        text = data.get("text", "").strip()
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return

        session_id = data.get("session_id") or make_id("s-")
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))
        await self._emit_session_status(ws, session_id, "running", "start")

        try:
            result = await self._gateway.orchestrate(text, session_id)
            await self._emit_session_status(ws, session_id, "idle", "done")
            await ws.send(json.dumps({"type": "done", "text": result}))
        except Exception as e:
            log.error("orchestrate error: %s", e, exc_info=True)
            await self._emit_session_status(ws, session_id, "idle", "error")
            await ws.send(json.dumps({"type": "error", "message": str(e)}))

    async def _handle_run_hierarchical(self, ws, data: dict) -> None:
        text = data.get("text", "").strip()
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Empty text"}))
            return
        commander = (data.get("commander") or "").strip()
        if not commander:
            await ws.send(json.dumps({"type": "error", "message": "commander is required"}))
            return
        mode = (data.get("mode") or "parallel").strip().lower()
        session_id = data.get("session_id") or make_id("s-")
        await ws.send(json.dumps({"type": "session", "session_id": session_id}))
        await self._emit_session_status(ws, session_id, "running", "start")
        try:
            result = await self._gateway.execute_hierarchical(
                commander_name=commander,
                text=text,
                mode=mode,
                session_id=session_id,
            )
            await self._emit_session_status(ws, session_id, "idle", "done")
            await ws.send(json.dumps({"type": "done", "text": result}))
        except Exception as e:
            log.error("run_hierarchical error: %s", e, exc_info=True)
            await self._emit_session_status(ws, session_id, "idle", "error")
            await ws.send(json.dumps({"type": "error", "message": str(e)}))
