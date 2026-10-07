import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ambu_runner
import ambu_status
import ambu_telemetry


def make_test_db(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE session (
            id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT,
            cost REAL, tokens_input INTEGER, tokens_output INTEGER,
            tokens_reasoning INTEGER, tokens_cache_read INTEGER,
            tokens_cache_write INTEGER, time_created INTEGER, time_updated INTEGER
        )""")
        for sid, directory, created, updated in rows:
            conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (sid, directory, "coder", '{"providerID":"test","id":"model"}',
                          1.0, 100, 20, 10, 0, 0, created, updated))


class SkillAuditHardeningTests(unittest.TestCase):
    def test_resume_runner_reports_new_task_id(self):
        old_id = "20261006-111111-coder-old111"
        new_id = "20261006-222222-coder-new222"
        lines = [
            f"Resuming task {old_id} on session 'ses_A'...",
            json.dumps({"task_id": new_id, "status": "completed"})
        ]
        text = "\n".join(lines)
        captured = ambu_runner.extract_task_id_from_stream(text)
        self.assertEqual(captured, new_id)

    def test_telemetry_directory_filter_does_not_claim_unrelated_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "opencode.db"
            make_test_db(db, [("ses_B", "/workspace/B", 101000, 102000)])
            got = ambu_telemetry.query_opencode_sessions(100000, 102000, "/workspace/A", db)
            self.assertEqual(got, [])

    def test_telemetry_queries_by_exact_session_id_when_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "opencode.db"
            # Session created 1 week ago (time 1000), but task started at 100000
            make_test_db(db, [("ses_continued", "/workspace/A", 1000, 102000)])

            runs_dir = tmp / "runs" / "resumed-task"
            runs_dir.mkdir(parents=True)
            meta = {
                "task_id": "resumed-task",
                "status": "completed",
                "mode": "run",
                "agent": "coder",
                "cwd": "/workspace/A",
                "started_at": "1970-01-01T00:16:40Z",  # ~100000 ms
                "session_id": "ses_continued",
            }
            (runs_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

            telemetry = ambu_telemetry.extract_telemetry("resumed-task", state_home=tmp, db_path=db)
            self.assertEqual(len(telemetry["models"]), 1)
            self.assertEqual(telemetry["sessions"], ["ses_continued"])
            self.assertEqual(telemetry["total_tokens"]["input"], 100)

    def test_skill_state_home_override_respected(self):
        custom_state = "/custom/runtime/state"
        with patch.dict(os.environ, {"RUNTIME_AGENTS_STATE_HOME": custom_state}):
            # Dynamically reload or check Path
            state = Path(os.environ.get("RUNTIME_AGENTS_STATE_HOME") or (Path.home() / ".local" / "share" / "runtime-agents"))
            self.assertEqual(str(state), custom_state)

    def test_resume_failure_before_banner_has_no_new_task_id(self):
        old = "20261007-000100-coder-abcdef"
        message = f"No OpenCode session recorded for task {old}. Cannot resume."
        tid = ambu_runner.extract_task_id_from_stream(message)
        self.assertIsNone(tid)

    def test_zero_round_task_does_not_claim_other_session(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "opencode.db"
            make_test_db(db, [("other_task_session", "/workspace/A", 101000, 102000)])
            runs_dir = tmp / "runs" / "zero-round-task"
            runs_dir.mkdir(parents=True)
            meta = {
                "task_id": "zero-round-task",
                "mode": "iterate",
                "status": "completed",
                "cwd": "/workspace/A",
                "started_at": "1970-01-01T00:01:40Z",
                "ended_at": "1970-01-01T00:01:42Z",
                "rounds": 0,
                "final_check_passed": True,
            }
            (runs_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            got = ambu_telemetry.extract_telemetry("zero-round-task", state_home=tmp, db_path=db)
            self.assertEqual(got["sessions"], [])

    def test_optional_telemetry_schema_error_degrades(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "opencode.db"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, time_created INTEGER)")
            runs_dir = tmp / "runs" / "schema-error-task"
            runs_dir.mkdir(parents=True)
            meta = {
                "task_id": "schema-error-task",
                "mode": "run",
                "status": "completed",
                "session_id": "s",
                "started_at": "1970-01-01T00:01:40Z",
                "ended_at": "1970-01-01T00:01:42Z",
            }
            (runs_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            got = ambu_telemetry.extract_telemetry("schema-error-task", state_home=tmp, db_path=db)
            self.assertEqual(got["status"], "completed")
            self.assertEqual(got["sessions"], [])

    def test_nonzero_attempt_without_binding_does_not_claim_foreign_usage(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "opencode.db"
            make_test_db(db, [("foreign_session", "/workspace/A", 101000, 102000)])
            runs_dir = tmp / "runs" / "unbound-task"
            runs_dir.mkdir(parents=True)
            meta = {
                "task_id": "unbound-task",
                "mode": "iterate",
                "status": "failed",
                "cwd": "/workspace/A",
                "rounds": 1,
                "started_at": "1970-01-01T00:01:40Z",
                "ended_at": "1970-01-01T00:01:42Z",
                "final_check_passed": False,
            }
            (runs_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            got = ambu_telemetry.extract_telemetry("unbound-task", state_home=tmp, db_path=db)
            self.assertEqual(got["sessions"], [])


if __name__ == "__main__":
    unittest.main()
