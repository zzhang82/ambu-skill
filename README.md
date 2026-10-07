# Ambu Skill (`ambu-skill`)

> **The high-UX supervisor skill and telemetry bridge for OpenCode and Codex coding agents.**

`ambu-skill` wraps [`ambu-runtime-agents`](https://github.com/zzhang82/ambu-runtime-agents) (`agentctl`) to deliver a smooth developer experience:
- **No Manual Polling Loops**: Runs tasks under managed process groups and streams real-time milestone events.
- **Authoritative Token Observability**: Aggregates exact token spend (input, output, reasoning, cache read/write) by model directly from OpenCode's SQLite database.
- **Anti-Loop Iteration Visibility**: Reports loop counts, gap classifications, tool usage counts, and duration.
- **One-Shot Status & Cancellation**: Instant PID alive status, log tails, and process group signals without custom scripts.

---

## Architecture Overview

```
User / Agent (OpenCode / Codex / Hermes)
   │
   ▼
SKILL.md  ──►  scripts/ambu_runner.py
                  │
                  ├──► agentctl (do / iterate / run)
                  │       │
                  │       └──► OpenCode Runtime (Astra Max, Grok 4.7, etc.)
                  │
                  ├──► scripts/ambu_status.py (Process Health & Log Tail)
                  │
                  └──► scripts/ambu_telemetry.py
                          │
                          ├──► ~/.local/share/runtime-agents/runs/<id>/metadata.json
                          └──► ~/.local/share/opencode/opencode.db
                                  │
                                  ▼
                        Standardized Output Schema
                     (Tokens by Model + Loops + Tools)
```

---

## Quickstart

### 1. Run with Milestones & Telemetry

```bash
# Run iterative coding with verification check
python3 scripts/ambu_runner.py do "Add user authentication unit tests" \
  --workspace my-project \
  --check "pytest tests/test_auth.py" \
  --model local/gpt-6-astra
```

Terminal output:
```text
[Ambu 14:02:10] 🚀 Launching: agentctl do ...
[Ambu 14:02:11] 🔍 Evaluating initial check: pytest tests/test_auth.py
[Ambu 14:02:12] ❌ Initial check failed. Dispatching agent...
[Ambu 14:02:12] 🔄 Starting Round 1/5...
[Ambu 14:02:13] ⚙️ Attempt 1/2: Running on model 'local/gpt-6-astra' (thinking...)
[Ambu 14:03:45] 🧪 Agent finished iteration. Running verification check...
[Ambu 14:03:47] ✅ Verification passed!

============================================================
### ✅ Task Execution Report: `20261006-224510-iterate-coder-91a2bc`
- **Status**: `completed` (Check Passed)
- **Duration**: `97.2s` | **Loops / Rounds**: `1`
- **Workspace**: `my-project`

#### 🧠 Model & Token Spend Breakdown
| Model | Variant | Input | Output | Reasoning | Cache Read | Total |
|---|---|---|---|---|---|---|
| `local/gpt-6-astra` | max | 18,200 | 1,420 | 2,850 | 142,000 | **164,470** |
| **TOTAL** | - | **18,200** | **1,420** | **2,850** | **142,000** | **164,470** |

#### 🛠️ Tools Called
`read`: 8, `edit`: 4, `bash`: 3
============================================================
```

### 2. Inspect Running Tasks & Resume from Failure

```bash
# Snapshot of active and recent tasks
python3 scripts/ambu_status.py

# Detailed status of specific task (shows OpenCode session and PID)
python3 scripts/ambu_status.py <task_id>

# Resume an interrupted or quota-exhausted task seamlessly using an alternative model:
python3 scripts/ambu_runner.py resume <task_id> --model local/gemini-3.8-flash-high

# Cancel a stuck or running task cleanly
python3 scripts/ambu_status.py <task_id> --cancel
```

---

## Running Tests

```bash
python3 -m unittest discover -s tests
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
