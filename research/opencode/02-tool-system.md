# opencode Harness Reference — Tool System & Permissions

Scope: `packages/opencode/src/tool/` and `packages/opencode/src/permission/`. This is the
canonical harness. Note: the `opencode` package here is **already Effect-based** — tools are
Effect programs (`Effect.gen`), services are `Context.Service`, and errors are `Schema.TaggedErrorClass`.
The `packages/core/src` package holds the shared schema/v1 definitions (`@opencode-ai/core/v1/...`,
`@opencode-ai/schema/...`) that `opencode` imports. Where a type physically lives in `core`/`schema`
it is noted.

---

## 1. Overview

A tool in opencode is a lazily-initialized Effect that yields a definition object with an `id`,
`description`, a parameter **schema** (Effect `Schema`, not Zod), an optional precomputed
`jsonSchema`, and an `execute(args, ctx)` Effect. Tools are declared with `Tool.define(id, initEffect)`
in individual files under `tool/`, collected by a **registry** service
(`tool/registry.ts`), filtered/annotated per model+agent, then bridged into the Vercel **AI SDK**
`tool()` shape in `session/tools.ts` where the actual dispatch and result plumbing happen.

Every tool call flows through a **permission gate**: tools call `ctx.ask({ permission, patterns, always, metadata })`,
which is evaluated against a ruleset (agent defaults + user config + session/subagent rules) producing
`allow` / `ask` / `deny`. `ask` blocks on a `Deferred` until the UI replies.

Key files:

| Concern | File |
|---|---|
| Tool contract, `define`, `Context`, arg validation wrapper | `packages/opencode/src/tool/tool.ts` |
| Registry (discovery, plugin/custom tools, per-model filtering) | `packages/opencode/src/tool/registry.ts` |
| Effect-Schema → JSON Schema conversion | `packages/opencode/src/tool/json-schema.ts` |
| Output truncation (line/byte caps, spill-to-file) | `packages/opencode/src/tool/truncate.ts` |
| AI SDK bridge / actual dispatch & MCP tools | `packages/opencode/src/session/tools.ts` |
| Permission engine (ask/reply/evaluate) | `packages/opencode/src/permission/index.ts` |
| Bash command-prefix arity table | `packages/opencode/src/permission/arity.ts` |
| Permission wire types (Rule, Request, Ruleset…) | `packages/schema/src/v1/permission.ts` (re-exported via `@opencode-ai/core/v1/permission`) |
| Config permission schema | `packages/core/src/v1/config/permission.ts` |
| Agent default rulesets | `packages/opencode/src/agent/agent.ts` |

---

## 2. The tool definition contract

All in `packages/opencode/src/tool/tool.ts`.

### 2.1 `Context` — what every `execute` receives

```ts
type Context<M extends Metadata = Metadata> = {
  sessionID: SessionID
  messageID: MessageID
  agent: string                       // agent name, e.g. "build"
  abort: AbortSignal                  // from the AI SDK tool-execution options
  callID?: string                     // tool_call id
  extra?: { [key: string]: unknown }  // e.g. { model, bypassAgentCheck, promptOps }
  messages: SessionV1.WithParts[]     // conversation so far
  metadata(input: { title?: string; metadata?: M }): Effect.Effect<void>  // live-update UI mid-run
  ask(input: Omit<PermissionV1.Request, "id" | "sessionID" | "tool">): Effect.Effect<void>
}
```

`ctx.metadata(...)` streams intermediate state (e.g. bash streams partial output into the tool
call's `state.metadata.output`). `ctx.ask(...)` is the permission gate (see §6).
`ctx.extra` carries `model`, `bypassAgentCheck`, and `promptOps` (the task tool needs `promptOps`
to spawn subagent sessions). It is populated in `session/tools.ts:context(...)`.

### 2.2 Result shape

```ts
interface ExecuteResult<M extends Metadata = Metadata> {
  title: string                       // short UI label
  metadata: M                         // structured data for UI / follow-up tools
  output: string                      // the text returned to the model
  attachments?: Omit<SessionV1.FilePart, "id" | "sessionID" | "messageID">[]
                                      // e.g. images/PDFs as data: URLs
}
```

`output` is the model-facing string. `attachments` become file parts (images/PDFs) merged into the
message. `metadata` is not sent to the model but is stored on the tool-call part and drives UI.

### 2.3 The `Def` / `Info` interfaces

```ts
interface Def<Parameters extends Schema.Decoder<unknown>, M extends Metadata> {
  id: string
  description: string
  parameters: Parameters              // an Effect Schema (Schema.Struct(...))
  jsonSchema?: JSONSchema7            // optional precomputed override (AI-SDK provider type)
  execute(args: Schema.Schema.Type<Parameters>, ctx: Context): Effect.Effect<ExecuteResult<M>>
  formatValidationError?(error: unknown): string
}

interface Info<Parameters, M> {
  id: string
  init: () => Effect.Effect<DefWithoutID<Parameters, M>>   // lazy init
}
```

`Info` is what a tool module exports (e.g. `ReadTool`); `Def` is the resolved, ready-to-run form
produced by `Tool.init(info)`. `DefWithoutID` is `Def` minus `id` (id comes from `define`).

### 2.4 `Tool.define` and the arg-validation wrapper

```ts
export function define<Parameters, Result, R, ID extends string>(
  id: ID,
  init: Effect.Effect<Init<Parameters, Result>, never, R>,
): Effect.Effect<Info<Parameters, Result>, never, R | Truncate.Service | Agent.Service> & { id: ID }
```

`define` runs the init effect (which usually acquires services like `FSUtil.Service`, `LSP.Service`),
grabs `Truncate.Service` and `Agent.Service`, and returns an `Info` whose `init()` wraps the raw
`execute` with cross-cutting behavior (`wrap(...)`):

1. **Arg validation.** `Schema.decodeUnknownEffect(parameters)` is compiled once per init, then run
   on every call. On failure it maps to a typed **`InvalidArgumentsError`** (tagged
   `"ToolInvalidArgumentsError"`) whose `.message` getter tells the model:
   *"The {tool} tool was called with invalid arguments: {detail}. Please rewrite the input so it
   satisfies the expected schema."* A tool may customize `detail` via `formatValidationError`.
2. **Run** the real `execute` on the decoded args.
3. **Auto-truncation.** Unless the result already set `metadata.truncated`, the output is passed
   through `truncate.output(...)` (per-agent limits) and, if truncated, `metadata.truncated=true`
   plus `metadata.outputPath` are added.
4. Everything is wrapped in `Effect.orDie` and an OpenTelemetry span (`Tool.execute` with
   `tool.name`, `session.id`, `message.id`, `tool.call_id`).

`Tool.init(info)` simply resolves `info.init()` and stamps `id`.

Note: `id` is a `ToolID` schema branded as starting with `"tool"` only for the truncation temp-file
naming (`tool/schema.ts`); the tool's public name (e.g. `"read"`) is the `define` id.

---

## 3. Registration & discovery

`packages/opencode/src/tool/registry.ts` defines `ToolRegistry.Service` with interface:

```ts
interface Interface {
  ids():  Effect.Effect<string[]>
  all():  Effect.Effect<Tool.Def[]>
  named(): Effect.Effect<{ task: TaskDef; read: ReadDef }>
  tools(model: {                       // the per-request, model+agent-aware view
    providerID; modelID; agent: Agent.Info; permission?: PermissionV1.Ruleset
  }): Effect.Effect<Tool.Def[]>
}
```

### 3.1 Built-in set

Built-ins are hardcoded and initialized eagerly into an `InstanceState`. The ordered list
(`state.builtin`) is:

```
invalid, [question?], bash(shell), read, glob, grep, edit, write, task, webfetch(fetch),
todowrite, websearch(search), skill, apply_patch, [execute(code-mode)?],
[lsp?], [plan_exit?]
```

Conditional inclusion:
- `question` — only when `flags.client ∈ {app,cli,desktop}` or `enableQuestionTool`.
- `execute` (code-mode) — only under `flags.experimentalCodeMode`, dynamically imported.
- `lsp` — only under `flags.experimentalLspTool`.
- `plan_exit` — only under `flags.experimentalPlanMode && client==="cli"`.

`task` and `read` are also exposed via `named()` because other subsystems reference them directly.

### 3.2 Custom & plugin tools

Two sources, both normalized by `fromPlugin(id, def)` into a `Tool.Def`:

1. **Filesystem tools**: `Glob.scanSync("{tool,tools}/*.{js,ts}", ...)` over config directories;
   each module is dynamically imported (`pathToFileURL` for Windows). Exported members passing
   `isPluginTool` (has `args`+`description`+`execute`) are registered as `namespace` (default export)
   or `namespace_export`.
2. **Plugin tools**: from `plugin.list()` → each plugin's `tool` record.

Plugin tools still use **Zod** publicly. `fromPlugin` boxes that at the boundary: if every arg entry
is a Zod type it builds `z.object(args)` and converts to JSON Schema (`zodJsonSchema`, via
`z.toJSONSchema` with an input-mode metadata registry); otherwise it treats entries as raw JSON
Schema (`legacyJsonSchema`). The runtime `parameters` for a plugin tool is a permissive
`Schema.declare`/`Schema.Unknown`, and the LLM is given the original JSON Schema. The plugin's
Promise-based `execute` is bridged to Effect (`EffectBridge`), and its `ask` (Effect) is wrapped as a
Promise for the plugin. Output is truncated like native tools.

### 3.3 Presenting the tool set to the model — `tools(input)`

`registry.tools(...)` produces the final `Tool.Def[]` for one request:

- **Model-conditional filtering:**
  - `websearch` only if `webSearchEnabled(providerID, {exa,parallel})` (opencode providers, or Exa/Parallel flags).
  - **GPT models use `apply_patch`; everyone else uses `edit`+`write`.** `usePatch = modelID.includes("gpt-") && !oss && !gpt-4`; when true, `apply_patch` is kept and `edit`/`write` dropped, and vice-versa.
- **Code-mode:** if `execute` is present, its description is generated from the visible MCP tool
  catalog (`describeCodeMode`); if no MCP tools are visible, `execute` is dropped.
- **Description augmentation via plugin hook** `tool.definition` (plugins may rewrite
  description/parameters/jsonSchema). The `task` tool's description is appended with the list of
  available subagent types (`describeTask` — lists non-primary agents not `deny`d for `task`).

The registry returns `Tool.Def`s; the JSON-Schema conversion and AI-SDK wrapping happen later in
`session/tools.ts`.

---

## 4. Execution flow

### 4.1 Bridging into the AI SDK — `session/tools.ts`

`SessionTools.resolve(input)` builds `Record<string, AITool>` for the model call. For each
`Tool.Def` from `registry.tools(...)`:

```ts
const schema = ProviderTransform.schema(model, ToolJsonSchema.fromTool(item))  // JSON Schema, provider-tweaked
tools[item.id] = tool({
  description: item.description,
  inputSchema: jsonSchema(schema),
  execute(args, options) {
    return run.promise(Effect.gen(function* () {
      const ctx = context(args, options)
      yield* plugin.trigger("tool.execute.before", {...}, { args })
      const result = yield* item.execute(args, ctx)          // <- Effect tool, incl. arg-decode + truncate
      const output = { ...result, attachments: result.attachments?.map(add ids/session/message) }
      yield* plugin.trigger("tool.execute.after", {...}, output)
      if (options.abortSignal?.aborted) yield* processor.completeToolCall(options.toolCallId, output)
      return output
    }))
  },
})
```

`run` is an `EffectBridge` that runs Effects as Promises for the AI SDK. `context(args, options)`
constructs the `Tool.Context` (see §2.1): it wires `metadata` to `processor.updateToolCall(...)` and
`ask` to `permission.ask({..., ruleset: merge(agent.permission, session.permission)})` (`.orDie`).

`ToolJsonSchema.fromTool(item)` returns `item.jsonSchema` if the tool precomputed one, otherwise
converts `item.parameters` via `fromSchema`.

### 4.2 Args validation & result shape

- **Validation** happens inside the tool (the `wrap` decode step, §2.4), *not* in the AI SDK layer,
  producing `InvalidArgumentsError` whose message is returned to the model as the tool result — the
  intended "rewrite your input" loop. There is also a dedicated `invalid` tool (`tool/invalid.ts`,
  id `"invalid"`, description "Do not use") that simply echoes an error, used when the model calls a
  non-existent tool.
- **Result** is `{ title, metadata, output, attachments? }`; `output` is the model-visible text.

### 4.3 Error handling

Tools use `Effect.orDie` liberally, meaning tool failures become defects that the AI-SDK/session
layer surfaces as an errored tool part. Domain "expected" errors (validation, permission) are typed
`Schema.TaggedErrorClass` whose `.message` getter is the model-facing text:
- `ToolInvalidArgumentsError` (rewrite input)
- `PermissionDeniedError` — "The user has specified a rule which prevents you… relevant rules {json}"
- `PermissionRejectedError` — "The user rejected permission to use this specific tool call."
- `PermissionCorrectedError` (carries `feedback`) — reject with user feedback text.

Many tools also `throw new Error(...)` inside `Effect.gen` for input problems (e.g. `read` "File not
found" with did-you-mean suggestions; `edit` "Could not find oldString…").

### 4.4 Output truncation — `tool/truncate.ts`

`Truncate.Service.output(text, opts?, agent?)` returns `{content, truncated:false}` or
`{content, truncated:true, outputPath}`. Defaults `MAX_LINES=2000`, `MAX_BYTES=50 KB`
(overridable by config `tool_output.max_lines/max_bytes`). When exceeded it writes the full text to a
temp file (`tool/truncation-dir.ts`, filenames `tool_...`, 7-day retention with hourly cleanup) and
returns a head/tail preview plus a hint. The hint differs by whether the agent has the `task` tool
(delegate to an explore agent vs. use Grep/Read with offset/limit). Bash and MCP paths call this too.

### 4.5 JSON-Schema conversion — `tool/json-schema.ts`

`fromSchema(schema)` = `Schema.toJsonSchemaDocument(schema, { additionalProperties:true })` +
`normalize(...)` + inline local `$defs`/`definitions` + drop unresolved defs. Cached in a `WeakMap`.
`normalize` performs LLM-friendliness fixes: strips nullable `anyOf` branches on optional fields,
collapses single-member unions, flattens `allOf`, drops `additionalProperties:true`, and clamps
unbounded `integer` to safe min/max. This matters because the model sees this JSON Schema.

---

## 5. Built-in tool inventory

| id (tool name) | Purpose | Key params | Permission key(s) asked |
|---|---|---|---|
| `read` | Read a file (with line numbers) or list a directory; renders images/PDFs as attachments | `filePath`, `offset?`, `limit?` | `read` + `external_directory` |
| `write` | Overwrite/create a file; formats + reports LSP diagnostics | `content`, `filePath` | `edit` + `external_directory` |
| `edit` | Exact string replacement with fuzzy fallback matchers | `filePath`, `oldString`, `newString`, `replaceAll?` | `edit` + `external_directory` |
| `apply_patch` | GPT-model file editor using an envelope diff (add/update/delete/move) | `patchText` | `edit` + `external_directory` |
| `bash` (shell) | Execute a shell command (bash/pwsh/powershell/cmd), streamed, truncated | `command`, `timeout?` (ms), `workdir?` | `bash` + `external_directory` |
| `grep` | ripgrep content search (regex), grouped by file, capped at 100 | `pattern`, `path?`, `include?` | `grep` + `external_directory` |
| `glob` | ripgrep filename glob, capped at 100 | `pattern`, `path?` | `glob` + `external_directory` |
| `task` | Spawn a subagent session (foreground or background) | `description`, `prompt`, `subagent_type`, `task_id?`, `command?`, `background?` | `task` (pattern = subagent_type) |
| `todowrite` | Replace the session todo list | `todos: {content,status,priority}[]` | `todowrite` |
| `webfetch` | Fetch a URL → text/markdown/html (or image attachment) | `url`, `format` (default markdown), `timeout?` (s) | `webfetch` |
| `websearch` | Web search via Exa or Parallel provider | `query`, `numResults?`, `livecrawl?`, `type?`, `contextMaxCharacters?` | `websearch` |
| `skill` | Load a named skill's content + file list into context | `name` | `skill` (pattern = skill name) |
| `question` | Ask the user structured multiple-choice questions | `questions: {question,header,options,multiple?,custom?}[]` | (uses Question service, gated by `question` config) |
| `lsp` (experimental) | LSP code intelligence (definition, references, hover, symbols, call hierarchy) | `operation`, `filePath`, `line`, `character`, `query?` | `lsp` + `external_directory` |
| `plan_exit` (experimental) | Prompt user to switch from plan agent to build agent | `{}` (none) | via Question service |
| `execute` (code-mode, experimental) | Run a confined orchestration script over MCP tools | `code` | per-MCP-tool |
| `invalid` | Placeholder returned when the model calls an unknown tool | `tool`, `error` | none |
| `list_mcp_resources` / `list_mcp_resource_templates` / `read_mcp_resource` | MCP resource access (added in `session/tools.ts` when an MCP server exposes resources) | `server?` / `server,uri` | `read` (pattern `mcp:server:uri`) |

### Per-tool notes

**`read`** (`tool/read.ts`) — Resolves relative paths against `instance.directory`. Asks `read`
permission with `always:["*"]`. Directory reads list entries (dirs suffixed `/`, symlinks resolved),
paginated by offset/limit. Files: sniffs MIME on a 4 KB sample; images
(`jpeg/png/gif/webp`) and PDFs return as base64 `data:` **attachments**; binary files are rejected
(extension blocklist + >30% non-printable heuristic). Text is streamed line-by-line with per-line cap
`MAX_LINE_LENGTH=2000` and total cap `MAX_BYTES=50 KB`; output wraps content in `<path>/<type>/<content>`
tags with `N: line` numbering and a continuation hint (`Use offset=…`). Warms the LSP for the file.
Injects `<system-reminder>` blocks from `Instruction.resolve` (rules) when relevant.

**`write`** (`tool/write.ts`) — Preserves BOM. Diffs old vs new (`createTwoFilesPatch` + `trimDiff`),
asks `edit` permission with the diff in metadata, writes (creating dirs), runs formatter, then
reports LSP diagnostics for the file (and up to 5 other files). Output "Wrote file successfully." plus
any LSP errors.

**`edit`** (`tool/edit.ts`) — The most intricate tool. Per-file `Semaphore` lock. Empty `oldString`
is only allowed to create a new file (else error). Normalizes line endings, then tries a **cascade of
replacer generators** until one yields a unique match:
`SimpleReplacer → LineTrimmedReplacer → BlockAnchorReplacer → WhitespaceNormalizedReplacer →
IndentationFlexibleReplacer → EscapeNormalizedReplacer → TrimmedBoundaryReplacer →
ContextAwareReplacer → MultiOccurrenceReplacer`. Uses Levenshtein similarity (threshold 0.65) for
block-anchor fuzzy matching. Rejects "disproportionate" matches (matched span far larger than
`oldString`). Distinguishes "not found" vs "multiple matches" errors. Asks `edit` permission with the
diff; formats; reports LSP diagnostics.

**`apply_patch`** (`tool/apply_patch.ts`) — Alternative to edit/write for GPT models. Parses the
`*** Begin Patch … *** End Patch` envelope into hunks (add/update/delete, with optional `move_path`).
Validates every hunk (files exist for update/delete; derives new content), asks a **single** `edit`
permission covering all relative paths with a combined diff + per-file metadata, then applies,
formats, and reports diagnostics. Output is a `A/M/D path` summary.

**`bash`** (`tool/shell.ts` + `tool/shell/prompt.ts` + `tool/shell/id.ts`) — Public id is `"bash"`
(the code calls it "shell"; kept for back-compat). Chooses shell via config (`Shell.acceptable`) and
generates a shell-specific description (bash / pwsh / powershell 5.1 / cmd) with usage rules
("prefer Read/Grep/Glob/Edit over cat/grep/find/sed"; use `workdir` not `cd`). **Command safety
scanning:** parses the command with tree-sitter (bash or PowerShell WASM), walks each `command` node,
and for file-touching commands (`rm cp mv mkdir touch chmod chown cat …` + PowerShell/cmd variants)
resolves path args; any path **outside the project** triggers an `external_directory` ask. For every
non-`cd` command it asks the `bash` permission with `patterns:[full command source]` and
`always:[<command-prefix> *]` — the prefix computed by `BashArity.prefix(tokens)` (the arity table in
`permission/arity.ts`, e.g. `git`→2 tokens, `npm run`→3, flags never counted). Execution streams
combined stdout/stderr; keeps a rolling buffer (`maxBytes*2`), spills to a temp file when over the byte
cap, streams a preview into `ctx.metadata({output})`, enforces `timeout` (default 2 min) and abort
(kill with 3s force). Returns tail preview + `<shell_metadata>` for timeout/abort, metadata includes
`exit`, `truncated`, `outputPath`.

**`grep`** (`tool/grep.ts`) — `ripgrep.grep({cwd, pattern, include, limit:100})`. Asks `grep`
permission (pattern = the regex). Groups matches `path:` then `  Line N: text`, notes truncation.

**`glob`** (`tool/glob.ts`) — `ripgrep.glob({cwd, pattern, limit:100})`. Asks `glob` permission.
Returns absolute paths; errors if `path` is a file.

**`task`** (`tool/task.ts`) — Spawns a subagent. Enforces `subagent_depth` (default 1). Asks `task`
permission with `pattern=subagent_type` (unless `ctx.extra.bypassAgentCheck`). Derives the child
session's permission ruleset via `deriveSubagentSessionPermission` (parent deny +
external_directory rules, plus default `todowrite`/`task` denies unless the subagent explicitly
allows them). Uses `ctx.extra.promptOps` (`resolvePromptParts`/`prompt`/`cancel`) to run the child.
**Background mode** (`background:true`, gated by `experimentalBackgroundSubagents`) launches via
`BackgroundJob` and returns immediately, injecting the result later as a synthetic user message and
notifying the parent; `task_id` resumes an existing subagent session. Output uses
`<task id=… state=running|completed|error>` XML. Note: when background subagents are disabled, it
publishes a **static** `jsonSchema` from `BaseParameters` (omitting the `background` field) so the
model never sees it.

**`todowrite`** (`tool/todo.ts`) — Replaces the todo list via `Todo.Service.update`. Todo shape
(`packages/schema/src/session-todo.ts`): `{content, status, priority}` (all strings; status ∈
pending/in_progress/completed/cancelled, priority ∈ high/medium/low). Output is the JSON of the todos.

**`webfetch`** (`tool/webfetch.ts`) — Requires http(s). Asks `webfetch`. 5 MB cap, 30 s default / 120 s
max timeout. Sets format-specific `Accept` headers + a browser UA (retries with `opencode` UA on
Cloudflare 403 challenge). HTML→markdown via Turndown, or text extraction via htmlparser2. Images
returned as attachments.

**`websearch`** (`tool/websearch.ts` + `tool/mcp-websearch.ts`) — Provider chosen by
`selectWebSearchProvider` (env override `OPENCODE_WEBSEARCH_PROVIDER`, else flags, else deterministic
hash of sessionID → exa/parallel). Calls the provider's MCP-style endpoint. Asks `websearch`.

**`skill`** (`tool/skill.ts`) — Loads a skill by name (`Skill.require`); asks `skill` permission with
`pattern=name` and `always:[name]`. Emits the skill's markdown wrapped in `<skill_content>` plus a
sampled `<skill_files>` list (relative to the skill's base dir).

**`question`** (`tool/question.ts`) — Structured Q&A via `Question.Service.ask`. Question shape
(`packages/schema/src/v1/question.ts`): `{question, header (≤30 chars), options:[{label,description}],
multiple?, custom?(default true)}`. Answers returned as arrays of selected labels.

**`plan_exit`** (`tool/plan.ts`) — In plan mode, asks the user (via `question`) to approve switching to
the build agent; on Yes it appends a synthetic user message re-agenting to `build`.

**`lsp`** (`tool/lsp.ts`) — Operation enum over the LSP service; 1-based line/character converted to
0-based; asks `lsp`. Returns JSON of results.

**`execute` / code-mode** (`tool/code-mode.ts`, experimental) — A single `code` param runs a confined
interpreter (`@opencode-ai/codemode`) that can call connected MCP tools programmatically; description
is generated from the visible MCP catalog.

**MCP tools** (`session/tools.ts`, `mcp/catalog.ts`) — Native MCP tools are converted from each
client, their input JSON Schema provider-transformed, and each asks permission keyed by the tool name
with `patterns:["*"]`. Results map MCP content parts (text/image/resource) into `output` + attachments,
truncated. When `experimentalCodeMode` is on, raw MCP tools are hidden (only `execute` remains).

---

## 6. Permission system

### 6.1 Data model — `packages/schema/src/v1/permission.ts`

```ts
Action  = "allow" | "deny" | "ask"
Rule    = { permission: string; pattern: string; action: Action }
Ruleset = Rule[]

Request = {
  id: PermissionID; sessionID; permission: string;
  patterns: string[]; metadata: Record<string,unknown>;
  always: string[]; tool?: { messageID; callID }
}
AskInput  = Request-without-id + { ruleset: Ruleset }   // what Permission.ask receives
Reply     = "once" | "always" | "reject"
ReplyInput= { requestID; reply; message? }
```

A tool's `ctx.ask({ permission, patterns, always, metadata })` becomes an `AskInput` in
`session/tools.ts` (id/sessionID/tool/ruleset filled in). `permission` is the capability key
(`read`, `edit`, `bash`, …); `patterns` are the concrete things being acted on (file path glob,
command source, URL, subagent name); `always` are the pattern(s) that get persisted if the user
picks "always".

### 6.2 Evaluation — `permission/index.ts` `evaluate(permission, pattern, ...rulesets)`

```ts
rulesets.flat().findLast(r =>
  Wildcard.match(permission, r.permission) && Wildcard.match(pattern, r.pattern))
  ?? { action: "ask", permission, pattern: "*" }     // default when nothing matches
```

**Last matching rule wins** (so later rulesets/user config override earlier defaults; within a config
object, key order is preserved — `propertyOrder: "original"`). Default action is `ask`. Matching is
glob-style via `Wildcard.match` on **both** the permission key and the pattern.

### 6.3 The ask loop — `Permission.Service`

`ask(input)`:
1. For each requested pattern, `evaluate(permission, pattern, ruleset, approved)`.
   - any `deny` → fail immediately with `PermissionDeniedError` (returns relevant rules to the model).
   - `allow` → continue.
   - otherwise mark `needsAsk`.
2. If nothing needs asking → return (proceed).
3. Otherwise create a `Deferred`, register a pending `Request`, publish `permission.asked` event, and
   **block** on the deferred (with a finalizer that removes it).

`reply({requestID, reply, message?})`:
- `reject` → fail the deferred with `PermissionRejectedError` (or `PermissionCorrectedError` if
  `message` given) and **reject all other pending requests in the same session**.
- `once` → succeed the deferred only.
- `always` → succeed, and push `{permission, pattern, action:"allow"}` into the in-memory `approved`
  list for each `always` pattern, then auto-resolve any other pending requests now satisfied.

`approved` is per-instance (session-scoped) in-memory state; on scope teardown all pending are rejected.

### 6.4 Config → ruleset — `Permission.fromConfig` and config schema

Config permission schema (`packages/core/src/v1/config/permission.ts`) accepts either a bare action
string (shorthand for `{"*": action}`) or an object keyed by permission with either an action or a
`{pattern: action}` map. Known keys: `read, edit, glob, grep, list, bash, task, external_directory,
todowrite, question, webfetch, websearch, lsp, doom_loop, skill` (plus arbitrary extra keys for MCP
tools). `fromConfig(info)` flattens this into `Rule[]`, expanding `~`/`$HOME` in patterns.

### 6.5 Agent default rulesets — `agent/agent.ts`

Each agent gets `permission = merge(defaults, agent-specific, user)` where `merge` is just
`rulesets.flat()` (order = precedence, later wins). Notable defaults (`build`/`plan`/`general`):

- global `"*": "allow"`, but `doom_loop: ask`, `question: deny`→(build/plan) allow,
  `plan_enter/plan_exit: deny`→enabled per agent.
- `external_directory: {"*": "ask", <whitelisted dirs>: "allow"}` — anything outside the project asks.
- `read: {"*": allow, "*.env": ask, "*.env.*": ask, "*.env.example": allow}` — dotenv files gated.
- `plan` agent: `edit: {"*": deny, ".opencode/plans/*.md": allow, …}` and `task: {general: deny}`.
- `general` subagent: `todowrite: deny`.

User config is merged **last**, so users can override any default.

### 6.6 Integration points / helpers

- `session/tools.ts:context().ask` supplies the ruleset as `merge(agent.permission, session.permission)`.
- `deriveSubagentSessionPermission` (`agent/subagent-permissions.ts`) computes a spawned subagent
  session's ruleset from parent (deny + external_directory) rules plus default task/todowrite denies.
- `Permission.disabled(tools, ruleset)` / `visibleTools(...)` — hide tools whose permission key is
  hard-`deny`d at pattern `"*"` (edit-family maps to `edit`, mcp-resource tools map to `read`). Used
  for code-mode catalog visibility.
- `truncate.ts` uses `evaluate("task","*",agent.permission)` to decide the truncation hint wording.

---

## 7. Notes for a Python + OpenRouter reimplementation

**Tool contract.** Model a tool as a dataclass/protocol: `id`, `description`, a params model, a
JSON-Schema (precomputed), and `execute(args, ctx) -> ToolResult`. Use **Pydantic** for the params
model — `Model.model_json_schema()` is the analog of opencode's Effect-Schema→JSON-Schema step.
Return a structured `ToolResult(title, output, metadata, attachments=None)`; only `output` (+ any
image/file attachments) goes to the model, `metadata` is for your UI/state.

**Context object.** Replicate `Context`: `session_id`, `message_id`, `agent`, an
`asyncio` cancellation token (analog of `AbortSignal`), `call_id`, an `extra` bag (model, flags,
prompt-ops), the running message list, an async `metadata(...)` callback for live UI updates, and an
async `ask(...)` permission call. Keeping `ask` on the context (not a global) is what lets each tool
declare its own permission semantics inline.

**Registration / discovery.** A simple registry mapping id→tool is enough. Keep opencode's
**per-request filtering** idea: given the model id + agent, decide which tools to expose (e.g.
apply_patch vs edit/write by model family; drop tools whose permission is hard-denied; append dynamic
description text like the subagent list). Don't hand the raw registry to the model — build the tool
list fresh per turn.

**Presenting to OpenRouter.** OpenRouter uses the OpenAI `tools` array:
`{"type":"function","function":{"name","description","parameters":<json schema>}}`. Feed your
Pydantic JSON Schema as `parameters`. Apply opencode's normalization lessons: strip nullable
`anyOf`/`allOf` cruft, avoid unbounded integers, set `additionalProperties:false` on objects — models
handle clean, flat schemas far better. Cache the schema per tool.

**Validation loop.** Mirror `InvalidArgumentsError`: validate the model's tool args against the
Pydantic model; on failure, return a tool result whose text says *"invalid arguments: {detail}; rewrite
the input to match the schema"* rather than crashing the loop. This is a first-class recovery path.

**Dispatch & errors.** Wrap every execute in: (1) arg validation, (2) permission ask, (3) run,
(4) output truncation, (5) tracing. Convert expected failures (validation/permission/not-found) into
model-facing text results; only truly unexpected errors should abort the turn. opencode returns
permission-denied/rejected as descriptive text so the model can adapt.

**Truncation.** Implement the line+byte cap (2000 lines / 50 KB) with spill-to-tempfile and a hint
telling the model to Grep/Read the saved file (or delegate to a subagent). Do this centrally so bash,
read, MCP, and custom tools all inherit it. This is essential for context hygiene.

**Permissions.** Port the model directly: `Rule = (permission, pattern, action∈{allow,ask,deny})`,
a `Ruleset` list, `evaluate = last wildcard-matching rule wins, default ask`. Build rulesets by
merging (in precedence order): built-in agent defaults → agent-specific → user config → session/subagent
rules. The `ask` primitive should block on an async future resolved by your UI, support
`once/always/reject(+feedback)`, persist `always` patterns in-session, and reject sibling pending
requests on a reject. Keep the capability keys coarse (`read/edit/bash/webfetch/…`) and let `patterns`
carry the specifics (file glob, command source, URL, subagent name). For bash, replicate the
tree-sitter command scan + the **arity prefix table** so "always allow" persists a sensible prefix
(e.g. `git *`) rather than the exact command, and so out-of-project file paths trigger an
`external_directory` ask.

**Gotchas.**
- opencode's tool id `"bash"` is the shell tool; the permission key is also `"bash"`. Keep names stable
  — users and configs pin them.
- Relative paths resolve against the *instance directory*; titles/patterns are computed relative to the
  *worktree*. Decide your project-root semantics up front.
- The GPT-family branch swaps edit/write for apply_patch. If you only support one editing tool, pick
  based on your model's demonstrated diff-following ability; the fuzzy multi-replacer cascade in
  `edit.ts` is worth porting because exact-match edits fail often.
- Image/PDF/binary handling in `read` and attachment plumbing (base64 `data:` URLs) is non-trivial;
  OpenRouter multimodal support varies by model — gate attachments on model capability.
- Subagent depth limits, background jobs, and the todo/question/plan/skill/lsp tools are opencode-UX
  specific; treat them as optional. The load-bearing core for a general harness is
  read/write/edit(or patch)/bash/grep/glob/webfetch(+task if you want subagents), all behind the
  permission gate.
