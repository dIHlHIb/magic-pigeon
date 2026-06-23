# magic-pigeon 🐦

A Python AI agent for general conversation and software-engineering tasks, written mainly as a learning project to understand how coding agents actually work under the hood. It can handle real tasks, but that was never the main point. It runs on the Anthropic API and borrows design decisions from Codex, ChatGPT, and Claude Code; since it's on Claude's API, it follows Claude Code's fine-grained tool definitions rather than Codex's minimal shell-only style.

The project is organized around a single engine, `agent_core.py` (the agent loop, tools, memory, and history), with three front-ends on top of it. All four pieces share the same on-disk state (`~/.agent_history/`):

| Surface | File | What it is |
|---------|------|------------|
| **Web UI** | `server.py` + `frontend/` | A React + Flask/Socket.IO chat app with live streaming, message branching, edit/regenerate, and tool cards. |
| **Terminal** | `cli.py` | A REPL on the same engine: streaming replies, `/model` and `/effort` to switch model/effort, `/plan` or Shift+Tab for plan mode, and dangerous-command confirmation. |
| **Gateway** | `gateway.py` | An OpenAI-compatible `/v1/chat/completions` endpoint plus a Twilio WhatsApp webhook, so the agent is reachable from any OpenAI client or a phone. |
| **Engine** | `agent_core.py` | The headless `AgentSession` that all of the above drive via callbacks. |

## Version history

**v1 — Terminal agent.** One Python file, 8 tools (read/write/edit files, bash, grep, glob, memory, history search), dangerous-command confirmation, git snapshots before writes, context compaction at 90% window, cross-session memory with auto-generated tags. No web UI, no web search, model hardcoded to Opus 4.6.

**v2 — Web UI.** Pulled the engine out into a reusable library, added a React frontend and a gateway layer. New additions: web search tool, multi-model switching, effort parameter, prompt caching (3 breakpoints), message branching (edit/regenerate with fork protection on impactful tool calls), auto-generated chat titles. Same core agent, more ways to use it.

**v3 — Runtime controls.** The agent worked fine for short tasks, but on longer coding sessions it would start editing before we'd agreed on an approach, burn through context reading files, and silently produce broken code. This round is mostly about reining that in.

**Plan mode** blocks all writes and bash at the runtime level, so the agent can't modify anything until you approve a plan. It delegates the file-reading to explore sub-agents, assembles a numbered plan from their summaries plus its own context, and hands it over with `present_plan`. Doing the reading in disposable sub-agents rather than its own window keeps the main context lean — mainly a way to save money. Approve and it switches to auto mode and builds; revise and it keeps planning.

**Sub-agents** come from a `spawn_subagent` tool that spins up a short-lived agent with its own context. Each type has a fixed system prompt the main agent can't change — the main agent only sets the task and the context it passes in. There are three: explore and review are read-only (explore reads and reports a summary, review reads and returns a list of findings), while testing can read, make limited edits, and run bash, but only to run and verify tests. Each one's final message is the summary it hands back, so the work stays in a disposable context instead of piling into the main agent's window.

**Hooks** come from an `.agent-hooks.json` in the project root. PreToolUse hooks can block calls; PostToolUse hooks run checks (py_compile, JSON validation, type-checking) after a write, and if a check fails its output is appended to that tool call's result — the failure rides back into the model's context along with the result, so it sees the breakage on the next turn.

**The supervisor** is an independent two-stage check before every impactful tool call. Stage 1 is a cheap model (Haiku) doing a binary suspicious/OK screen; stage 2 is a strong model (Opus) that looks closely only if stage 1 flags something, mainly to avoid over-blocking. It's shown only the user's instructions and the tool's name and arguments — never tool results, never the agent's own text — so neither attacker-controlled content in tool output nor anything in the agent's own response can sway it. What it judges on is the user's intent plus the agent's actual tool calls, both the ones it already made and the one pending. Fails open on API errors.

**Project context** comes from a `pigeon/` directory: `Rule.md`, my instructions (the agent is told not to change it unless I explicitly ask — not hard-locked, just instructed), and `Observation.json`, the agent's own project notes (written through `update_memory`, capped at 30 entries). Both go into the system prompt, with Rule taking priority over Observation — a simplified version of Claude Code's `CLAUDE.md`.

**Adaptive thinking** is enabled on Opus 4.7+ and Sonnet 4.6 via `thinking: {"type": "adaptive"}`. No UI display, just better reasoning under the hood.

**Tool output** is capped at 60,000 characters so a huge file or verbose command doesn't blow up the context. Grep and glob skip noise directories (`node_modules`, `.git`, `dist`, `.venv`, etc.) by default.

## Quick Start

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
```

**Web UI** (recommended):

```bash
cd frontend && npm install && npm run build && cd ..
python server.py          # open the printed http://127.0.0.1:5001/?token=… URL
```

**Terminal**:

```bash
python cli.py             # REPL on the same engine; /model, /effort, /plan (or Shift+Tab)
```

**Gateway** (OpenAI-compatible API + WhatsApp):

```bash
python gateway.py         # serves /v1/chat/completions on :5002
```

## Development Notes

The backend core is essentially the earlier single-file version with the modifications described above. The web frontend, the gateway, and the CLI were built with AI assistance, referencing the interaction behavior of ChatGPT and Claude Code.
