from __future__ import annotations

from pydantic import BaseModel, Field

from open_harness.tools.base import ToolContext, ToolResult


class ReadParams(BaseModel):
  file_path: str = Field(description="Path to the file, relative to the working directory")


class ReadTool:
  id = "read"
  description = "Read a text file and return its contents with line numbers."
  params = ReadParams

  async def execute(self, args: ReadParams, ctx: ToolContext) -> ToolResult:
    return ToolResult(
      title=f"read {args.file_path}",
      output="",
    )
