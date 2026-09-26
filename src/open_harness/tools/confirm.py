from __future__ import annotations

import shlex
from fnmatch import fnmatchcase
from typing import Any, Literal, Protocol

from open_harness.tools.base import PermissionDenied

Decision = Literal["yes", "no", "always"]

_ARITY: dict[str, int] = {
  "git": 2,
  "npm": 2,
  "pnpm": 2,
  "yarn": 2,
  "uv": 2,
  "cargo": 2,
  "docker": 2,
  "kubectl": 2,
  "brew": 2,
  "ruff": 2,
  "pip": 2,
}

_NESTED_ARITY: dict[tuple[str, str], int] = {
  ("npm", "run"): 3,
  ("pnpm", "run"): 3,
  ("yarn", "run"): 3,
  ("uv", "run"): 3,
  ("cargo", "run"): 3,
}

_GIT_VALUE_OPTIONS = {"-c", "-C"}
_SHELL_SYNTAX = frozenset(";&|<>$`()\n\r")


class PromptFn(Protocol):
  async def __call__(self, *, permission: str, detail: str) -> Decision: ...


def bash_prefix(command: str) -> str:
  try:
    tokens = shlex.split(command)
  except ValueError:
    tokens = command.split()

  if not tokens:
    return command

  words = [tokens[0]]
  remaining = iter(tokens[1:])

  for token in remaining:
    if token.startswith("-"):
      if words == ["git"] and token in _GIT_VALUE_OPTIONS:
        next(remaining, None)
      continue
    words.append(token)

  depth = _ARITY.get(words[0], 1)
  if len(words) >= 2:
    depth = _NESTED_ARITY.get((words[0], words[1]), depth)

  return " ".join(words[:depth]) + " *"


class ConfirmGate:
  def __init__(self, prompt: PromptFn, *, auto_approve: bool = False) -> None:
    self._prompt = prompt
    self._auto_approve = auto_approve
    self._approved: set[tuple[str, str]] = set()

  def _is_approved(self, permission: str, pattern: str) -> bool:
    if permission == "bash" and any(char in _SHELL_SYNTAX for char in pattern):
      return False

    for approved_permission, approved_pattern in self._approved:
      if permission != approved_permission:
        continue

      if fnmatchcase(pattern, approved_pattern):
        return True

      if (
        permission == "bash"
        and approved_pattern.endswith(" *")
        and pattern == approved_pattern[:-2]
      ):
        return True

    return False

  @staticmethod
  def _detail(
    permission: str,
    patterns: list[str],
    metadata: dict[str, Any],
  ) -> str:
    detail = f"{permission}: {', '.join(patterns)}"
    diff = metadata.get("diff")
    if isinstance(diff, str) and diff:
      return f"{detail}\n\n{diff}"
    return detail

  async def ask(
    self,
    *,
    permission: str,
    patterns: list[str],
    metadata: dict[str, Any],
    always: list[str] | None = None,
  ) -> None:
    if self._auto_approve:
      return

    if patterns and all(self._is_approved(permission, pattern) for pattern in patterns):
      return

    decision = await self._prompt(
      permission=permission,
      detail=self._detail(permission, patterns, metadata),
    )

    if decision == "yes":
      return

    if decision == "always":
      self._approved.update((permission, pattern) for pattern in always or patterns)
      return

    raise PermissionDenied("The user rejected permission to use this specific tool call.")
