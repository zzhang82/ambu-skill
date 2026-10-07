#!/usr/bin/env python3
"""Supervised execution runner for Ambu (agentctl).

Wraps agentctl commands ('do', 'iterate', 'run') in a monitored subprocess,
suppresses noisy polling loops, streams high-signal progress milestones,
and emits an organized summary with exact token spend by model and loop counts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# Add script directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ambu_telemetry


ANSI_RESET = "\033[0m"
ANSI_CYAN = "\033[36m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_BOLD = "\033[1m"


def log_milestone(prefix: str, message: str, color: str = ANSI_CYAN, json_only: bool = False) -> None:
    timestamp = time.strftime("%H:%M:%S")
    target_stream = sys.stderr if json_only else sys.stdout
    print(f"{color}{ANSI_BOLD}[Ambu {timestamp}]{ANSI_RESET} {message}", file=target_stream, flush=True)


def parse_milestone(line: str) -> str | None:
    """Detect meaningful milestone events from agentctl execution stream."""
    clean = line.strip()
    if not clean:
        return None

    clean_lower = clean.lower()
    if clean.startswith("[Round 0] Evaluating initial check:"):
        cmd = clean.split(":", 1)[1].strip()
        return f"🔍 Evaluating initial check: `{cmd}`"
    if clean.startswith("[Round 0] Check failed"):
        return f"❌ Initial check failed. Dispatching agent..."
    if "[Round " in clean and "] Preparing agent dispatch..." in clean:
        m = re.search(r"\[Round (\d+/\d+)\]", clean)
        rnd = m.group(1) if m else "?"
        return f"🔄 Starting Round {rnd}..."
    if "[Round " in clean and "] Dispatching agent" in clean:
        return f"🚀 {clean}"
    if "Attempt " in clean and "Running on model" in clean:
        return f"⚙️ {clean}"
    if "Failed with exit code" in clean:
        return f"⚠️ {clean}"
    if "Falling back to next candidate model:" in clean:
        return f"🔀 {clean}"
    if "Agent completed successfully. Running verification check..." in clean:
        return f"🧪 Agent finished iteration. Running verification check..."
    if "[ANTI-LOOP HALT]" in clean:
        return f"🛑 Anti-loop halt triggered: {clean}"
    if "check passed" in clean_lower or "verification passed" in clean_lower:
        return f"✅ Verification passed!"
    return None


def extract_task_id_from_stream(text: str) -> str | None:
    """Extract 2026xxxx-xxxxxx-xxxx-xxxx task id from output."""
    if not text:
        return None
    # 1. Prefer structured JSON output: {"task_id": "..."}
    json_matches = re.findall(r'"task_id":\s*"([0-9]{8}-[0-9]{6}-[a-zA-Z0-9_-]+)"', text)
    if json_matches:
        return json_matches[-1]
    # 2. Look for "Starting task <id>"
    starting = re.findall(r'Starting task\s+([0-9]{8}-[0-9]{6}-[a-zA-Z0-9_-]+)', text)
    if starting:
        return starting[-1]
    # 3. Look for "Task <id> finished"
    finished = re.findall(r'Task\s+([0-9]{8}-[0-9]{6}-[a-zA-Z0-9_-]+)\s+finished', text)
    if finished:
        return finished[-1]
    return None


def run_supervised(cmd: list[str], json_only: bool = False) -> tuple[int, str, str | None]:
    """Execute command in monitored subprocess group."""
    popen_kwargs: dict[str, Any] = {
        "text": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "bufsize": 1,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)

    stdout_lines: list[str] = []
    task_id: str | None = None

    def read_output():
        nonlocal task_id
        try:
            if proc.stdout:
                for line in proc.stdout:
                    stdout_lines.append(line)
                    found_id = extract_task_id_from_stream(line)
                    if found_id:
                        if '"task_id":' in line or "Starting task" in line or not task_id:
                            task_id = found_id
                    milestone = parse_milestone(line)
                    if milestone:
                        log_milestone("milestone", milestone, json_only=json_only)
        except Exception:
            pass

    t = threading.Thread(target=read_output, daemon=True)
    t.start()

    def handle_signal(sig, _frame):
        log_milestone("signal", f"Received termination signal ({sig}). Cancelling Ambu task group...", ANSI_YELLOW, json_only=json_only)
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                pass
        else:
            proc.terminate()
        sys.exit(130)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    proc.wait()
    t.join(timeout=2.0)

    full_stdout = "".join(stdout_lines)
    final_id = extract_task_id_from_stream(full_stdout)
    if final_id:
        task_id = final_id

    return proc.returncode, full_stdout, task_id


def build_agentctl_cmd(args: argparse.Namespace) -> list[str]:
    """Translate runner args to agentctl command invocation."""
    cmd = ["agentctl", args.subcommand]

    if args.subcommand == "do":
        cmd.append(args.goal)
        if args.workspace:
            cmd.extend(["--workspace", args.workspace])
        if args.check:
            cmd.extend(["--check", args.check])
        if args.agent:
            cmd.extend(["--agent", args.agent])
        if args.model:
            cmd.extend(["--model", args.model])
        if args.max_rounds:
            cmd.extend(["--max-rounds", str(args.max_rounds)])
        if args.approve:
            for app in args.approve:
                cmd.extend(["--approve", app])
    elif args.subcommand == "iterate":
        cmd.extend([args.agent, args.goal, "--check", args.check])
        if args.workspace:
            cmd.extend(["--workspace", args.workspace])
        if args.model:
            cmd.extend(["--model", args.model])
        if args.max_rounds:
            cmd.extend(["--max-rounds", str(args.max_rounds)])
    elif args.subcommand == "run":
        cmd.extend([args.agent, args.goal])
        if args.workspace:
            cmd.extend(["--workspace", args.workspace])
        if args.model:
            cmd.extend(["--model", args.model])
    elif args.subcommand == "resume":
        cmd.append(args.task_id)
        if getattr(args, "model", None):
            cmd.extend(["--model", args.model])
        if getattr(args, "prompt", None):
            cmd.extend(["--prompt", args.prompt])

    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ambu_runner",
        description="Supervised runner for agentctl with clean milestones and token telemetry.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # do command
    p_do = sub.add_parser("do", help="Run smart intent routing with optional iterate check")
    p_do.add_argument("goal", help="Task goal or prompt")
    p_do.add_argument("--workspace", help="Declared workspace key or root")
    p_do.add_argument("--check", help="Verification command for iterate loop")
    p_do.add_argument("--agent", help="Explicit agent override")
    p_do.add_argument("--model", help="Explicit model override")
    p_do.add_argument("--max-rounds", type=int, default=5, help="Max iteration rounds")
    p_do.add_argument("--approve", action="append", help="Capabilities to approve")
    p_do.add_argument("--json-only", action="store_true", help="Print only raw JSON schema at end")

    # iterate command
    p_iter = sub.add_parser("iterate", help="Run iterative loop with anti-loop guardrails")
    p_iter.add_argument("agent", help="Agent name")
    p_iter.add_argument("goal", help="Task goal")
    p_iter.add_argument("--check", required=True, help="Verification command")
    p_iter.add_argument("--workspace", help="Workspace key")
    p_iter.add_argument("--model", help="Model override")
    p_iter.add_argument("--max-rounds", type=int, default=5, help="Max iteration rounds")
    p_iter.add_argument("--json-only", action="store_true", help="Print only raw JSON schema at end")

    # run command
    p_run = sub.add_parser("run", help="Run single task dispatch")
    p_run.add_argument("agent", help="Agent name")
    p_run.add_argument("goal", help="Task goal")
    p_run.add_argument("--workspace", help="Workspace key")
    p_run.add_argument("--model", help="Model override")
    p_run.add_argument("--json-only", action="store_true", help="Print only raw JSON schema at end")

    # resume command
    p_res = sub.add_parser("resume", help="Resume an interrupted or failed task using its OpenCode session")
    p_res.add_argument("task_id", help="Task ID to resume")
    p_res.add_argument("--model", help="Alternative model to use for resumption (e.g. after quota exhaustion)")
    p_res.add_argument("--prompt", help="Optional specific continuation instructions")
    p_res.add_argument("--json-only", action="store_true", help="Print only raw JSON schema at end")

    args = parser.parse_args()
    json_only = bool(getattr(args, "json_only", False))

    agentctl_cmd = build_agentctl_cmd(args)
    log_milestone("start", f"Launching: {' '.join(agentctl_cmd)}", ANSI_GREEN, json_only=json_only)

    rc, stdout, task_id = run_supervised(agentctl_cmd, json_only=json_only)

    if not task_id:
        if json_only:
            err_payload = {
                "error": "task_launch_failed",
                "returncode": rc,
                "message": "Task execution finished without producing an authoritative task ID.",
                "output": stdout.strip(),
            }
            print(json.dumps(err_payload, indent=2, sort_keys=True))
        else:
            log_milestone("error", "Task execution failed before minting a task ID.", ANSI_RED, json_only=False)
            if stdout.strip():
                print(stdout.strip(), file=sys.stderr)
        return rc if rc != 0 else 1

    telemetry = ambu_telemetry.extract_telemetry(task_id)
    if not json_only:
        run_dir = ambu_telemetry.get_task_run_dir(task_id)
        if run_dir and (run_dir / "stdout.log").is_file():
            agent_output = (run_dir / "stdout.log").read_text(encoding="utf-8").strip()
            if agent_output:
                print("\n" + "=" * 60)
                print(f"📄 Agent Response Output ({telemetry.get('agent', 'agent')}):")
                print("=" * 60)
                print(agent_output)

        print("\n" + "=" * 60)
        print(ambu_telemetry.format_markdown_report(telemetry))
        print("=" * 60 + "\n")

        # Actionable Recovery Guidance if task failed or blocked
        task_status = telemetry.get("status")
        session_list = telemetry.get("sessions") or []
        last_session = session_list[-1] if session_list else None
        if (rc != 0 or task_status in ("failed", "blocked")) and last_session:
            print("=" * 60)
            print("💡 Actionable Recovery Options:")
            print(f"Task `{task_id}` has an active OpenCode session (`{last_session}`).")
            print("You can continue the conversation session using an alternative model:")
            print(f"  python3 scripts/ambu_runner.py resume {task_id} --model <alternative_model>")
            print("=" * 60 + "\n")

        print("```json")
        print(json.dumps(telemetry, indent=2, sort_keys=True))
        print("```\n")
    else:
        # In json_only mode, print pure valid JSON directly to stdout
        print(json.dumps(telemetry, indent=2, sort_keys=True))

    return rc


if __name__ == "__main__":
    sys.exit(main())
