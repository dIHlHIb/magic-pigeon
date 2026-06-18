# magic-pigeon 🐦

A Python AI agent for general conversation and software-engineering tasks, written mainly as a learning project to understand how coding agents actually work under the hood. It can handle real tasks, but that was never the main point. It runs on the Anthropic API and borrows design decisions from Codex, ChatGPT, and Claude Code; since it's on Claude's API, it follows Claude Code's fine-grained tool definitions rather than Codex's minimal shell-only style.

The project is organized around a single engine, `agent_core.py` (the agent loop, tools, memory, and history), with two front-ends on top of it. All three pieces share the same on-disk state (`~/.agent_history/`):

| Surface | File | What it is |
|---------|------|------------|
| **Web UI** | `server.py` + `frontend/` | A React + Flask/Socket.IO chat app with live streaming, message branching, edit/regenerate, and tool cards. |
| **Gateway** | `gateway.py` | An OpenAI-compatible `/v1/chat/completions` endpoint plus a Twilio WhatsApp webhook, so the agent is reachable from any OpenAI client or a phone. |
| **Engine** | `agent_core.py` | The headless `AgentSession` that both of the above drive via callbacks. |

In practice you mostly use the Web UI: a chat page in the browser where you send messages, watch replies stream in, see each tool call with its result, and branch the conversation by editing an earlier message. The Gateway is for everything else, exposing the same agent as an OpenAI-compatible API and a WhatsApp webhook so another program or a phone can reach it. Both run the same engine against the same on-disk history, so a conversation is identical no matter how you open it.

## What changed from the single-file version

This started as one Python file you run in the terminal (it's kept around as the `v0.1.0` release). Honestly, most of the "brain" was already there back then: reading/writing/editing files, running bash, grep and glob, remembering things across sessions (a memory file plus auto-generated session tags, and a tool to search old chats), compressing the context when it gets long, taking a git snapshot before touching files, and stopping to ask before dangerous commands.

The new version isn't really about making the agent smarter, it's mostly about how you use it. I pulled the core logic out of that single file into a reusable engine and put a web UI on top, basically a ChatGPT-style chat page, so I can use it in a browser instead of living in the terminal.

A few things did get added. There's a web search tool now, so it can look things up online. You can pick which Claude model to run and, on models that support it, how much effort it spends. There's a gateway that lets me reach it from my phone or point any OpenAI-style client at it. And you can edit an earlier message, which forks the chat into a new branch you can switch between, as long as nothing after that message has already changed files on disk (text can be undone, file changes can't).

Nothing really got removed; the core features all carried over, and the old terminal entry point just became a library that the web UI and gateway call.

So really it's the same agent, just more ways to use it.

## How it works

Memory spans sessions. Every conversation is written to disk as a JSONL log, and the agent has a tool to keep a short list of durable facts in `memories.json` (who it's talking to, preferences, project context) that gets injected into the system prompt at startup. It can also search its own past: a `search_history` tool runs the agent's own keywords against the raw text of every old session, and when a session ends the agent auto-generates a few topic tags for it. Those tags are matched during search and are also dropped into the system prompt on startup, giving the model a rough sense of what recent sessions were about. The whole setup is loosely modeled on how the ChatGPT app handles memory.

When a conversation grows long enough that the existing context, the new input, and the model's max output together approach 90% of the window, it compacts. It keeps whichever is larger of the last 4 rounds or the last 20,000 tokens verbatim, summarizes everything older through the API, and continues. Every user message and tool result also carries a UTC timestamp, which gives the model a sense of time across long or resumed sessions.

For current information there's a `web_search` tool. It uses the Google Custom Search API when keys are set and otherwise falls back to a dependency-free DuckDuckGo scraper, so it works without any setup. Search is read-only and never touches files.

Repeated input is cached. Three cache breakpoints (after the system prompt, after the tools, and near the end of the conversation) mean the unchanged prefix on each turn is billed at about a tenth of the normal input rate.

Safety leans on git rather than a blacklist. Before any write, edit, or bash command the agent takes an automatic git snapshot (initializing a repo if there isn't one), so anything it does can be rolled back. Obviously destructive commands like `rm -rf /` are also flagged for explicit confirmation, but the snapshot is the real net. Tool errors aren't fatal; they go back to the model as plain text, the same as a normal result, and the model decides what to do next.

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

For frontend development, run `npm run dev` in `frontend/` (Vite on :5173) and `python server.py` separately — Vite proxies the API and Socket.IO to the backend.

**Gateway** (OpenAI-compatible API + WhatsApp):

```bash
python gateway.py         # serves /v1/chat/completions on :5002
```

## Development Notes

The backend core is essentially the earlier single-file version with the modifications described above. The web frontend and the gateway were built with AI assistance, referencing the frontend behavior of ChatGPT and Claude Code.
