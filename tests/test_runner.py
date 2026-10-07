import argparse
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ambu_runner


class AmbuRunnerTests(unittest.TestCase):
    def test_parse_milestone_events(self):
        self.assertIn("Evaluating initial check", ambu_runner.parse_milestone("[Round 0] Evaluating initial check: pytest tests"))
        self.assertIn("Initial check failed", ambu_runner.parse_milestone("[Round 0] Check failed (exit 1). Gap: execution"))
        self.assertIn("Starting Round 1/3", ambu_runner.parse_milestone("[Round 1/3] Preparing agent dispatch..."))
        self.assertIn("Attempt 1", ambu_runner.parse_milestone("[oracle] Attempt 1/4: Running on model 'local/gpt-6-astra'..."))
        self.assertIn("Verification passed", ambu_runner.parse_milestone("[Round 1] Check passed!"))
        # Verify variant wording
        self.assertIn("Verification passed", ambu_runner.parse_milestone("[Round 1] Verification passed! Goal achieved."))
        self.assertIn("Verification passed", ambu_runner.parse_milestone("[Round 0] Check passed immediately. Task completed."))

    def test_extract_task_id(self):
        text = '{"task_id": "20261006-222134-iterate-oracle-e33943", "status": "blocked"}'
        self.assertEqual(ambu_runner.extract_task_id_from_stream(text), "20261006-222134-iterate-oracle-e33943")

    def test_build_agentctl_cmd_do(self):
        args = argparse.Namespace(
            subcommand="do",
            goal="Refactor auth",
            workspace="test-ws",
            check="pytest tests",
            agent="coder",
            model="local/grok-4.7",
            max_rounds=3,
            approve=["workspace_write"],
        )
        cmd = ambu_runner.build_agentctl_cmd(args)
        expected = [
            "agentctl", "do", "Refactor auth",
            "--workspace", "test-ws",
            "--check", "pytest tests",
            "--agent", "coder",
            "--model", "local/grok-4.7",
            "--max-rounds", "3",
            "--approve", "workspace_write",
        ]
        self.assertEqual(cmd, expected)

    def test_build_agentctl_cmd_iterate(self):
        args = argparse.Namespace(
            subcommand="iterate",
            agent="fixer",
            goal="Fix typo",
            check="pytest tests/unit",
            workspace="demo",
            model=None,
            max_rounds=5,
        )
        cmd = ambu_runner.build_agentctl_cmd(args)
        expected = [
            "agentctl", "iterate", "fixer", "Fix typo",
            "--check", "pytest tests/unit",
            "--workspace", "demo",
            "--max-rounds", "5",
        ]
        self.assertEqual(cmd, expected)

    def test_build_agentctl_cmd_resume(self):
        args = argparse.Namespace(
            subcommand="resume",
            task_id="20261006-223151-oracle-55f2e2",
            model="local/gemini-3.8-flash-high",
            prompt="Continue the scan from previous turn",
        )
        cmd = ambu_runner.build_agentctl_cmd(args)
        expected = [
            "agentctl", "resume", "20261006-223151-oracle-55f2e2",
            "--model", "local/gemini-3.8-flash-high",
            "--prompt", "Continue the scan from previous turn",
        ]
        self.assertEqual(cmd, expected)

    def test_missing_task_id_fails_closed_without_unrelated_telemetry(self):
        # When agentctl fails before creating a task id, runner must fail closed and emit error
        with patch.object(sys, "argv", ["ambu_runner", "do", "broken goal", "--json-only"]), \
             patch.object(ambu_runner, "run_supervised", return_value=(2, "Invalid workspace: nonexistent", None)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            rc = ambu_runner.main()
            self.assertEqual(rc, 2)
            output = mock_stdout.getvalue()
            # Output must be pure valid JSON
            payload = json.loads(output)
            self.assertEqual(payload["error"], "task_launch_failed")
            self.assertEqual(payload["returncode"], 2)
            self.assertIn("nonexistent", payload["output"])

    def test_json_only_mode_emits_valid_json_on_success(self):
        telemetry = {
            "task_id": "20261006-111111-oracle-aabbcc",
            "status": "completed",
            "duration_seconds": 12.3,
            "total_tokens": {"total": 5000},
        }
        with patch.object(sys, "argv", ["ambu_runner", "do", "inspect", "--json-only"]), \
             patch.object(ambu_runner, "run_supervised", return_value=(0, 'task_id: 20261006-111111-oracle-aabbcc', "20261006-111111-oracle-aabbcc")), \
             patch.object(ambu_runner.ambu_telemetry, "extract_telemetry", return_value=telemetry), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            rc = ambu_runner.main()
            self.assertEqual(rc, 0)
            output = mock_stdout.getvalue()
            parsed = json.loads(output)
            self.assertEqual(parsed["task_id"], "20261006-111111-oracle-aabbcc")
            self.assertEqual(parsed["status"], "completed")


if __name__ == "__main__":
    unittest.main()
