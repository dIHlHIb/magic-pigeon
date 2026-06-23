  # magic-pigeon 🐦   

  A learning project about building a local AI agent, kept in three versions so the evolution is visible.

  | Version | Path | What it is |
  |---------|------|------------|
  | v1 | `Version1/` | The original single-file Python agent, terminal only. |
  | v2 | `Version2/` | The agent refactored into a reusable engine, with a web UI, an OpenAI-compatible gateway, web search, and a model picker. |
  | v3 | `Version3/` | Adds runtime controls on top of v2: plan mode, sub-agents, project hooks, an autonomous-mode supervisor, a `pigeon/` project-context directory, adaptive thinking, and a terminal CLI. |
  
  The current version lives in `Version3/`. To run it, start there:

  ```bash
  cd Version3
  ```

  and follow `Version3/README.md`. I built this mainly to understand how coding agents work under the hood, not as a polished replacement for tools like Claude Code.

