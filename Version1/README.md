# magic-pigeon 🐦

A single-file Python AI agent for both general conversation and software engineering tasks. Built on the Anthropic API, referencing Codex, ChatGPT, and Claude Code for design decisions. Since it runs on Claude's API, it follows Claude Code's approach of fine-grained tool definitions rather than Codex's minimal shell-only style.

## Context Management

When the three values added together — existing context, the user's latest input, and the model's max output capacity — reach 90% of the context window, compaction is triggered. The mechanism first compares two candidates: the last 20,000 tokens of conversation, and the last 4 rounds of interaction. Whichever has a higher token count is used as the retention boundary — recent context within that boundary is kept as-is, and everything older gets sent to the API for summarization, allowing a session to keep working much longer without hitting the limit.

## Timestamps

The Claude app only provides basic info like device type. In this agent, every user message and tool result includes UTC timestamps accurate to the second. This gives the LLM more detailed temporal context for both general conversation and coding tasks.

## Cross-Session Memory

Each session's full uncompressed conversation is stored locally as a JSONL file. The LLM also has a tool to add, delete, or view a persistent memory list (`memories.json`) — important facts that the model or user considers worth keeping long-term. These memories are injected into the system prompt on every session start, so the LLM knows who it's talking to without being told again.

The agent also has a `search_history` tool that lets the LLM generate its own keywords and search through raw conversation content from any past session.

## Session Tags

On exit, the agent auto-generates English topic tags for the session. When searching past conversations, both raw content and tags are checked — if either matches, the result is surfaced for the LLM to evaluate. Tags are also injected alongside the system prompt on startup, giving the model a lightweight but broad sense of what recent sessions covered, which helps when users ask questions that reference earlier conversations.

The cross-session memory system as a whole is loosely based on how ChatGPT's app handles memory.

## Safety and Rollback

Since this is also a terminal-based SWE agent, I decided that git is the only reliable way to prevent catastrophic file damage during long autonomous runs. Every write, edit, and bash operation triggers an automatic git snapshot beforehand. If the working directory isn't a git repo, one is initialized automatically. Clearly dangerous bash commands (like `rm -rf /`) are blacklisted and require explicit user confirmation with a red highlighted warning — but the primary safety net is the ability to rollback via git, not the blacklist.

## Streaming Output

Response tokens are buffered and printed line by line with a fixed 50ms delay between lines. This was the best balance I found between readability and simplicity — it avoids the jumpy token-by-token look without getting into complex terminal rendering.

## Error Handling

All tool executions are wrapped in try/except. Errors are returned to the LLM as plain text, the same way successful tool results are. The model decides how to handle them.

## Prompt Caching

Following Anthropic's API format, three cache breakpoints are set: after the system prompt, after the last tool definition, and on the second-to-last message in the conversation. The API checks from the longest cached prefix backward — any segment that matches the cache is billed at 10% of the normal input token rate.

## Quick Start

```bash
pip install anthropic
export ANTHROPIC_API_KEY="sk-ant-..."
python magic_pigeon.py
```

## Development Notes

Built with AI-assisted development. I wrote the core agent loop and tool definitions; design decisions like English tag-based search, dual-path history matching, max(4 rounds, 20K tokens) retention, and halt-on-deny came out of back-and-forth discussion with Claude.
