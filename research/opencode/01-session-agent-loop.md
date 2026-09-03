# opencode — Session & Agent Loop (Reference)

Scope: the core conversation/agent loop and session management in
`/tmp/opencode-src/packages/opencode/src` (dirs `session/`, `session/llm/`,
`agent/`, `bus/`, plus `event-manifest.ts`). This is a reference for a team
reimplementing an equivalent harness in Python against OpenRouter.

> Important architectural note discovered while reading: the `packages/opencode`
> package is **already Effect-based** and imports its data model, persistence,
> and event system from the newer `@opencode-ai/core` (`packages/core/src`) and
> `@opencode-ai/schema` (`packages/schema/src`). The turn-loop *logic* lives in
> `packages/opencode/src/session`, but the message/part schemas, the SQLite
> tables, and the event projector that actually persists data live in
> `packages/core` / `packages/schema`. Both are cited below with exact paths.

---

## 1. Overview of the subsystem

A single user turn flows through these layers (all are Effect `Context.Service`s
wired by `LayerNode`):

```
HTTP / TUI / SDK
   │  Prompt.prompt(input)
   ▼
SessionPrompt (session/prompt.ts)      ── orchestrates a turn
   │  createUserMessage → persists user msg+parts (as events)
   │  loop → SessionRunState.ensureRunning (session/run-state.ts, single-flight guard)
   ▼
runLoop (session/prompt.ts)            ── the while(true) agent loop
   │  builds system prompt + history + tools
   │  creates an assistant message
   ▼
SessionProcessor (session/processor.ts) ── consumes the LLM event stream,
   │  turns stream events into message Parts, drives tool execution
   ▼
LLM (session/llm.ts)                   ── one model call; returns Stream<LLMEvent>
   │  LLMRequestPrep.prepare (session/llm/request.ts) builds the request
   │  AI SDK streamText (default) → LLMAISDK.toLLMEvents (session/llm/ai-sdk.ts)
   │  or native runtime (session/llm/native-runtime.ts, opt-in)
   ▼
Provider (AI SDK / @opencode-ai/llm) → actual HTTP to the model
```

Every mutation (`updateMessage`, `updatePart`, `updatePartDelta`) is published as
an **event** on the bus (`session/session.ts`). Persistence is decoupled: a
**projector** in core (`packages/core/src/session/projector.ts`) subscribes to
the durable events and upserts JSON blobs into SQLite. The event bus is also how
the TUI/clients receive streaming deltas.

The loop is **not** a fixed request/response; it's a `while (true)` that keeps
re-reading the full message history from the DB each iteration and decides
whether to call the model, run a queued subtask, run compaction, or exit.

---

## 2. Component inventory

### `session/` (in `packages/opencode/src`)

| File | Purpose |
|---|---|
| `prompt.ts` | **Turn orchestrator.** `prompt`, `loop`, `runLoop`, `command`, `shell`, title generation, user-message construction/expansion, subtask handling. The `while(true)` loop lives here. |
| `processor.ts` | **Stream consumer / part builder.** `SessionProcessor.create` returns a `Handle` whose `process(streamInput)` consumes `Stream<LLMEvent>` and converts each event into message parts, drives tool lifecycle, retries, cleanup. |
| `llm.ts` | **Model-call service.** `LLM.stream(input)` returns `Stream<LLMEvent>`. Selects native vs AI-SDK runtime, wires abort, telemetry, GitLab-workflow tool executor. |
| `llm/request.ts` | `LLMRequestPrep.prepare` — assembles system messages, provider options/params, headers, tool set, message array actually sent to the provider. |
| `llm/ai-sdk.ts` | `LLMAISDK.toLLMEvents` — adapter converting AI SDK `fullStream` parts into the provider-neutral `LLMEvent` union; `adapterState()` holds per-stream cursor. |
| `llm/native-runtime.ts` | Opt-in adapter over `@opencode-ai/llm` that emits `LLMEvent`s directly (bypassing AI SDK). Returns `{type:"supported"|...}` or a fallback reason. |
| `llm/native-request.ts` | Builds the native `@opencode-ai/llm` request body (messages/tools/params) for the native runtime. |
| `message-v2.ts` | **Message/part data access + model-message conversion.** `toModelMessagesEffect` (parts → AI SDK `ModelMessage[]`), `page`/`stream`/`parts`/`get`, `filterCompacted`, `latest`, `fromError`. |
| `message.ts` | Legacy/compat message helpers (thin). |
| `message-error.ts` | Error-part shape helper. |
| `session.ts` | **Session service.** CRUD over sessions, `updateMessage`/`updatePart`/`updatePartDelta` (publish events only), `getUsage` (token/cost math), `fork`, `getPart`, `findMessage`, `messages`. |
| `schema.ts` | Re-exports `SessionID`, `MessageID`, `PartID` branded IDs. |
| `run-state.ts` | **Concurrency guard.** `SessionRunState` keeps a `Runner` per session; `ensureRunning`/`startShell`/`cancel`/`assertNotBusy`. Prevents two turns running at once; cancels background jobs. |
| `status.ts` | Per-session status (`idle`/`busy`/`retry`), published as events; in-memory map. |
| `processor.ts` (retry) | uses `retry.ts`. |
| `retry.ts` | `SessionRetry.policy` — exponential backoff w/ jitter, honors `retry-after` headers, classifies retryable errors, max 5 retries. |
| `compaction.ts` | Auto/manual context compaction: `create`, `process`, `isOverflow`, `prune`. Summarizes history and rewrites what the model sees. |
| `summary.ts` | Background per-turn summary/diff generation (`summarize`). |
| `overflow.ts` | `isOverflow({cfg,tokens,model})` — token-budget check. |
| `reminders.ts` | `SessionReminders.apply` — injects system reminders (e.g. plan-mode, todo) into the message list before the model call. |
| `instruction.ts` | Collects system-instruction blocks (AGENTS.md, rules); `system()`; per-message `clear`. |
| `system.ts` | `SystemPrompt` service: `provider(model)` picks the base prompt file by model family; `environment(model)` (env block), `skills(agent)`, `mcp(agent)`. |
| `tools.ts` | `SessionTools.resolve` — builds the AI SDK tool map for a turn (native tools + MCP tools + MCP resource tools), wiring each `execute` back through the processor + permission system. |
| `todo.ts` | Todo list state per session. |
| `revert.ts` | Revert/checkpoint support (`cleanup`, snapshot diffing). |
| `run-state.ts` | (see above) |
| `prompt/*.txt` | Base system prompts per model family (`anthropic.txt`, `gpt.txt`, `gemini.txt`, `beast.txt`, `codex.txt`, `default.txt`, plan-mode, etc.). |

### `agent/`

| File | Purpose |
|---|---|
| `agent.ts` | **Agent registry.** `Agent.Info` schema; builds built-in agents (`build`, `plan`, `general`, `explore`, `compaction`, `title`, `summary`) merged with user config; `get`/`list`/`defaultInfo`/`defaultAgent`/`generate`. Agents carry prompt, model, permission ruleset, mode, step cap. |
| `subagent-permissions.ts` | Permission helpers for subagents. |
| `prompt/*.txt` | Prompts for hidden agents: `title.txt`, `summary.txt`, `compaction.txt`, `explore.txt`. |
| `generate.txt` | Prompt used by `Agent.generate` to author a new agent config. |

### `bus/`

| File | Purpose |
|---|---|
| `global.ts` | `GlobalBus` — a Node `EventEmitter` singleton (`emit("event", {directory,project,workspace,payload})`). Assigns an event id if missing. This is the final fan-out point clients subscribe to. |

Note: the *typed* event system (`EventV2`) lives in core; opencode publishes
through `event-v2-bridge.ts`, which attaches instance/location metadata and
re-emits onto `GlobalBus` (both a live copy and a durable "sync" copy).

### `event-manifest.ts`

Re-exports `Definitions`, `Durable`, `Latest` from
`@opencode-ai/schema/event-manifest`
(`packages/schema/src/event-manifest.ts`) — the registry of every event type in
the system (session, message, part, permission, mcp, lsp, tui, etc.). Durable
vs live is a per-definition flag; durable events are what the projector persists
and what clients replay.

---

## 3. The turn lifecycle in detail (step-by-step trace)

All `file:function` refs are in `packages/opencode/src` unless noted.

### 3.1 Entry — user message in

1. `SessionPrompt.prompt(input)` — `session/prompt.ts:1052`.
   `PromptInput` (schema at `prompt.ts:1499`) carries `sessionID`, optional
   `agent`, `model`, `variant`, output `format`, and `parts` (union of
   `TextPartInput | FilePartInput | AgentPartInput | SubtaskPartInput`).
2. `sessions.get` + `revert.cleanup(session)` (rolls back any pending checkpoint).
3. `createUserMessage(input)` — `prompt.ts:635`:
   - resolves the agent (`agents.get` / `agents.defaultInfo`) and the model
     (input.model → agent.model → `currentModel(sessionID)` which reads the
     session row, else last user msg, else provider default — `prompt.ts:614`).
   - persists the session's current agent/model via `sessions.setAgentModel`.
   - builds `SessionV1.User` message info (`prompt.ts:656`).
   - **expands each input part** via `resolvePart` (`prompt.ts:699`): file parts
     are actually executed through the `read` tool and inlined as synthetic text
     parts; `data:` text urls are decoded; MCP `resource` sources are fetched;
     `agent` parts become a synthetic "call the task tool with subagent X" text
     part; images are normalized. This is where a lot of "context assembly"
     happens *before* the model ever sees it.
   - fires the `chat.message` plugin hook.
   - persists the message and every part via `sessions.updateMessage` /
     `sessions.updatePart` (each publishes an event; see §6).
4. Session-level tool permissions from `input.tools` are converted to a
   permission ruleset and stored (`prompt.ts:1060`).
5. If `noReply`, return the message; otherwise call `loop({sessionID})`.

### 3.2 Concurrency guard

`loop` (`prompt.ts:1343`) delegates to
`SessionRunState.ensureRunning(sessionID, onInterrupt=lastAssistant, work=runLoop)`
(`run-state.ts:88`). One `Runner` per session; a second concurrent prompt for a
busy session hits `BusyError`. Cancellation aborts the runner and cascades to
background jobs. Status transitions (`idle`↔`busy`) publish events.

### 3.3 The loop — `runLoop(sessionID)` (`prompt.ts:1081`)

`while (true)`:

1. `status.set(busy)`.
2. **Load history**: `MessageV2.filterCompactedEffect(sessionID)`
   (`message-v2.ts:574`) reads *all* messages+parts for the session
   (`stream` → paginated `page`), then applies compaction filtering/reordering.
3. `MessageV2.latest(msgs)` (`message-v2.ts:582`) computes `{user, assistant,
   finished, tasks}` where `tasks` = pending `compaction`/`subtask` parts.
4. **Termination check** (`prompt.ts:1111`): if the last assistant message has a
   `finish` reason that is NOT `tool-calls`/`unknown`, has no un-executed tool
   calls, and is parented to the last user message → **break** (turn done).
   `hasToolCalls` (`prompt.ts:1106`) treats "stop with dangling tool calls" as
   *keep looping* so tool results can still be sent back. Orphaned
   interrupted tool parts are ignored.
5. `step++`. On step 1, fork background title generation (`title`, `prompt.ts:193`).
6. Resolve model (`getModel`, `prompt.ts:594`).
7. **Queued task dispatch**:
   - `subtask` → `handleSubtask` (`prompt.ts:255`): creates an assistant message
     + a `task` ToolPart, invokes the `task` tool (spawns a child agent/session),
     streams its result back as the tool output. `continue`.
   - `compaction` → `compaction.process`; may `stop` or `continue`.
8. **Auto-compaction**: if `finished.tokens` overflow the model budget
   (`compaction.isOverflow`), create a compaction task and `continue`
   (`prompt.ts:1161`).
9. Resolve the agent; `maxSteps = agent.steps ?? Infinity`;
   `isLastStep = step >= maxSteps`.
10. `SessionReminders.apply` injects reminders into `msgs` (`prompt.ts:1180`).
11. **Create the assistant message** (`SessionV1.Assistant`, `prompt.ts:1186`)
    and persist it (empty, to be filled by the stream).
12. `processor.create({assistantMessage, sessionID, model})` → `Handle`
    (`processor.ts:98`). An `onInterrupt` finalizer marks the assistant message
    aborted if the fiber is interrupted.
13. **Assemble the request** (`prompt.ts:1221`+):
    - `SessionTools.resolve(...)` builds the tool map (`tools.ts:41`).
    - If output `format` is `json_schema`, add a synthetic `StructuredOutput`
      tool (`prompt.ts:1565`) and force `toolChoice: "required"`.
    - On step 1, fork `summary.summarize`.
    - `plugin.trigger("experimental.chat.messages.transform", …)`.
    - Concurrently build: `sys.skills(agent)`, `sys.environment(model)`,
      `instruction.system()`, `sys.mcp(...)`, and
      `MessageV2.toModelMessagesEffect(msgs, model)` (parts → `ModelMessage[]`).
    - Compose the `system: string[]` array from env + instructions + mcp +
      skills (+ structured-output directive).
    - Append `MAX_STEPS_PROMPT` as a trailing assistant message if `isLastStep`.
14. **Call the model**: `handle.process({user, agent, permission, system,
    messages, tools, model, toolChoice})` (`prompt.ts:1272`). Blocks until the
    stream for this step completes (all tools resolved). Returns
    `"continue" | "stop" | "compact"`.
15. **Post-call decisions** (`prompt.ts:1288`+):
    - structured output captured → set `message.structured`, `break`.
    - `content-filter` finish → set `ContentFilterError`, publish, `break`.
    - json_schema requested but no structured output produced →
      `StructuredOutputError`, `break`.
    - `result === "stop"` → `break`; `"compact"` → create compaction, loop again.
16. Loop continues; the next iteration re-reads history (now including the new
    assistant message + tool result parts) and re-evaluates termination.

After breaking: fork `compaction.prune`, return `lastAssistant(sessionID)`
(`prompt.ts:1073`).

### 3.4 One model call — `LLM.stream` (`llm.ts:357`)

- `run(input)` (`llm.ts:85`) resolves the language model, config, provider, auth.
- `LLMRequestPrep.prepare` (`llm/request.ts:56`) produces `Prepared`:
  - `system`: `[ agentPrompt || SystemPrompt.provider(model), ...input.system,
    user.system ]` joined (`request.ts:58`). Collapsed to at most 2 blocks
    (header + rest) after the `system.transform` plugin hook.
  - `messages`: for most providers, `system` blocks are prepended as
    `{role:"system"}` messages, then `input.messages`. (OpenAI-oauth and GitLab
    workflow keep instructions separate.)
  - `params`: temperature/topP/topK/maxOutputTokens/options resolved through
    `ProviderTransform` + agent overrides + variant, via the `chat.params` hook.
  - `tools`: filtered by permission (`resolveTools`, `request.ts:208`), sorted,
    with provider-specific fixups (OpenAI `strict:false`, Copilot `_noop`).
  - `headers`: session-affinity + `x-opencode-*` headers via `chat.headers` hook.
- Default path: AI SDK `streamText({...})` (`llm.ts:280`) with
  `experimental_repairToolCall` (lowercases tool names / routes unknown to an
  `invalid` tool), abort signal, `maxRetries`, provider-transform middleware.
- The `fullStream` is converted to `Stream<LLMEvent>` via
  `LLMAISDK.toLLMEvents` (`llm.ts:373`, adapter in `llm/ai-sdk.ts`).
- Native path (opt-in `flags.experimentalNativeLlm`): `LLMNativeRuntime.stream`
  returns a ready `LLMEvent` stream; falls back to AI SDK with a logged reason.

### 3.5 Consuming the stream — `handle.process` (`processor.ts:641`)

`process(streamInput)`:

- `llm.stream(streamInput)` → `Stream.tap(handleEvent) → takeUntil(needsCompaction)
  → runDrain`, wrapped with `onInterrupt` (marks aborted), retry
  (`SessionRetry.policy`), `catch(halt)`, and `ensuring(cleanup())`.
- `handleEvent` (`processor.ts:278`) is a switch over `LLMEvent.type`:
  - `text-start`/`text-delta`/`text-end` → maintain `ctx.currentText` TextPart;
    deltas go out via `session.updatePartDelta` (streaming), full part via
    `updatePart`. `text-end` runs `experimental.text.complete` plugin hook.
  - `reasoning-*` → `ctx.reasoningMap[id]` ReasoningPart, same delta pattern.
  - `tool-input-start/delta/end` → `ensureToolCall` creates a pending ToolPart
    keyed by tool-call id (`processor.ts:216`).
  - `tool-call` → mark the ToolPart `running` with parsed `input`; **doom-loop
    guard**: if the last 3 parts are the same tool with identical input, ask the
    `doom_loop` permission (`processor.ts:331`).
  - `tool-result` → `toolResultOutput` normalizes `{title,metadata,output,
    attachments}`; images normalized; `completeToolCall` sets the ToolPart
    `completed` (`processor.ts:383`).
  - `tool-error` / provider error in result → `failToolCall` (`processor.ts:186`);
    if the error is a permission/question rejection, sets `ctx.blocked`.
  - `step-start` → capture git snapshot, add a `step-start` part.
  - `step-finish` → compute usage/cost (`Session.getUsage`), update assistant
    message tokens/cost/finish, add a `step-finish` part, snapshot patch (diff),
    fork summary; if tokens overflow, set `ctx.needsCompaction`.
  - `provider-error` → throw (drives retry/halt).
- Tool execution itself is driven by the **AI SDK**: each tool's `execute`
  (built in `tools.ts:99`) runs the opencode tool through `EffectBridge`, calls
  permission `ask`, fires `tool.execute.before/after` hooks, and streams live
  metadata back into the ToolPart via `processor.updateToolCall`. The result the
  tool returns becomes the AI SDK `tool-result` event that `handleEvent` then
  persists. So **the tool-call → tool-result → next model turn feedback loop is
  a combination of (a) AI SDK executing tools within one `streamText` call and
  (b) the outer `while` loop replaying the accumulated parts as `ModelMessage`s
  on the next iteration.**
- `cleanup` (`processor.ts:553`): flushes dangling text/reasoning parts, waits
  briefly for outstanding tool calls, marks any still-running tools as errored
  with `metadata.interrupted=true`, stamps `message.time.completed`.
- Return value: `"compact"` if `needsCompaction`, `"stop"` if `blocked` or the
  message has an error, else `"continue"`.

### 3.6 Termination

The loop ends when (`prompt.ts:1111` / return values):
- assistant `finish` is terminal (`stop`, `length`, etc.) with no pending tool
  calls, OR
- structured output captured, content-filter, or structured-output error, OR
- processor returned `"stop"` (blocked by denied permission — unless
  `experimental.continue_loop_on_deny`).

---

## 4. Key data structures (type shapes)

All schemas are Effect `Schema` definitions in
`packages/schema/src/v1/session.ts` (re-exported via
`@opencode-ai/core/v1/session` as `SessionV1`). IDs are branded strings:
`msg_<ulid>`, `prt_<ulid>`, session ids similar.

### Message: `User` (`v1/session.ts:332`)

```ts
User = {
  id: MessageID              // "msg…"
  sessionID: SessionID
  role: "user"
  time: { created: number }
  agent: string
  model: { providerID, modelID, variant? }
  format?: OutputFormatText | OutputFormatJsonSchema
  system?: string            // per-message system override
  tools?: Record<string, boolean>   // deprecated; merged into permissions
  summary?: { title?, body?, diffs: FileDiff[] }
}
```

### Message: `Assistant` (`v1/session.ts:453`)

```ts
Assistant = {
  id: MessageID
  sessionID: SessionID
  role: "assistant"
  parentID: MessageID        // the user message this replies to
  time: { created: number, completed?: number }
  modelID; providerID; mode: string; agent: string
  path: { cwd: string, root: string }
  cost: number
  tokens: { total?, input, output, reasoning, cache: { read, write } }
  finish?: string            // provider finish reason ("stop","tool-calls",...)
  error?: AssistantError     // union: APIError, AbortedError, AuthError,
                             //   ContextOverflowError, ContentFilterError,
                             //   OutputLengthError, StructuredOutputError, Unknown
  structured?: any           // captured structured output
  variant?: string
  summary?: boolean          // true if this is a summary-generation message
}
Info = User | Assistant      // discriminated by "role"
WithParts = { info: Info, parts: Part[] }
```

### Part union (`v1/session.ts:357`)

`Part = TextPart | ReasoningPart | FilePart | ToolPart | StepStartPart |
StepFinishPart | SnapshotPart | PatchPart | AgentPart | RetryPart |
CompactionPart | SubtaskPart` — discriminated by `type`. Common base:
`{ id: PartID, sessionID, messageID }`.

Selected shapes:

```ts
TextPart      = base & { type:"text", text, synthetic?, ignored?, time?, metadata? }
ReasoningPart = base & { type:"reasoning", text, metadata?, time:{start,end?} }
FilePart      = base & { type:"file", mime, filename?, url, source? }
                // url may be file://, data:…;base64, or http
ToolPart      = base & { type:"tool", callID, tool, state: ToolState, metadata? }
StepStartPart = base & { type:"step-start", snapshot? }
StepFinishPart= base & { type:"step-finish", reason, snapshot?, cost, tokens }
CompactionPart= base & { type:"compaction", auto, overflow?, tail_start_id? }
SubtaskPart   = base & { type:"subtask", prompt, description, agent, model?, command? }
AgentPart     = base & { type:"agent", name, source? }
PatchPart     = base & { type:"patch", hash, files:string[] }
```

`ToolState` (`v1/session.ts:304`) — discriminated by `status`:

```ts
pending   = { status:"pending",  input:{}, raw:string }
running   = { status:"running",  input, title?, metadata?, time:{start} }
completed = { status:"completed",input, output:string, title, metadata,
              time:{start,end,compacted?}, attachments?: FilePart[] }
error     = { status:"error",    input, error:string, metadata?, time:{start,end} }
```

### Session `Info` (`session/session.ts:224`, mirrored `v1/session.ts:543`)

```ts
Info = {
  id: SessionID; slug; projectID; workspaceID?; directory; path?
  parentID?                       // set for child (subtask) sessions
  title; version; agent?
  model?: { id, providerID, variant? }
  cost?; tokens?; summary?; share?; metadata?
  permission?: PermissionV1.Ruleset
  revert?: { messageID, partID?, snapshot?, diff? }
  time: { created, updated, compacting?, archived? }
}
```

### Agent `Info` (`agent/agent.ts:35`)

```ts
Agent.Info = {
  name; description?
  mode: "subagent" | "primary" | "all"
  native?; hidden?
  temperature?; topP?; color?
  permission: PermissionV1.Ruleset   // per-tool allow/ask/deny w/ glob patterns
  model?: { modelID, providerID }
  variant?; prompt?
  options: Record<string, unknown>   // provider options
  steps?                             // max loop steps for this agent
}
```

Built-ins (`agent.ts:140`): `build` (default primary), `plan` (edits denied),
`general` + `explore` (subagents), and hidden `compaction`/`title`/`summary`.
User config merges/overrides at `agent.ts:267`.

### `LLMEvent` (provider-neutral stream) — `packages/llm/src/schema/events.ts`

Tagged union on `type`:
`step-start | text-start | text-delta | text-end | reasoning-start |
reasoning-delta | reasoning-end | tool-input-start | tool-input-delta |
tool-input-end | tool-call | tool-result | tool-error | step-finish | finish |
provider-error`. `Usage` (`events.ts:51`) carries inclusive
`inputTokens/outputTokens/totalTokens` plus a non-overlapping breakdown
(`nonCached/cacheRead/cacheWrite/reasoning`). This is the single interface both
runtimes emit and the processor consumes — the key abstraction to copy.

---

## 5. The event bus / streaming model

Three layers:

1. **`EventV2` (core)** — typed pub/sub. Definitions come from the event
   manifest (`event-manifest.ts` → `@opencode-ai/schema/event-manifest`). Each
   definition is live or **durable** (`durable: {aggregate, version}`); durable
   events are persisted and replayable. Session/message/part events are defined
   in `v1/session.ts:571` (`session.created/updated/deleted`, `message.updated/
   removed`, `message.part.updated/removed/delta`, `session.error`, `session.diff`).
   Note `message.part.delta` is **not** durable — it's the streaming token feed.

2. **`EventV2Bridge` (`event-v2-bridge.ts`)** — opencode's publish boundary.
   `publish` attaches instance `Location` (directory/workspace/project).
   It `listen`s to every core event and re-emits onto `GlobalBus`: a live copy
   `{id,type,properties}` and, for durable events, a `sync` copy
   `{type:"sync", syncEvent:{id, versionedType, seq, aggregateID, data}}`.

3. **`GlobalBus` (`bus/global.ts`)** — a process-wide Node `EventEmitter`
   emitting `("event", {directory, project, workspace, payload})`. The HTTP/SSE
   server and TUI subscribe here. IDs are auto-assigned if missing.

**Streaming path for a turn**: the processor calls `session.updatePartDelta`
(`session.ts:877`) for each text/reasoning delta → publishes `message.part.delta`
→ bridge → GlobalBus → SSE → client renders incrementally. Full parts are
published via `session.updatePart` → `message.part.updated`. So clients get both
fine-grained deltas (live only) and authoritative full-part snapshots (durable).

`Session.updateMessage`/`updatePart` themselves do **no DB writes** — they only
publish. See §6.

---

## 6. State & persistence touchpoints

Persistence is **event-sourced / projection-based**, split across packages:

- **Write side (opencode)**: mutations in `session/session.ts`
  (`updateMessage` :629, `updatePart` :635, `updatePartDelta` :877, `patch`
  :734 for session fields) only `events.publish(...)`.
- **Projection side (core)**: `packages/core/src/session/projector.ts`
  subscribes with `events.project(<Definition>, handler)`:
  - `MessageUpdated` → upsert `MessageTable` (`projector.ts:260`):
    `insert({id, session_id, time_created, data}).onConflictDoUpdate({data})`.
  - `PartUpdated` → upsert `PartTable` (`projector.ts:310`) and adjust session
    token/usage aggregates.
  - `MessageRemoved`/`PartRemoved` → delete + reverse usage.
  - `Session.Updated/Deleted/Moved`, `AgentSwitched`, `ModelSwitched`,
    `Prompted` → update `SessionTable`.
- **Storage schema**: `packages/core/src/session/sql.ts` (Drizzle + SQLite):
  - `SessionTable` — columns for id/project/workspace/parent/slug/title/version,
    tokens (`tokens_input/output/reasoning/cache_read/cache_write`), `agent`,
    `model` (json), `permission` (json), `revert` (json), `metadata` (json),
    `time_created/updated/compacting/archived`.
  - `MessageTable` — `{id PK, session_id FK, time_created, data JSON}` where
    `data` is the full `Info` minus id/session_id. Indexed by
    `(session_id, time_created, id)`.
  - `PartTable` — `{id PK, message_id FK, session_id, time_created, data JSON}`.
    Indexed by `(message_id, id)` and `session_id`.
- **Read side**: `message-v2.ts` reads these tables. `page` (:425) paginates
  newest-first with a base64 cursor `{id,time}`; `stream` (:469) walks all pages
  oldest-first; `hydrate` (:98) joins parts to messages; `info`/`part` (:80/:87)
  reconstruct the full objects from `{columns + data}`.

Other stateful touchpoints per turn:
- **Session row** updated with current agent/model each prompt
  (`setAgentModel`), `touch` bumps `time_updated`.
- **Snapshots** (`Snapshot.track/patch`) capture git working-tree state at
  step boundaries; diffs stored as `patch` parts and session `summary`.
- **Status** (`status.ts`) is in-memory only (per-session map) but published.
- **RunState** (`run-state.ts`) holds the live `Runner` fiber per session
  in-memory (single-flight + cancellation), also in-memory.
- **Compaction** rewrites what the model sees by inserting `compaction` parts +
  a summary assistant message; `filterCompacted` (`message-v2.ts:521`) reorders
  history so the model gets `[compaction-user, summary, …retained tail…]`.

---

## 7. Notes for a Python + OpenRouter reimplementation

### Keep (these are the load-bearing ideas)

- **The provider-neutral event stream** (`LLMEvent` union in
  `packages/llm/src/schema/events.ts`). Define one internal streaming event type
  (`text_start/delta/end`, `reasoning_*`, `tool_input_*`, `tool_call`,
  `tool_result`, `tool_error`, `step_start/finish`, `finish`, `provider_error`)
  and normalize OpenRouter SSE into it. Everything downstream (part building,
  persistence, UI) keys off this and nothing else. This is the single most
  important seam to copy.
- **Message = info + ordered parts**, parts as a discriminated union with an
  explicit `tool` part carrying a `state` machine (`pending → running →
  completed | error`). Store the whole message/part as a JSON blob with a few
  extracted columns (id, session_id, time_created) for indexing — do **not**
  fully normalize into columns. Round-tripping is trivial and schema evolution
  is cheap.
- **The outer `while` loop that re-reads history each iteration** rather than
  holding conversation state in memory. Termination = "last assistant finished
  with a non-tool-calls reason and no dangling tool calls." Treat provider
  `stop` with pending tool calls as *continue*.
- **`toModelMessages` conversion** (`message-v2.ts:131`) as a dedicated, tested
  function: parts → OpenRouter chat messages. Handle: dropping empty/step-start
  parts, converting synthetic/text-file parts to text, emitting `tool` role
  messages for tool results, and **injecting dangling tool calls as
  error tool-results** so the API never sees an unmatched tool_use
  (`message-v2.ts:349`). This class of bug is guaranteed to bite you.
- **Per-session single-flight guard + cancellation** (`run-state.ts`) — one
  active turn per session, with an abort signal threaded to the HTTP client and
  tool executions.
- **System-prompt assembly order** (`request.ts:58`): agent prompt (or
  model-family default) → environment block → instruction files → MCP
  instructions → skills → per-message system override, all joined and sent as
  `system` message(s). Model-family selection of the base prompt is in
  `system.ts:27`.
- **Streaming deltas as a separate, non-persisted event** from the
  full-part upsert. Persist full parts; broadcast deltas.
- **Usage/cost accounting at step-finish** with a non-overlapping token
  breakdown (`Session.getUsage`, `session.ts:338`; `Usage` docstring in
  `events.ts:51`). OpenRouter returns usage in the final SSE chunk — map it once.

### Adapt

- **Effect + LayerNode DI** → plain classes / dependency-injected services or
  a small service registry in Python. The `Context.Service` pattern is just DI.
- **AI SDK `streamText` + tool execution.** opencode lets the AI SDK execute
  tools *inside* one model call (`tools.ts` wires `execute`), then the outer loop
  replays. With OpenRouter you'll likely run the classic manual loop: call model
  → parse `tool_calls` from the stream → execute tools yourself → append
  `tool` results → call again. That's simpler and maps cleanly onto the same
  part/event model; you don't need the two-level (inner AI-SDK + outer while)
  structure. Just make sure a single "assistant turn" can contain multiple
  tool calls and you loop until a text-only stop.
- **The event-sourced projector** (`core/session/projector.ts`) is elegant but
  optional. A simpler start: have `update_part`/`update_message` write to
  SQLite directly *and* emit an event. Keep the emit — clients need it — but you
  don't need full CQRS on day one.
- **AI SDK provider transforms / repair** (`llm.ts:296` repairToolCall,
  `ProviderTransform`). For OpenRouter you mostly get one wire format; keep a
  thin per-model options map (temperature, max_tokens, reasoning) and a
  tool-name repair step (models sometimes emit wrong-cased or unknown tool
  names — route unknowns to an explicit `invalid` tool result instead of
  crashing).
- **Compaction / summary / title** are background niceties — implement the loop
  and persistence first; add these once overflow becomes real. The overflow
  check (`overflow.ts`, `compaction.isOverflow`) is just `tokens > budget`.

### Gotchas

- **Dangling tool calls**: every `tool_use`/`tool_call` in an assistant turn
  MUST have a matching tool result before the next model call, or Anthropic/
  OpenAI-family endpoints 400. opencode synthesizes error results for
  interrupted/pending tools (`message-v2.ts:349`, `processor.ts` cleanup at
  :591). Do the same.
- **Reasoning replay**: signed/thinking blocks (Anthropic) must be preserved
  verbatim across turns; opencode keeps empty text separators as a single space
  and skips reasoning replay when the model differs (`message-v2.ts:262`, :362).
  If you support reasoning models, don't drop or mutate reasoning parts.
- **Media in tool results**: some providers don't accept images/PDFs inside a
  `tool` message; opencode extracts them into a following synthetic `user`
  message (`message-v2.ts:298`). OpenRouter behavior varies by underlying model
  — plan for the fallback.
- **Doom-loop protection**: identical tool+input three times in a row triggers a
  permission gate (`processor.ts:331`). Cheap and worth copying.
- **Abort semantics**: interruption must (a) abort the HTTP stream, (b) abort
  in-flight tool subprocesses, (c) finalize the assistant message with an
  `aborted` error, (d) mark running tool parts interrupted. opencode threads an
  `AbortController` from `LLM.stream` (`llm.ts:361`) and finalizes in
  `onInterrupt` handlers (`prompt.ts:1203`, `processor.ts:662`).
- **Model/agent resolution precedence**: input → agent default → session's last
  model → provider default (`prompt.ts:614`, `:646`). Persist the chosen
  agent/model on the session so follow-up turns are consistent.
- **`step-finish` vs `finish`**: `step-finish` fires per model step (usage/cost
  accrue there); `finish` is terminal for the whole stream. The assistant
  message's `finish` reason is set from the last `step-finish` reason
  (`processor.ts:457`).
- **History is read every loop iteration** — with large sessions this is a full
  paginated scan of two tables. Fine for local SQLite; index on
  `(session_id, time_created, id)` as opencode does, and consider a working-set
  cache if you scale.

---

## Appendix — most important files to read first (in order)

1. `packages/opencode/src/session/prompt.ts` — `runLoop` (:1081), `prompt` (:1052),
   `createUserMessage` (:635).
2. `packages/opencode/src/session/processor.ts` — `handleEvent` (:278),
   `process` (:641), `cleanup` (:553).
3. `packages/opencode/src/session/message-v2.ts` — `toModelMessagesEffect` (:131),
   `filterCompacted` (:521), `latest` (:582).
4. `packages/llm/src/schema/events.ts` — the `LLMEvent` union + `Usage`.
5. `packages/schema/src/v1/session.ts` — message/part/session schemas + event defs.
6. `packages/opencode/src/session/llm.ts` + `llm/request.ts` + `llm/ai-sdk.ts` —
   the model-call seam and the AI-SDK→LLMEvent adapter.
7. `packages/core/src/session/projector.ts` + `packages/core/src/session/sql.ts`
   — persistence.
8. `packages/opencode/src/agent/agent.ts`, `session/tools.ts`, `session/system.ts`
   — agents, tool assembly, system prompt.
