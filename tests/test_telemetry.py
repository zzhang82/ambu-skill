import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ambu_telemetry


class AmbuTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ambu-test-"))
        self.db_path = self.tmp / "opencode.db"
        self._init_mock_db()

    def _init_mock_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                directory TEXT,
                agent TEXT,
                model TEXT,
                cost REAL,
                tokens_input INTEGER,
                tokens_output INTEGER,
                tokens_reasoning INTEGER,
                tokens_cache_read INTEGER,
                tokens_cache_write INTEGER,
                time_created INTEGER,
                time_updated INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE part (
                id TEXT PRIMARY KEY,
                message_id TEXT,
                time_created INTEGER,
                data TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                time_created INTEGER,
                data TEXT
            )
        """)
        # Insert a sample session
        model_json = json.dumps({"id": "gpt-6-astra", "providerID": "local", "variant": "max"})
        cur.execute("""
            INSERT INTO session VALUES (
                'ses_mock_1', '/test/dir', 'oracle', ?, 0.05,
                1000, 200, 150, 5000, 0, 1000000, 1010000
            )
        """, (model_json,))
        # Insert tool parts
        cur.execute("INSERT INTO message VALUES ('msg_1', 'ses_mock_1', 1005000, '{}')")
        cur.execute("""
            INSERT INTO part VALUES ('prt_1', 'msg_1', 1005100, ?)
        """, (json.dumps({"type": "tool", "tool": "read"}),))
        cur.execute("""
            INSERT INTO part VALUES ('prt_2', 'msg_1', 1005200, ?)
        """, (json.dumps({"type": "tool", "tool": "read"}),))
        cur.execute("""
            INSERT INTO part VALUES ('prt_3', 'msg_1', 1005300, ?)
        """, (json.dumps({"type": "tool", "tool": "bash"}),))
        conn.commit()
        conn.close()

    def test_query_opencode_sessions(self):
        sessions = ambu_telemetry.query_opencode_sessions(
            start_ms=999000, end_ms=1020000, db_path=self.db_path
        )
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["session_id"], "ses_mock_1")
        self.assertEqual(s["model"], "local/gpt-6-astra")
        self.assertEqual(s["variant"], "max")
        self.assertEqual(s["tokens_input"], 1000)
        self.assertEqual(s["tokens_output"], 200)
        self.assertEqual(s["tokens_reasoning"], 150)
        self.assertEqual(s["tokens_cache_read"], 5000)

    def test_query_tools_used(self):
        tools = ambu_telemetry.query_tools_used_for_sessions(["ses_mock_1"], db_path=self.db_path)
        self.assertEqual(tools.get("read"), 2)
        self.assertEqual(tools.get("bash"), 1)

    def test_extract_telemetry_with_metadata(self):
        runs_dir = self.tmp / "runs" / "test-task-123"
        runs_dir.mkdir(parents=True)
        meta = {
            "task_id": "test-task-123",
            "status": "completed",
            "mode": "iterate",
            "agent": "oracle",
            "session_id": "ses_mock_1",
            "cwd": "/test/dir",
            "started_at": "1970-01-01T00:16:39Z",  # ~999,000 ms
            "ended_at": "1970-01-01T00:16:51Z",    # ~1,011,000 ms
            "rounds": 2,
            "final_check_passed": True,
        }
        (runs_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

        telemetry = ambu_telemetry.extract_telemetry(
            "test-task-123", state_home=self.tmp, db_path=self.db_path
        )
        self.assertEqual(telemetry["task_id"], "test-task-123")
        self.assertEqual(telemetry["status"], "completed")
        self.assertEqual(telemetry["loop_count"], 2)
        self.assertEqual(telemetry["verification_state"], "passed")
        self.assertTrue(telemetry["final_check_passed"])
        self.assertEqual(len(telemetry["models"]), 1)
        self.assertEqual(telemetry["models"][0]["model"], "local/gpt-6-astra")
        self.assertEqual(telemetry["total_tokens"]["input"], 1000)
        self.assertEqual(telemetry["total_tokens"]["output"], 200)
        self.assertEqual(telemetry["total_tokens"]["reasoning"], 150)
        self.assertEqual(telemetry["total_tokens"]["cache_read"], 5000)
        self.assertEqual(telemetry["total_tokens"]["total"], 6350)
        self.assertEqual(telemetry["tools_used"]["read"], 2)

    def test_direct_run_without_check_reports_verification_not_run(self):
        runs_dir = self.tmp / "runs" / "test-run-456"
        runs_dir.mkdir(parents=True)
        meta = {
            "task_id": "test-run-456",
            "status": "completed",
            "mode": "run",
            "agent": "oracle",
            "cwd": "/test/dir",
            "started_at": "1970-01-01T00:16:39Z",
            "ended_at": "1970-01-01T00:16:51Z",
        }
        (runs_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

        telemetry = ambu_telemetry.extract_telemetry(
            "test-run-456", state_home=self.tmp, db_path=self.db_path
        )
        self.assertEqual(telemetry["verification_state"], "not_run")
        self.assertIsNone(telemetry["final_check_passed"])
        report = ambu_telemetry.format_markdown_report(telemetry)
        self.assertIn("Direct Execution (No Check)", report)


if __name__ == "__main__":
    unittest.main()
