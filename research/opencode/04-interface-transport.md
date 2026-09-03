# opencode: Interface & Transport Layer

How the user-facing clients (TUI, web UI, editors, headless CLI) talk to the
opencode harness engine. All paths are relative to `/tmp/opencode-src` unless
noted. Canonical harness package: `packages/opencode/src`.

> Note on the codebase: opencode has migrated to **Effect** (the `effect`
> library, including the unstable `effect/unstable/http` and
> `effect/unstable/httpapi` modules). The HTTP server, routing, schemas, and
> even the client SDK generation are all Effect-native. This is unusual and
> heavily shapes the design — a Python reimplementation will not mirror the
> mechanics, but the *architecture* (a local HTTP server that every client
> talks to) is very much worth adopting.

---

## 1. Overview & process model

opencode is built around a single idea: **the engine is an HTTP server, and
every UI is a client of it.** There is no in-process "library" API that the TUI
calls directly; the TUI, the web UI, editors (via ACP), and the headless `run`
command all speak the same HTTP + SSE API. This is the most important
architectural takeaway.

Concretely there are several ways the server gets stood up:

- **`opencode serve`** — starts a standalone headless HTTP server that listens
  on a TCP port. Clients (browser, remote TUI, another machine) connect over
  the network. Entry: `packages/opencode/src/cli/cmd/serve.ts`.
- **`opencode` (default, no subcommand) → TUI** — spawns a **Worker thread**
  that hosts the engine, and by default does **not** open a TCP port at all.
  HTTP requests from the TUI are tunneled over `postMessage` RPC straight into
  the in-process server's `app.fetch(request)`. See §5.
- **`opencode acp`** — boots a local `Server.listen()` and bridges it to an
  editor over stdio JSON-RPC. See §6.
- **`opencode web`** — like `serve` but also opens a browser.
- **`@opencode-ai/sdk-next`** — runs the server's router fully in-memory (no
  socket) behind a JS SDK, for embedding. See §7.

So "what runs where" has one answer with two transports:

```
                    ┌─────────────────────────────────────────┐
                    │  opencode engine (Effect service graph)   │
                    │  session, LLM, tools, MCP, permissions... │
                    └───────────────▲───────────────────────────┘
                                    │  Effect HttpApi (routes)
                    ┌───────────────┴───────────────────────────┐
                    │  HTTP server  (in-process app.fetch  OR    │
                    │                real TCP listener + SSE/WS) │
                    └───▲──────────▲──────────▲──────────▲───────┘
     Worker postMessage │   HTTP   │   HTTP   │  stdio   │  in-memory
        RPC (local TUI) │  +SSE    │  +SSE    │ JSON-RPC │  fetch
                    ┌───┴───┐  ┌───┴───┐  ┌───┴────┐ ┌───┴─────┐
                    │  TUI  │  │  Web  │  │Editor  │ │sdk-next │
                    │(Solid)│  │  UI   │  │(ACP/Zed)│ │(embed) │
                    └───────┘  └───────┘  └────────┘ └─────────┘
```

The server can also act as a **control plane**: a single listener can serve
many project directories and even proxy to *remote* workspaces. Every request
carries a `directory` (or `workspace`) selector; see §2, "Workspace routing".

---

## 2. The server

### Framework

Effect's HTTP stack, not Express/Fastify/Hono:

- `effect/unstable/http` — `HttpRouter`, `HttpServer`, `HttpServerResponse`,
  `HttpMiddleware`, etc.
- `effect/unstable/httpapi` — `HttpApi`, `HttpApiEndpoint`, `HttpApiGroup`,
  `HttpApiBuilder`, `OpenApi`. This is a **schema-first, typed** API framework:
  endpoints declare `params` / `query` / `payload` / `success` / `error` as
  Effect `Schema`s, and the framework does validation, serialization, and
  OpenAPI generation from the same declaration.
- Node binding via `@effect/platform-node` `NodeHttpServer` over `node:http`
  `createServer()`.

### Key files

| File | Role |
|---|---|
| `packages/opencode/src/server/server.ts` | Public `Server` module. `Server.listen(opts)` (real TCP listener), `Server.Default()` (in-memory `app.fetch`/`app.request`), `Server.openapi()`. Port fallback, mDNS, graceful shutdown. |
| `.../server/routes/instance/httpapi/server.ts` | `HttpApiApp` — assembles the whole route tree into an Effect `Layer`, wires all handlers + middleware + the full service graph (`app = LayerNode.group([...])`, ~60 services). Exposes `createRoutes(corsOptions)` and `webHandler()`. |
| `.../httpapi/api.ts` | Composes the typed API: `RootHttpApi`, `InstanceHttpApi`, `OpenCodeHttpApi`, and the V2 `ServerApi` (from `@opencode-ai/protocol`). |
| `.../httpapi/public.ts` | `PublicApi` = the API annotated for OpenAPI export, with a large `matchLegacyOpenApi` transform that normalizes Effect's generated spec to the shape the published SDK expects. |
| `.../httpapi/groups/*.ts` | Endpoint **definitions** (routes, methods, schemas), one file per domain. |
| `.../httpapi/handlers/*.ts` | Endpoint **implementations**. |
| `.../httpapi/middleware/*.ts` | auth, cors-vary, compression, error, fence, instance-context, workspace-routing, proxy, schema-error. |

### Route tree (from `server.ts` `createRoutes`)

Five typed API layers plus two raw routes are merged:

- `rootApiRoutes` — `RootHttpApi` = control + control-plane + **global** routes.
- `eventApiRoutes` — the instance SSE stream (`GET /event`).
- `ptyConnectApiRoutes` — the PTY WebSocket upgrade route.
- `instanceApiRoutes` — everything else instance-scoped (`InstanceHttpApi`).
- `serverRoutes` — the **V2 `/api/*`** surface (`Api` from `@opencode-ai/server`).
- `docRoute` — raw `GET /doc` returning the OpenAPI JSON (lazily built).
- `uiRoute` — raw catch-all `* /*` serving the embedded/proxied web UI.

### Endpoint table (legacy/instance surface — selected)

Instance routes are selected via `?directory=` / `x-opencode-directory` /
`?workspace=` (see Workspace routing). `:name` = path param.

**Global / root** (`groups/global.ts`, `control.ts`, `control-plane.ts`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/global/health` | `{ healthy: true, version }` |
| GET | `/global/event` | **Global SSE stream** (all directories, incl. sync events) |
| GET / PATCH | `/global/config` | Get / update global config |
| POST | `/global/dispose` | Dispose all instances |
| POST | `/global/upgrade` | Upgrade opencode to a version |
| PUT / DELETE | `/auth/:providerID` | Set / remove provider auth |
| POST | `/log` | Client log ingestion |
| POST | `/control-plane/...` | `moveSession` (remote workspace moves) |

**Session** (`groups/session.ts`, root `/session`) — the core surface:

| Method | Path | Purpose |
|---|---|---|
| GET | `/session` | List sessions |
| GET | `/session/status` | Status map for all sessions |
| POST | `/session` | Create session |
| GET | `/session/:sessionID` | Get session |
| GET | `/session/:sessionID/children` | Child (forked) sessions |
| GET | `/session/:sessionID/todo` | Todo list |
| GET | `/session/:sessionID/diff` | File diff for a message |
| GET | `/session/:sessionID/message` | List messages (with parts) |
| GET | `/session/:sessionID/message/:messageID` | Get one message |
| POST | `/session/:sessionID/message` | **Send prompt (streams response)** |
| POST | `/session/:sessionID/prompt_async` | Send prompt, return immediately (202-style) |
| POST | `/session/:sessionID/command` | Run a slash command |
| POST | `/session/:sessionID/shell` | Run a shell command in session context |
| POST | `/session/:sessionID/abort` | Abort active turn |
| POST | `/session/:sessionID/summarize` | Compact/summarize |
| POST | `/session/:sessionID/init` | Generate `AGENTS.md` |
| POST | `/session/:sessionID/fork` | Fork at a message |
| PATCH | `/session/:sessionID` | Update title/metadata/permission |
| DELETE | `/session/:sessionID` | Delete session |
| POST | `/session/:sessionID/share` · DELETE unshare | Share link |
| POST | `/session/:sessionID/revert` · `/unrevert` | Revert/restore messages |
| POST | `/session/:sessionID/permissions/:permissionID` | Reply to a permission (deprecated form) |
| DELETE | `/session/:sessionID/message/:messageID` | Delete message |
| DELETE/PATCH | `/session/:sessionID/message/:messageID/part/:partID` | Delete/update part |

**File / search** (`groups/file.ts`): `GET /find` (ripgrep text search),
`/find/file`, `/find/symbol` (LSP), `/file` (list), `/file/content`,
`/file/status`.

**Instance** (`groups/instance.ts`): `/path`, `/vcs`, `/vcs/status`,
`/vcs/diff`, `/vcs/diff/raw`, `/vcs/apply`, `/command`, `/agent`, `/skill`,
`/lsp`, `/formatter`, `/instance/dispose`.

**Others**: `groups/config.ts` (`GET/PATCH /config`, `/config/providers`),
`permission.ts` (`GET /permission`, `POST /permission/:id/reply`),
`question.ts` (`list`/`reply`/`reject`), `provider.ts`
(`list`/`auth`/`authorize`/`callback`), `mcp.ts`
(`/mcp`, `/mcp/:name/connect|disconnect|auth...`), `project.ts`,
`project-copy.ts`, `sync.ts` (`start`/`replay`/`steal`/`history`),
`workspace.ts` (`adapters`/`list`/`create`/`status`/`remove`/`warp`),
`experimental.ts` (`capabilities`, `tool`, `worktree`, `session`, `resource`,
`console`), `tui.ts` (see §5), `pty.ts` (see below).

### PTY (pseudo-terminal) routes (`groups/pty.ts`)

`GET /pty/shells`, `GET /pty`, `POST /pty`, `GET/PUT/DELETE /pty/:ptyID`,
`POST /pty/:ptyID/connect-token`, and the **WebSocket** route
`GET /pty/:ptyID/connect` (upgraded; auth by short-lived ticket query param).

### V2 `/api/*` surface (`packages/protocol/src/groups/*.ts`)

A newer, cleaner, transport-neutral contract (see §7). Full list:

```
GET  /api/health
GET  /api/event                          (SSE)
GET  /api/agent          GET /api/command      GET /api/model
GET  /api/provider       GET /api/provider/:providerID
GET  /api/skill          GET /api/reference    GET /api/location
GET  /api/fs/find        GET /api/fs/list      GET /api/fs/read/*
GET  /api/integration ...(+connect/attempt)    PATCH /api/credential/:id
GET  /api/permission/request   GET /api/permission/saved
GET  /api/question/request

GET  /api/session                 POST /api/session
GET  /api/session/active
GET  /api/session/:sessionID
POST /api/session/:sessionID/agent            (switch agent)
POST /api/session/:sessionID/model            (switch model)
POST /api/session/:sessionID/prompt
POST /api/session/:sessionID/compact
POST /api/session/:sessionID/wait
POST /api/session/:sessionID/interrupt
GET  /api/session/:sessionID/context
GET  /api/session/:sessionID/history
GET  /api/session/:sessionID/event            (per-session SSE)
GET  /api/session/:sessionID/message[/:messageID]
POST /api/session/:sessionID/revert/{stage,clear,commit}
GET/POST /api/session/:sessionID/permission[/:requestID[/reply]]
GET/POST /api/session/:sessionID/question[/:requestID/{reply,reject}]

GET  /api/pty  POST /api/pty  GET/PUT /api/pty/:ptyID
POST /api/pty/:ptyID/connect-token   GET /api/pty/:ptyID/connect (WS)
```

### Request / response shapes

Because endpoints are declared with Effect `Schema`, the wire shapes are the
decoded schema types. Examples:

- **Create session** `POST /session` — body `Session.CreateInput`, returns
  `Session.Info`.
- **Send prompt** `POST /session/:sessionID/message` — body `PromptPayload`
  (`SessionPrompt.PromptInput` minus `sessionID`: parts, providerID, modelID,
  agent, etc.), returns `{ info: AssistantMessage, parts: Part[] }`.
- **List messages** — returns `SessionV1.WithParts[]` (`{ info, parts }`).
- **Errors** are typed unions per endpoint: `HttpApiError.BadRequest`,
  `ApiNotFoundError`, `SessionBusyError`, `PermissionNotFoundError`, etc.,
  serialized as `{ name, data: { message, ... } }`
  (`middleware/error.ts`, `errors.ts`, `public.ts` `addLegacyErrorSchemas`).

### Auth (`middleware/authorization.ts`)

Optional HTTP **Basic auth**. If `OPENCODE_SERVER_PASSWORD` (username defaults
to `opencode`) is set, all routes require it; otherwise the server is open (and
`serve` prints a warning). Credentials accepted via the `Authorization: Basic`
header **or** an `?auth_token=<base64 user:pass>` query param (so browsers/SSE
can auth). Public static UI paths bypass auth (`isPublicUIPath`). PTY WebSocket
connects auth via a short-lived ticket query param instead.

### CORS (`packages/server/src/cors.ts`)

Allows `localhost`/`127.0.0.1`, `oc://renderer`, `tauri://...`, `*.opencode.ai`,
plus any origins passed via `--cors` / config `server.cors`.

### Workspace routing (`middleware/workspace-routing.ts`, `instance-context.ts`)

The single most non-obvious design point. One server can serve **many project
directories** and proxy to **remote** workspaces:

- Directory selection order: `?directory=` query → `x-opencode-directory`
  header → `process.cwd()`.
- `?workspace=<id>` selects a control-plane workspace. If that workspace
  resolves to a **remote** target, the middleware **HTTP/WebSocket-proxies** the
  request to the remote opencode (`proxyRemote`, with a "fence"/sync-wait
  header protocol). Otherwise it's served **Local** with a `WorkspaceRouteContext`
  ({ directory, workspaceID }) injected.
- These query fields (`directory`, `workspace`) are spread into *every*
  endpoint's query schema (`WorkspaceRoutingQueryFields`), because the Effect
  HttpApi middleware layer cannot declare query params itself.

The upshot: instances are loaded **per request**, keyed by directory. `serve`
sets `instance: false` (no ambient project at startup) precisely because of this.

---

## 3. Event streaming to clients

### Mechanism: Server-Sent Events (SSE), not WebSocket

The primary event channel is SSE. Three SSE endpoints exist:

- `GET /event` — instance-scoped stream (events for the routed directory/workspace).
- `GET /global/event` — global stream across all directories (used by the TUI
  and web UI; includes durable "sync" events).
- `GET /api/session/:sessionID/event` and `GET /api/event` — V2 streams.

WebSockets are used **only** for PTY (interactive terminal) I/O
(`GET /pty/:ptyID/connect`), not for the event feed.

### How the SSE stream is built (`handlers/event.ts`)

`eventResponse` (in `handlers/event.ts`) is the reference implementation:

1. Create an unbounded Effect `Queue`, and eagerly `events.listen(...)`
   (register the listener *before* streaming so no event is lost during
   startup) — `events` is the `EventV2Bridge` service.
2. Build an Effect `Stream` from the queue, **filtered** to the routed
   instance's `directory` (and `workspaceID` if present), then mapped to
   `{ id, type, properties }`.
3. Merge in a `disposed` stream (server instance shutdown) and
   `takeUntil(server.instance.disposed)`.
4. Merge in a **heartbeat**: `server.heartbeat` every 10 seconds.
5. Prepend a synthetic first event `{ type: "server.connected" }`.
6. Encode each item as an SSE `message` event (`Sse.encode()` from
   `effect/unstable/encoding/Sse`), the data being `JSON.stringify(event)`.
7. Return `HttpServerResponse.stream(...)` with headers
   `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`,
   `X-Accel-Buffering: no`, `X-Content-Type-Options: nosniff`.

So the wire protocol is: newline-delimited SSE frames, each
`data: {"id":"...","type":"...","properties":{...}}`, opening with
`server.connected` and keep-aliving with `server.heartbeat` every 10s. Event
type schemas are generated from the `EventManifest` (union of all event types)
and documented in the OpenAPI as the `Event` / `GlobalEvent` / `V2Event`
schemas (`public.ts` special-cases these since HttpApi has no first-class SSE
response type).

### Client consumption

Clients open the SSE endpoint via the SDK's dedicated SSE transport
(`client.sse.*` / `sdk.global.event(...)` in `@opencode-ai/sdk/v2`) and iterate
`events.stream`, with **exponential backoff reconnect** (1s → 30s) and, in the
TUI, a 16 ms batching window before pushing into SolidJS reactive state
(`packages/tui/src/context/sdk.tsx`).

---

## 4. CLI structure & entry point

### Bin & entry

- `packages/opencode/package.json` → `"bin": { "opencode": "./bin/opencode" }`.
- `packages/opencode/bin/opencode` — a small CommonJS Node shim that `spawn`s
  the real target (from `OPENCODE_BIN_PATH` or a bundled path), forwards
  SIGINT/SIGTERM/SIGHUP, and propagates exit code/signal.
- Real TS entry: `packages/opencode/src/index.ts`.

### Arg parsing: **yargs** (v18)

`src/index.ts` builds one yargs `cli`: `.scriptName("opencode")`, `--help/-h`,
`--version/-v` (= `InstallationVersion`), global flags `--print-logs`,
`--log-level`, `--pure`, shell `.completion(...)`, `.strict()`, and a
`.middleware(...)` that sets env (`OPENCODE_PRINT_LOGS`, `OPENCODE_LOG_LEVEL`,
`OPENCODE_PURE`, `AGENT=1`, `OPENCODE=1`, `OPENCODE_PID`) and starts the heap
profiler.

Two thin wrappers standardize commands:

- `cli/cmd/cmd.ts` — `cmd()`: typed pass-through over yargs `CommandModule`
  (adds `--` passthrough typing).
- `cli/effect-cmd.ts` — `effectCmd()`: runs a handler as an Effect under
  `AppRuntime`; unless `instance: false`, it loads a project `InstanceContext`
  via `InstanceStore` and disposes it in `finally`.

### Commands (all in `cli/cmd/`)

| Command | Purpose |
|---|---|
| *(default `$0 [project]`)* → `tui` | Launch the terminal UI (§5) |
| `serve` | Headless HTTP server (`instance: false`); `Server.listen`, then `Effect.never` |
| `web` | `serve` + open browser; prints local/network/mDNS URLs |
| `run [message..]` | **Headless one-shot / mini interactive**: send a prompt, stream events to stdout, exit on idle. Flags `--mini`, `--attach`, `--command`, `--format json`, `--continue`/`--session`/`--fork` |
| `acp` | ACP server over stdio, backed by a local `Server.listen` (§6) |
| `attach <url>` | Attach TUI to an already-running server |
| `agent` | Manage agents (`create`, `list`) |
| `mcp` | Manage MCP servers (`add`, `list`, `auth`, `logout`, `debug`) |
| `models [provider]` | List models (`--refresh` re-fetches models.dev) |
| `providers` | Provider listing/management |
| `session` | `list`, `delete <id>` |
| `stats` | Token usage / cost |
| `github` | GitHub agent (`install`, `run`) |
| `pr <number>` | Checkout a PR branch and run |
| `import <file>` / `export [sessionID]` | Session import/export (JSON / share URL) |
| `generate` | Emit the OpenAPI spec (with JS code samples) to stdout |
| `upgrade [target]` / `uninstall` | Self-management |
| `console` (`account.ts`) | Cloud console auth (`login`/`logout`/`switch`/`orgs`/`open`) |
| `db` | SQLite tools (`instance: false`) |
| `plugin <module>` (`plug.ts`) | Install a plugin |
| `debug` | Troubleshooting (`config`, `lsp`, `ripgrep`, `snapshot`, `startup`, `paths`, ...) |

### Startup sequence

`bin/opencode` (shim) → `spawn` real target → `hideBin(process.argv)` → yargs
parses → middleware sets env + starts heap → command matched (or `$0` → TUI) →
handler runs (Effect commands run under `AppRuntime.runPromise`, loading an
`InstanceContext` unless `instance:false`). Top-level errors are formatted by
`cli/error.ts` `FormatError`; a `finally` calls `process.exit()`.

For `serve`: dynamically `import("../../server/server")`, warn if
`OPENCODE_SERVER_PASSWORD` unset, `resolveNetworkOptions()` (`cli/network.ts`),
`await Server.listen(opts)`, print `opencode server listening on
http://<host>:<port>`, then block on `Effect.never`.

### Network options (`cli/network.ts`)

Defaults: `--port 0` (→ tries **4096** first, then any free port; see
`startWithPortFallback` in `server.ts`), `--hostname 127.0.0.1`, `--mdns false`
(if enabled, hostname defaults to `0.0.0.0`), `--mdns-domain opencode.local`,
`--cors []`. Config file `server.*` values are merged unless a flag is explicit.

---

## 5. TUI ↔ server connection

### The TUI is TypeScript/SolidJS, not Go

`packages/tui` (`@opencode-ai/tui`) is a **SolidJS-in-the-terminal** app
rendered via `@opentui/solid` + `@opentui/core` + `@opentui/keymap`. Entry
`src/index.tsx` → `src/app.tsx` (`run`, `TuiInput`). (Older opencode had a Go
TUI; that is gone.) It talks to the engine exclusively through the generated
SDK `@opencode-ai/sdk/v2` (`createOpencodeClient`): plain HTTP methods plus the
SSE transport (`client.sse.*`). Events arrive via `sdk.global.event(...)` →
`GET /global/event`.

### Process model: one process, two threads

Launcher: `cli/cmd/tui.ts` (`TuiThreadCommand`) + `cli/tui/{layer,worker}.ts`.

1. Resolve project dir, `process.chdir`.
2. Spawn a **Node `Worker`** running `cli/tui/worker.ts`.
3. Wrap it in `Rpc.client<typeof rpc>(worker)` — a **custom JSON-RPC over
   `postMessage`** (`packages/opencode/src/util/rpc.ts`:
   `rpc.request`/`rpc.result`/`rpc.event` envelopes with incrementing ids).

**The worker hosts the engine/server.** On boot it starts the heap profiler,
subscribes `GlobalBus.on("event", e => Rpc.emit("global.event", e))`, and
exposes RPC methods: `fetch`, `server`, `snapshot`, `checkUpgrade`, `reload`,
`shutdown`.

### Two transport modes (chosen in `tui.ts`)

- **Local / in-process (default): no TCP port.** `transport.url =
  "http://opencode.internal"` (dummy). The SDK's `fetch` is replaced by
  `createWorkerFetch(client)`: each `Request` is serialized and sent as
  `client.call("fetch", ...)`; the worker's `rpc.fetch` runs it against
  `Server.Default().app.fetch(request)` **in memory** (no sockets). Events use
  `createEventSource(client)` = `client.on("global.event", ...)`, i.e. GlobalBus
  events forwarded over the worker RPC channel instead of real SSE.
- **External (when `--port`/`--hostname`/`--mdns` given):** main thread calls
  `client.call("server", network)`; the worker runs `Server.listen(...)` (real
  TCP + optional mDNS) and returns `{ url }`. The TUI then connects over **real
  HTTP+SSE** to that URL with `ServerAuth.headers()`.

Handshake: chdir → spawn worker → build RPC client → resolve network opts →
pick transport → `validateSession` (`cli/tui/validate-session.ts`, does
`sdk.session.get`) → schedule `checkUpgrade` after 1 s → `import("../tui/layer").run(...)`.
Lifecycle: `SIGUSR2` → `client.call("reload")`; shutdown → `client.call("shutdown")`
(5 s timeout) → `worker.terminate()`.

### TUI control channel (server → TUI callbacks)

Beyond the event stream, the server can **drive** the TUI (open dialogs, append
a prompt, run a command) and **request** things back from it. Two pieces:

- `server/tui-event.ts` (re-exports `TuiEvent` from `@opencode-ai/schema`:
  `PromptAppend`, `CommandExecute`, `ToastShow`, `SessionSelect`) — these are
  published through `EventV2Bridge` and reach the TUI over the normal
  `/global/event` SSE stream.
- `server/shared/tui-control.ts` — a **request/response control channel** built
  on two in-memory `AsyncQueue`s. `TuiRequest = { path, body }`. Exposed via
  `groups/tui.ts` + `handlers/tui.ts`:
  - `GET /tui/control/next` — **long-poll**: awaits `nextTuiRequest()`; the TUI
    polls this to pick up a request the server side pushed.
  - `POST /tui/control/response` — the TUI submits the result
    (`submitTuiResponse`).
  - Plus fire-and-forget POSTs: `/tui/append-prompt`, `/open-help`,
    `/open-sessions`, `/open-themes`, `/open-models`, `/submit-prompt`,
    `/clear-prompt`, `/execute-command`, `/show-toast`, `/publish`,
    `/select-session`.

### Two TUIs

Both are SolidJS terminal apps on the same SDK+SSE stack:

- `packages/tui` — the full, routed, plugin-capable TUI (dialogs, config).
- `cli/cmd/run/` — a minimal inline "mini" runtime (`runtime.ts`, split
  `footer.*` + `scrollback.*` design, `stream.transport.ts`). Reached via
  `opencode run --mini`. Two sub-modes: `runInteractiveMode` (attach to an SDK
  client / server) and `runInteractiveLocalMode` (fully in-process, no server).

---

## 6. ACP & IDE integration

### ACP (Agent Client Protocol)

ACP lets an external editor (notably **Zed**) drive opencode as an "agent."
opencode implements the **agent side**. Protocol types/connection primitives
come from the npm package **`@agentclientprotocol/sdk` (v0.21.0)** (see
`packages/opencode/package.json`). Source: `packages/opencode/src/acp/`.

- **Transport: JSON-RPC over stdio** (newline-delimited JSON). Entry
  `cli/cmd/acp.ts` (`opencode acp`): (1) boots a local `Server.listen()`,
  (2) creates an in-process `OpencodeClient` SDK pointed at that local
  `http://host:port` with `ServerAuth.headers()`, (3) wraps `process.stdout`/
  `stdin` as `WritableStream`/`ReadableStream`, (4) frames via `ndJsonStream`,
  (5) `new AgentSideConnection((conn) => agent.create(conn), stream)`. Sets
  `OPENCODE_CLIENT="acp"`. So the editor↔opencode transport is stdio JSON-RPC,
  while opencode's engine is reached over its own **local HTTP API via the SDK**.
- `acp/agent.ts` — `class Agent implements ACPAgent`. Methods: `initialize`,
  `authenticate`, `newSession`, `loadSession`, `listSessions`, `resumeSession`,
  `closeSession`, `unstable_forkSession`, `setSessionConfigOption`,
  `setSessionMode`, `unstable_setSessionModel`, `prompt`, and `cancel`.
- `acp/service.ts` (~1100 lines) — the mapping layer. `initialize` →
  `protocolVersion: 1` + agent capabilities (loadSession, MCP http/sse, embedded
  context + image prompts, session close/fork/list/resume) + auth method
  `opencode-login`. Session ops call `sdk.session.create/get/messages/fork`,
  register MCP servers, send `available_commands_update`. `prompt` converts ACP
  content blocks to opencode parts, detects slash commands, and calls
  `sdk.session.prompt` / `session.command` / `session.summarize` (`/compact`),
  wrapped in `runUntilIdle` so it resolves only after the turn's events drain.
- `acp/event.ts` (`class Subscription`) — subscribes to `sdk.global.event()`
  and translates opencode events into ACP `session/update` notifications
  (`agent_message_chunk`, `agent_thought_chunk`, `tool_call` /
  `tool_call_update`, `usage_update`, permission requests); resolves
  `runUntilIdle` on `session.status idle`.
- `acp/tool.ts` — maps opencode tool names → ACP `ToolKind` (bash→execute,
  edit/write→edit, grep/glob→search, read→read, task→think, webfetch→fetch),
  emits diffs and image attachments.
- `acp/permission.ts` — `permission.asked` → ACP `requestPermission`
  (once/always/reject), replies via `sdk.permission.reply`.
- `acp/content.ts`, `config-option.ts`, `directory.ts`, `usage.ts`, `error.ts`,
  `session.ts`, `profile.ts` — content conversion (handles `zed://` URIs),
  model/effort/mode selects, cached per-directory snapshot, cost/usage,
  error→`RequestError` mapping, in-memory ACP session store, optional
  `OPENCODE_ACP_PROFILE` timing.

Data flow: `editor ⇄ (stdio JSON-RPC) ⇄ ACP Agent/Service ⇄ (HTTP + global
event SSE, via SDK) ⇄ opencode server/engine`. ACP never touches engine
internals directly.

### IDE integration (`packages/opencode/src/ide/index.ts`) — separate from ACP

A small helper for the opencode **VS Code-family extension** (not ACP): detects
the host IDE from env (`TERM_PROGRAM==="vscode"` + `GIT_ASKPASS` match against a
`SUPPORTED_IDES` list: Windsurf, VS Code, VS Code Insiders, Cursor, VSCodium),
`alreadyInstalled()` via `OPENCODE_CALLER`, and `install(ide)` shelling out to
e.g. `code --install-extension sst-dev.opencode`. Its `Event` is re-exported
from `@opencode-ai/schema/ide-event`. Purely extension detection/installation.

---

## 7. SDK / client generation

**The authoritative source of truth is the Effect `HttpApi` definition** — not
a hand-written spec. There are two parallel pipelines: a legacy OpenAPI/Hey-API
one and a new Effect-`HttpApi`-native one.

### Contract layer: `packages/protocol`

`@opencode-ai/protocol` is the transport-neutral API contract, built from
`@opencode-ai/schema` groups (`src/groups/*.ts`) plus middleware/errors. It
exposes `makeApi`/`makeDefaultApi(middlewareKeys)` — the caller (server or
client) injects concrete middleware identities, so the contract stays Core-free.
Both the running server (`packages/server/src/api.ts` = `makeDefaultApi`, with
real handlers) and the generated client (`ClientApi` = `makeDefaultApi`) build
on this same contract, which is what keeps them in sync. This is the V2 `/api/*`
surface (§2).

### Legacy pipeline → `packages/sdk` (published)

`@opencode-ai/sdk` (v1.x, public). Committed `packages/sdk/openapi.json` (~1 MB),
JS client under `packages/sdk/js/src/gen/` and `.../v2/gen/`, generated by
**`@hey-api/openapi-ts`** (files header `// This file is auto-generated by
@hey-api/openapi-ts`). Promise/fetch-based (`@hey-api/client-fetch`).

- **How `openapi.json` is produced:** `cli/cmd/generate.ts` (`opencode
  generate`) calls `Server.openapi()` = `OpenApi.fromApi(PublicApi)` (see
  `server.ts:67`), injects `x-codeSamples`, prints Prettier JSON.
- Build script `packages/sdk/js/script/build.ts` runs `bun dev generate >
  openapi.json`, prunes unreachable schemas, then runs Hey API into
  `./src/v2/gen`, then applies textual patches (numeric query params, an SSE
  codegen fix).
- Full chain: `HttpApi (PublicApi)` → `OpenApi.fromApi` → `openapi.json` →
  `@hey-api/openapi-ts` → `sdk/js/src/gen`.
- **This is the SDK the TUI and ACP consume** (`@opencode-ai/sdk/v2`).

### New pipeline → `packages/client` (+ `httpapi-codegen`)

`@opencode-ai/client` is generated **directly from the Effect `HttpApi`** (no
OpenAPI). Two entrypoints: `.` = zero-Effect Promise/fetch client
(`src/generated/`), `./effect` = rich Effect client via `HttpApiClient`
(`src/generated-effect/`, re-exporting decoded datatypes from
`@opencode-ai/schema`). Generation driven by `packages/client/script/build.ts`
using `@opencode-ai/httpapi-codegen`; output is committed and a `check:generated`
script runs `git diff --exit-code` to catch drift.

`@opencode-ai/httpapi-codegen` (single file `src/index.ts`, ~1185 lines, deps
`effect` + `prettier`) is the generic tool: `compile(Api)` → shared `Contract`;
`emitPromise(contract)` → Promise/fetch client with lazy `AsyncIterable` SSE
streams; `emitEffect(contract)` → Effect client with `Stream` streaming and
runtime schemas; `write(output, dir)`.

### `packages/sdk-next` (embedding, in-memory)

`@opencode-ai/sdk-next` (private) is the transitional replacement for `sdk`. It
runs the server router **in memory, no socket**: `src/opencode.ts` builds a web
handler from `createEmbeddedRoutes()` (`@opencode-ai/server/routes`) and wires it
into `OpenCode.make(...)` from `@opencode-ai/client/effect` via a custom `fetch`
that dispatches `Request`s to the in-process handler. Also adds local-only
`tools.register(...)`. It consumes the generated `client`, it is not itself
generated.

### Package relationship summary

- `schema` → typed datatypes; `protocol` → shared Effect `HttpApi` contract.
- `server` → concrete handlers/middleware over `protocol`; exposes `PublicApi`.
- `opencode generate` (`OpenApi.fromApi(PublicApi)`) → `sdk/openapi.json`.
- `sdk` (published, legacy) → Hey-API TS client from `openapi.json`.
- `httpapi-codegen` → generic Effect-`HttpApi` → Promise+Effect TS clients.
- `client` → new generated target from `protocol`'s `ClientApi`.
- `sdk-next` → in-process host consuming `client/effect`; slated to replace `sdk`.

---

## 8. Notes for a Python + OpenRouter reimplementation

### Adopt the client/server split — but you don't need the Worker dance

The core architecture — **engine = local HTTP server, every UI = a client** —
is the right call and worth copying. It cleanly decouples UI from engine,
enables remote/headless/editor use for free, and gives you one testable API
surface. Recommendation: **run a real local HTTP server (loopback) always**,
and let the TUI/CLI connect to it over HTTP+SSE. opencode's default
"in-process Worker `postMessage` fetch, no port" mode exists mainly to avoid TCP
overhead and port management in the common single-user case; in Python this is
more trouble than it's worth (no cheap in-process `app.fetch` equivalent, GIL,
`multiprocessing` complexity). Bind to `127.0.0.1:0`, discover the chosen port,
hand it to the UI. Keep the option to bind a public host for remote/`serve` use.

### Recommended server framework

**FastAPI + Uvicorn** (or Starlette directly). Rationale:

- First-class **SSE** via streaming responses (`StreamingResponse` /
  `sse-starlette`'s `EventSourceResponse`) — maps directly onto opencode's
  `text/event-stream` model.
- **Pydantic** models replace Effect `Schema` for request/response validation
  and give you **automatic OpenAPI** (`/openapi.json`, `/docs`) — which is
  exactly how opencode bootstraps its SDKs. Generate typed clients from that
  spec (e.g. `openapi-python-client`, or `@hey-api/openapi-ts` for a TS TUI).
- Async-native, so a single event loop can hold many long-lived SSE connections
  plus concurrent LLM streaming.
- WebSocket support (Starlette) for the one thing opencode uses WS for: PTY.

### Event streaming

Mirror opencode's SSE contract closely — it's well designed:

- One JSON envelope per event: `{ "id", "type", "properties" }`.
- Emit a synthetic `server.connected` first, then a `server.heartbeat` every
  ~10 s to keep proxies/load balancers from idling the connection.
- Set `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no`
  (critical if you ever sit behind nginx).
- **Register the event listener before you start the response body** so events
  produced during connection setup aren't dropped (opencode does this
  deliberately — see `handlers/event.ts`). In Python, back the stream with an
  `asyncio.Queue` subscribed to your event bus before the first `yield`.
- Handle client disconnect (FastAPI `request.is_disconnected()` /
  `asyncio.CancelledError`) to unsubscribe and free the queue.
- Prefer SSE over WebSocket for the event feed: it's one-directional
  (server→client), reconnects trivially, and works through more proxies.
  Commands go the other way as ordinary POSTs. Reserve WS for interactive PTY.

### CLI

Use **Typer** or **Click** for the command tree (analog of yargs). Keep the same
command shape: default → TUI, plus `serve`, `run` (headless one-shot), `acp`,
`attach`. The headless `run` command that streams events to stdout and exits on
idle is genuinely useful for scripting/CI — build it early.

### Multi-project / workspace routing — decide early, keep it simple

opencode's per-request `?directory=` / `x-opencode-directory` routing (one
server, many project dirs, plus remote proxying) is powerful but a significant
source of complexity (the `workspace-routing` + `instance-context` middleware,
fence/sync protocol, remote proxy). For a first cut, **pin one server to one
project directory** and skip the control-plane/remote-proxy machinery. If you
later need multi-project, add a required `directory` selector on requests rather
than an ambient global — that's the part of opencode's design that ages well.

### Auth & CORS

Copy the simple model: optional HTTP Basic (`OPENCODE_SERVER_PASSWORD`), also
accepting a token via query param so browser `EventSource` (which can't set
headers) and file downloads can authenticate. Restrict CORS to
localhost/loopback plus explicitly configured origins. Warn loudly when the
server is unsecured, as opencode does.

### SDK generation

Let the framework's OpenAPI schema be the source of truth (FastAPI gives this
for free) and **generate** clients rather than hand-writing them — this is the
single most valuable process lesson from opencode. If your TUI is also Python,
you may just import the Pydantic models directly; if it's another language,
generate from `/openapi.json`. Add a CI check that regenerates and diffs
(opencode's `check:generated` / `git diff --exit-code`) to catch drift.

### Gotchas observed in opencode

- **SSE has no first-class typed response** in these frameworks — opencode has
  to hand-patch the OpenAPI spec to document the stream shape (`public.ts`).
  Plan to document your SSE endpoints manually.
- **Query-param typing**: middleware-declared query params (`directory`,
  `workspace`) had to be spread into every endpoint schema because the
  framework's middleware layer couldn't declare them. In FastAPI, use a shared
  dependency (`Depends`) for common query params instead.
- **Config provider caching**: opencode had to reinstall a fresh env-based
  config provider per listener because the default one snapshots `process.env`
  once (`server.ts` comment). In Python, don't cache config at import time if
  you want per-process/per-test overrides to take effect.
- **Port fallback**: opencode prefers a fixed port (4096) then falls back to a
  random free port. A fixed default is friendlier for bookmarks/tooling; keep a
  `0` fallback.
- **Ordering of the log/observability layer**: opencode had a bug where an
  eagerly-forked background task captured stdout and corrupted the TUI. Lesson:
  when the CLI and TUI share a process, make sure background tasks never write
  to stdout/stderr that the TUI owns. With a separate server process this
  largely goes away — another point in favor of a real loopback server.
```
