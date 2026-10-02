from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from open_harness.tools.base import ToolContext, ToolFailure, ToolResult

MAX_LINE_LENGTH = 2000


class ReadParams(BaseModel):
  file_path: str = Field(description="Path to the file, relative to the working directory")
  offset: int = Field(
    default=1,
    ge=0,
    description="Starting line number (1-based; 0 uses line 1)",
  )
  limit: int = Field(
    default=2000,
    ge=0,
    description="maximum number of lines to return",
  )


class ReadTool:
  id = "read"
  description = "Read a text file and return its contents with line numbers."
  params = ReadParams

  async def execute(self, args: ReadParams, ctx: ToolContext) -> ToolResult:
    path = ctx.cwd / args.file_path
    try:
      text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except FileNotFoundError as exc:
      raise ToolFailure(f"File not found: {args.file_path}") from exc
    except IsADirectoryError as exc:
      raise ToolFailure(f"{args.file_path} is a directory. Use the ls tool instead.") from exc

    lines = text.splitlines()
    offset = args.offset or 1

    empty_file_start = not lines and offset == 1
    if offset > len(lines) and not empty_file_start:
      raise ToolFailure(f"Offset {offset} is out of range for this file ({len(lines)} lines)")

    start = offset - 1
    window = lines[start : start + args.limit]

    rendered: list[str] = []
    for number, line in enumerate(window, start=offset):
      if len(line) > MAX_LINE_LENGTH:
        line = line[:MAX_LINE_LENGTH] + f"... (line truncated to {MAX_LINE_LENGTH} chars)"
      rendered.append(f"{number}: {line}")

    output = "\n".join(rendered)

    next_offset = offset + len(window)
    if next_offset <= len(lines):
      output += f"\n\nRead again with offset={next_offset} to continue."

    return ToolResult(
      title=f"read {args.file_path}",
      output=output if lines else "(empty file)",
    )
