import json
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ambu_status


class AmbuStatusTests(unittest.TestCase):
    def test_format_elapsed_completed_task(self):
        # A task that ran for 45 seconds should show 0m 45s, not grow based on current time
        start = "2026-10-07T00:00:00Z"
        end = "2026-10-07T00:00:45Z"
        self.assertEqual(ambu_status.format_elapsed(start, end), "0m 45s")

    def test_format_elapsed_running_task(self):
        # When end_iso is None, calculates elapsed from current time
        start = "2026-10-07T00:00:00Z"
        self.assertIsNotNone(ambu_status.format_elapsed(start))

    def test_list_recent_tasks_combines_queue_and_direct(self):
        queue_events = [
            {"queue_id": "q1", "task_id": "t1", "status": "running", "agent": "coder", "pid": 1234},
        ]
        task_events = [
            {"task_id": "t1", "status": "running", "agent": "coder"},
            {"task_id": "t2", "status": "running", "agent": "oracle", "pid": 5678},
            {"task_id": "t3", "status": "completed", "agent": "fixer"},
        ]
        with patch.object(ambu_status, "read_jsonl", side_effect=[task_events, queue_events]), \
             patch.object(ambu_status, "is_pid_alive", return_value=True):
            with patch("sys.stdout"):
                # In json mode
                with patch("builtins.print") as mock_print:
                    ambu_status.list_recent_tasks(json_mode=True)
                    payload = json.loads(mock_print.call_args.args[0])
                    # Active tasks must include queue item (t1) and direct task (t2)
                    active_ids = {a["task_id"] for a in payload["running"]}
                    self.assertIn("t1", active_ids)
                    self.assertIn("t2", active_ids)
                    self.assertEqual(len(payload["running"]), 2)

    def test_cancel_task_signals_active_process(self):
        task_events = [{"task_id": "t1", "status": "running", "pid": 88888}]
        with patch.object(ambu_status, "read_jsonl", return_value=task_events), \
             patch.object(ambu_status, "is_pid_alive", return_value=True), \
             patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=88888):
            rc = ambu_status.cancel_task("t1")
            self.assertEqual(rc, 0)
            mock_killpg.assert_called_once_with(88888, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
