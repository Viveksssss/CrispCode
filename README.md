# CrispCode

> A local AI agent system with a daemon-core architecture, built for long-running tasks, multi-agent orchestration, and extensible tool integration.

CrispCode is a dual-process local AI agent system. The persistent daemon (`crisp-core`) handles all LLM interactions, tool execution, and session management. Lightweight clients (`crisp` CLI and `crisp-tui` TUI) communicate with it over TCP loopback using a typed JSON-RPC 2.0 protocol.

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
  - [Dual-Process Design](#dual-process-design)
  - [Protocol Layer](#protocol-layer)
  - [Transport Layer](#transport-layer)
  - [Event System](#event-system)
  - [Client-Server Communication Flow](#client-server-communication-flow)
- [Project Structure](#project-structure)
- [Development](#development)
- [Configuration](#configuration)
- [Stage Evolution (S0–S7)](#stage-evolution-s0s7)
- [Codebase Statistics](#codebase-statistics)
- [License](#license)

---

## Features

- **Persistent daemon core** — LLM connections, tool execution, and sessions survive across client disconnects
- **Multi-agent orchestration** — Spawn isolated sub-agents (planner/executor/reviewer) with foreground or background execution
- **Skill system** — Markdown-based skill definitions with `/command` syntax and slash-command autocomplete in TUI
- **MCP integration** — Connect to external MCP servers (stdio/TCP) for third-party tool extension
- **Permission system** — 6-tier policy engine with persistent/dedicated approval, session-level caching, and timeout
- **Context compression** — Automatic and manual context compaction to manage long conversations
- **Extended thinking** — Native support for Anthropic's thinking blocks in conversation history
- **Rich TUI** — Terminal UI with real-time streaming, permission prompts, sub-agent progress trees, and slash-command completion

---

## Quick Start

### Prerequisites

| Dependency | Version |
|------------|---------|
| OS | macOS / Linux |
| Python | 3.12.x |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.4 |

Install uv (if not already installed):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Python 3.12 is automatically managed by uv — no manual installation required.

### Setup

```bash
git clone <repo> && cd CrispCode
uv sync
cp .env.example .env        # edit as needed
```

### Run

```bash
# Terminal 1: start the daemon
uv run crisp-core

# Terminal 2: verify connectivity
uv run crisp ping
# → pong server=0.0.1 uptime=12ms latency=2ms

# Terminal 3: launch the TUI
uv run crisp-tui
```

### Run Tests

```bash
uv run pytest tests/unit -v    # unit tests (fast, no daemon)
uv run pytest tests/ -v        # all tests
```

---

## Architecture

### Dual-Process Design

```
┌─────────────────────────────────────────────────────────────────┐
│                         crisp-tui (TUI)                         │
│  Real-time streaming · Permission prompts · Slash commands      │
│  Sub-agent progress trees · Context usage bar                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ TCP loopback (NDJSON)
                           │ 127.0.0.1:7437
┌──────────────────────────┴──────────────────────────────────────┐
│                       crisp-core (Daemon)                       │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │  Agent    │  │ Session  │  │  Tool    │  │  Permission   │  │
│  │  Loop     │  │ Manager  │  │ Registry │  │  Manager      │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬───────┘  │
│       │              │              │                │           │
│  ┌────┴──────────────┴──────────────┴────────────────┴───────┐  │
│  │                    EventBus (in-process)                   │  │
│  └────┬──────────────────────────────────────────────────────┘  │
│       │                                                         │
│  ┌────┴──────────────────────────────────────────────────────┐  │
│  │              IpcEventBroadcaster (cross-process)           │  │
│  └────┬──────────────────────────────────────────────────────┘  │
│       │                                                         │
│  ┌────┴──────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  LLM Provider     │  │  Subagent    │  │  MCP Manager   │  │
│  │  (Anthropic)      │  │  Registry    │  │  (stdio/TCP)   │  │
│  └───────────────────┘  └──────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

The **TUI** is the primary frontend. All user-facing work on task management, observability, and interaction is designed for and validated in the TUI first. The **CLI** (`crisp`) exists for quick scripted testing and debugging — it is not a product surface.

### Protocol Layer

All IPC messages are typed Pydantic v2 models with a **discriminated union on the `type` field**. This is the contract boundary — adding a new command or event means adding a new model class and extending the union.

**Two message formats share a single TCP connection:**

| Direction | Format | Structure |
|-----------|--------|-----------|
| Client → Daemon | JSON-RPC 2.0 request | `{"jsonrpc":"2.0", "id":"...", "method":"...", "params":{...}}` |
| Daemon → Client (response) | JSON-RPC 2.0 result | `{"jsonrpc":"2.0", "id":"...", "result":{...}}` |
| Daemon → Client (push) | Event envelope | `{"kind":"event", "event":{...}}` |

Clients distinguish responses from events by checking for the `"jsonrpc"` key: present = response to a pending command; absent with `"kind":"event"` = server push.

**Registered commands:**

| Command | Purpose |
|---------|---------|
| `core.ping` | Connectivity check |
| `agent.run` | One-shot agent run (outside session) |
| `event.subscribe` | Subscribe to event stream with topic/scope filters |
| `session.create` | Create a chat or one-shot session |
| `session.send_message` | Send a message and block until the run completes |
| `session.get_history` | Retrieve full conversation history |
| `session.close` | Close a session |
| `session.compact` | Manually compress conversation context |
| `permission.respond` | Respond to a tool permission request |

**Event types (server push):**

| Category | Events |
|----------|--------|
| Lifecycle | `core.started`, `run.started`, `run.finished` |
| Steps | `step.started`, `step.finished` |
| Tools | `tool.started`, `tool.finished`, `tool.failed` |
| LLM | `llm.model_selected`, `llm.token`, `llm.usage` |
| Session | `session.created`, `session.message_received`, `session.waiting_for_input`, `session.resumed`, `session.closed` |
| Permissions | `permission.requested`, `permission.granted`, `permission.denied` |
| Context | `context.compacted` |
| Sub-agents | `subagent.started`, `subagent.finished` |
| Skills | `skill.invoked` |
| Logging | `log.line` |

### Transport Layer

- **TCP loopback** on `127.0.0.1:7437` (override via `CRISP_HOST` / `CRISP_PORT`)
- Each message is one `\n`-terminated JSON line (**NDJSON**)
- **Frame limit**: 64 MB per line (accommodates MCP large-file tool results)
- **Concurrency**: each inbound command is dispatched as an independent `asyncio.Task`, so long-running commands (e.g. `session.send_message`) do not block concurrent commands (e.g. `permission.respond`)

### Event System

Events flow through a two-layer architecture:

```
In-process:  EventBus.publish(event)
                  │
                  ├── AgentLoop publishes (llm.token, tool.started, etc.)
                  ├── SessionManager publishes (session.created, etc.)
                  ├── Compactor publishes (context.compacted)
                  ├── SpawnAgentTool publishes (subagent.started/finished)
                  └── PermissionManager emits (permission.requested)
                  │
                  ▼
Cross-process: IpcEventBroadcaster.handle(event)
                  │
                  ├── Match topic (fnmatch glob: "llm.*", "tool.*", etc.)
                  ├── Match scope ("global" or "run:<run_id>")
                  │
                  ▼
              For each matching subscriber:
                  writer.write({"kind":"event","event":{...}} + "\n")
```

### Client-Server Communication Flow

A complete request-response cycle for `session.send_message`:

```
Client                          Daemon
  │                                │
  │── JSON-RPC request ──────────→│  _read_loop: readline()
  │   method: "session.send_message" │  create_task(_handle_line)
  │   params: {session_id, content}  │    │
  │   id: "u-xxx"                │    │
  │                                │    ▼
  │                                │  SessionManager.send_message()
  │                                │    → Runner.run_and_capture()
  │                                │      → AgentLoop.run()
  │                                │        → LLM chat (streaming)
  │                                │          │
  │◄── Event: llm.token ─────────│          │  (broadcaster push)
  │◄── Event: llm.token ─────────│          │
  │◄── Event: llm.usage ─────────│          │  (token stats)
  │◄── Event: tool.started ──────│          │  (tool execution)
  │                                │    │
  │                                │    │  [if permission needed]
  │◄── Event: permission.requested│    │
  │                                │         │
  │── JSON-RPC: permission.respond│         │  (concurrent task!)
  │   {tool_use_id, "allow_once"} │    │
  │◄── JSON-RPC response: {ok} ──│         │
  │                                │    │  (tool continues)
  │                                │    ▼
  │◄── Event: session.waiting...──│  run finished
  │                                │
  │◄── JSON-RPC response ────────│  _send: JsonRpcSuccess
  │    {run_id: "run-xxx"}        │
  │                                │
```

Key design decisions:
- **Concurrent command handling**: `_read_loop` creates a new `asyncio.Task` per inbound line, so `permission.respond` runs independently of the blocking `session.send_message`
- **Event filtering**: clients subscribe with topic globs and scope; the broadcaster pushes only matching events
- **Graceful disconnect**: dead connections are detected on write failure and lazily unsubscribed

---

## Project Structure

```
CrispCode/
├── src/crispcode/
│   ├── core/                    # Daemon (crisp-core)
│   │   ├── app.py               # Daemon entry point, handler registration
│   │   ├── loop.py              # Agent reasoning loop (plan → act → observe)
│   │   ├── runner.py            # Orchestrates provider, registry, context
│   │   ├── context.py           # ExecutionContext: messages, 3-layer memory
│   │   ├── config.py            # 4-tier config (default → TOML → .env → env)
│   │   ├── runs.py              # Run ID generation, RUNS_DIR
│   │   ├── bus/                 # Protocol layer (JSON-RPC envelope, commands, events)
│   │   ├── transport/           # TCP server/client, IPC broadcaster
│   │   ├── llm/                 # Anthropic provider, streaming, retry, thinking blocks
│   │   ├── tools/               # Tool base class, registry, invocation, builtin tools
│   │   ├── permissions/         # 6-tier permission policy, manager, storage
│   │   ├── session/             # Session store, model, manager
│   │   ├── task/                # Task model and manager
│   │   ├── compact/             # Context compression (budget + compactor)
│   │   ├── subagent/            # SpawnAgent/AgentResult tools, background registry
│   │   ├── agents/              # Agent profile loader (planner/executor/reviewer)
│   │   ├── skills/              # Skill loader (Markdown + frontmatter)
│   │   ├── mcp/                 # MCP client, server manager, tool wrapper
│   │   ├── memory/              # Context file loader (~/.CRISP/context.md)
│   │   ├── events/              # EventBus, EventWriter
│   │   └── trace/               # Trace recording and writing
│   ├── tui/                     # Terminal UI (crisp-tui, Textual-based)
│   └── cli/                     # CLI client (crisp, for testing/debugging)
├── tests/
│   ├── unit/                    # ~46 test files, no daemon required
│   └── integration/             # Integration tests (spawn real daemon)
├── scripts/
│   └── gen_protocol_doc.py      # Auto-generate WIRE_PROTOCOL.md from models
├── .CRISP/
│   ├── skills/builtin/          # Built-in skills (init, orchestrate, review, summarize)
│   └── context.md               # Project-level context (auto-injected)
├── WIRE_PROTOCOL.md             # Auto-generated protocol documentation
├── RUNBOOK.md                   # Operations manual
└── AGENTS.md                    # Developer guide for AI assistants
```

---

## Development

### Commands

```bash
# Install / sync dependencies
uv sync

# Lint
uv run ruff check src tests scripts

# Type check
uv run mypy src

# Tests
uv run pytest tests/unit -v           # unit only (fast, no daemon)
uv run pytest tests/integration -v    # integration tests
uv run pytest tests/ -v               # all tests

# Single test
uv run pytest tests/unit/test_envelope.py::test_request_roundtrip -v

# Regenerate protocol docs
uv run python scripts/gen_protocol_doc.py

# Verify protocol docs are in sync
uv run python scripts/gen_protocol_doc.py --check

# Run daemon manually
uv run crisp-core                        # foreground; Ctrl+C to stop
CRISP_PORT=8000 uv run crisp-core        # override port
```

### Adding a New Command

1. Add request/response models in `src/crispcode/core/bus/commands.py`
2. Add to the `Command` discriminated union
3. Register a handler in `app.py`
4. Update `scripts/gen_protocol_doc.py` imports and regenerate `WIRE_PROTOCOL.md`

### Adding a New Event

1. Add the event model in `src/crispcode/core/bus/events.py`
2. Add to the `Event` discriminated union
3. Publish the event at the appropriate point in the codebase
4. Regenerate `WIRE_PROTOCOL.md`

---

## Configuration

### Priority (low → high)

**Built-in defaults** → **`~/.crisp/config.toml`** → **`.CRISP/config.toml`** (project-local) → **`.env`** → **System environment variables**

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CRISP_CONFIG` | `~/.crisp/config.toml` | Override config file path |
| `CRISP_HOST` | `127.0.0.1` | TCP listen address |
| `CRISP_PORT` | `7437` | TCP listen port |
| `CRISP_LOG_LEVEL` | `INFO` | Log level (DEBUG / INFO / WARNING / ERROR) |
| `CRISP_LOG_FILE` | `~/.crisp/logs/core.log` | Log file path (empty = stderr only) |
| `CRISP_LOG_FORMAT` | `text` | Log format (`text` or `json`) |
| `CRISP_LLM_DEFAULT_MODEL` | `claude-sonnet-4-6` | Default LLM model |
| `CRISP_MAX_STEPS` | `20` | Maximum reasoning steps per run |
| `CRISP_COMPACT_THRESHOLD` | `0.0` | Auto-compact when context usage ≥ this ratio (0 = disabled) |
| `CRISP_PERMISSION_TIMEOUT_S` | `60.0` | Permission approval timeout in seconds |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (required for LLM calls) |

### TOML Config Example

```toml
[core]
host = "127.0.0.1"
port = 7437

[logging]
level  = "INFO"
file   = "~/.crisp/logs/core.log"
format = "text"

[llm]
default_model = "claude-sonnet-4-6"

[agent]
max_steps = 20

[permission]
timeout_s = 60.0

[compaction]
auto_threshold = 0.80   # auto-compact at 80% context usage
tool_result_limit = 8000
tool_result_keep = 4000

[[mcp.servers]]
name = "filesystem"
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
```

---

## Stage Evolution (S0–S7)

CrispCode was built incrementally across 8 stages, each on a dedicated branch (`stage/sN`). Each stage is a self-contained milestone that builds on the previous one.

### S0 — Foundation & Protocol Contract

> **+459 lines** · Dual-process skeleton, JSON-RPC protocol, TCP transport, config system

Established the project structure and IPC contract between daemon and client. The core architectural decisions — TCP loopback, NDJSON framing, Pydantic discriminated unions for message routing — were made here and have not changed since.

**Key additions**: `bus/envelope.py`, `bus/commands.py`, `bus/events.py`, `transport/socket_server.py`, `transport/socket_client.py`, `config.py`, `app.py`

### S1 — Core Agent Loop

> **+781 lines** · LLM provider, agent reasoning loop, tool system, trace

Implemented the core reasoning cycle: LLM call → tool execution → observe → repeat. Introduced the Anthropic streaming provider, tool registry, and the trace system for debugging.

**Key additions**: `llm/provider.py`, `loop.py`, `runner.py`, `tools/base.py`, `tools/registry.py`, `trace/`

### S2 — TUI Frontend

> **+498 lines** · Terminal UI, dual-process integration

Built the Textual-based TUI as the primary user interface, with real-time token streaming, log display, and session management. Validated the full client→daemon→LLM→TUI round-trip.

**Key additions**: `tui/app.py`, `cli/` commands

### S3 — Built-in Tool Suite

> **+1,093 lines** · Context assembly, compression, memory, complete tool set

Fleshed out the tool ecosystem with bash, read_file, write_file, list_dir, and task tools. Added context assembly with 3-layer memory injection (global → project → session), context compression for long conversations, and the memory loader.

**Key additions**: `tools/builtin/*`, `context.py`, `compact/`, `memory/loader.py`

### S4 — Session Persistence

> **+764 lines** · Session storage, session-level IPC, conversation history

Made sessions durable: messages persist to `thread.jsonl`, metadata to `meta.json`, and notes to `notes.md`. Sessions survive daemon restarts. Added `session.get_history`, `session.close`, and `session.send_message` as distinct IPC commands.

**Key additions**: `session/store.py`, `session/model.py`, `session/manager.py`

### S5 — Permission System

> **+871 lines** · Tool permission policy, approval flow, persistent storage

Introduced a 6-tier permission engine: deny_patterns → outside_cwd_heuristic → session_cache → persistent_cache → allow_patterns → tool_default → user_approval. The TUI renders permission prompts inline and blocks tool execution until the user responds.

**Key additions**: `permissions/policy.py`, `permissions/manager.py`, `permissions/storage.py`

### S6 — Context Management

> **+477 lines** · Layered memory, context compression, stream retry, task management

Added global/project context files (`~/.CRISP/context.md`, `.CRISP/context.md`) injected into every system prompt. Implemented automatic context compaction triggered by context usage threshold. Added stream retry with exponential backoff for network resilience. Introduced the task sub-system for in-conversation task tracking.

**Key additions**: `compact/compactor.py`, `compact/budget.py`, `memory/loader.py`, `task/model.py`, `task/manager.py`

### S7 — Multi-Agent Orchestration

> **+1,182 lines** · Sub-agents, skills, MCP, extended thinking

The largest feature addition. Introduced isolated sub-agents with foreground/background execution, a skill system with Markdown-based definitions and `/command` syntax, MCP client for external tool integration, and extended thinking support. The TUI gained slash-command autocomplete, sub-agent progress trees, and an ASCII banner.

**Key additions**: `subagent/`, `agents/`, `skills/`, `mcp/`, extended thinking in `llm/provider.py` + `loop.py`

### Growth Summary

| Stage | Cumulative Lines | Key Milestone |
|-------|-----------------|---------------|
| S0 | 459 | Dual-process skeleton + protocol |
| S1 | 1,240 | Agent loop + tools + LLM |
| S2 | 1,738 | TUI frontend |
| S3 | 2,831 | Full tool suite + context |
| S4 | 3,595 | Session persistence |
| S5 | 4,466 | Permission system |
| S6 | 4,943 | Context management |
| S7 | 6,125 | Multi-agent orchestration |

---

## Codebase Statistics

> Based on `stage/s7` (latest)

| Category | Files | Non-empty Lines |
|----------|-------|-----------------|
| Product code (`src/`) | 85 | 6,125 |
| Test code (`tests/`) | 46 | ~3,392 |
| Scripts (`scripts/`) | 1 | 293 |
| Config / skill docs (`.CRISP/`) | 7 | 699 |
| **Total (src + tests pure code)** | **131** | **~9,182** |

### Product Code by Module

| Module | Path | Lines | Responsibility |
|--------|------|-------|----------------|
| Tool system | `core/tools/` | 664 | Tool registry, invocation, builtin tools |
| Transport | `core/transport/` | 361 | TCP socket server/client, IPC broadcast |
| Sessions | `core/session/` | 358 | Session storage, model, manager |
| Protocol bus | `core/bus/` | 348 | JSON-RPC envelope, commands, events |
| Permissions | `core/permissions/` | 314 | Policy engine, approval flow, storage |
| Sub-agents | `core/subagent/` | 312 | Background task registry, spawn/poll tools |
| MCP | `core/mcp/` | 291 | MCP client, server manager, tool wrapper |
| Compression | `core/compact/` | 190 | Context budget, compaction |
| LLM abstraction | `core/llm/` | 182 | Provider, streaming, retry, thinking blocks |
| Tasks | `core/task/` | 151 | Task model and manager |
| Tracing | `core/trace/` | 144 | Trace recording and writing |
| Skills | `core/skills/` | 119 | Skill loader (Markdown + frontmatter) |
| Agent profiles | `core/agents/` | 43 | Role-based agent profiles (TOML) |
| Memory | `core/memory/` | 10 | Context file loader |
| Core top-level | `core/*.py` | 1,252 | app, loop, runner, context, config, logging |
| TUI | `tui/` | 1,112 | Terminal UI (Textual) |
| CLI | `cli/` | 620 | Command-line client |

---

## License

[MIT](./LICENSE) © 2025
