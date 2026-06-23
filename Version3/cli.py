#!/usr/bin/env python3
"""
cli.py — terminal entry point for the Version2 agent.

Version2 refactored magic_pigeon.py into an importable library (agent_core.py)
and a Flask web server (server.py), which dropped the original `while True:
input()` terminal loop. This script restores a pure-terminal experience by
driving AgentSession.run_turn() with stdout/stdin callbacks — no web server,
no browser.

All agent capabilities are preserved because they live in agent_core, not the
web layer. In particular the cross-session MEMORY feature (the update_memory
tool + memories injected into the system prompt) works here exactly as it does
in the web UI: both share ~/.agent_history/memories.json. The same goes for the
spawn_subagent tool.

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python3 cli.py
    python3 cli.py --version   # print version and exit
"""

import os
import sys

import agent_core

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings


# ── ANSI colors (kept minimal; mirrors the terminal version's vibe) ──
DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[1;31m"
RESET = "\033[0m"


def make_handlers():
    """Build the callback dict run_turn() expects. on_dangerous MUST return a
    bool (True = allow, False = block) — execute_tool raises CommandBlocked on
    False."""

    def on_text(chunk):
        # Stream assistant text live, no trailing newline.
        sys.stdout.write(chunk)
        sys.stdout.flush()

    def on_assistant_done(_txt):
        # on_text already printed the body; just close the line.
        sys.stdout.write("\n")
        sys.stdout.flush()

    def on_tool_use(_id, name, tool_input):
        print(f"{CYAN}  → {name}{RESET} {DIM}{tool_input}{RESET}")

    def on_tool_result(_id, outcome):
        text = outcome if isinstance(outcome, str) else str(outcome)
        if len(text) > 2000:
            text = text[:2000] + f"{DIM} …(truncated){RESET}"
        print(f"{DIM}  {text}{RESET}")

    def on_dangerous(command):
        # Ctrl-D (EOFError) → deny safely. Ctrl-C (KeyboardInterrupt) is NOT
        # caught here: it propagates to main()'s per-turn handler, aborting the
        # whole turn instead of just this prompt.
        try:
            ans = input(f"{RED}  Dangerous command flagged:{RESET} {command}\n"
                        f"{RED}  Allow? (y/n): {RESET}")
        except EOFError:
            print()
            return False
        return ans.strip().lower() in ("y", "yes")

    def on_plan(plan):
        # Show the proposed plan and block for approve / request-changes.
        print(f"\n{CYAN}{BOLD}┌─ Proposed plan ─────────────────────────────{RESET}")
        for line in plan.splitlines():
            print(f"{CYAN}│{RESET} {line}")
        print(f"{CYAN}{BOLD}└─────────────────────────────────────────────{RESET}")
        try:
            ans = input(f"{BOLD}  Approve and start building? (y = approve / n = request changes): {RESET}")
            if ans.strip().lower() in ("y", "yes"):
                return {"approved": True}
            feedback = input(f"{BOLD}  What should change? {RESET}")
        except EOFError:
            print()
            return {"approved": False, "feedback": "(no feedback — treat as not approved)"}
        return {"approved": False, "feedback": feedback}

    def on_supervisor_block(name, reason):
        print(f"{YELLOW}  ⚠ supervisor blocked {name}: {reason}{RESET}")

    def on_warning(msg):
        print(f"{RED}  {msg}{RESET}")

    def on_usage(summary):
        print(f"{DIM}  [{summary['model']} · {summary['total_tokens']} tok "
              f"· ${summary['cost_usd']}]{RESET}")

    def on_title(title):
        print(f"{DIM}  (session titled: {title}){RESET}")

    def on_compaction():
        print(f"{YELLOW}  [context compacted]{RESET}")

    def on_error(msg):
        print(f"{RED}  ERROR: {msg}{RESET}")

    return {
        "on_text": on_text,
        "on_assistant_done": on_assistant_done,
        "on_tool_use": on_tool_use,
        "on_tool_result": on_tool_result,
        "on_dangerous": on_dangerous,
        "on_plan": on_plan,
        "on_supervisor_block": on_supervisor_block,
        "on_warning": on_warning,
        "on_usage": on_usage,
        "on_title": on_title,
        "on_compaction": on_compaction,
        "on_error": on_error,
    }


def main():
    if "--version" in sys.argv[1:] or "-V" in sys.argv[1:]:
        print(f"magic-pigeon {agent_core.__version__}")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"{RED}ERROR: ANTHROPIC_API_KEY is not set.{RESET}")
        print('  export ANTHROPIC_API_KEY="sk-ant-..."  then re-run.')
        return 1

    agent = agent_core.AgentSession()
    handlers = make_handlers()

    # Shift+Tab toggles plan mode (same as the web UI). The prompt message is a
    # callable so the [PLAN] tag redraws live when the binding flips the state.
    kb = KeyBindings()

    @kb.add("s-tab")
    def _(event):
        agent.set_plan_mode(not agent.plan_mode)
        event.app.invalidate()

    def prompt_message():
        tag = f"{YELLOW}[PLAN] {RESET}" if agent.plan_mode else ""
        return ANSI(f"{tag}{BOLD}u : {RESET}")

    ps = PromptSession(key_bindings=kb)

    print(f"{BOLD}magic-pigeon (Version2) — terminal mode{RESET}")
    print(f"{DIM}session {agent.session_id} · history in {agent_core.HISTORY_DIR}{RESET}")
    print(f"{DIM}type 'exit' or Ctrl-D to quit · Shift+Tab (or '/plan') to toggle plan mode{RESET}\n")

    while True:
        try:
            user_input = ps.prompt(prompt_message)
        except (EOFError, KeyboardInterrupt):
            print()
            break

        stripped = user_input.strip()
        if stripped.lower() in ("exit", "quit", ":q"):
            break
        if stripped.lower() in ("/plan", "/plan-mode"):
            on = agent.set_plan_mode(not agent.plan_mode)
            print(f"{YELLOW}  plan mode {'ON — investigate & plan, no edits until approved' if on else 'OFF'}{RESET}")
            continue
        if stripped.lower() == "/model" or stripped.lower().startswith("/model "):
            parts = stripped.split(maxsplit=1)
            if len(parts) == 1:
                print(f"{BOLD}  models (current: {agent.model} · effort {agent.effort}):{RESET}")
                for mid, meta in agent_core.MODELS.items():
                    mark = f"  {YELLOW}*{RESET}" if mid == agent.model else ""
                    print(f"    {mid}  {DIM}({meta['label']}){RESET}{mark}")
                print(f"{DIM}  usage: /model <id or substring>   ·   /effort <{'|'.join(agent_core.EFFORT_LEVELS)}>{RESET}")
            else:
                q = parts[1].strip()
                match = q if q in agent_core.MODELS else next(
                    (m for m in agent_core.MODELS
                     if q.lower() in m.lower() or q.lower() in agent_core.MODELS[m]["label"].lower()),
                    None)
                if match:
                    res = agent.set_model(model=match)
                    print(f"{YELLOW}  model → {res['model']} (effort {res['effort']}){RESET}")
                else:
                    print(f"{RED}  no model matching {q!r}; type /model to list{RESET}")
            continue
        if stripped.lower().startswith("/effort"):
            parts = stripped.split(maxsplit=1)
            if len(parts) == 2 and parts[1].strip() in agent_core.EFFORT_LEVELS:
                res = agent.set_model(effort=parts[1].strip())
                print(f"{YELLOW}  effort → {res['effort']}{RESET}")
            else:
                print(f"{DIM}  usage: /effort <{'|'.join(agent_core.EFFORT_LEVELS)}>  (current: {agent.effort}){RESET}")
            continue
        if not stripped:
            continue

        # Ctrl-C during a turn aborts just this turn and returns to the prompt,
        # instead of crashing the CLI. KeyboardInterrupt is not an Exception, so
        # run_turn's `except Exception` never catches it — we must here.
        try:
            agent.run_turn(user_input, handlers)
        except KeyboardInterrupt:
            agent.request_stop()
            print(f"\n{YELLOW}  [turn interrupted]{RESET}")

    agent.finalize()
    print(f"{DIM}bye.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
