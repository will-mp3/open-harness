# opencode — Supporting Subsystems Reference (Config, Plugins, Skills/Commands, Storage, Project, Snapshot/Worktree/Git, Share/Sync, Installation)

Scope: the subsystems that surround the core agent loop. Canonical harness is
`/tmp/opencode-src/packages/opencode/src`; the newer Effect rewrite is
`/tmp/opencode-src/packages/core/src` (noted where relevant). The codebase is written in
TypeScript on the [Effect](https://effect.website) runtime: services are declared with
`Context.Service`, wired with `Layer`/`LayerNode.make`/`makeGlobalNode`, and per-project
state is held in `InstanceState` (keyed by working directory). Durable data has largely
migrated from loose JSON files to a **SQLite/Drizzle** database in `core`.

Two "generations" coexist:
- **v1 (opencode package)**: JSON-file storage, config schemas under `core/src/v1/config/*`.
- **v2 (core package)**: SQLite tables (Drizzle), `ProjectV2`, `SkillV2`, sync/event-sourcing.

Global paths come from `packages/core/src/global.ts` using XDG base dirs, app name `opencode`:
- `Global.Path.data` = `$XDG_DATA_HOME/opencode` (typically `~/.local/share/opencode`)
- `Global.Path.config` = `$XDG_CONFIG_HOME/opencode` (typically `~/.config/opencode`)
- `Global.Path.cache` = `$XDG_CACHE_HOME/opencode`, `Global.Path.state` = `$XDG_STATE_HOME/opencode`
- Derived: `bin = cache/bin`, `log = data/log`, `repos = data/repos`, `tmp = os.tmpdir()/opencode`
- `Global.Path.config` is overridable by env `OPENCODE_CONFIG_DIR`.

---

## 1. Config

Primary files:
- `config/config.ts` — the Config service (loading, merging, precedence, writes).
- `config/paths.ts` — where config files/dirs are discovered.
- `config/parse.ts` — JSONC parse + Effect Schema decode with rich error formatting.
- `config/variable.ts` — `{env:VAR}` and `{file:path}` substitution.
- `config/managed.ts` — enterprise/MDM managed config (macOS plist, `/etc/opencode`, etc.).
- `config/plugin.ts`, `config/command.ts`, `config/agent.ts`, `config/markdown.ts`, `config/entry-name.ts` — loaders for the `.opencode/{plugin,command,agent,mode}` file-based extension points.
- Schema definitions: `packages/core/src/v1/config/config.ts` (`ConfigV1.Info`) and siblings (`command.ts`, `skills.ts`, `agent.ts`, `permission.ts`, `plugin.ts`, `mcp.ts`, `provider.ts`, `server.ts`, `formatter.ts`, `lsp.ts`, `attachment.ts`).

### Schema (top-level keys, `ConfigV1.Info`)
From `core/src/v1/config/config.ts`. Notable fields:
- `$schema` (string), `shell`, `logLevel`, `username`.
- `model`, `small_model` (format `provider/model`), `default_agent`, `subagent_depth` (default 1).
- `provider` (Record → custom provider configs / model overrides), `disabled_providers`, `enabled_providers`.
- `agent` (Record of `ConfigAgentV1.Info`; well-known: plan, build, general, explore, title, summary, compaction), `mode` (deprecated alias folded into `agent` with `mode: "primary"`).
- `command` (Record → `ConfigCommandV1.Info`), `skills` (`{ paths[], urls[] }`), `references`/`reference` (git/local dir refs), `mcp` (Record → MCP server config), `plugin` (array of specs — string or `[spec, options]`).
- `permission` (`ConfigPermissionV1.Info`), `tools` (Record<string, boolean>; folded into `permission`).
- `instructions` (array of files/globs — **concatenated & deduped** across sources, not overwritten), `formatter`, `lsp`, `watcher.ignore`.
- `snapshot` (boolean, default on), `share` (`"manual" | "auto" | "disabled"`), `autoshare` (deprecated → `share: "auto"`), `autoupdate` (`boolean | "notify"`).
- `server`, `enterprise.url`, `tool_output` (`max_lines` default 2000, `max_bytes` default 51200), `compaction` (`auto`, `prune`, `tail_turns`, `preserve_recent_tokens`, `reserved`).
- `experimental` (`batch_tool`, `primary_tools`, `continue_loop_on_deny`, `mcp_timeout`, `policies`, `openTelemetry`, ...).

Agent schema (`core/src/v1/config/agent.ts`): `model`, `variant`, `temperature`, `top_p`,
`prompt`, `tools` (deprecated), `disable`, `description`, `mode` (`subagent|primary|all`),
`hidden`, `options`, `color`, `steps` (max iterations), `permission`.

Permission schema (`core/src/v1/config/permission.ts`): value is either a single action
(`ask|allow|deny`) applied to `"*"`, or an object keyed by tool (`read, edit, glob, grep,
list, bash, task, external_directory, todowrite, question, webfetch, websearch, lsp, skill,
doom_loop`) whose value is an action or a `Record<pattern, action>`. **Key order is preserved**
(`propertyOrder: "original"`) because permission matching is precedence-ordered.

Command schema (`core/src/v1/config/command.ts`): `template` (required), `description`,
`agent`, `model`, `variant`, `subtask`.

### Config sources & precedence (from `config.ts:loadInstanceState`)
Merged in this order (later wins; `mergeDeep`, but `instructions` arrays are concatenated+deduped):
1. **Remote well-known config** — for any auth entry of type `wellknown`, fetch
   `${url}/.well-known/opencode` (`ConfigV1.WellKnown`), optionally follow `remote_config.url`
   with substituted headers. Scope: global.
2. **Global config** — `Global.Path.config/{config.json, opencode.json, opencode.jsonc}`
   (merged in that order). A legacy TOML `config` file is migrated to `config.json` on first run.
   Global file is seeded with `{ "$schema": "https://opencode.ai/config.json" }` if missing.
3. **`OPENCODE_CONFIG`** env — explicit single config file path.
4. **Project config files** — `ConfigPaths.files("opencode", directory, worktree)` walks **up**
   from `directory` to `worktree` collecting `opencode.jsonc`/`opencode.json`, then reverses so
   the nearest file wins. Scope: local. Disabled by `OPENCODE_DISABLE_PROJECT_CONFIG`.
5. **`.opencode/` directory configs** — for each config directory (see below): `opencode.json` /
   `opencode.jsonc`, plus file-based `command/`, `agent/`, `mode/`, `plugin/` loaders.
6. **`OPENCODE_CONFIG_CONTENT`** env — inline JSON config string. Scope: local.
7. **Active org / console config** — `${account.url}/api/config` when logged in with an org.
8. **Managed config dir** — `ConfigManaged.managedConfigDir()` (`/Library/Application Support/opencode`
   on macOS, `%ProgramData%/opencode` on Windows, `/etc/opencode` on Linux) `opencode.json[c]`.
9. **macOS MDM managed preferences** — `/Library/Managed Preferences/[user/]ai.opencode.managed.plist`
   converted via `plutil`; **overrides everything** (highest precedence).
Finally: `OPENCODE_PERMISSION` env (JSON) merged into `permission`; `tools` folded into
`permission`; `autoshare`→`share`; `OPENCODE_DISABLE_AUTOCOMPACT`/`OPENCODE_DISABLE_PRUNE` applied.

### Config directories (`config/paths.ts:directories`)
Unique list of: `Global.Path.config`; every `.opencode` dir walking up from `directory` to
`worktree`; every `.opencode` under `$HOME`; and `OPENCODE_CONFIG_DIR` if set. For each of these
dirs opencode also (a) ensures a `.gitignore` (node_modules, package.json, lockfiles), and
(b) background-installs the `@opencode-ai/plugin` npm dep so local `.ts` plugins resolve.

### Variable substitution (`config/variable.ts`)
Applied to raw config text before JSONC parse:
- `{env:VAR}` → value from provided env map or `process.env` (empty string if missing).
- `{file:relative/or/absolute/path}` → file contents (JSON-string-escaped), resolved relative to
  the config file's dir; `~/` expands to home; lines starting with `//` are skipped. Missing file
  is a hard `InvalidError` (unless `missing: "empty"`).

### Env vars / flags (`core/src/flag/flag.ts`)
`OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG_CONTENT`, `OPENCODE_DISABLE_PROJECT_CONFIG`,
`OPENCODE_PERMISSION`, `OPENCODE_DB`, `OPENCODE_DISABLE_AUTOCOMPACT`, `OPENCODE_DISABLE_PRUNE`,
`OPENCODE_DISABLE_AUTOUPDATE`, `OPENCODE_DISABLE_MODELS_FETCH`, `OPENCODE_MODELS_URL/PATH`,
`OPENCODE_PURE` (no external plugins), `OPENCODE_EXPERIMENTAL*`, `OPENCODE_FAKE_VCS`,
`OPENCODE_SERVER_USERNAME/PASSWORD`, `OPENCODE_PLUGIN_META_FILE`, `OPENCODE_TEST_HOME`, etc.

### Parsing & error handling (`config/parse.ts`)
`jsonc()` uses `jsonc-parser` (trailing commas allowed) and throws a `JsonError` with a
line/column-annotated snippet on syntax errors. `schema()` runs Effect `decodeUnknownExit`
(`errors: "all"`, `onExcessProperty: "ignore"`, `propertyOrder: "original"`) and throws a
structured `InvalidError` with per-issue path+message. **opencode hard-fails on invalid config.**

### Writes
- `update(config)` writes/merges the project `config.json` (JSON, `mergeDeep`).
- `updateGlobal(config)` patches the global file; `.jsonc` files are patched in-place with
  `jsonc-parser` `modify`/`applyEdits` (preserving comments); `.json` files are re-serialized.
  Returns `{ info, changed }` and invalidates the cache when changed.
- Derived field `plugin_origins` (plugin spec + source file + scope) is stripped before writing.

### Integration points
Config is an Effect service (`@opencode/Config`, `use = serviceUse(Service)`). Consumed by nearly
every subsystem: Plugin (`plugin_origins`), Skill (`skills.paths/urls`, config dirs), Command,
Snapshot (`snapshot` flag), Share (`share`, `enterprise.url`), Provider/Agent/Permission. Global
config is cached with infinite TTL and invalidated on write; per-instance config is stored in
`InstanceState` keyed on the instance directory/worktree.

---

## 2. Plugins

Primary files:
- `plugin/index.ts` — the Plugin service: builds `PluginInput`, loads internal + external plugins, registers hooks, and exposes `trigger(name, input, output)`.
- `plugin/loader.ts` (`PluginLoader`) — resolve → (install) → detect entrypoint → compatibility → import pipeline, with retry for file plugins.
- `plugin/shared.ts` — spec parsing (`npm-package-arg`), npm vs file source detection, entrypoint resolution from `package.json` `exports`/`main`, id/compat checks, theme discovery.
- `plugin/install.ts` — CLI install: resolve target, read manifest targets, patch `opencode.json[c]` plugin array (add/replace/dedupe) with file locks.
- `config/plugin.ts` (`ConfigPlugin`) — normalize path specs relative to declaring config file, auto-discover `.opencode/{plugin,plugins}/*.{ts,js}`, dedupe by identity while keeping `Origin`.
- API contract: `packages/plugin/src/index.ts` (the `@opencode-ai/plugin` package) — `Hooks`, `PluginInput`, `Plugin`, `AuthHook`, `ProviderHook`, `ToolDefinition`.
- Built-in provider/auth plugins: `plugin/openai/`, `plugin/github-copilot/`, `plugin/azure.ts`, `plugin/cerebras.ts`, `plugin/cloudflare.ts`, `plugin/digitalocean.ts`, `plugin/xai.ts`, `plugin/snowflake-cortex.ts`, `plugin/modal/`.

### Plugin API / data model (`packages/plugin/src/index.ts`)
A plugin is `type Plugin = (input: PluginInput, options?) => Promise<Hooks>`. Modern form is a
default-exported object `{ id?, server: Plugin, tui?: never }` (`PluginModule`); legacy form is a
bare exported function. `PluginInput` gives the plugin: an SDK `client` (`createOpencodeClient`
bound to the running server), `project`, `directory`, `worktree`, `serverUrl`, Bun shell `$`,
and `experimental_workspace.register(type, adapter)`.

`Hooks` (the extension surface) — key members:
- `dispose()`, `event({event})`, `config(config)`.
- `tool: { [name]: ToolDefinition }` — **plugins add tools** by exposing a tool map.
- `auth: AuthHook` — **register an auth provider** (oauth/api methods, prompts, loader).
- `provider: ProviderHook` — **register/augment a model provider** (`models(provider, ctx)`).
- Trigger-style `(input, output) => Promise<void>` hooks (mutate `output` in place):
  `chat.message`, `chat.params`, `chat.headers`, `permission.ask`, `command.execute.before`,
  `tool.execute.before`, `tool.execute.after`, `tool.definition`, `shell.env`,
  `experimental.chat.messages.transform`, `experimental.chat.system.transform`,
  `experimental.provider.small_model`, `experimental.session.compacting`,
  `experimental.compaction.autocontinue`, `experimental.text.complete`.

`Plugin.trigger(name, input, output)` iterates every registered hook that defines `name`, awaits
it, and returns the (possibly mutated) `output`. This is how the core loop calls out to plugins.

### Spec model & resolution
A config `plugin` entry is `string | [string, options]`. `ConfigPlugin.Origin = { spec, source,
scope: "global"|"local" }` tracks provenance so dedupe keeps the winning file and location.
- **Source detection** (`shared.ts:pluginSource`): file if spec starts with `file://`, `.`, or is
  absolute; otherwise npm.
- **File plugins** resolve relative to the declaring config file (`ConfigPlugin.resolvePluginSpec`),
  then to a `package.json` `exports["./server"]`/`main` or a directory `index.{ts,tsx,js,mjs,cjs}`.
  File plugins **must export an `id`**.
- **npm plugins** are installed on demand via `Npm.add(pkg@version)` (`resolvePluginTarget`), then
  gated by `checkPluginCompatibility` (their `package.json` `engines.opencode` semver range vs
  `InstallationVersion`). Their id defaults to the package name.
- **Auto-discovery**: `ConfigPlugin.load(dir)` globs `.opencode/{plugin,plugins}/*.{ts,js}` and
  adds them as file specs.

### Loading pipeline (`plugin/loader.ts`)
`loadExternal({items, kind, report})` runs each candidate through `resolve` (install/target →
entrypoint detection → compatibility) then `load` (dynamic `import`). File plugins whose *pre-import*
setup failed (missing dep) are retried once after `config.waitForDependencies()` completes; import
failures are permanent (Bun caches failed module resolution). Reporting stages: `install`,
`compatibility`, `entry`, `load`, plus `missing` (package exists but no entrypoint of that kind).
Hooks are applied sequentially to keep registration order deterministic.

### On-disk layout
- Config declares plugins in `opencode.json[c]` `"plugin": [...]`.
- Auto-discovered plugins: `.opencode/plugin/*.ts` or `.opencode/plugins/*.ts` (any config dir).
- npm plugins install under opencode's npm workspace (managed by `core/src/npm.ts`); the
  `@opencode-ai/plugin` types dep is background-installed into each config dir.
- `install.ts` writes new plugin entries into `.opencode/opencode.json[c]` (project) or the global
  config (`--global`), using a `Flock` lock per config file.

### Internal (built-in) plugins
`internalPlugins()` in `plugin/index.ts` always loads (unless `disableDefaultPlugins`): OpenAI Codex
auth, GitHub Copilot, Modal, GitLab, Poe, Cloudflare (Workers + AI Gateway), Azure, DigitalOcean,
Snowflake Cortex, xAI, Cerebras. These primarily register `auth`/`provider` hooks.

### Integration points
Layer deps: `EventV2Bridge`, `Config`, `RuntimeFlags`. The service subscribes to the event bus and
forwards each event to every hook's `event()`; runs `dispose()` on shutdown via finalizers; and
calls each hook's `config()` once after load. `OPENCODE_PURE` skips all external plugins.

---

## 3. Skills & Commands

This is opencode's analogue of Claude Code skills and slash-commands. **Both are Markdown files
with YAML frontmatter.** Skills are surfaced to the model as available capabilities and also
exposed as commands.

### Skills (`skill/index.ts`, `skill/discovery.ts`; v2 in `core/src/skill.ts`)
Skill data model (`Skill.Info`): `{ name, description?, location, content }`. Loaded from a
`SKILL.md` file whose frontmatter must have `name` (and optional `description`); `content` is the
markdown body.

**Discovery sources** (`discoverSkills` in `skill/index.ts`):
- **External (Claude Code compatible)**: `.claude/skills/**/SKILL.md` and `.agents/skills/**/SKILL.md`,
  both under `$HOME` and walking up from `directory` to `worktree`. `.claude` disabled by
  `disableClaudeCodeSkills`; both disabled by `disableExternalSkills`.
- **opencode dirs**: `{skill,skills}/**/SKILL.md` under every config directory.
- **Config `skills.paths`**: extra folders (globs `**/SKILL.md`), `~/` expanded.
- **Config `skills.urls`**: remote skill registries — fetched/cached by `skill/discovery.ts`.
- A built-in skill `customize-opencode` is always registered first (so a disk skill can override it);
  its body comes from `SkillPlugin.CustomizeOpencodeContent`.

Duplicate names log a warning (first-loaded wins after the built-in). `Skill.available(agent)`
filters by permission (`Permission.evaluate("skill", name, agent.permission) !== "deny"`).
`Skill.fmt(list, {verbose})` renders the skill list for the system prompt (either a `<available_skills>`
XML block or a `## Available Skills` markdown list).

**Remote skill registries** (`skill/discovery.ts`): given a base URL, fetch `index.json`
(`{ skills: [{ name, files[], version? }] }`), require each skill declares `SKILL.md`, and
download every listed file into `Global.Path.cache/skills/<name>/`. Versioned skills are refreshed
atomically via a staging dir + rename, with a `.opencode-version` marker file.

### On-disk layout (skills)
- `~/.claude/skills/<name>/SKILL.md`, `~/.agents/skills/<name>/SKILL.md` (+ project-level walk-up).
- `<config-dir>/skill(s)/<name>/SKILL.md` (e.g. `.opencode/skills/...`).
- Cached remote skills: `~/.cache/opencode/skills/<name>/{SKILL.md,...,.opencode-version}`.
- A skill's sibling files (e.g. `scripts/`, `references/`) are resolved relative to its dir; the
  command template appends "Base directory for this skill: <dir>" so the model can find them.

### Commands (`command/index.ts`; config in `config/command.ts`)
A command is `Command.Info`: `{ name, description?, agent?, model?, source: "command"|"mcp"|"skill",
template, subtask?, hints[] }`. `hints` are derived from the template placeholders (`$1..$9`,
`$ARGUMENTS`). Commands come from four sources, merged into one map:
1. **Built-ins**: `init` (guided AGENTS.md setup) and `review`, whose templates are
   `command/template/initialize.txt` / `review.txt` (with `${path}` = worktree substituted).
2. **Config `command` records** (from `opencode.json`) and file-based commands loaded by
   `config/command.ts` from `{command,commands}/**/*.md` under each config dir — the command name
   comes from the file path (`config/entry-name.ts`), frontmatter provides `agent/model/...`, body is
   the template.
3. **MCP prompts**: each MCP server prompt becomes a command; its template lazily resolves the MCP
   prompt with `$1..$n` argument placeholders.
4. **Skills**: every skill also becomes a command (`source: "skill"`) unless a same-named command
   already exists.

Custom agents (`config/agent.ts`) and modes (`config/agent.ts:loadMode`) load analogously from
`{agent,agents}/**/*.md` and `{mode,modes}/*.md` (frontmatter → `ConfigAgentV1.Info`, body → `prompt`).

### Integration points
Skill service deps: `Discovery, Config, EventV2Bridge, FSUtil, Global, RuntimeFlags`. Command service
deps: `Config, MCP, Skill`. Both hold state per-instance in `InstanceState`. The core loop reads
`Skill.available(agent)` to build the system prompt and `Command.get(name)` to expand a slash-command
into a prompt template.

---

## 4. Storage / Persistence

**There are two persistence layers.**

### 4a. JSON-file key/value store (v1) — `storage/storage.ts`
Service `@opencode/Storage`. A simple namespaced JSON store rooted at
`Global.Path.data/storage/` (i.e. `~/.local/share/opencode/storage/`). Interface:
- `read<T>(key[])`, `write<T>(key[], content)`, `update<T>(key[], fn)`, `remove(key[])`, `list(prefix[])`.
- A `key` array maps to a file: `file(dir, key) = join(dir, ...key) + ".json"`. E.g.
  `["session", projectID, sessionID]` → `storage/session/<projectID>/<sessionID>.json`;
  `["message", sessionID, messageID]`, `["part", messageID, partID]`, `["project", projectID]`,
  `["session_diff", sessionID]`.
- Concurrency: per-file reentrant read/write locks (`TxReentrantLock` via `RcMap`). Writes are
  `writeWithDirs` (mkdir -p + JSON.stringify pretty). Missing files map to a typed `NotFoundError`.
- **Migrations**: a numeric marker file `storage/migration` records applied migrations. Two exist:
  (1) migrate the old `project/<id>/storage/...` layout to the flat `session|message|part` layout,
  deriving `projectID` from the git root commit; (2) split session `summary.diffs` out into
  `session_diff/<id>.json`.

In the current opencode package this JSON store is used only for a few residual things (e.g.
`session/revert.ts` writes `["session_diff", sessionID]`). Sessions/messages/parts have moved to SQLite.

### 4b. SQLite database (v2, canonical) — `core/src/database/`
`storage/schema.ts` (in the opencode package) simply re-exports the core Drizzle tables:
`AccountTable, AccountStateTable, ControlAccountTable, ProjectTable, SessionTable, MessageTable,
PartTable, TodoTable, SessionShareTable, WorkspaceTable`.

- **DB file location** (`core/src/database/database.ts:path`): `Global.Path.data/opencode.db`, or
  `opencode-<channel>.db` for non-latest channels, or `Global.Path.data/$OPENCODE_DB` if the env is set.
- Opened via a Drizzle-over-SQLite layer (`sqlite.bun.ts` / `sqlite.node.ts`), with migrations
  applied on open (`database/migration.ts`, generated `migration.gen.ts`, `schema.sql.ts`).
- `database/path.ts` provides custom Drizzle column types that store/validate **absolute POSIX
  paths** (Windows paths normalized to `/`).

**Session tables** (`core/src/session/sql.ts`):
- `session`: `id` (PK), `project_id` (FK→project, cascade), `workspace_id?`, `parent_id?`, `slug`,
  `directory`, `path?`, `title`, `version`, `share_url?`, `summary_*` (additions/deletions/files/diffs
  json), `metadata` (json), `cost` (real), `tokens_input/output/reasoning/cache_read/cache_write`,
  `revert` (json `Revert.State`), `permission` (json ruleset), `agent`, `model` (json
  `{id, providerID, variant?}`), timestamps (`time_created/updated`), `time_compacting?`,
  `time_archived?`. Indexed by project, workspace, parent.
- `message`: `id` (PK), `session_id` (FK cascade), timestamps, `data` (json — the full message minus
  id/type). Indexed by `(session_id, time_created, id)`.
- `part`: `id` (PK), `message_id` (FK cascade), `session_id`, timestamps, `data` (json). Indexed by
  `(message_id, id)` and `session_id`.
- `todo`: composite PK `(session_id, position)`, `content`, `status`, `priority`, timestamps.

**Project tables** (`core/src/project/sql.ts`): see Project section.
**Share table** (`core/src/share/sql.ts`): see Share section.

### Integration points
The v1 `Storage` service deps are `FSUtil` + `Git` (Git only for the migration that derives project
ids from root commits). The SQLite `Database` service is a global node injected into Project, Session,
Share, Worktree, Workspace, etc. Message/part streaming during the agent loop writes to the SQLite
`message`/`part` tables.

---

## 5. Project (detection & persistence)

Primary files:
- `project/project.ts` — the Project service (SQLite persistence, git init, sandboxes).
- `core/src/project.ts` (`ProjectV2`) — the authoritative directory→project resolution.
- `project/vcs.ts` — per-instance git branch/status/diff/patch service.
- `project/instance-context.ts` — the `{ directory, worktree, project }` context object.
- `project/instance-store.ts` — global registry of live per-directory instances.
- `project/bootstrap.ts` / `bootstrap-service.ts` / `instance-runtime.ts` — instance boot wiring.

### Project detection (`core/src/project.ts:resolve`)
Given an absolute directory:
1. `git.repo.discover(input)`. **No git repo** → project id `global`, `directory` = filesystem root,
   `vcs = undefined`. (Non-git projects use `worktree === "/"`.)
2. Git repo → read repo-local cache file `<git-common-dir>/opencode` for a `previous` id.
3. Compute id with precedence:
   **(a) git remote origin URL** normalized to `host/path` (lowercased, `.git`/slashes stripped) and
   hashed: `Hash.fast("git-remote:" + normalized)`; → **(b) `previous` cached id**; → **(c) first
   root commit hash** (`git rev-list --max-parents=0`, sorted, first). Falls back to `global`.
4. `directory = repo.worktree`, `vcs = { type: "git", store: <git-common-dir> }`.
5. `commit({store, id})` writes the resolved id back to `<git-common-dir>/opencode` so identity is
   stable even if the remote later changes. This is the **only non-DB project artifact on disk**.

This scheme means all clones of a repo (same remote) share one project id, and worktrees of a repo
resolve to the same worktree root.

### Project persistence (`project/sql.ts`, `project/project.ts`)
- `project` table: `id` (PK), `worktree` (abs), `vcs`, `name`, `icon_url[/_override/_color]`,
  timestamps, `time_initialized`, `sandboxes` (abs-path array), `commands` (json `{ start? }`).
- `project_directory` table: composite PK `(project_id, directory)`, `type`
  (`main|root|git_worktree`), `strategy`, `time_created` — records every directory that has opened
  this project.
- `Project.fromDirectory(directory)` is the entry point: resolves via `ProjectV2`, migrates an old
  project id to the new one (re-parenting `session`/`workspace` rows), upserts the project row,
  records the directory, re-parents "global" sessions matching this directory, and (git only) writes
  the id marker. Other methods: `list`, `get`, `update` (name/icon/commands), `initGit`
  (`git init --quiet` then re-resolve), `setInitialized` (stamped when the `/init` command runs),
  `sandboxes`/`addSandbox`/`removeSandbox`. Emits `Project.Event.Updated` on the global bus.

### Instance context & store
- `InstanceContext = { directory, worktree, project }`. `containsPath(file, ctx)` = file is under
  `directory` or `worktree` (worktree check skipped when `worktree === "/"` so `external_directory`
  permission prompts still work).
- `InstanceStore` (global service, `@opencode/InstanceStore`) maps a **resolved absolute directory →
  a booted `InstanceContext`**, deduping concurrent boots via a `Deferred`. `load`/`reload`/`dispose`/
  `disposeDirectory`/`disposeAll`/`provide(input, effect)`. `boot` resolves the project via
  `Project.fromDirectory` (unless project+worktree are passed) then runs `InstanceBootstrap.run`.
  Disposal emits `server.instance.disposed` on the global bus and evicts the cache. All per-service
  state (Config, Skill, Command, Vcs, ShareNext, Snapshot) is keyed to this instance and filters bus
  events by `event.location.directory`.

### Vcs service (`project/vcs.ts`)
Per-instance git wrapper for the UI/review features: `branch()`, `defaultBranch()`, `status()`
(`FileStatus[]`), `diff(mode)` (`"git"` = working vs HEAD, `"branch"` = vs merge-base with default
branch), `diffRaw()`, `apply(patch)`. Watches `HEAD` changes (via the file watcher) and publishes
`Vcs.Event.BranchUpdated`. Diffs are byte-capped (10 MB per file / total). Deps: `Git`, `EventV2Bridge`.

---

## 6. Snapshot / Worktree / Git

### Git service (`git/index.ts`) — `@opencode/Git`
A thin, read-mostly wrapper around the `git` CLI (invoked as `"git"` on PATH via a child-process
runner; no libgit, no explicit locate). Every call is prefixed with safety flags: `--no-optional-locks
-c core.autocrlf=false -c core.fsmonitor=false -c core.longpaths=true -c core.symlinks=true
-c core.quotepath=false`. Callers always pass an explicit `cwd` (repo root detection is not done here).
Helpers: `run`, `branch`, `prefix` (`rev-parse --show-prefix`), `defaultBranch` (resolves via remote
`refs/remotes/<remote>/HEAD`, prefer `origin`; fallbacks to `init.defaultBranch`, `main`, `master`),
`hasHead`, `mergeBase`, `show`, `status` (`--porcelain=v1 -z`), `diff`/`stats` (`--name-status`/
`--numstat -z`), `patch`/`patchAll`/`patchUntracked`, `statUntracked`, `applyPatch` (the only mutating
op). Output uses `-z` NUL parsing and truncation via `maxOutputBytes`. Only dep: the process runner.

### Snapshot service (`snapshot/index.ts`) — `@opencode/Snapshot`
**Purpose**: restore points for the files the agent edits, without touching the user's real repo.
Implemented as a **separate "shadow" git repository** whose `GIT_DIR` lives outside the user's repo
and whose work-tree points at the user's actual worktree.

- **Shadow GIT_DIR**: `Global.Path.data/snapshot/<project.id>/<Hash.fast(worktree)>` — one shadow repo
  per (project, worktree). All commands pass `["--git-dir", gitdir, "--work-tree", worktree, ...]`.
- **Init**: `git init` (env `GIT_DIR`/`GIT_WORK_TREE`) with perf configs (`index.version=4`,
  `feature.manyFiles=true`, `core.untrackedCache=true`, `core.fsmonitor=false`, etc.). To avoid
  re-hashing large repos it writes `<gitdir>/objects/info/alternates` pointing at the user repo's
  object store (`git rev-parse --git-common-dir`) and copies the user repo's `index`. It also seeds
  `<gitdir>/info/exclude` from the source repo plus per-file entries for oversized files (>2 MiB).
- **Snapshot identity = a tree hash** (not a commit). `track()`: stage changes (`git add --all
  --sparse --pathspec-from-file=- --pathspec-file-nul`, honoring gitignore via `git check-ignore`),
  then `git write-tree` → the printed tree hash is the snapshot id. No commits, no branches.
- **restore(hash)**: `git read-tree <hash>` + `git checkout-index -a -f` (resets the whole worktree
  to that tree).
- **revert(patches)**: per-file `git checkout <hash> -- <file>` (batched up to 100 adjacent files;
  deletes files not present in the snapshot tree).
- **patch(hash)** / **diff(hash)** / **diffFull(from,to)**: `git diff --cached ...` and
  `--name-status`/`--numstat` between trees; blob contents pulled with `git cat-file --batch`.
- Enabled only when `project.vcs === "git"` and config `snapshot !== false`. Ops serialized per shadow
  repo via a semaphore. A background fiber runs `git gc --prune=7.days` hourly. Deps: `FSUtil`,
  process runner, `Config`. (Note: Snapshot spawns git directly, not through the Git service, because
  it needs the `--git-dir`/`--work-tree` indirection.)

### Worktree service (`worktree/index.ts`) — `@opencode/Worktree`
**Purpose**: real `git worktree` sandboxes for isolated/parallel sessions.
- `Info = { name, branch?, directory }`. Worktree root: `Global.Path.data/worktree/<project.id>`;
  each worktree at `<root>/<name>`. Branch = `opencode/<slug>` (unless detached). Names via `Slug.create`,
  up to 26 attempts, checking the dir doesn't exist and `refs/heads/opencode/<name>` is free.
- **create**: `git worktree add --no-checkout -b <branch> <dir>` (or `--detach ... HEAD`), then
  `project.addSandbox`, then **boot** it as its own instance (`git reset --hard` to populate files,
  then `InstanceStore.load({directory})`, emit `Ready`/`Failed`, run project `commands.start` startup
  script via `bash -lc`).
- **list**: `git worktree list --porcelain` (excludes the primary). **remove**: dispose instance +
  `git worktree remove --force` + fsmonitor stop + force-delete dir (retry) + `git branch -D`.
  **reset**: hard-reset to default branch + `git clean -ffdx` + submodule reset, re-run start scripts.
- Deps: `FSUtil`, `Path`, process runner, `Git` (`defaultBranch`), `Project`, `InstanceStore`
  (the tie into the session loop), `Database` (reads `project.commands.start`).

---

## 7. Share / Sync

### Share (`share/session.ts` + `share/share-next.ts`)
- `share/session.ts` (`@opencode/SessionShare`) is the thin orchestrator: `create` (auto-shares root
  sessions when `share: "auto"` or the `autoShare` flag), `share` (throws if `share: "disabled"`,
  else `shareNext.create` and persist the URL on the session), `unshare`.
- `share/share-next.ts` (`@opencode/ShareNext`) is the real engine. It uploads session data to a
  remote share server and keeps it live. Disabled entirely by env `OPENCODE_DISABLE_SHARE`.
  - **Share record** `= { id, url, secret }` (from the server), persisted in the SQLite
    `session_share` table (`core/src/share/sql.ts`: `session_id` PK/FK, `id`, `secret`, `url`,
    timestamps). The `secret` is the per-session upload credential.
  - **What is uploaded** (`type Data`): the `session` record, each `message`, each `part`, the
    `session_diff` (snapshot file diffs), and the `model`s used.
  - **Endpoints**: `POST /api/{res}` (create), `POST /api/{res}/{id}/sync` (incremental),
    `DELETE /api/{res}/{id}` (remove). `res` is `share` (anonymous, baseUrl `enterprise.url ??
    https://opncd.ai`, no auth) or `shares` (logged-in org mode, baseUrl = account url, headers
    `Authorization: Bearer <token>` + `x-org-id`).
  - **Live sync**: subscribes (filtered to the instance directory) to session/message/part/diff/delete
    events; coalesces items into a per-session queue keyed by identity, debounced 1s, then POSTs
    `{ secret, data: [...] }` to the sync endpoint. `create` also forks a `full()` snapshot upload.
  - Deps: `Account, EventV2Bridge, Config, Database, httpClient, Provider, Session`.

### Sync (`sync/README.md` + `sync/schema.ts`)
The `sync/` directory here contains a **design doc** (README) plus an `EventID` schema; the
implementation lives elsewhere (`SyncEvent` module + `server/projectors`). Design:
- An **event-sourcing / session-replay** layer over the existing `Bus`, for **single-writer
  multi-device sync**: one device writes; others replay an ordered event log. Total ordering via a
  simple incrementing integer `seq` (no vector clocks needed under single-writer).
- Sync events are emitted **before** the mutation; "projectors" apply the effect. A sync event is
  `{ type, id, seq, aggregateID, data }` (`SyncEvent.define({type, version, aggregate, schema,
  busSchema?})`, `aggregate` names the id field, e.g. `sessionID`). Sync events auto-republish as Bus
  events (via an optional `busSchema` + `convertEvent`) for backwards compatibility, so `Bus.subscribe`
  stays the single subscription surface. This is **not** DB replication or CRDTs.

`sync/schema.ts` defines `EventID` (branded `evt`-prefixed id with an `ascending` factory).

---

## 8. Installation

`installation/index.ts` (`@opencode/Installation`) — version/update/self-upgrade.
- Version/channel are **build-time global constants** (`core/src/installation/version.ts`):
  `InstallationVersion = OPENCODE_VERSION ?? "local"`, `InstallationChannel = OPENCODE_CHANNEL ??
  "local"`. `isLocal()` = channel `local`; `isPreview()` = channel != `latest`. Channels seen:
  `latest` (stable), non-latest preview tags, `local` (dev).
- `Info = { version, latest }`. `USER_AGENT = opencode/<channel>/<version>/cli`.
- `Method = "curl"|"npm"|"yarn"|"pnpm"|"bun"|"brew"|"scoop"|"choco"|"unknown"`. The install method is
  **not persisted** — it is re-detected each run from `process.execPath` (`.opencode/bin` or
  `.local/bin` → `curl`) or by probing each package manager's global list. Package name is `opencode`
  for brew/choco/scoop, else `opencode-ai`.
- `latest(method?)` queries the appropriate source (Homebrew formula/tap, npm registry
  `opencode-ai/<channel>`, chocolatey, scoop bucket, or the GitHub `releases/latest` tag).
- `upgrade(method, target)` shells out per manager (curl pipes `https://opencode.ai/install` to a
  shell with `VERSION=target`; npm/pnpm/bun `install -g opencode-ai@target`; brew upgrade; choco/scoop
  install pinned; `unknown` fails). `getReleaseType(current, latest)` → `major|minor|patch` via semver.
- Deps: `httpClient`, process runner.

---

## Notes for a Python + OpenRouter reimplementation

### Essential-first (build these to get a working harness)
1. **Config**: single JSON/JSONC (or TOML/YAML) schema with a clear precedence chain. Model it as
   ordered layers merged deep — recommend `global → project (walk up to repo root) → env override`.
   Ship `{env:VAR}` / `{file:path}` substitution early (very useful for API keys). Use a real schema
   validator (pydantic v2) and fail loud with path+message on invalid config. Keep an
   `instructions`-style list that concatenates rather than overwrites. Skip well-known/remote/MDM/org
   layers until you need enterprise features.
2. **Storage**: go straight to **SQLite** (this is where opencode ended up). Tables `session`,
   `message`, `part`, `project` with JSON blob columns for the flexible payloads and explicit columns
   for what you query/aggregate (tokens, cost, timestamps, title, model, parent_id). Use SQLModel or
   SQLAlchemy + Alembic for migrations. A single DB file under an XDG data dir
   (`~/.local/share/<app>/<app>.db`) is the right default; support an env override. Don't start with
   the per-file JSON store — opencode is migrating away from it.
3. **Project detection**: derive a stable project id from `git remote get-url origin` (normalized +
   hashed) → else first root-commit hash → else "global" for non-git dirs; cache it in a repo-local
   marker file (opencode uses `.git/opencode`). Key all per-project state on the resolved absolute
   working directory. This gives you free cross-clone session continuity.
4. **Skills & commands**: adopt opencode's Markdown+frontmatter convention and **be Claude-Code
   compatible** — scan `.claude/skills/**/SKILL.md`, `.agents/skills/**/SKILL.md`, and your own
   `.<app>/skills`. Commands = same files (frontmatter `agent/model/subtask`, body = template with
   `$1..$9`/`$ARGUMENTS` placeholders). Render available skills into the system prompt.
5. **Snapshot**: implement the **shadow-repo trick** — a separate `GIT_DIR` with `--work-tree` at the
   user's repo, `objects/info/alternates` pointing at the real object store, snapshots = `git
   write-tree` tree hashes, restore = `read-tree` + `checkout-index`, revert = per-file `checkout
   <tree> -- <file>`. This is cheap, safe, and never touches the user's branches/HEAD/index. In
   Python just subprocess `git`.

### Later (defer until the core loop is solid)
- **Plugins**: design the `Hooks` surface up front (event, tool map, provider/auth registration, and
  `(input, output)` mutation hooks for chat params / tool before-after / system prompt transform), but
  a Python port can use entry-points or a simple "import module, read a `register(hooks)` function"
  loader instead of npm-on-demand install. Keep provenance (which config file/scope declared each
  plugin) for dedupe and safe writes. OpenRouter model/provider config maps naturally onto the
  `provider` hook + config `provider` records.
- **Worktrees**: `git worktree add` sandboxes booted as separate instances — only needed once you
  support parallel/background sessions.
- **Share / Sync**: share is a remote upload feature (store a per-session secret, POST session/
  messages/parts/diffs, debounced). Sync is a single-writer event-sourcing/replay design — both are
  optional; the takeaways are (a) store a per-session secret for uploads, (b) if you want multi-device
  replay, use a single monotonically increasing `seq` and replay events rather than reaching for CRDTs.
- **Installation**: trivial to reimplement (detect pip/pipx/uv/brew, query PyPI/GitHub for latest);
  don't persist the method, re-detect it. Version/channel as build constants.

### How to model config/storage/plugins in Python (concrete)
- **Config**: pydantic `BaseSettings`-style models per layer; a `load_config(cwd)` that walks up for
  project files, merges dicts deeply (with array-concat for instruction lists), applies env overrides,
  and validates. Cache per-directory; invalidate on write. Write JSON with a `$schema` pointer for
  editor completion.
- **Storage**: SQLAlchemy models mirroring `session/message/part/project`; JSON columns for payloads;
  a thin repository/service object with `read/write/update/list` plus streaming append for parts.
- **Plugins**: a `Protocol`/ABC defining the hook methods; a registry that loads modules (from config
  paths + a conventional `.<app>/plugins/*.py`), instantiates them with a `PluginInput` (client,
  project, directory, worktree), and a `trigger(name, input, output)` dispatcher that awaits each hook
  in registration order and returns the mutated `output`. Filter event delivery by instance directory.

### Key file paths (canonical)
- Config: `/tmp/opencode-src/packages/opencode/src/config/config.ts`, `.../config/paths.ts`,
  `.../config/variable.ts`, `.../config/parse.ts`, schema
  `/tmp/opencode-src/packages/core/src/v1/config/config.ts`.
- Plugins: `/tmp/opencode-src/packages/opencode/src/plugin/{index,loader,shared,install}.ts`,
  `.../config/plugin.ts`, API `/tmp/opencode-src/packages/plugin/src/index.ts`.
- Skills/Commands: `/tmp/opencode-src/packages/opencode/src/skill/{index,discovery}.ts`,
  `.../command/index.ts`, `.../config/{command,agent,markdown,entry-name}.ts`.
- Storage: `/tmp/opencode-src/packages/opencode/src/storage/{storage,schema}.ts`,
  `/tmp/opencode-src/packages/core/src/database/{database,path}.ts`,
  `/tmp/opencode-src/packages/core/src/session/sql.ts`.
- Project: `/tmp/opencode-src/packages/opencode/src/project/{project,vcs,instance-context,instance-store}.ts`,
  `/tmp/opencode-src/packages/core/src/project.ts`, `.../core/src/project/sql.ts`.
- Snapshot/Worktree/Git: `/tmp/opencode-src/packages/opencode/src/{snapshot,worktree,git}/index.ts`.
- Share/Sync: `/tmp/opencode-src/packages/opencode/src/share/{session,share-next}.ts`,
  `.../core/src/share/sql.ts`, `.../opencode/src/sync/{README.md,schema.ts}`.
- Installation: `/tmp/opencode-src/packages/opencode/src/installation/index.ts`,
  `/tmp/opencode-src/packages/core/src/installation/version.ts`.
- Global paths: `/tmp/opencode-src/packages/core/src/global.ts`; flags
  `/tmp/opencode-src/packages/core/src/flag/flag.ts`.
