# open-harness

## Critical Rules

- I type all code myself. Do NOT use Edit/Write/NotebookEdit on source/implementation files. Present each change as a code block with its file path and enough surrounding context for me to place it, then stop and let me type and test it. This overrides and "write the file" step in a skill or workflow.
- Exempt (you may edit directly when asked): non-code artifacts such as memory files, docs, config, and generated/vendored output.
- Design explanation should come before, during, and after implementation code. Explanation should be viewed as part of the implementation, not an accessory.

## Source of truth

Project specifications and implementation plans live here:

- Specifications: `docs/the-ark/specs/`
- Implementation plans: `docs/the-ark/plans/`

Use the relevant documents there as the project evolves.

## Load-bearing architecture

- `LLMEvent` is the only contract between the provider client and session layer.
- Use asyncio throughout. Keep provider-specific HTTP and OpenRouter details isolated to the provider client.
- Model messages are headers plus ordered typed parts; tool calls have explicit lifecycle state.
- Rebuild each model request from session messages on every loop iteration; do not maintain a mutable request history.
- The processor folds and re-yields the event stream while executing tools inline.
- Preserve a clean engine/CLI boundary so a future UI or server does not require a rewrite.

## Project shape

Use Python 3.12+, `uv`, and a `src/open_harness/` package layout. `rg` (ripgrep) is a required runtime dependency for the search tools.
