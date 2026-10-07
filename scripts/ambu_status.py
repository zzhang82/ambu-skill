#!/usr/bin/env python3
"""Single-shot status inspector and debugger for Ambu Runtime Agents.

Provides instant visibility into active runs, worker process health (PID/CPU),
recent tasks, log tails, and safe cancellation without messy polling loops.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

STATE_HOME = Path(os.environ.get("RUNTIME_AGENTS_STATE_HOME") or (Path.home() / ".local" / "share" / "runtime-agents"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    return records


def latest_by_id(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    merged = {}
    for r in records:
        val = r.get(key)
        if val:
            merged[val] = {**merged.get(val, {}), **r}
    return merged


def is_pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def get_task_run_dir(task_id: str) -> Path | None:
    p = STATE_HOME / "runs" / task_id
    return p if p.is_dir() else None


def format_elapsed(start_iso: str | None, end_iso: str | None = None) -> str:
    if not start_iso:
        return "unknown"
    try:
        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        if end_iso:
            end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
            secs = max(0, int(end_dt.timestamp() - start_dt.timestamp()))
        else:
            secs = max(0, int(time.time() - start_dt.timestamp()))
        mins, s = divmod(secs, 60)
        return f"{mins}m {s}s"
    except Exception:
        return "unknown"


def show_task_detail(task_id: str, json_mode: bool = False) -> int:
    tasks = latest_by_id(read_jsonl(STATE_HOME / "tasks.jsonl"), "task_id")
    task = tasks.get(task_id)

    run_dir = get_task_run_dir(task_id)
    metadata = {}
    if run_dir and (run_dir / "metadata.json").is_file():
        try:
            metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        except Exception:
            pass

    merged = {**(task or {}), **metadata}
    pid = merged.get("pid")
    alive = is_pid_alive(pid) if pid else False

    stdout_tail = []
    if run_dir and (run_dir / "stdout.log").is_file():
        lines = (run_dir / "stdout.log").read_text(encoding="utf-8").splitlines()
        stdout_tail = lines[-10:]

    session_id = merged.get("session_id")

    payload = {
        "task_id": task_id,
        "status": merged.get("status", "unknown"),
        "agent": merged.get("agent"),
        "model": merged.get("model"),
        "session_id": session_id,
        "mode": merged.get("mode", "run"),
        "workspace": merged.get("workspace"),
        "goal": merged.get("goal"),
        "pid": pid,
        "process_alive": alive,
        "started_at": merged.get("started_at"),
        "ended_at": merged.get("ended_at"),
        "elapsed": format_elapsed(merged.get("started_at"), merged.get("ended_at")),
        "rounds": merged.get("rounds"),
        "stdout_tail": stdout_tail,
    }

    if json_mode:
        print(json.dumps(payload, indent=2))
        return 0

    status_icon = "🟢" if alive else "✅" if merged.get("status") == "completed" else "❌"
    print(f"\n{status_icon} Task: {task_id}")
    print(f"  Status:       {merged.get('status')} (PID: {pid or 'N/A'}, Alive: {alive})")
    print(f"  Agent:        {merged.get('agent')} (Model: {merged.get('model')})")
    if session_id:
        print(f"  Session:      {session_id}")
    print(f"  Workspace:    {merged.get('workspace') or 'default'}")
    print(f"  Elapsed:      {payload['elapsed']}")
    if merged.get("goal"):
        print(f"  Goal:         {merged['goal'][:100]}...")

    if merged.get("status") in ("failed", "blocked") and session_id:
        print(f"\n  💡 Resumable: python3 scripts/ambu_runner.py resume {task_id} --model <model>")

    if stdout_tail:
        print("\n  --- Recent Log Tail ---")
        for line in stdout_tail[-5:]:
            print(f"  | {line}")
    print("")
    return 0


def list_recent_tasks(limit: int = 5, json_mode: bool = False) -> int:
    tasks = list(latest_by_id(read_jsonl(STATE_HOME / "tasks.jsonl"), "task_id").values())
    queue = latest_by_id(read_jsonl(STATE_HOME / "queue.jsonl"), "queue_id")

    active_tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # 1. Queue items currently running
    for q in queue.values():
        if q.get("status") == "running":
            qid = q.get("queue_id")
            tid = q.get("task_id")
            if tid:
                seen_ids.add(tid)
            if qid:
                seen_ids.add(qid)
            pid = q.get("pid")
            active_tasks.append({
                "type": "queue",
                "id": qid,
                "task_id": tid,
                "agent": q.get("agent"),
                "pid": pid,
                "alive": is_pid_alive(pid),
                "started_at": q.get("started_at"),
            })

    # 2. Direct tasks currently running
    for t in tasks:
        tid = t.get("task_id")
        if t.get("status") == "running" and tid not in seen_ids:
            run_dir = get_task_run_dir(tid)
            meta = {}
            if run_dir and (run_dir / "metadata.json").is_file():
                try:
                    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
                except Exception:
                    pass
            pid = t.get("pid") or meta.get("pid")
            active_tasks.append({
                "type": "direct",
                "id": tid,
                "task_id": tid,
                "agent": t.get("agent"),
                "pid": pid,
                "alive": is_pid_alive(pid),
                "started_at": t.get("started_at"),
            })

    recent = tasks[-limit:] if tasks else []

    if json_mode:
        payload = {
            "running": active_tasks,
            "recent": recent,
        }
        print(json.dumps(payload, indent=2))
        return 0

    print("\n⚡ [Ambu Live Status Overview]")
    if active_tasks:
        print(f"\n🚀 Active Running Tasks ({len(active_tasks)}):")
        for item in active_tasks:
            pid = item.get("pid")
            alive = item.get("alive")
            print(f"  - [{item.get('type')}] Task: {item.get('task_id') or item.get('id')}")
            print(f"    Agent: {item.get('agent')} | PID: {pid} (Alive: {alive}) | Elapsed: {format_elapsed(item.get('started_at'))}")
    else:
        print("\n💤 No currently running worker tasks.")

    print(f"\n📋 Recent Completed / Recorded Tasks (Last {len(recent)}):")
    for t in reversed(recent):
        icon = "✅" if t.get("status") == "completed" else "❌" if t.get("status") == "failed" else "⚠️"
        print(f"  {icon} {t.get('task_id')} | Status: {t.get('status'):<10} | Agent: {t.get('agent'):<8} | Model: {t.get('model')}")

    print("")
    return 0


def cancel_task(task_id: str) -> int:
    tasks = latest_by_id(read_jsonl(STATE_HOME / "tasks.jsonl"), "task_id")
    task = tasks.get(task_id) or {}
    run_dir = get_task_run_dir(task_id)
    meta = {}
    if run_dir and (run_dir / "metadata.json").is_file():
        try:
            meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        except Exception:
            pass

    pid = task.get("pid") or meta.get("pid")

    if pid and is_pid_alive(pid):
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
            print(f"🛑 Signaled termination to task {task_id} (PID {pid}).")
            return 0
        except Exception as e:
            print(f"⚠️ Failed to signal PID {pid}: {e}")
            return 1
    else:
        print(f"Task {task_id} is not actively running (PID alive: False).")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ambu_status",
        description="Fast single-shot status inspector and debugger for Ambu.",
    )
    parser.add_argument("task_id", nargs="?", help="Specific task ID to inspect or cancel")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument("--cancel", action="store_true", help="Cancel active running task")
    parser.add_argument("--limit", type=int, default=5, help="Number of recent tasks to list")

    args = parser.parse_args()

    if args.cancel and args.task_id:
        return cancel_task(args.task_id)

    if args.task_id:
        return show_task_detail(args.task_id, json_mode=args.json)

    return list_recent_tasks(limit=args.limit, json_mode=args.json)


if __name__ == "__main__":
    sys.exit(main())
