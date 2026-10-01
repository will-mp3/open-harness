from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from open_harness.tools.base import ToolContext, ToolFailure, ToolResult


class ReadParams(BaseModel):
  file_path: str = Field(description="Path to the file, relative to the working directory")


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

    output = "\n".join(
      f"{number}: {line}" for number, line in enumerate(text.splitlines(), start=1)
    )

    return ToolResult(
      title=f"read {args.file_path}",
      output=output,
    )
