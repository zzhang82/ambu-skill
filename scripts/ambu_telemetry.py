"""Telemetry extraction module for Ambu Runtime Agents and OpenCode.

Extracts token spend by model, reasoning tokens, cache read/write,
tool call breakdown, and loop counts from agentctl run artifacts and opencode.db.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_STATE_HOME = Path(os.environ.get("RUNTIME_AGENTS_STATE_HOME") or (Path.home() / ".local" / "share" / "runtime-agents"))
DEFAULT_OPENCODE_DB = Path(os.environ.get("OPENCODE_DB_PATH") or (Path.home() / ".local" / "share" / "opencode" / "opencode.db"))


def parse_iso_to_epoch_ms(iso_str: str | None) -> int | None:
    """Parse ISO8601 string to millisecond epoch timestamp."""
    if not iso_str:
        return None
    try:
        # Handle formats like 2026-10-07T02:21:34.464658+00:00 or Z
        clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def get_task_run_dir(task_id: str, state_home: Path | None = None) -> Path | None:
    """Resolve run directory for a task id."""
    root = (state_home or DEFAULT_STATE_HOME) / "runs" / task_id
    if root.is_dir():
        return root
    return None


def load_task_metadata(task_id: str, state_home: Path | None = None) -> dict[str, Any]:
    """Read metadata.json from task run directory."""
    run_dir = get_task_run_dir(task_id, state_home)
    if not run_dir:
        return {}
    meta_path = run_dir / "metadata.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def query_opencode_sessions(
    start_ms: int,
    end_ms: int | None = None,
    directory: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Query sessions from OpenCode SQLite DB created in the given time window."""
    db = db_path or DEFAULT_OPENCODE_DB
    if not db.is_file():
        return []

    # Generous margin for clock differences (5 seconds before start)
    query_start = max(0, start_ms - 5000)
    query_end = (end_ms + 10000) if end_ms else int(time.time() * 1000) + 10000

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        sql = """
            SELECT id, directory, agent, model, cost,
                   tokens_input, tokens_output, tokens_reasoning,
                   tokens_cache_read, tokens_cache_write,
                   time_created, time_updated
            FROM session
            WHERE time_created >= ? AND time_created <= ?
        """
        params: list[Any] = [query_start, query_end]
        if directory:
            sql += " AND (directory = ? OR directory LIKE ?)"
            params.extend([directory, f"{directory}/%"])
        sql += " ORDER BY time_created ASC"

        cur.execute(sql, params)
        rows = cur.fetchall()

        sessions = []
        for r in rows:
            model_info = r[3]
            model_id = "unknown"
            variant = None
            if model_info:
                try:
                    m = json.loads(model_info) if isinstance(model_info, str) else model_info
                    prov = m.get("providerID", "local")
                    mid = m.get("id", "unknown")
                    model_id = f"{prov}/{mid}" if prov != "local" or "/" not in mid else mid
                    if prov == "local" and not model_id.startswith("local/"):
                        model_id = f"local/{mid}"
                    variant = m.get("variant")
                except Exception:
                    model_id = str(model_info)

            sessions.append({
                "session_id": r[0],
                "directory": r[1],
                "agent": r[2],
                "model": model_id,
                "variant": variant,
                "cost": r[4] or 0.0,
                "tokens_input": r[5] or 0,
                "tokens_output": r[6] or 0,
                "tokens_reasoning": r[7] or 0,
                "tokens_cache_read": r[8] or 0,
                "tokens_cache_write": r[9] or 0,
                "time_created": r[10],
                "time_updated": r[11],
            })
        return sessions
    finally:
        conn.close()


def query_opencode_sessions_by_ids(
    session_ids: list[str],
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Query sessions from OpenCode SQLite DB by exact session IDs."""
    if not session_ids:
        return []
    db = db_path or DEFAULT_OPENCODE_DB
    if not db.is_file():
        return []

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        placeholders = ",".join(["?"] * len(session_ids))
        sql = f"""
            SELECT id, directory, agent, model, cost,
                   tokens_input, tokens_output, tokens_reasoning,
                   tokens_cache_read, tokens_cache_write,
                   time_created, time_updated
            FROM session
            WHERE id IN ({placeholders})
            ORDER BY time_created ASC
        """
        cur.execute(sql, session_ids)
        rows = cur.fetchall()
        sessions = []
        for r in rows:
            model_info = r[3]
            model_id = "unknown"
            variant = None
            if model_info:
                try:
                    m = json.loads(model_info) if isinstance(model_info, str) else model_info
                    prov = m.get("providerID", "local")
                    mid = m.get("id", "unknown")
                    model_id = f"{prov}/{mid}" if prov != "local" or "/" not in mid else mid
                    if prov == "local" and not model_id.startswith("local/"):
                        model_id = f"local/{mid}"
                    variant = m.get("variant")
                except Exception:
                    model_id = str(model_info)

            sessions.append({
                "session_id": r[0],
                "directory": r[1],
                "agent": r[2],
                "model": model_id,
                "variant": variant,
                "cost": r[4] or 0.0,
                "tokens_input": r[5] or 0,
                "tokens_output": r[6] or 0,
                "tokens_reasoning": r[7] or 0,
                "tokens_cache_read": r[8] or 0,
                "tokens_cache_write": r[9] or 0,
                "time_created": r[10],
                "time_updated": r[11],
            })
        return sessions
    finally:
        conn.close()


def query_tools_used_for_sessions(
    session_ids: list[str],
    db_path: Path | None = None,
) -> dict[str, int]:
    """Query tool usage breakdown for the specified OpenCode sessions."""
    if not session_ids:
        return {}
    db = db_path or DEFAULT_OPENCODE_DB
    if not db.is_file():
        return {}

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        placeholders = ",".join(["?"] * len(session_ids))
        cur.execute(
            f"""
            SELECT json_extract(p.data, '$.tool') as tool_name, count(*) as call_count
            FROM part p
            JOIN message m ON p.message_id = m.id
            WHERE m.session_id IN ({placeholders})
              AND json_extract(p.data, '$.type') = 'tool'
            GROUP BY tool_name
            """,
            session_ids,
        )
        return {row[0]: row[1] for row in cur.fetchall() if row[0]}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def extract_telemetry(
    task_id: str,
    state_home: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Extract complete telemetry payload for an agentctl task."""
    meta = load_task_metadata(task_id, state_home)
    started_at = meta.get("started_at")
    ended_at = meta.get("ended_at")
    cwd = meta.get("cwd")

    start_ms = parse_iso_to_epoch_ms(started_at)
    end_ms = parse_iso_to_epoch_ms(ended_at)

    duration = 0.0
    if start_ms and end_ms:
        duration = round((end_ms - start_ms) / 1000.0, 2)
    elif start_ms:
        duration = round((time.time() * 1000 - start_ms) / 1000.0, 2)

    sessions = []
    direct_session_ids: list[str] = []
    if meta.get("session_id"):
        direct_session_ids.append(meta["session_id"])
    if meta.get("sessions") and isinstance(meta["sessions"], list):
        for s in meta["sessions"]:
            if s and s not in direct_session_ids:
                direct_session_ids.append(s)

    if direct_session_ids:
        sessions = query_opencode_sessions_by_ids(direct_session_ids, db_path=db_path)
    elif start_ms:
        candidate_sessions = query_opencode_sessions(
            start_ms=start_ms,
            end_ms=end_ms,
            directory=cwd,
            db_path=db_path,
        )
        if len(candidate_sessions) == 1:
            sessions = candidate_sessions
        else:
            # Ambiguous sessions in same workspace without explicit task binding: fail closed
            sessions = []

    session_ids = [s["session_id"] for s in sessions]
    tools_used = query_tools_used_for_sessions(session_ids, db_path=db_path)

    # Aggregate token spend by model
    by_model: dict[str, dict[str, Any]] = {}
    total_tokens = {
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache_read": 0,
        "cache_write": 0,
        "total": 0,
    }

    for s in sessions:
        m_key = s["model"]
        if m_key not in by_model:
            by_model[m_key] = {
                "model": m_key,
                "variant": s.get("variant"),
                "agent": s.get("agent"),
                "tokens": {
                    "input": 0,
                    "output": 0,
                    "reasoning": 0,
                    "cache_read": 0,
                    "cache_write": 0,
                    "total": 0,
                },
                "cost": 0.0,
            }
        t = by_model[m_key]["tokens"]
        inp = s["tokens_input"]
        out = s["tokens_output"]
        rea = s["tokens_reasoning"]
        cr = s["tokens_cache_read"]
        cw = s["tokens_cache_write"]
        tot = inp + out + rea + cr + cw

        t["input"] += inp
        t["output"] += out
        t["reasoning"] += rea
        t["cache_read"] += cr
        t["cache_write"] += cw
        t["total"] += tot
        by_model[m_key]["cost"] += s["cost"]

        total_tokens["input"] += inp
        total_tokens["output"] += out
        total_tokens["reasoning"] += rea
        total_tokens["cache_read"] += cr
        total_tokens["cache_write"] += cw
        total_tokens["total"] += tot

    loop_count = meta.get("rounds", 1 if meta.get("mode") == "iterate" else 0)
    if isinstance(loop_count, list):
        loop_count = len(loop_count)

    mode = meta.get("mode", "run")
    has_verification = mode == "iterate" or bool(meta.get("check")) or "final_check_passed" in meta
    if has_verification:
        verification_state = "passed" if meta.get("final_check_passed", meta.get("status") == "completed") else "failed"
        final_check_passed = (verification_state == "passed")
    else:
        verification_state = "not_run"
        final_check_passed = None

    return {
        "task_id": task_id,
        "status": meta.get("status", "unknown"),
        "mode": mode,
        "agent": meta.get("agent"),
        "goal": meta.get("goal"),
        "workspace": meta.get("workspace"),
        "cwd": cwd,
        "duration_seconds": duration,
        "loop_count": loop_count,
        "verification_state": verification_state,
        "final_check_passed": final_check_passed,
        "models": list(by_model.values()),
        "total_tokens": total_tokens,
        "tools_used": tools_used,
        "session_count": len(sessions),
        "sessions": session_ids,
        "halt_reason": meta.get("halt_reason"),
        "gap_classification": meta.get("gap_classification"),
    }


def format_markdown_report(telemetry: dict[str, Any]) -> str:
    """Format human-readable summary table and metrics."""
    status = telemetry.get("status", "unknown")
    icon = "✅" if status == "completed" else "⚠️" if status == "blocked" else "❌"

    verification_state = telemetry.get("verification_state", "not_run")
    if verification_state == "passed":
        verif_str = "Verification: Passed"
    elif verification_state == "failed":
        verif_str = "Verification: Failed"
    else:
        verif_str = "Direct Execution (No Check)"

    lines = [
        f"### {icon} Task Execution Report: `{telemetry.get('task_id')}`",
        f"- **Status**: `{status}` ({verif_str})",
        f"- **Duration**: `{telemetry.get('duration_seconds')}s` | **Loops / Rounds**: `{telemetry.get('loop_count')}`",
    ]
    if telemetry.get("workspace"):
        lines.append(f"- **Workspace**: `{telemetry.get('workspace')}`")

    models = telemetry.get("models") or []
    if models:
        lines.append("\n#### 🧠 Model & Token Spend Breakdown")
        lines.append("| Model | Variant | Input | Output | Reasoning | Cache Read | Total |")
        lines.append("|---|---|---|---|---|---|---|")
        for m in models:
            t = m["tokens"]
            v = m.get("variant") or "default"
            lines.append(
                f"| `{m['model']}` | {v} | {t['input']:,} | {t['output']:,} | {t['reasoning']:,} | {t['cache_read']:,} | **{t['total']:,}** |"
            )

        tot = telemetry.get("total_tokens") or {}
        lines.append(
            f"| **TOTAL** | - | **{tot.get('input', 0):,}** | **{tot.get('output', 0):,}** | **{tot.get('reasoning', 0):,}** | **{tot.get('cache_read', 0):,}** | **{tot.get('total', 0):,}** |"
        )

    tools = telemetry.get("tools_used") or {}
    if tools:
        lines.append("\n#### 🛠️ Tools Called")
        tool_items = [f"`{tool}`: {cnt}" for tool, cnt in sorted(tools.items(), key=lambda x: x[1], reverse=True)]
        lines.append(", ".join(tool_items))

    if telemetry.get("halt_reason"):
        lines.append(f"\n> **Halt Reason**: `{telemetry.get('halt_reason')}` (gap: `{telemetry.get('gap_classification')}`)")

    return "\n".join(lines)
