# opencode — Provider / Model Integration Layer

Reference for reimplementing opencode's provider/model subsystem in Python, with an
emphasis on OpenRouter and OpenAI-compatible wiring.

Source read for this document (all paths under `/tmp/opencode-src`):

- `packages/opencode/src/provider/provider.ts` — provider/model catalog, resolution, SDK loading (the core, ~2070 lines)
- `packages/opencode/src/provider/transform.ts` — message/param/schema/variant transforms (~1900 lines)
- `packages/opencode/src/provider/auth.ts` — provider auth methods (OAuth/API prompts, plugin-driven)
- `packages/opencode/src/provider/error.ts`, `model-status.ts`
- `packages/opencode/src/auth/index.ts` — credential store (`auth.json`)
- `packages/opencode/src/session/llm.ts` — the session LLM service (orchestration + runtime selection)
- `packages/opencode/src/session/llm/{ai-sdk.ts,request.ts,native-request.ts,native-runtime.ts,AGENTS.md}`
- `packages/opencode/src/session/tools.ts` — builds the tool map (incl. MCP tools)
- `packages/opencode/src/mcp/{index.ts,catalog.ts,auth.ts,oauth-provider.ts,oauth-callback.ts}`
- `packages/core/src/models-dev.ts` — models.dev catalog fetch/cache
- `packages/core/src/v1/config/{provider.ts,mcp.ts}` — user config schemas
- `packages/llm/src/**` — the newer native LLM SDK (`@opencode-ai/llm`): `providers/`, `route/`, `protocols/`, `schema/`

> **Two execution paths exist.** The default path is the **Vercel AI SDK** (`ai` npm package + `@ai-sdk/*`
> provider packages). A newer, opt-in **native runtime** (`@opencode-ai/llm`, the `packages/llm` workspace)
> reimplements provider transport directly and is gated behind `OPENCODE_EXPERIMENTAL_NATIVE_LLM=true`.
> For a Python reimplementation the AI SDK path is the mature reference; the native package is the cleaner
> data-model reference (explicit `LLMRequest`/`LLMEvent` schemas). Both converge on the same normalized
> `LLMEvent` stream.

Note: the codebase uses [Effect](https://effect.website) (`Effect.Effect<A, E>`, `Layer`, `Context.Service`,
`Stream`) pervasively. Read `Effect.fn("name")(function* (...) {...})` as an async function and `yield*` as
`await`. Services are dependency-injected singletons. None of this is required to reimplement in Python — it
is structure, not behavior.

---

## 1. Overview

The layer answers four questions and executes one request:

1. **What models exist?** — a catalog seeded from **models.dev** (`https://models.opencode.ai/api.json`),
   extended by user config and plugins. See §3.
2. **How is a model resolved to an SDK client?** — `Provider.getLanguage(model)` maps `model.api.npm`
   (e.g. `@ai-sdk/openai`, `@openrouter/ai-sdk-provider`, `@ai-sdk/openai-compatible`) to a lazily-imported
   AI SDK provider factory, configured with baseURL/apiKey/headers. See §2–3.
2. **How is a request built?** — `LLMRequestPrep.prepare` assembles system prompt, messages, tools, params
   (temperature/topP/topK/maxOutputTokens), provider-specific options, and headers. `ProviderTransform`
   applies per-model message rewrites and options mapping. See §4.
3. **How is the response consumed?** — `streamText(...)` from the AI SDK produces a `fullStream`; `LLMAISDK`
   normalizes each part into a provider-neutral `LLMEvent`. See §5.

The public service (`packages/opencode/src/session/llm.ts`) exposes a single method:

```ts
interface Interface {
  readonly stream: (input: StreamInput) => Stream.Stream<LLMEvent, unknown>
}
```

`StreamInput` (session/llm.ts:35) is the entire request surface:

```ts
type StreamInput = {
  user: SessionV1.User
  sessionID: string
  parentSessionID?: string
  model: Provider.Model         // fully-resolved model record (see §3)
  agent: Agent.Info             // prompt, temperature/topP overrides, options, permissions
  permission?: PermissionV1.Ruleset
  system: string[]              // extra system prompt fragments
  messages: ModelMessage[]      // AI SDK message shape
  small?: boolean               // "small model" path (title/summary generation)
  tools: Record<string, Tool>   // AI SDK Tool objects, already wired to executors
  retries?: number
  toolChoice?: "auto" | "required" | "none"
}
```

---

## 2. Provider abstraction & interface

There is no OO "Provider" interface each provider implements. Instead the abstraction is **data + a
registry of loaders**. Providers are plain records; per-provider behavior lives in a `custom(dep)` map of
loader functions plus a `BUNDLED_PROVIDERS` map of dynamic-import factories.

### 2.1 The `Provider.Service` interface (`provider/provider.ts:1191`)

```ts
interface Interface {
  list():        Effect<Record<ProviderID, Info>>
  getProvider(providerID): Effect<Info>
  getModel(providerID, modelID): Effect<Model, ModelNotFoundError>
  getLanguage(model: Model): Effect<LanguageModelV3, ModelNotFoundError>   // <- the SDK model instance
  closest(providerID, query[]): Effect<{providerID, modelID} | undefined>
  getSmallModel(providerID): Effect<Model | undefined>
  defaultModel(): Effect<{providerID, modelID}, DefaultModelError>
}
```

`getLanguage` is the key seam: it returns a `LanguageModelV3` (from `@ai-sdk/provider`) — the object
`streamText({ model })` consumes.

### 2.2 `Provider.Info` and `Provider.Model` (`provider/provider.ts:1074`, `:1091`)

```ts
Model = {
  id: string                    // opencode model id (may differ from api.id)
  providerID: string
  api: { id: string; url: string; npm: string }   // npm = which SDK package to load
  name: string
  family?: string
  capabilities: {
    temperature, reasoning, attachment, toolcall: boolean
    input:  { text, audio, image, video, pdf: boolean }
    output: { text, audio, image, video, pdf: boolean }
    interleaved: boolean | { field: "reasoning" | "reasoning_content" | "reasoning_text" | string }
  }
  cost: { input, output, cache:{read,write}, tiers?, experimentalOver200K? }
  limit: { context, input?, output }
  status: "alpha" | "beta" | "deprecated" | "active"
  options: Record<string, any>   // baked-in provider-body options (e.g. reasoningEffort defaults)
  headers: Record<string, string>
  release_date: string
  variants?: Record<string, Record<string, any>>   // reasoning "effort" presets (low/high/max/...)
}

Info = {
  id: string
  name: string
  source: "env" | "config" | "custom" | "api"
  env: string[]                 // env var names that hold the api key, e.g. ["OPENROUTER_API_KEY"]
  key?: string                  // resolved api key when unambiguous
  options: Record<string, any>  // provider-level SDK options (apiKey, baseURL, headers, fetch, ...)
  models: Record<string, Model>
}
```

### 2.3 The two loader tables

**`BUNDLED_PROVIDERS`** (`provider/provider.ts:113`) — maps `api.npm` to a lazy dynamic import returning the
provider factory. This is the entire set of "known" SDKs, e.g.:

```ts
"@ai-sdk/openai":            () => import("@ai-sdk/openai").then(m => m.createOpenAI),
"@ai-sdk/openai-compatible": () => import("@ai-sdk/openai-compatible").then(m => m.createOpenAICompatible),
"@openrouter/ai-sdk-provider":() => import("@openrouter/ai-sdk-provider").then(m => m.createOpenRouter),
"@ai-sdk/anthropic":         () => import("@ai-sdk/anthropic").then(m => m.createAnthropic),
"@ai-sdk/google":            ...,  "@ai-sdk/amazon-bedrock": ...,  "@ai-sdk/azure": ...,  etc.
```

Anything not bundled is `npm install`-ed on demand (`Npm.add(model.api.npm)`) and dynamically imported — the
factory is found by `Object.keys(mod).find(k => k.startsWith("create"))` (`provider.ts:1852`). So *any*
AI-SDK-compatible provider package works without code changes.

**`custom(dep)`** (`provider/provider.ts:174`) — a `Record<providerID, CustomLoader>`. Each loader returns:

```ts
{
  autoload: boolean                      // include this provider even with no explicit config?
  getModel?: (sdk, modelID, options?, model?) => Promise<LanguageModel>  // override model selection
  vars?:    (options) => Record<string,string>   // substitute ${VAR} in baseURL
  options?: Record<string, any>          // provider options patched in (headers, apiKey, baseURL...)
  discoverModels?: () => Promise<Record<string, Model>>   // dynamic model discovery (e.g. gitlab)
}
```

Examples relevant to OpenAI-compatible/OpenRouter:

- `openai` loader forces the **Responses API** (`getModel: sdk => sdk.responses(modelID)`) and sets a 300s
  `headerTimeout` (`provider.ts:208`).
- `openrouter` loader injects headers `{ "HTTP-Referer": "https://opencode.ai/", "X-Title": "opencode" }`
  (`provider.ts:474`). No `getModel` override → uses `sdk.languageModel(modelID)`.
- `github-copilot`, `azure`, `amazon-bedrock`, `google-vertex`, `cloudflare-*`, `snowflake-cortex`, etc. have
  richer loaders that resolve credentials, region prefixes, and endpoints.

`dep` (`CustomDep`, `provider.ts:153`) gives loaders access to `auth`, `config`, `env`, and `env.get(key)`.

---

## 3. Model/provider resolution & config (incl. models.dev catalog)

### 3.1 models.dev catalog (`packages/core/src/models-dev.ts`)

- Fetches `${OPENCODE_MODELS_URL || "https://models.opencode.ai"}/api.json` (models.dev's aggregated catalog),
  caches to `${cache}/models.json`, TTL 5 min, refreshed hourly in the background (`models-dev.ts:160,255`).
- Cross-process safe via a file lock (`Flock`). Falls back to a compiled-in `OPENCODE_MODELS_DEV` snapshot,
  and to `{}` if `OPENCODE_DISABLE_MODELS_FETCH` is set.
- Catalog shape (`models-dev.ts:67,123`): `Record<providerID, Provider>` where each `Provider` has
  `{ id, name, api?, npm?, env: string[], models: Record<id, Model> }` and each `Model` carries
  `cost`, `limit`, `modalities`, `reasoning`, `tool_call`, `temperature`, `reasoning_options`,
  `interleaved`, `status`, and an optional `provider: { npm?, api? }` override.

`reasoning_options` (`models-dev.ts:52`) drives variant generation and is one of:
`{ type:"effort", values:(string|null)[] }`, `{ type:"toggle" }`, `{ type:"budget_tokens", min?, max? }`.

### 3.2 Catalog → opencode records

`fromModelsDevProvider` / `fromModelsDevModel` (`provider.ts:1318`, `:1261`) convert catalog entries into
`Provider.Info` / `Provider.Model`. Key rules:

- `api.npm` fallback chain: `cloudflareGatewayNpm(...) ?? model.provider?.npm ?? provider.npm ?? "@ai-sdk/openai-compatible"`.
  **The default for any unknown provider is `@ai-sdk/openai-compatible`** — important for Python: treat
  unknown providers as OpenAI-compatible chat/completions.
- `api.url` = `model.provider?.api ?? provider.api ?? ""`.
- `variants` computed from `reasoningVariants(model)` (from `reasoning_options`) else `variants(model)`
  (hardcoded heuristics). See §4.4.
- `experimental.modes` produce extra synthetic models `${id}-${mode}` with merged cost/options/headers.

### 3.3 The build pipeline (`provider.ts:1385` — the `Layer.effect` state builder)

Runs once, producing `State { providers, catalog, models(cache), sdk(cache), modelLoaders, varsLoaders }`.
Order matters:

1. Fetch models.dev → `catalog` (raw) and `database` (public copy).
2. Run plugins' `provider.models(...)` hooks to mutate model lists.
3. **Config providers** (`cfg.provider`): merge/extend `database` with user-defined providers and models
   (deep-merged onto the catalog entry). This is how a user defines a custom OpenAI-compatible endpoint.
4. **Env keys**: for each provider, if any name in `provider.env` is set in the environment, mark
   `source:"env"` and set `key` (when a single env name).
5. **API keys**: load `auth.json`; for each `type:"api"` entry set `source:"api"`, `key`.
6. **Plugin auth loaders**: run to patch provider `options` (custom fetch, refreshed tokens…).
7. **`custom(dep)` loaders**: run for each; if `autoload` or the provider is already present, register its
   `getModel`/`vars`/`discoverModels` and patch `options`.
8. Re-apply config `source:"config"`, run gitlab discovery, then filter: drop disabled/`alpha`(unless flag)/
   `deprecated` models, apply per-provider `whitelist`/`blacklist`, drop providers with zero models.

`enabled_providers` / `disabled_providers` config gate the whole set (`provider.ts:1442`).

### 3.4 Resolving a model to an SDK client — `resolveSDK` + `getLanguage`

`getLanguage(model)` (`provider.ts:1892`):

```
key = `${providerID}/${model.id}`; return cached if present
sdk = resolveSDK(model, state, env)               // provider factory, cached by hash of {providerID,npm,options}
language = modelLoaders[providerID]
  ? await modelLoaders[providerID](sdk, model.api.id, {...provider.options, ...model.options}, model)
  : sdk.languageModel(model.api.id)
cache & return language
```

`resolveSDK` (`provider.ts:1730`) assembles the provider `options`:

- baseURL: `options.baseURL || model.api.url`, then `${VAR}` substitution via `varsLoaders` and env.
- apiKey: `options.apiKey ?? provider.key`.
- headers: merge `options.headers` + `model.headers`.
- For `@ai-sdk/openai-compatible`: force `includeUsage: true` unless explicitly disabled (`:1751`).
- Wraps `fetch` with **header timeout** (300s default), **chunk timeout** (300s — aborts if no SSE chunk
  arrives; `wrapSSE`, `provider.ts:37`), and combined AbortSignals. This is a notable robustness detail.
- Looks up `BUNDLED_PROVIDERS[npm]`; if absent, `Npm.add(npm)` then dynamic import (see §2.3).

### 3.5 Model selection helpers

- `parseModel("provider/model")` → `{providerID, modelID}` (`provider.ts:2054`).
- `defaultModel()` — config `model`, else recent-model file `${state}/model.json`, else highest-priority model
  (`sort()` uses `priority = ["gpt-5","claude-sonnet-4","big-pickle","gemini-3-pro"]`).
- `getSmallModel()` — config `small_model`, else plugin hook, else family priority
  `["gemini-flash","gpt-nano","claude-haiku"]`.
- `getModel` returns `ModelNotFoundError` with fuzzy-match suggestions (`fuzzysort`).

### 3.6 User config for providers (`packages/core/src/v1/config/provider.ts`)

`cfg.provider[<id>]` shape (fields all optional):

```
{ api?, name?, env?[], id?, npm?, whitelist?[], blacklist?[],
  options?: { apiKey?, baseURL?, enterpriseUrl?, setCacheKey?, timeout?, headerTimeout?, chunkTimeout?, ...any },
  models?: Record<id, { id?, name?, npm? (via provider), api?, reasoning?, tool_call?, cost?, limit?,
                        modalities?, options?, headers?, variants?, status?, ... }> }
```

**To add a custom OpenAI-compatible provider**, a user sets `provider.<id>.options.baseURL`,
`options.apiKey` (or `env`), and defines `models`. With no `npm`, models default to
`@ai-sdk/openai-compatible`.

---

## 4. Request construction (messages / tools / params mapping)

Entry point: `LLMRequestPrep.prepare(input)` (`session/llm/request.ts:56`). Output `Prepared`
(`request.ts:38`):

```ts
Prepared = {
  system: string[]
  messages: ModelMessage[]
  tools: Record<string, Tool>
  params: { temperature?, topP?, topK?, maxOutputTokens?, options: Record<string,any> }
  messageTransformOptions: Record<string, any>
  headers: Record<string, string>
}
```

### 4.1 System prompt

`system[0]` = join of: agent prompt (or `SystemPrompt.provider(model)` default) + `input.system` +
`user.system`. A plugin hook `experimental.chat.system.transform` may mutate it. For **OpenAI OAuth** the
system prompt goes into `options.instructions` instead of a system message (`request.ts:99`). Otherwise
system strings are prepended as `role:"system"` messages (`request.ts:101`).

### 4.2 Params & options

- `options` base = `ProviderTransform.smallOptions(model)` (small path) or `ProviderTransform.options({model,
  sessionID, providerOptions})`, then deep-merged with `model.options`, `agent.options`, and the selected
  `variant`. Plugin hook `chat.params` can override.
- `temperature` = `agent.temperature ?? ProviderTransform.temperature(model)` (only if
  `capabilities.temperature`).
- `topP` / `topK` = `ProviderTransform.topP/topK(model)`.
- `maxOutputTokens` = `min(model.limit.output, OUTPUT_TOKEN_MAX=32000)`.
- `headers`: telemetry headers (`x-session-affinity`, `X-Session-Id`, `User-Agent: opencode/<version>`;
  opencode-managed providers get `x-opencode-*`), then `model.headers`, then plugin `chat.headers`.

`ProviderTransform.options` (`transform.ts:1207`) encodes a large body of provider-specific defaults, e.g.:
`store:false` for OpenAI/Responses-family; `{usage:{include:true}}` for OpenRouter/llmgateway;
`promptCacheKey = sessionID` for OpenAI/xai/mistral/etc; Google `thinkingConfig`; GPT-5 `reasoningEffort:medium`
+ `reasoningSummary:auto` + `include:["reasoning.encrypted_content"]` (`transform.ts:1340`).

### 4.3 Message transforms — `ProviderTransform.message(msgs, model, options)` (`transform.ts:465`)

Applied via a **middleware on the language model** (`session/llm.ts:325`, `transformParams`) so it runs on the
final AI-SDK prompt just before send. Steps:

1. `unsupportedParts` — replace file/image parts the model can't accept with an error text part.
2. `normalizeMessages` — surrogate sanitization; Anthropic/Bedrock drop empty content; scrub tool-call IDs for
   Claude/Mistral; DeepSeek requires reasoning parts on assistant messages; **interleaved-reasoning models**
   move `reasoning` text into `providerOptions.openaiCompatible[field]` (field from
   `capabilities.interleaved.field`, e.g. `reasoning_content`) — **but not for OpenRouter** (`transform.ts:321`).
3. `applyCaching` — inject `cacheControl`/`cachePoint` markers on first-2 system + last-2 messages for
   Anthropic/OpenRouter/Bedrock/openai-compatible/copilot/alibaba (`transform.ts:358`). Placement is
   message-level for Anthropic/Bedrock, content-part-level otherwise.
4. **Key remap**: rewrite `providerOptions[providerID]` → `providerOptions[sdkKey(npm)]`
   (`sdkKey` map at `transform.ts:42`, e.g. `@openrouter/ai-sdk-provider → "openrouter"`,
   `@ai-sdk/openai-compatible → "openaiCompatible"`, `@ai-sdk/openai → "openai"`).
5. Strip Responses `itemId` when `store !== true` (stateless replay).

### 4.4 Reasoning variants — `ProviderTransform.variants(model)` (`transform.ts:777`)

Produces `Record<effortName, providerBodyFragment>` keyed by the **SDK's expected option shape**. This is the
single densest piece of provider-specific knowledge. Highlights:

- **OpenRouter** (`@openrouter/ai-sdk-provider`): `{ [effort]: { reasoning: { effort } } }`; for OpenAI-family
  ids it uses the OpenAI effort ladder, otherwise `low/medium/high` (`transform.ts:858`).
- **OpenAI-compatible / xai / groq / cerebras / etc.**: `{ [effort]: { reasoningEffort: effort } }`.
- **OpenAI Responses**: `{ reasoningEffort, reasoningSummary:"auto", include:["reasoning.encrypted_content"] }`.
- **Anthropic**: `thinking:{type:"adaptive"|"enabled", budgetTokens|effort}`; Bedrock uses `reasoningConfig`;
  Google uses `thinkingConfig:{thinkingBudget|thinkingLevel}`.

`reasoningVariants(catalogModel, target)` (`transform.ts:1704`) is the newer, catalog-driven path built from
`reasoning_options`; `reasoningEffort/reasoningBudget/reasoningToggle` (`:1769`,`:1860`,`:1755`) emit the
per-npm body fragment.

### 4.5 Tools passed to the model

`resolveTools` (`request.ts:208`) filters the tool map by agent/session permissions and per-user tool toggles.
Special cases in `prepare`:

- OpenAI/Azure/Bedrock-mantle: every function tool gets `strict:false` (so MCP/dynamic schemas that don't meet
  OpenAI structured-output constraints still register) (`request.ts:152`).
- github-copilot with zero tools but prior tool calls: inject a `_noop` tool for API compatibility
  (`request.ts:159`).
- Tools are sorted by name for deterministic ordering.

The actual `streamText` call (`session/llm.ts:280`) passes `tools`, `activeTools`, `toolChoice`,
`temperature/topP/topK`, `maxOutputTokens`, `providerOptions:
ProviderTransform.providerOptions(model, options)`, `headers`, `maxRetries`, `abortSignal`, and
`experimental_repairToolCall` (lowercases mis-cased tool names, else routes to an `invalid` tool).

`ProviderTransform.providerOptions(model, options)` (`transform.ts:1408`) wraps the flat options object under
the SDK key: `{ [sdkKey]: normalized }` (Azure emits both `openai` and `azure`; Gateway splits `gateway` vs the
upstream slug). This is the object the AI SDK reads as `providerOptions`.

---

## 5. Streaming response consumption & normalization

### 5.1 AI SDK path

`session/llm.ts` calls `streamText(...)` (returns immediately with a `fullStream` async iterable) and adapts:

```ts
Stream.fromAsyncIterable(result.result.fullStream, ...)          // session/llm.ts:373
  .pipe(Stream.mapEffect(e => LLMAISDK.toLLMEvents(state, e)),
        Stream.flatMap(events => Stream.fromIterable(events)))
```

`LLMAISDK.toLLMEvents(state, event)` (`session/llm/ai-sdk.ts:77`) is a switch over the AI SDK
`fullStream` part types → zero-or-more `LLMEvent`s. Mapping table:

| AI SDK part                | LLMEvent(s)                              |
|----------------------------|------------------------------------------|
| `start`                    | (none)                                   |
| `start-step`               | `stepStart{index}`                       |
| `finish-step`              | `stepFinish{index,reason,usage,providerMetadata}` (fails on `rawFinishReason==="network_error"`) |
| `finish`                   | `finish{reason,usage,providerMetadata}` + resets adapter state |
| `text-start/delta/end`     | `textStart/textDelta/textEnd{id,...}`    |
| `reasoning-start/delta/end`| `reasoningStart/reasoningDelta/reasoningEnd` |
| `tool-input-start/delta/end`| `toolInputStart/Delta/End{id,name,text}`|
| `tool-call`                | `toolCall{id,name,input,providerExecuted}` |
| `tool-result`              | `toolResult{id,name,result,...}`         |
| `tool-error`               | `toolError{id,name,message,error}`       |
| `error`                    | fail the stream                          |
| `abort`/`source`/`file`/approval | (none)                             |
| `raw`                      | extracts Copilot billing (`copilotTotalNanoAiu`) |

`adapterState()` (`ai-sdk.ts:10`) tracks step index, synthesizes text/reasoning IDs when the provider omits
them, and maps tool-call IDs → names. `usage()` (`ai-sdk.ts:45`) normalizes token counts including
`cacheReadInputTokens`/`cacheWriteInputTokens`/`reasoningTokens`.

**Tool execution is owned by the AI SDK in this path** — `streamText` calls the `Tool.execute` functions
directly and emits `tool-result` parts; opencode just observes them.

### 5.2 Native path (`@opencode-ai/llm`) — the cleaner data model

The `LLMEvent` union is defined explicitly (`packages/llm/src/schema/events.ts:209`): `step-start`,
`text-start/delta/end`, `reasoning-start/delta/end`, `tool-input-start/delta/end`, `tool-call`,
`tool-result`, `tool-error`, `step-finish`, `finish`, `provider-error`. Each carries an optional
`providerMetadata` (raw provider payload keyed by provider name).

`Usage` (`events.ts:51`) is well-documented: **inclusive totals** (`inputTokens` includes cache reads/writes,
`outputTokens` includes reasoning) plus a **non-overlapping breakdown** (`nonCachedInputTokens`,
`cacheReadInputTokens`, `cacheWriteInputTokens`, `reasoningTokens`) with the invariant
`nonCached + cacheRead + cacheWrite = inputTokens`. This is a good target shape for a Python usage model.

`LLMResponse` (`events.ts:561`) is a **fold** over the event stream (`LLMResponse.reduce`) that assembles a
final `Message` with `content: ContentPart[]`, `usage`, `finishReason`. Reimplement this reducer in Python to
get both streaming and a final assembled message from one code path.

Native transport: `LLMClient.stream(request)` (`packages/llm/src/route/client.ts:396`) →
`compile(request)` builds the provider-native JSON body via `route.body.from(request)`, validates it against
the route's schema, then `route.streamPrepared` decodes SSE frames into protocol events and folds them into
provider-neutral `LLMEvent`s. A `Route` (`client.ts:36`) composes four axes: **Protocol** (what API),
**Endpoint** (where), **Auth** (how), **Framing** (how to cut the SSE stream). `generate` is `stream` +
`runFold(LLMResponse.reduce)`.

In the native runtime, **tool execution stays opencode-owned**: `native-runtime.ts:103` intercepts non-
provider-executed `tool-call` events, dispatches to `ToolRuntime.dispatch`, and offers the resulting events
back into the stream via a queue.

---

## 6. Credentials / auth per provider (API key, OAuth)

### 6.1 The credential store (`packages/opencode/src/auth/index.ts`)

Stored at `${data}/auth.json`, mode `0600`, as `Record<providerID, Auth.Info>` where `Auth.Info` is a union:

```ts
Oauth    = { type:"oauth", refresh, access, expires, accountId?, enterpriseUrl? }
Api      = { type:"api",   key, metadata?: Record<string,string> }
WellKnown= { type:"wellknown", key, token }
```

Interface: `get/all/set/remove`. Env override: `OPENCODE_AUTH_CONTENT` (JSON) short-circuits file reads.
`OAUTH_DUMMY_KEY = "opencode-oauth-dummy-key"` is used where the SDK requires *some* apiKey but real auth is a
custom `fetch`.

### 6.2 Where auth becomes SDK options

Precedence when building provider `options` (see §3.4 and the `custom` loaders): explicit
`config.provider.<id>.options.apiKey` → env var(s) in `provider.env` → `auth.json` (`type:"api"` → `.key`;
`type:"oauth"` → `.access` or a custom refreshing `fetch`). For OAuth providers, loaders install an
`options.fetch` that injects/refreshes the bearer token (e.g. google-vertex at `provider.ts:538`; OpenAI OAuth
uses a plugin-provided `fetch`, checked in the native runtime at `native-runtime.ts:148`).

### 6.3 Provider auth *methods* (interactive) — `provider/auth.ts`

`ProviderAuth.Service` exposes `methods()`, `authorize(input)`, `callback(input)`. Auth methods are
contributed by **plugins** via a `Hooks["auth"]` hook keyed by provider id. A method is
`{ type:"oauth"|"api", label, prompts?: (TextPrompt|SelectPrompt)[] }`. The OAuth flow: `authorize` runs the
plugin's `authorize(inputs)` → returns `{url, method:"auto"|"code", instructions}`; `callback` runs the
plugin's `callback(code?)` and persists either an `api` key or an `oauth` `{access,refresh,expires}` record via
`Auth.set`. So **API-key vs OAuth is a per-provider plugin concern**, not hardcoded.

### 6.4 Native runtime credential gate (`native-runtime.ts:50`)

`status()` decides native support: only `openai`/`anthropic`/`opencode*` providers, only npm in
`{@ai-sdk/openai, @ai-sdk/openai-compatible, @ai-sdk/anthropic}`, OAuth only if there's a provider `fetch`
override, and an apiKey must resolve. Otherwise it returns `{type:"unsupported", reason}` and the caller falls
back to the AI SDK.

---

## 7. MCP integration as a tool source

MCP servers are **just another source of `Tool` objects** merged into the same `tools` map passed to the model.

### 7.1 MCP service (`packages/opencode/src/mcp/index.ts`)

Uses the official `@modelcontextprotocol/sdk`. Config (`packages/core/src/v1/config/mcp.ts`) is a union:

```
Local  = { type:"local",  command:string[], cwd?, environment?, enabled?, timeout? }
Remote = { type:"remote", url, headers?, enabled?, timeout?,
           oauth?: false | { clientId?, clientSecret?, scope?, callbackPort?, redirectUri? } }
```

- **Local**: `StdioClientTransport` spawns `command` with merged `process.env + environment`.
- **Remote**: tries `StreamableHTTPClientTransport` then `SSEClientTransport`, each with an optional
  `McpOAuthProvider` (unless `oauth:false`). On `UnauthorizedError` it sets status `needs_auth` (or
  `needs_client_registration`) and stops.
- Client capabilities advertised: `roots` only (sampling/elicitation/tasks commented out; `index.ts:39`).
- On connect it lists tools (`McpCatalog.defs`), caches `defs`/`instructions`, and subscribes to
  `notifications/tools/list_changed` to refresh (`watch`, `index.ts:442`).
- `Interface.tools()` (`index.ts:666`) returns `Record<toolKey, McpTool>` where `toolKey =
  McpCatalog.toolName(server, tool) = sanitize(server) + "_" + sanitize(name)` and `McpTool = { def, client,
  timeout }`.
- Also exposes MCP **prompts** and **resources**/**resource templates** (paginated).

### 7.2 MCP tool → AI SDK Tool bridge

Two conversions exist:

- `McpCatalog.convertTool(def, client, timeout)` (`mcp/catalog.ts:42`) wraps a `dynamicTool` whose `execute`
  calls `client.callTool(...)`, handles `isError`, and flattens `structuredContent`.
- `SessionTools.resolve` (`session/tools.ts:390`) is where MCP tools actually enter the model's tool map. For
  each `mcp.tools()` entry it: rebuilds the input schema through `ProviderTransform.schema(model, schema)`
  (Gemini/OpenAI/Moonshot schema sanitization), wraps `execute` to fire `tool.execute.before/after` plugin
  hooks and a permission `ask` (`permission: key, patterns: ["*"]`), then converts MCP result `content`
  (text/image/resource) into text + file attachments and truncates output. Result is stored at `tools[key]`.
- Additionally, if any connected server exposes resources, three synthetic tools are added:
  `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource` (`session/tools.ts:27,139`).

MCP OAuth token storage is separate from provider auth: `mcp/auth.ts` + `mcp/oauth-provider.ts` +
`mcp/oauth-callback.ts` (loopback callback on port 19876, path `/mcp/oauth/callback`, PKCE, state check for
CSRF at `index.ts:910`).

---

## 8. OpenRouter / OpenAI-compatible specifics

### 8.1 OpenRouter (AI SDK path — the default)

- **npm package**: `@openrouter/ai-sdk-provider` (factory `createOpenRouter`), bundled at `provider.ts:124`.
- **Catalog / env**: models.dev advertises provider `openrouter` with `env: ["OPENROUTER_API_KEY"]`. Set the
  env var (or `auth.json` api key, or `config.provider.openrouter.options.apiKey`) and models autoload.
- **Custom loader** (`provider.ts:474`): injects headers `HTTP-Referer: https://opencode.ai/` and
  `X-Title: opencode`. No `getModel` override → `sdk.languageModel(modelID)` (OpenRouter's own chat route).
- **Options** (`ProviderTransform.options`, `transform.ts:1236`): sets `usage:{include:true}`; for
  `gemini-3` ids adds `reasoning:{effort:"high"}`. `providerOptions` are nested under the `"openrouter"` key
  (via `sdkKey`). Cache markers use `openrouter:{cacheControl:{type:"ephemeral"}}` (`transform.ts:366`).
- **Reasoning variants**: `{ [effort]: { reasoning: { effort } } }` (`transform.ts:858`); budget form is
  `{ reasoning: { max_tokens } }` (`transform.ts:1862`). OpenRouter's `reasoning.effort` maps to the upstream
  model's native control server-side.
- **Interleaved reasoning is left alone for OpenRouter** (the `reasoning_content` inlining in
  `normalizeMessages` explicitly excludes `@openrouter/ai-sdk-provider`, `transform.ts:323`).

### 8.2 OpenRouter (native path — `@opencode-ai/llm`)

`packages/llm/src/providers/openrouter.ts`:
- Protocol `openrouter-chat` extends OpenAI **chat/completions** (`OpenAIChat`), adding OpenRouter body
  options: `usage:{include:true}`, `reasoning`, `prompt_cache_key` (`bodyOptions`, `openrouter.ts:56`).
- Endpoint: `POST {baseURL}/chat/completions`, `baseURL` from the `openrouter` profile; framing = SSE.
- Auth: `AuthOptions.bearer(input, "OPENROUTER_API_KEY")` — Bearer token, env fallback (`openrouter.ts:84`).
- Usage: `configure({baseURL?, apiKey?/auth?, providerOptions?}).model(modelID)`.

Note: the native `native-request.ts:177` maps `@openrouter/ai-sdk-provider` → `OpenRouter.configure(...).model(...)`,
but the native runtime gate (`native-runtime.ts:58`) currently only accepts openai/openai-compatible/anthropic
npm packages, so **OpenRouter still runs through the AI SDK today**.

### 8.3 OpenAI-compatible

- **npm package**: `@ai-sdk/openai-compatible` (factory `createOpenAICompatible`), bundled. It is also the
  **default fallback** for any provider without an explicit npm (§3.2).
- `resolveSDK` forces `includeUsage:true` for this package (`provider.ts:1751`).
- `providerOptions` key resolution (`transform.ts:1454`): for `@ai-sdk/openai-compatible` (and
  `@ai-sdk/openai`, `@ai-sdk/anthropic`) the key is `model.providerID.split(".")[0]` (dot-split), because those
  SDK packages derive their `providerOptions` name that way. Reasoning inlining uses the `openaiCompatible` key
  (`transform.ts:343`).
- Reasoning variants: `{ [effort]: { reasoningEffort: effort } }` (`transform.ts:981`), with `max` added for
  deepseek-v4 and special-cased ids.
- DeepSeek on openai-compatible defaults `interleaved: { field: "reasoning_content" }` (`provider.ts:1541`),
  routing reasoning into that body field.
- Native path (`packages/llm/src/providers/openai-compatible.ts`): `configure({provider, baseURL,
  apiKey?/auth?})` builds an `OpenAICompatibleChat` route; predefined profiles: `baseten, cerebras, deepinfra,
  deepseek, fireworks, groq, togetherai`. Auth is Bearer (`AuthOptions.bearer(input, [])`).

### 8.4 OpenAI (for contrast)

The `openai` provider forces the **Responses API** (`sdk.responses(modelID)`, `provider.ts:208`), sets
`store:false`, and `include:["reasoning.encrypted_content"]` for stateless reasoning replay. Native OpenAI
provider (`packages/llm/src/providers/openai.ts`) exposes `responses`/`responsesWebSocket`/`chat` factories;
`model` defaults to Responses. `OpenAIOptionsInput` (`openai-options.ts:7`) documents the shared wire fields:
`store, promptCacheKey, reasoningEffort, reasoningSummary, include, textVerbosity, serviceTier` — deliberately
identical between the AI SDK `providerOptions.openai` and the native SDK so no translation is needed.

---

## 9. Notes for a Python + OpenRouter reimplementation

### 9.1 Recommended libraries

- **HTTP/SSE**: `httpx` (async, HTTP/2) + a small SSE line parser, or `httpx-sse`. You do **not** need an AI
  SDK equivalent — talk to OpenRouter's `POST /api/v1/chat/completions` directly. OpenRouter is OpenAI
  chat/completions-shaped, so the `openai` Python SDK also works (`base_url="https://openrouter.ai/api/v1"`),
  but rolling your own request/stream gives you the normalized event model below with less magic.
- **Schemas / validation**: `pydantic` v2 — model the `LLMRequest`, `Message`/`ContentPart`, `ToolDefinition`,
  `LLMEvent` union, and `Usage` after `packages/llm/src/schema/*`. Use discriminated unions on a `type` field.
- **Catalog**: fetch `https://models.opencode.ai/api.json` (or models.dev directly), cache to disk with a TTL
  (5 min like opencode) and a hourly background refresh; ship a bundled snapshot for offline.
- **MCP** (optional): the official `mcp` Python package (`mcp.client`) mirrors the TS SDK
  (stdio/streamable-http/SSE transports).

### 9.2 The request shape to target (OpenRouter chat/completions)

Build a provider-neutral request, then lower to the OpenAI-chat body:

```jsonc
POST https://openrouter.ai/api/v1/chat/completions
Headers: {
  "Authorization": "Bearer $OPENROUTER_API_KEY",
  "HTTP-Referer": "<your-app-url>",   // OpenRouter attribution (opencode sends this)
  "X-Title": "<your-app-name>",
  "Content-Type": "application/json"
}
Body: {
  "model": "openai/gpt-5",            // provider/model form
  "messages": [ {role, content|parts}, ... ],   // system/user/assistant/tool
  "tools": [ { "type":"function", "function": { name, description, parameters(JSON schema) } } ],
  "tool_choice": "auto" | "required" | "none" | {type:"function",function:{name}},
  "temperature": <opt>, "top_p": <opt>, "max_tokens": <opt>,
  "stream": true,
  "usage": { "include": true },       // OpenRouter: return token usage in the final SSE chunk
  "reasoning": { "effort": "low|medium|high" }   // OpenRouter reasoning control (maps to upstream native)
  // cache: set "cache_control":{"type":"ephemeral"} on trailing system/user content parts for caching
}
```

Provider-neutral → body mapping mirrors opencode's `sdkKey`/`providerOptions` split: keep model-specific knobs
(`reasoning`, `usage`, `prompt_cache_key`) in an OpenRouter-namespaced options bag and merge them into the body
at send time (see `providers/openrouter.ts:bodyOptions`).

### 9.3 The stream shape to target

Consume the SSE `data:` lines; each is a chat.completions chunk (`choices[].delta`). Normalize into an event
enum modeled on `LLMEvent` (`schema/events.ts`):

`step-start | text-start | text-delta | text-end | reasoning-start | reasoning-delta | reasoning-end |
tool-input-start | tool-input-delta | tool-input-end | tool-call | tool-result | tool-error | step-finish |
finish | provider-error`.

Then implement one **reducer** (port `LLMResponse.reduce`, `events.ts:531`) that folds events into an assembled
assistant `Message` with `content: [text|reasoning|tool-call|tool-result]` plus `usage` and `finishReason`.
This gives you streaming and a final message from a single code path. For OpenRouter/OpenAI-chat: `delta.content`
→ text-delta; `delta.reasoning`/`delta.reasoning_content` → reasoning-delta; `delta.tool_calls[]` → tool-input
deltas assembled by index into a final tool-call; the final chunk's `usage` → step-finish/finish usage.

Model `Usage` exactly as opencode does (inclusive totals + non-overlapping breakdown, §5.2) — it avoids the
"subtract and underflow" bug class.

### 9.4 Tool execution loop

opencode's model layer only *emits* `tool-call` events (native path) or lets the SDK execute (AI SDK path).
In Python, run the loop yourself: on a `tool-call`, look up the tool, execute it (with abort/timeout and a
permission gate if you have one), append a `tool` message with the result, and re-request. Tool inputs stream
as partial JSON — accumulate `tool-input-delta` text and `json.loads` at `tool-call`. Sanitize tool JSON
schemas per target model if you support more than OpenRouter (see `ProviderTransform.schema`, `transform.ts:1562`
— Gemini integer-enum→string, OpenAI boolean-schema stripping, Moonshot `$ref` sibling removal).

### 9.5 Config & resolution to replicate

- Model id parse `"provider/model"` → `(provider, model)`.
- Precedence for apiKey: explicit config → env var(s) named by the catalog `env` list → stored credential.
- Default provider knobs: OpenRouter → `usage.include=true` + attribution headers; unknown providers →
  treat as OpenAI-compatible chat/completions; force-include usage for openai-compatible.
- Reasoning "variants": expose named efforts (`low/medium/high[/max/xhigh]`) that map to the provider's body
  fragment. For OpenRouter that is `{reasoning:{effort}}`; for raw OpenAI-compatible it's `{reasoning_effort}`.

### 9.6 Gotchas

- **Reasoning field placement differs by provider**: OpenAI uses `reasoning_effort` + `reasoning.encrypted_content`
  include (stateless, `store:false`); Anthropic uses `thinking:{budget_tokens|effort}`; OpenRouter normalizes via
  `reasoning:{effort|max_tokens}`. Don't send OpenAI's fields to OpenRouter and vice-versa.
- **Interleaved reasoning replay**: some upstreams (DeepSeek, GLM) require the previous turn's
  `reasoning_content` echoed back on assistant messages; opencode inlines it into the message body —
  **except through OpenRouter**, which handles it server-side.
- **Empty content**: Anthropic/Bedrock reject empty text/reasoning parts — filter them (`transform.ts:170`).
- **Tool-call ID charset**: Claude requires `[A-Za-z0-9_-]`; Mistral requires exactly 9 alphanumerics —
  scrub/pad IDs before sending (`transform.ts:224,258`).
- **Prompt caching**: mark only the first ~2 system + last ~2 messages as ephemeral; provider-level vs
  content-part-level placement differs (Anthropic/Bedrock are message-level).
- **Timeouts**: implement a header timeout and an inter-chunk (SSE) timeout that aborts a stalled stream;
  opencode defaults both to 300s and wraps the SSE body reader (`provider.ts:37`).
- **Temperature**: many reasoning models reject `temperature` (Claude, some Gemini) — opencode omits it unless
  `capabilities.temperature` (`transform.ts:527`). Gate it on model capability.
- **max_tokens cap**: opencode clamps output to `min(model.limit.output, 32000)`.
- **Usage on OpenRouter** only appears in the final chunk **if** you send `usage:{include:true}`.
- **models.dev is the source of truth** for capabilities/limits/cost/reasoning options — don't hardcode; fetch
  and cache it.
```
