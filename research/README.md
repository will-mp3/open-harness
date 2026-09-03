# Research

Reference material gathered while designing **open-harness** (our Python + OpenRouter
agent harness).

## opencode/

A complete structural map of [`sst/opencode`](https://github.com/sst/opencode), used as
a **baseline reference** (not a 1:1 copy) for our first build. Start with
[`opencode/00-architecture-overview.md`](opencode/00-architecture-overview.md) — it ties
the five subsystem deep-dives together and proposes a Python repo shape.

| Doc | Subsystem |
|-----|-----------|
| `00-architecture-overview.md` | Top-level map, package graph, repo-design proposal |
| `01-session-agent-loop.md` | Turn loop, session state, message/part model, event bus |
| `02-tool-system.md` | Tool contract, registry, built-in tools, permissions |
| `03-provider-model-layer.md` | Provider abstraction, model catalog, streaming, OpenRouter |
| `04-interface-transport.md` | Engine-as-server, HTTP+SSE API, CLI, TUI, ACP |
| `05-config-plugins-storage.md` | Config precedence, plugins, skills/commands, SQLite storage |
