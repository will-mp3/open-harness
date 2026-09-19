from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from open_harness.schema.message import new_id


@dataclass
class TruncateResult:
  content: str
  truncated: bool
  output_path: Path | None = None


def truncate(
  text: str,
  *,
  max_lines: int,
  max_bytes: int,
  spill_dir: Path,
) -> TruncateResult:
  """Cap tool output centrally so every tool inherits context hygiene"""
  encoded = text.encode("utf-8")
  lines = text.splitlines()

  if len(lines) <= max_lines and len(encoded) <= max_bytes:
    return TruncateResult(content=text, truncated=False)

  spill_dir.mkdir(parents=True, exist_ok=True)
  path = spill_dir / f"{new_id('tool')}.txt"
  path.write_text(text, encoding="utf-8")

  half = max(max_lines // 2, 1)
  head = lines[:half]
  tail = lines[-half:] if len(lines) > half else []

  marker = f"... output truncated: {len(lines)} lines, {len(encoded)} bytes ..."
  preview = "\n".join([*head, "", marker, "", *tail])
  preview = preview.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")

  hint = (
    f"\n\nFull output written to {path}. "
    "Use grep on that file, or read it with offset and limit, to inspect the rest."
  )

  return TruncateResult(
    content=preview + hint,
    truncated=True,
    output_path=path,
  )
