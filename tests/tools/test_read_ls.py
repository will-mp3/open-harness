from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from open_harness.tools.base import ToolContext
from open_harness.tools.builtin.read import ReadParams, ReadTool


def _ctx(root: Path) -> ToolContext:
  async def metadata(
    *,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
  ) -> None:
    pass

  async def ask(
    *,
    permission: str,
    patterns: list[str],
    metadata: dict[str, Any],
    always: list[str] | None = None,
  ) -> None:
    pytest.fail(f"Unexpected permission request: {permission}")

  return ToolContext(
    session_id="ses_1",
    message_id="msg_1",
    call_id="call_1",
    cwd=root,
    project_root=root,
    messages=[],
    metadata=metadata,
    ask=ask,
  )


async def test_read_returns_numbered_lines(tmp_path: Path) -> None:
  (tmp_path / "a.py").write_text("first\nsecond\n", encoding="utf-8")

  result = await ReadTool().execute(
    ReadParams(file_path="a.py"),
    _ctx(tmp_path),
  )

  assert "1: first" in result.output.splitlines()
  assert "2: second" in result.output.splitlines()
