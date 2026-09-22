from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any, Literal, Protocol

from open_harness.tools.base import PermissionDenied

Decision = Literal["yes", "no", "always"]


class PromptFn(Protocol):
  async def __call__(self, *, permission: str, detail: str) -> Decision: ...


class ConfirmGate:
  def __init__(self, prompt: PromptFn) -> None:
    self._prompt = prompt
    self._approved: set[tuple[str, str]] = set()

  def _is_approved(self, permission: str, pattern: str) -> bool:
    return any(
      permission == approved_permission and fnmatchcase(pattern, approved_pattern)
      for approved_permission, approved_pattern in self._approved
    )

  async def ask(
    self,
    *,
    permission: str,
    patterns: list[str],
    metadata: dict[str, Any],
    always: list[str] | None = None,
  ) -> None:
    if patterns and all(self._is_approved(permission, pattern) for pattern in patterns):
      return

    decision = await self._prompt(
      permission=permission, detail=f"{permission}: {', '.join(patterns)}"
    )

    if decision == "yes":
      return

    if decision == "always":
      self._approved.update((permission, pattern) for pattern in always or patterns)
      return

    raise PermissionDenied("The user rejected permission to use this specific tool call.")
