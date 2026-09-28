from __future__ import annotations

import re


def matches(value: str, pattern: str) -> bool:
  optional_arguments = pattern.endswith(" *")
  head = pattern[:-2] if optional_arguments else pattern

  expression = "".join(
    ".*" if char == "*" else "." if char == "?" else re.escape(char) for char in head
  )

  if optional_arguments:
    expression += "(?: .*)?"

  return re.fullmatch(expression, value, flags=re.DOTALL) is not None