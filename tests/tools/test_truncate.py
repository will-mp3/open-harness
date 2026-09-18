from __future__ import annotations

from pathlib import Path

from open_harness.tools.truncate import truncate

def test_short_output_passes_through_untouched(tmp_path: Path) -> None:
  result = truncate("hello", max_lines=10, max_bytes=100, spill_dir=tmp_path)

  assert result.truncated is False
  assert result.content == "hello"
  assert result.output_path is None

def test_line_overflow_spills_to_a_file_and_names_it(tmp_path: Path) -> None:
  text = "\n".join(f"line {i}" for i in range(100))

  result = truncate(text, max_lines=10, max_bytes=10_000, spill_dir=tmp_path)

  assert result.truncated is True
  assert result.output_path is not None
  assert result.output_path.read_text() == text
  assert str(result.output_path) in result.content
  assert "line 0" in result.content
  assert "line 99" in result.content
  assert "line 50" not in result.content

def test_byte_overflow_triggers_truncation(tmp_path: Path) -> None:
  result = truncate("x" * 500, max_lines=1000, max_bytes=100, spill_dir=tmp_path)

  assert result.truncated is True
  assert result.output_path is not None