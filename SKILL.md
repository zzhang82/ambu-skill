---
name: ambu
description: Thin workflow adapter skill for Ambu (agentctl). Provides supervised execution, milestone progress streaming, one-shot status inspection, and token spend telemetry when sessions are attributable.
version: 1.0.0
author: Ambu Team
license: MIT
metadata:
  tags: "agentctl, workflow-adapter, telemetry, progress-streaming"
  related_skills: "agent-discipline-overlay, cole-operator"
---

# Ambu Skill (`ambu`)

Use this skill whenever delegating tasks to the **Ambu runtime control plane (`agentctl`)**.

It provides **supervised process execution**, **clean milestone progress streaming**, and an **end-of-run telemetry report** capturing task status, verification outcomes, and token metrics when sessions are attributable.

---

## 🎯 When to Use

1. **Autonomous Coding & Iteration**: When running an implementation task that requires a verification check (`--check`) and anti-loop early halt.
2. **Deep Architectural Review**: When delegating codebase reviews to high-reasoning models (e.g. `local/gpt-6-astra`, `local/gemini-3.8-flash-high`).
3. **Multi-Model Orchestration**: When routing intents to the kitchen brigade roles (`oracle`, `fixer`, `coder`, `designer`, `librarian`, `explorer`).
4. **Token & Cost Observability**: When the user or supervisor needs exact token metrics (input, output, reasoning, cache read/write) broken down by model.
5. **Non-Blocking Status Checks**: When checking whether background runs are active, inspecting log tails, or cancelling tasks without messy shell scripts.

---

## 🚀 Core Workflows

### 1. Supervised Task Execution (`ambu_runner.py`)

Do **NOT** execute raw `agentctl` and poll in manual loops. Instead, run via `scripts/ambu_runner.py`:

```bash
# Automated intent routing with verification loop (recommended for features / bugfixes)
python3 scripts/ambu_runner.py do "<prompt>" \
  --workspace <workspace_name> \
  --check "<test_or_verification_command>" \
  [--model local/gpt-6-astra] \
  [--max-rounds 5]

# Single-shot execution (for read-only reviews or one-off tasks)
python3 scripts/ambu_runner.py do "<review_prompt>" \
  --workspace <workspace_name> \
  --agent oracle

# Direct agent iteration
python3 scripts/ambu_runner.py iterate <agent> "<prompt>" \
  --workspace <workspace_name> \
  --check "<command>"

# Resume an interrupted or quota-exhausted task seamlessly
python3 scripts/ambu_runner.py resume <task_id> \
  --model <alternative_model>
```

#### What `ambu_runner.py` Does Automatically:
- Spawns `agentctl` in an isolated process group.
- Suppresses spammy stderr/stdout polling.
- Emits real-time milestone events to the terminal:
  - `🔍 Evaluating initial check...`
  - `🔄 Starting Round X/Y...`
  - `⚙️ Attempt 1: Running on model 'local/gpt-6-astra' (thinking...)`
  - `🔀 Falling back to candidate model...`
  - `✅ Verification passed!`
- On completion, parses SQLite telemetry and run metadata.
- Prints a markdown summary and the standardized JSON schema.
- If a task runs out of quota, hits rate limits, or is interrupted, automatically surfaces an **Actionable Recovery Box** with eligible fallback models and the one-line resume command.

---

### 2. Session Continuation via OpenCode Session Fork

When a model runs out of plan quota, returns 429, or hits 503, you can continue the session using an alternative model:

Ambu maps each `task_id` to its underlying OpenCode `session_id`:
```bash
# Continue the conversation session using an alternative model:
python3 scripts/ambu_runner.py resume <task_id> --model local/gemini-3.8-flash-high
```
Under the hood, this invokes `opencode run --session <session_id> --fork --model <new_model>`, continuing the conversation history in a new fork. Note that session fork branches conversation history, not underlying filesystem or git checkpoints.

---

### 3. Output Telemetry Schema

Every `ambu_runner.py` execution concludes with the authoritative JSON block:

```json
{
  "task_id": "20261006-222134-iterate-oracle-e33943",
  "status": "completed",
  "mode": "iterate",
  "agent": "coder",
  "workspace": "my-project",
  "cwd": "/home/user/code/my-project",
  "duration_seconds": 142.5,
  "loop_count": 2,
  "final_check_passed": true,
  "models": [
    {
      "model": "local/gpt-6-astra",
      "variant": "max",
      "agent": "oracle",
      "tokens": {
        "input": 24500,
        "output": 1200,
        "reasoning": 3400,
        "cache_read": 182000,
        "cache_write": 0,
        "total": 211100
      },
      "cost": 0.0
    }
  ],
  "total_tokens": {
    "input": 24500,
    "output": 1200,
    "reasoning": 3400,
    "cache_read": 182000,
    "cache_write": 0,
    "total": 211100
  },
  "tools_used": {
    "read": 18,
    "grep": 6,
    "edit": 4,
    "bash": 5
  },
  "halt_reason": null,
  "gap_classification": null
}
```

---

### 4. One-Shot Status & Debugging (`ambu_status.py`)

When the user asks *"what is currently running?"* or *"is task X done yet?"*:

```bash
# Check all active running tasks and recent history
python3 scripts/ambu_status.py

# Inspect one specific task (PID alive status, elapsed time, log tail)
python3 scripts/ambu_status.py <task_id>

# Cancel a running task cleanly (sends SIGTERM to process group)
python3 scripts/ambu_status.py <task_id> --cancel
```

---

## 🛡️ Guardrail & Role Invariants

- **Read-Only vs Implementation Roles**:
  - `oracle`, `reviewer`, `planner`, `explorer`, `librarian` default to `read_only`.
  - `coder`, `fixer`, `eli` default to `workspace_write`.
  - To allow write actions under `oracle`, pass `--approve workspace_write`.
- **Anti-Loop Safety**:
  - `agentctl iterate` fingerprints test failures across whole-output hashes.
  - If identical failures recur consecutively (`max_same_failure`, default 2), execution halts early with `status: blocked` to protect quota and context.
- **Model Fallbacks**:
  - Wall-clock timeouts do not trigger model fallbacks.
  - Transient 5xx or provider rate-limit errors automatically cascade down the approved routing frame.

---

## 📂 Repository Layout

- `scripts/ambu_runner.py`: Supervised CLI runner and milestone streamer.
- `scripts/ambu_telemetry.py`: SQLite & metadata token spend aggregator.
- `scripts/ambu_status.py`: Single-shot process and task status inspector.
- `tests/test_telemetry.py`: Unit tests for token parsing and DB extraction.
- `tests/test_runner.py`: Unit tests for command translation and milestone detection.
