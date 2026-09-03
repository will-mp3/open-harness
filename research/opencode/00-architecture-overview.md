# opencode — System Architecture & Repo Design Overview

> Baseline reference for building **open-harness** (Python + OpenRouter). This file
> synthesizes the five subsystem deep-dives in this directory into a single top-level
> map: the monorepo layout, the package dependency graph, the layered runtime
> architecture, the cross-cutting design themes, and a recommended repo shape for our
> own build.
>
> Source snapshot: `github.com/sst/opencode` cloned at commit `d2efd81` (Sep 2026).
> Everything below was verified by reading source, not docs. Where this file is terse,
> the numbered companion docs carry the file-path-level detail.

## Companion documents

| # | File | Scope |
|---|------|-------|
| 00 | `00-architecture-overview.md` | This file — top-level map & repo design |
| 01 | `01-session-agent-loop.md` | The turn loop, session state, message/part model, event bus |
| 02 | `02-tool-system.md` | Tool contract, registry, dispatch, built-in tools, permissions |
| 03 | `03-provider-model-layer.md` | Provider abstraction, model catalog, request build, streaming, OpenRouter |
| 04 | `04-interface-transport.md` | Engine-as-server, HTTP+SSE API, CLI, TUI, ACP, SDK generation |
| 05 | `05-config-plugins-storage.md` | Config precedence, plugins, skills/commands, SQLite storage, snapshots |

---

## 1. What opencode is, structurally

opencode is an AI coding agent shipped as a single CLI (`opencode`) plus a family of
UIs. Its defining architectural decision is that **the agent engine runs as a local
HTTP server, and every user interface — TUI, web, editor (ACP), headless `run` — is a
client of that server** (see 04). There is no "library mode" that UIs call in-process;
they all speak the same HTTP + SSE contract. This one decision shapes the whole repo.

The codebase is mid-migration between two generations, which is important to read
correctly:

- **`packages/opencode`** — the canonical, complete, currently-shipping harness. Self
  contained: it owns the turn loop, tools, providers, server, CLI, config, storage.
  This is the package we mapped as the baseline.
- **`packages/core` (`@opencode-ai/core`)** + **`packages/schema`** + **`packages/llm`**
  — a newer **Effect-based** rewrite the shipping package increasingly *pulls from*.
  `opencode` already imports its data model, SQLite persistence, and typed event system
  from `core`/`schema`, and can run an experimental native LLM runtime from `llm`. So
  "opencode vs core" is not old-vs-new alternatives — it is **a shipping shell that is
  being hollowed out onto shared Effect libraries.**

For our purposes: **read `packages/opencode` for the complete behavior, read
`packages/llm` + `packages/schema` for the cleanest data-model definitions.**

---

## 2. Monorepo layout

Tooling: **Bun** workspaces (`bun@1.3.14`) + **Turbo** for task orchestration, `oxlint`
for lint, **SST** (`sst.config.ts`) for cloud infra, `husky` for git hooks. ~30 packages
under `packages/*`. Grouped by role:

### Engine & shared libraries (the parts that matter to us)
| Package | Name | Role |
|---|---|---|
| `opencode` | `opencode` | **The CLI + engine.** `bin` entry, owns the turn loop, tools, server, providers. |
| `core` | `@opencode-ai/core` | Effect-based shared core: data model, SQLite persistence, event bus, config, plugin, skill. |
| `schema` | `@opencode-ai/schema` | Effect `Schema` definitions for sessions/messages/parts/permissions (the v1 data model). |
| `llm` | `@opencode-ai/llm` | Experimental native LLM runtime: explicit `LLMRequest`/`LLMEvent` schemas + transport. |
| `plugin` | `@opencode-ai/plugin` | Public plugin API (hooks, tool/auth/provider registration). |
| `protocol` | `@opencode-ai/protocol` | The newer V2 `/api/*` HTTP contract. |
| `server` | `@opencode-ai/server` | Server scaffolding. |
| `codemode` | `@opencode-ai/codemode` | Confined code execution over schema-described tools ("code mode"). |

### Clients & UIs
`tui` (SolidJS/OpenTUI terminal UI — **not Go anymore**), `web` / `app` / `desktop`
(web + Tauri-style desktop), `session-ui`, `ui`, `storybook`, `slack` (Slack bot),
`sdk` / `sdk-next` / `client` (generated API clients).

### Infra / support
`enterprise`, `function`, `identity`, `stats`, `console`, `containers`, `docs`,
`effect-sqlite-node`, `effect-drizzle-sqlite`, `http-recorder` (record/replay HTTP for
tests), `httpapi-codegen` (generates clients from the Effect HttpApi contract), `script`.

> For a first Python build, ~8 of these ~30 packages matter. The rest are commercial
> surface (enterprise, console, stats, slack), alternate UIs, or build tooling.

---

## 3. Package dependency graph (the spine)

```
                 ┌─────────────────────────────────────────────┐
                 │  UIs / clients                              │
                 │  tui (Solid) · web · desktop · slack · ACP  │
                 └───────────────┬─────────────────────────────┘
                                 │  HTTP + SSE  (generated clients: sdk, client)
                                 ▼
                 ┌─────────────────────────────────────────────┐
                 │  packages/opencode  (engine + CLI)          │
                 │  ├─ server/   Effect HttpApi over node:http  │
                 │  ├─ cli/      yargs command tree             │
                 │  ├─ session/  the turn loop + processor      │
                 │  ├─ tool/     tool registry + built-ins      │
                 │  ├─ provider/ model resolution + AI-SDK glue │
                 │  ├─ permission/ config/ plugin/ mcp/ storage/│
                 └───────┬───────────────┬──────────────┬──────┘
                         │               │              │
             imports data│model    imports │       experimental │native runtime
                         ▼               ▼              ▼
              @opencode-ai/schema   @opencode-ai/core   @opencode-ai/llm
              (Effect Schema:       (SQLite persistence, (LLMRequest /
               session/message/      typed event bus,     LLMEvent schemas
               part/permission)      config, projector)   + transport)
```

The critical arrows: **all UIs depend on the engine only through the HTTP/SSE contract**
(never in-process), and **the engine's data model + persistence + event system are
being pushed down into `schema`/`core`** so multiple front-ends (opencode CLI, native
runtime, enterprise) can share them.

---

## 4. Layered runtime architecture

Read top-to-bottom as a request flows through a running system.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 1. TRANSPORT / INTERFACE  (doc 04)                                         │
│    Local HTTP server (Effect HttpApi / node:http). UIs are clients.        │
│    - Commands: yargs CLI tree; `run` headless; TUI as Worker thread.       │
│    - Events out: SSE envelopes {id,type,properties}, connected + 10s beat. │
│    - WebSocket only for PTY. ACP = stdio JSON-RPC bridge for editors.      │
└──────────────────────────────────────────────────────────────────────────┘
                                   │  POST a prompt → /session/:id
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ 2. SESSION / TURN LOOP  (doc 01)                                           │
│    SessionPrompt.prompt → SessionRunState.ensureRunning (single-flight)    │
│    → runLoop while(true): re-read full history, assemble system+history+   │
│      tools, create assistant message, SessionProcessor.process(stream).    │
│    Loop ends when last assistant finishes with non-`tool-calls` reason and │
│    no dangling tool calls. Doom-loop guard + abort semantics.              │
└──────────────────────────────────────────────────────────────────────────┘
              │ needs a model stream                 │ needs to run a tool
              ▼                                       ▼
┌───────────────────────────────────┐   ┌────────────────────────────────────┐
│ 3. PROVIDER / MODEL LAYER (doc 03) │   │ 3'. TOOL SYSTEM (doc 02)           │
│  Resolve model from models.dev     │   │  registry (built-ins + custom/MCP) │
│  catalog + config. Build request   │   │  → session/tools.ts dispatch       │
│  (ProviderTransform). Two paths:   │   │  → Tool.define(execute) with       │
│  Vercel AI SDK (default) OR native │   │    Context{sessionID,abort,        │
│  @opencode-ai/llm runtime.         │   │    metadata(),ask()}               │
│  Both normalize to ONE LLMEvent    │   │  → ExecuteResult{title,output,     │
│  stream.  OpenRouter = 1st-class.  │   │    metadata,attachments}           │
└───────────────────────────────────┘   │  Permission gate: ask/allow/deny,  │
                                          │  last-wildcard-match-wins ruleset. │
                                          └────────────────────────────────────┘
                                   │  every step emits events
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ 4. STATE / PERSISTENCE  (docs 01, 05)                                      │
│    Event-sourced: updateMessage/updatePart PUBLISH events; a projector     │
│    subscribes and upserts JSON blobs into SQLite (Drizzle).                │
│    Streaming deltas ride a non-durable message.part.delta event → SSE.     │
│    Config (deep-merge precedence), skills/commands, snapshots (shadow git).│
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 5. The five load-bearing abstractions

If we copy nothing else conceptually, copy these. Each is the "spine" of its subsystem.

1. **The provider-neutral `LLMEvent` stream** (docs 01, 03). Every model backend —
   AI-SDK adapters *and* the native runtime — is normalized into one discriminated
   event stream (text delta, reasoning, tool-call, step-start/finish, usage, error).
   The session processor only ever consumes `LLMEvent`; it knows nothing about
   OpenRouter or any SDK. **This is the single most important seam for us**: our Python
   OpenRouter client's only job is to emit this event stream.

2. **Part-based messages** (`WithParts = {info, parts[]}`, doc 01). A message is a
   header (`User | Assistant`) plus an ordered list of discriminated-union parts (text,
   reasoning, file, tool, step-start, step-finish, subtask, compaction). Tool calls and
   their results are parts with a `pending → running → completed/error` lifecycle. This
   is what makes streaming, resumption, and rich UI possible.

3. **The re-read-history turn loop** (doc 01). Each iteration re-reads the full
   persisted history rather than mutating an in-memory array, assembles
   system+history+tools fresh, and streams one assistant message. Termination is a
   function of the last message's finish reason + dangling-tool-call check.

4. **Engine-as-server + SSE** (doc 04). The engine is a local HTTP server; UIs are
   clients over HTTP + SSE. This decouples UI from engine, makes the CLI/TUI/web/editor
   all thin, and makes the whole thing scriptable and testable.

5. **Event-sourced persistence via a projector** (docs 01, 05). Mutations publish
   events; a projector materializes them into SQLite. The bus is the source of truth for
   both persistence and live UI streaming — one mechanism, two consumers.

Supporting themes worth noting: **config as a deep-merge precedence chain** (doc 05),
**data-driven provider resolution** via the models.dev catalog + `api.npm` factory
selection (doc 03), and **permissions as an ordered ruleset with a blocking `ask`
gate** (doc 02).

---

## 6. Where opencode is more than we need (first build)

opencode carries years of production surface. For a first baseline, treat these as
**later / optional** (all called out in the companion docs):

- Two LLM execution paths (AI SDK **and** native runtime) — we pick **one** (native
  OpenRouter client emitting `LLMEvent`).
- The CQRS projector / event-sourcing split — we can persist synchronously first and
  keep the event bus only for live streaming.
- Worker-thread TUI transport trick, ACP, IDE installer, share/sync, worktrees,
  enterprise/console/stats/slack, code-mode, plugin marketplace.
- On-demand `npm install` of provider adapters (irrelevant once we target OpenRouter
  directly).

Essential-first (per docs 02/05): config, SQLite storage, project detection, the tool
contract + a handful of built-in tools, permissions, skills/commands, snapshots.

---

## 7. Recommended repo shape for open-harness (Python)

A translation of opencode's spine into idiomatic Python. This is a **starting proposal**
for the design phase, not a final decision — it maps 1:1 onto the five load-bearing
abstractions above.

```
open-harness/
├─ pyproject.toml                # uv/hatch; ruff + pyright
├─ src/open_harness/
│  ├─ schema/                    # ← @opencode-ai/schema  (pydantic models)
│  │   ├─ message.py             #   Message = info + parts[]; Part union
│  │   ├─ session.py
│  │   ├─ events.py              #   LLMEvent union  ← THE seam (doc 03)
│  │   └─ permission.py
│  ├─ llm/                       # ← provider layer (doc 03)
│  │   ├─ openrouter.py          #   httpx streaming client → yields LLMEvent
│  │   ├─ catalog.py             #   models.dev fetch + cache
│  │   └─ request.py             #   build request from messages/tools/params
│  ├─ session/                   # ← the turn loop (doc 01)
│  │   ├─ loop.py                #   re-read-history while-loop + termination
│  │   ├─ processor.py           #   consume LLMEvent stream → parts
│  │   └─ state.py               #   single-flight run guard, abort
│  ├─ tools/                     # ← tool system (doc 02)
│  │   ├─ base.py                #   Tool contract: params schema, Context, ExecuteResult
│  │   ├─ registry.py
│  │   └─ builtin/               #   read/write/edit/bash/grep/glob/ls/webfetch/task/todo
│  ├─ permission/                # ← ruleset + ask() gate (doc 02)
│  ├─ config/                    # ← deep-merge precedence chain (doc 05)
│  ├─ storage/                   # ← SQLite (sqlite3/SQLModel) + projector (doc 05)
│  ├─ bus.py                     # ← typed event bus (async pub/sub)
│  ├─ server/                    # ← engine-as-server (doc 04): FastAPI + sse-starlette
│  └─ cli/                       # ← Typer/Click command tree; `run` headless
└─ research/opencode/            # ← these reference docs
```

Concrete tech picks the companion docs converge on:
- **HTTP client / streaming**: `httpx` (async, SSE-capable) → the OpenRouter call.
- **Server**: **FastAPI + Uvicorn + `sse-starlette`**; Pydantic for the typed contract;
  generate OpenAPI → client, with a CI drift check (doc 04).
- **CLI**: Typer or Click (mirrors the yargs command tree).
- **Schemas**: Pydantic v2 everywhere (replaces both zod and Effect `Schema`).
- **Storage**: `sqlite3`/SQLModel with the `session/message/part/todo/project` tables
  from doc 05.
- **Async model**: plain `asyncio` (we do *not* need Effect; Effect is opencode's way of
  getting typed effects/DI/streams in TS — Python gets those from asyncio + Pydantic +
  context managers).

### The one seam that de-risks everything
Define `LLMEvent` (schema/events.py) and the OpenRouter client that yields it **first**.
Everything upstream (session loop, processor, tools, UI) depends only on that event
stream, so the provider is swappable and the rest of the system can be built and tested
against a fake event stream without a live model.

---

## 8. Suggested reading order for the team

1. This file (00) — the map.
2. **01 (session/agent loop)** — the heart; establishes messages/parts/`LLMEvent`.
3. **03 (provider/model)** — how `LLMEvent` is produced; our OpenRouter target.
4. **02 (tools)** — the other half of the loop.
5. **04 (interface/transport)** — how it's all exposed.
6. **05 (config/plugins/storage)** — the supporting substrate.
