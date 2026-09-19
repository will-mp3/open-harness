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

  if len(lines) > max_lines:
    head_count = (max_lines + 1) // 2
    tail_count = max_lines // 2
    head_text = "\n".join(lines[:head_count])
    tail_text = "\n".join(lines[-tail_count:]) if tail_count else ""
  else:
    head_text = text
    tail_text = text

  marker = f"\n\n... output truncated: {len(lines)} lines, {len(encoded)} bytes ...\n\n"
  marker = marker[:max_bytes]

  remaining_bytes = max_bytes - len(marker.encode("utf-8"))
  head_budget = (remaining_bytes + 1) // 2
  tail_budget = remaining_bytes // 2

  head = head_text.encode("utf-8")[:head_budget].decode("utf-8", errors="ignore")
  tail = (
    tail_text.encode("utf-8")[-tail_budget:].decode("utf-8", errors="ignore") if tail_budget else ""
  )

  preview = head + marker + tail

  hint = (
    f"\n\nFull output written to {path}. "
    "Use grep on that file, or read it with offset and limit, to inspect the rest."
  )

  return TruncateResult(
    content=preview + hint,
    truncated=True,
    output_path=path,
  )
