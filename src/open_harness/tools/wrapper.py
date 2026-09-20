from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from open_harness.tools.base import (
  PermissionDenied,
  Tool,
  ToolContext,
  ToolFailure,
  ToolResult,
)
from open_harness.tools.truncate import truncate

INVALID_ARGUMENTS_TEMPLATE = (
  "The {tool} tool was called with invalid arguments: {detail}. "
  "Please rewrite the input so it satisfies the expected schema."
)


def _format_validation_error(exc: ValidationError) -> str:
  return "; ".join(
    f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
    for error in exc.errors()
  )


async def execute_tool(
  tool: Tool,
  raw_args: dict[str, Any],
  ctx: ToolContext,
  *,
  max_lines: int,
  max_bytes: int,
  spill_dir: Path,
) -> ToolResult:
  """Validate arguments, execute the tool, and cap model-facing output."""
  try:
    args = tool.params.model_validate(raw_args)
  except ValidationError as exc:
    result = ToolResult(
      title=f"{tool.id} (invalid arguments)",
      output=INVALID_ARGUMENTS_TEMPLATE.format(
        tool=tool.id,
        detail=_format_validation_error(exc),
      ),
      metadata={"invalid_arguments": True},
    )
  else:
    try:
      result = await tool.execute(args, ctx)
    except PermissionDenied as exc:
      result = ToolResult(
        title=f"{tool.id} (denied)",
        output=str(exc),
        metadata={"denied": True},
      )
    except ToolFailure as exc:
      result = ToolResult(
        title=f"{tool.id} (failed)",
        output=str(exc),
        metadata={"failed": True},
      )

  capped = truncate(
    result.output,
    max_lines=max_lines,
    max_bytes=max_bytes,
    spill_dir=spill_dir,
  )
  if not capped.truncated:
    return result

  return ToolResult(
    title=result.title,
    output=capped.content,
    metadata={
      **result.metadata,
      "truncated": True,
      "output_path": str(capped.output_path),
    },
  )
