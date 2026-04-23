"""Tests for security redaction and retention executor."""
from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path


class TestRedactCredentials(unittest.TestCase):
    def test_strips_anthropic_key(self):
        from hushclaw.core.security import redact_credentials
        raw = "Error calling sk-ant-api03-abc123xyz567890 — connection refused"
        result = redact_credentials(raw)
        self.assertNotIn("sk-ant-api03", result)
        self.assertIn("[REDACTED]", result)

    def test_strips_openai_key(self):
        from hushclaw.core.security import redact_credentials
        raw = "invalid key sk-abcDEF1234567890xxxxxxxx provided"
        result = redact_credentials(raw)
        self.assertNotIn("sk-abc", result)
        self.assertIn("[REDACTED]", result)

    def test_strips_bearer_token(self):
        from hushclaw.core.security import redact_credentials
        raw = "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
        result = redact_credentials(raw)
        self.assertNotIn("eyJhbGci", result)
        self.assertIn("[REDACTED]", result)

    def test_clean_string_unchanged(self):
        from hushclaw.core.security import redact_credentials
        raw = "Context window too long — please reduce input"
        self.assertEqual(redact_credentials(raw), raw)

    def test_error_classification_redacts(self):
        """classify_error() should not expose credentials in its message field."""
        from hushclaw.core.errors import classify_error
        exc = ValueError("HTTP 401 — invalid key sk-abcDEFGHIJKLMNOP returned")
        result = classify_error(exc)
        self.assertNotIn("sk-abc", result.message)
        self.assertTrue(result.is_auth_failure)


class TestRetentionExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_prunes_old_events(self):
        from hushclaw.memory.store import MemoryStore
        from hushclaw.runtime.retention import RetentionExecutor

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryStore(data_dir=Path(tmpdir))

            # Write an ancient event (200 days ago)
            old_ts = int((time.time() - 200 * 86400) * 1000)
            memory.conn.execute(
                "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, "
                "type, payload_json, artifact_id, status, ts) "
                "VALUES ('ev-old', 's-old', '', '', '', 'test', '{}', '', 'completed', ?)",
                (old_ts,),
            )
            # Write a recent event (1 hour ago)
            recent_ts = int((time.time() - 3600) * 1000)
            memory.conn.execute(
                "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, "
                "type, payload_json, artifact_id, status, ts) "
                "VALUES ('ev-recent', 's-recent', '', '', '', 'test', '{}', '', 'completed', ?)",
                (recent_ts,),
            )
            memory.conn.commit()

            executor = RetentionExecutor(memory)
            await executor._enforce()

            ids = [r[0] for r in memory.conn.execute("SELECT event_id FROM events").fetchall()]
            self.assertNotIn("ev-old", ids)
            self.assertIn("ev-recent", ids)

    async def test_no_policies_uses_default(self):
        """Empty security_policies table → default 90-day retention applied."""
        from hushclaw.memory.store import MemoryStore
        from hushclaw.runtime.retention import RetentionExecutor

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryStore(data_dir=Path(tmpdir))
            executor = RetentionExecutor(memory)
            policies = executor._load_policies()
            # No rows → empty list → default applied in _enforce
            self.assertEqual(policies, [])

    def test_start_safe_outside_event_loop(self):
        """RetentionExecutor.start() is safe to call from sync context."""
        from hushclaw.memory.store import MemoryStore
        from hushclaw.runtime.retention import RetentionExecutor

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryStore(data_dir=Path(tmpdir))
            executor = RetentionExecutor(memory)
            executor.start()  # must not raise
            # task stays None because no event loop is running
            self.assertIsNone(executor._task)

    async def test_retention_uses_artifact_id_column(self):
        """RetentionExecutor must use events.artifact_id column to locate orphaned artifacts.

        ADR-0005 requires that events.complete() write the artifact_id to the
        column (not only payload_json).  This test verifies the full round-trip:
        write an event via events.complete() → event expires → artifact pruned.
        """
        from hushclaw.memory.store import MemoryStore
        from hushclaw.runtime.retention import RetentionExecutor

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryStore(data_dir=Path(tmpdir))

            # Insert an artifact row with an old created timestamp.
            old_sec = int(time.time()) - 200 * 86400
            memory.conn.execute(
                "INSERT INTO artifacts (artifact_id, session_id, tool_name, storage_path, "
                "size_bytes, mime_type, summary, created) "
                "VALUES ('art-test', 'ses-art', 'write_file', '/tmp/x', 10, 'text/plain', '', ?)",
                (old_sec,),
            )

            # Write an event that references the artifact via events.complete().
            eid = memory.events.append(
                "ses-art", "tool_call_completed",
                {"tool": "write_file", "artifact_id": "art-test"},
                step_id="call-art",
                status="pending",
            )
            memory.events.complete(eid, {
                "tool": "write_file",
                "artifact_id": "art-test",
            })

            # Confirm the column was written (not just payload_json).
            col_val = memory.conn.execute(
                "SELECT artifact_id FROM events WHERE event_id=?", (eid,)
            ).fetchone()[0]
            self.assertEqual(col_val, "art-test",
                             "artifact_id column must be written by events.complete()")

            # Back-date the event so retention considers it expired.
            old_ts = int((time.time() - 200 * 86400) * 1000)
            memory.conn.execute("UPDATE events SET ts=? WHERE event_id=?", (old_ts, eid))
            memory.conn.commit()

            executor = RetentionExecutor(memory)
            await executor._enforce()

            # The event should be gone (expired).
            remaining_events = [
                r[0] for r in memory.conn.execute("SELECT event_id FROM events").fetchall()
            ]
            self.assertNotIn(eid, remaining_events)

            # The artifact should also be pruned (no live events reference it).
            remaining_artifacts = [
                r[0] for r in memory.conn.execute(
                    "SELECT artifact_id FROM artifacts"
                ).fetchall()
            ]
            self.assertNotIn("art-test", remaining_artifacts,
                             "artifact must be pruned when its referencing event expires")
            memory.close()


if __name__ == "__main__":
    unittest.main()
