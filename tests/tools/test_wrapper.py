from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from open_harness.tools.base import (
  PermissionDenied,
  ToolContext,
  ToolFailure,
  ToolResult,
)
from open_harness.tools.wrapper import execute_tool


class _Params(BaseModel):
  value: int


class _EchoTool:
  id = "echo"
  description = "Echo"
  params = _Params

  async def execute(self, args: _Params, ctx: ToolContext) -> ToolResult:
    return ToolResult(title="echo", output="v" * args.value)


class _DeniedTool(_EchoTool):
  async def execute(self, args: _Params, ctx: ToolContext) -> ToolResult:
    raise PermissionDenied("The user rejected permission for this call.")


class _FailingTool(_EchoTool):
  async def execute(self, args: _Params, ctx: ToolContext) -> ToolResult:
    raise ToolFailure("File not found: a.py")


def _ctx(tmp_path: Path) -> ToolContext:
  async def metadata(
    *,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
  ) -> None:
    return None

  async def ask(
    *,
    permission: str,
    patterns: list[str],
    metadata: dict[str, Any],
    always: list[str] | None = None,
  ) -> None:
    return None

  return ToolContext(
    session_id="ses_1",
    message_id="msg_1",
    call_id="call_1",
    cwd=tmp_path,
    project_root=tmp_path,
    messages=[],
    metadata=metadata,
    ask=ask,
  )


async def test_valid_arguments_run_the_tool(tmp_path: Path) -> None:
  result = await execute_tool(
    _EchoTool(),
    {"value": 3},
    _ctx(tmp_path),
    max_lines=100,
    max_bytes=100,
    spill_dir=tmp_path,
  )

  assert result.output == "vvv"


async def test_invalid_argumens_return_a_rewrite_instruction_rather_than_raising(
  tmp_path: Path,
) -> None:
  result = await execute_tool(
    _EchoTool(),
    {"value": "not an int"},
    _ctx(tmp_path),
    max_lines=100,
    max_bytes=1000,
    spill_dir=tmp_path,
  )

  assert "invalid arguments" in result.output
  assert "rewrite the input" in result.output
  assert result.metadata["invalid_arguments"] is True


async def test_permission_denied_becomes_model_facing_text(tmp_path: Path) -> None:
  result = await execute_tool(
    _DeniedTool(),
    {"value": 1},
    _ctx(tmp_path),
    max_lines=100,
    max_bytes=100,
    spill_dir=tmp_path,
  )

  assert "rejected permission" in result.output
  assert result.metadata["denied"] is True


async def test_expected_failure_becomes_model_facing_text(tmp_path: Path) -> None:
  result = await execute_tool(
    _FailingTool(),
    {"value": 1},
    _ctx(tmp_path),
    max_lines=100,
    max_bytes=100,
    spill_dir=tmp_path,
  )

  assert result.output == "File not found: a.py"
  assert result.metadata["failed"] is True


async def test_long_output_is_truncated_and_flagged(tmp_path: Path) -> None:
  result = await execute_tool(
    _EchoTool(),
    {"value": 500},
    _ctx(tmp_path),
    max_lines=1000,
    max_bytes=100,
    spill_dir=tmp_path,
  )

  assert result.metadata["truncated"] is True
  assert "output_path" in result.metadata


async def test_long_failure_output_is_truncated(tmp_path: Path) -> None:
  class _LongFailingTool(_EchoTool):
    async def execute(self, args: _Params, ctx: ToolContext) -> ToolResult:
      raise ToolFailure("x" * 500)

  result = await execute_tool(
    _LongFailingTool(),
    {"value": 1},
    _ctx(tmp_path),
    max_lines=1000,
    max_bytes=100,
    spill_dir=tmp_path,
  )

  assert result.metadata["failed"] is True
  assert result.metadata.get("truncated") is True

  output_path = result.metadata["output_path"]
  assert isinstance(output_path, str)
  assert Path(output_path).read_text(encoding="utf-8") == "x" * 500

  preview = result.output.split("\n\nFull output written to ", 1)[0]
  assert len(preview.encode("utf-8")) <= 100
