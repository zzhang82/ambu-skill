#!/usr/bin/env python3
"""Supervised execution runner for Ambu (agentctl).

Wraps agentctl commands ('do', 'iterate', 'run') in a monitored subprocess,
suppresses noisy polling loops, streams high-signal progress milestones,
and emits an organized summary with exact token spend by model and loop counts.
"""

from __future__ import annotations

import argparse
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


def log_milestone(prefix: str, message: str, color: str = ANSI_CYAN) -> None:
    timestamp = time.strftime("%H:%M:%S")
    print(f"{color}{ANSI_BOLD}[Ambu {timestamp}]{ANSI_RESET} {message}", flush=True)


def parse_milestone(line: str) -> str | None:
    """Detect meaningful milestone events from agentctl execution stream."""
    clean = line.strip()
    if not clean:
        return None

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
    if "Check passed!" in clean or "succeeded" in clean.lower() and "check" in clean.lower():
        return f"✅ Verification passed!"
    return None


def extract_task_id_from_stream(text: str) -> str | None:
    """Extract 2026xxxx-xxxxxx-xxxx-xxxx task id from output."""
    m = re.search(r'"task_id":\s*"([0-9]{8}-[0-9]{6}-[a-zA-Z0-9_-]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r'\b([0-9]{8}-[0-9]{6}-[a-zA-Z0-9_-]+)\b', text)
    if m:
        return m.group(1)
    return None


def run_supervised(cmd: list[str]) -> tuple[int, str, str | None]:
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
                    if not task_id:
                        found_id = extract_task_id_from_stream(line)
                        if found_id:
                            task_id = found_id
                    milestone = parse_milestone(line)
                    if milestone:
                        log_milestone("milestone", milestone)
        except Exception:
            pass

    t = threading.Thread(target=read_output, daemon=True)
    t.start()

    def handle_signal(sig, _frame):
        log_milestone("signal", f"Received termination signal ({sig}). Cancelling Ambu task group...", ANSI_YELLOW)
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
    if not task_id:
        task_id = extract_task_id_from_stream(full_stdout)

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

    args = parser.parse_args()

    agentctl_cmd = build_agentctl_cmd(args)
    log_milestone("start", f"Launching: {' '.join(agentctl_cmd)}", ANSI_GREEN)

    rc, stdout, task_id = run_supervised(agentctl_cmd)

    if not task_id:
        # Fallback to checking latest task
        tasks_file = Path.home() / ".local" / "share" / "runtime-agents" / "tasks.jsonl"
        if tasks_file.is_file():
            try:
                for line in tasks_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        data = json.loads(line)
                        if data.get("task_id"):
                            task_id = data["task_id"]
            except Exception:
                pass

    if task_id:
        telemetry = ambu_telemetry.extract_telemetry(task_id)
        if not getattr(args, "json_only", False):
            print("\n" + "=" * 60)
            print(ambu_telemetry.format_markdown_report(telemetry))
            print("=" * 60 + "\n")

        print("\n```json")
        print(json.dumps(telemetry, indent=2, sort_keys=True))
        print("```\n")
    else:
        log_milestone("warn", "Could not resolve task_id for telemetry extraction.", ANSI_YELLOW)
        print(stdout)

    return rc


if __name__ == "__main__":
    sys.exit(main())
