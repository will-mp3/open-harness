from __future__ import annotations

import re


def _utf16_units(text: str) -> str:
  encoded = text.encode("utf-16-le", errors="surrogatepass")
  return "".join(
    chr(int.from_bytes(encoded[index : index + 2], "little")) for index in range(0, len(encoded), 2)
  )


def matches(value: str, pattern: str) -> bool:
  value = _utf16_units(value.replace("\\", "/"))
  pattern = _utf16_units(pattern.replace("\\", "/"))

  optional_arguments = pattern.endswith(" *")
  head = pattern[:-2] if optional_arguments else pattern

  expression = "".join(
    ".*" if char == "*" else "." if char == "?" else re.escape(char) for char in head
  )

  if optional_arguments:
    expression += "(?: .*)?"

  return re.fullmatch(expression, value, flags=re.DOTALL) is not None
