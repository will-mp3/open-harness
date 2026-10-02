from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from open_harness.tools.base import ToolContext, ToolFailure
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


async def test_read_missing_file_is_an_expected_failure(tmp_path: Path) -> None:
  with pytest.raises(ToolFailure, match="not found") as exc:
    await ReadTool().execute(
      ReadParams(file_path="missing.py"),
      _ctx(tmp_path),
    )

  assert "missing.py" in str(exc.value)


async def test_read_directory_points_to_ls(tmp_path: Path) -> None:
  (tmp_path / "pkg").mkdir()

  with pytest.raises(ToolFailure, match="directory") as exc:
    await ReadTool().execute(
      ReadParams(file_path="pkg"),
      _ctx(tmp_path),
    )

  assert "pkg" in str(exc.value)
  assert "ls" in str(exc.value)


async def test_read_pages_lines_and_hints_continuation(tmp_path: Path) -> None:
  (tmp_path / "a.py").write_text(
    "first\nsecond\nthird\nfourth\n",
    encoding="utf-8",
  )

  result = await ReadTool().execute(
    ReadParams(file_path="a.py", offset=2, limit=2),
    _ctx(tmp_path),
  )

  lines = result.output.splitlines()
  assert "2: second" in lines
  assert "3: third" in lines
  assert "1: first" not in lines
  assert "4: fourth" not in lines
  assert "offset=4" in result.output


@pytest.mark.parametrize(
  ("content", "offset"),
  [
    ("first\nsecond\nthird", 4),
    ("", 2),
  ],
)
async def test_read_rejects_offset_beyond_end(
  tmp_path: Path,
  content: str,
  offset: int,
) -> None:
  (tmp_path / "a.py").write_text(content, encoding="utf-8")

  with pytest.raises(ToolFailure, match="out of range"):
    await ReadTool().execute(
      ReadParams(file_path="a.py", offset=offset),
      _ctx(tmp_path),
    )


async def test_read_empty_file_reports_it_is_empty(tmp_path: Path) -> None:
  (tmp_path / "empty.txt").write_text("", encoding="utf-8")

  result = await ReadTool().execute(
    ReadParams(file_path="empty.txt"),
    _ctx(tmp_path),
  )

  assert "empty" in result.output.lower()
  assert "offset=" not in result.output


async def test_read_caps_long_lines_and_marks_truncation(tmp_path: Path) -> None:
  (tmp_path / "long.txt").write_text(
    "x" * 5000 + "\ntail\n",
    encoding="utf-8",
  )

  result = await ReadTool().execute(
    ReadParams(file_path="long.txt"),
    _ctx(tmp_path),
  )

  lines = result.output.splitlines()
  assert lines[0].startswith("1: " + "x" * 2000)
  assert "x" * 2001 not in lines[0]
  assert "truncated" in lines[0]
  assert "2: tail" in lines


@pytest.mark.parametrize(
  "content",
  [
    b"hello\x00world",
    b"\x01\x02\x03\x04abcdef",
  ],
)
async def test_read_rejects_binary_content(
  tmp_path: Path,
  content: bytes,
) -> None:
  (tmp_path / "blob.txt").write_bytes(content)

  with pytest.raises(ToolFailure, match="binary"):
    await ReadTool().execute(
      ReadParams(file_path="blob.txt"),
      _ctx(tmp_path),
    )


async def test_read_accepts_unicode_text(tmp_path: Path) -> None:
  (tmp_path / "unicode.txt").write_text("你好，世界\n", encoding="utf-8")

  result = await ReadTool().execute(
    ReadParams(file_path="unicode.txt"),
    _ctx(tmp_path),
  )

  assert "1: 你好，世界" in result.output.splitlines()
