from __future__ import annotations

from typing import Any, Literal, Protocol

from open_harness.tools.base import PermissionDenied
from open_harness.tools.permission_match import matches

Decision = Literal["yes", "no", "always"]


class PromptFn(Protocol):
  async def __call__(self, *, permission: str, detail: str) -> Decision: ...


class ConfirmGate:
  def __init__(self, prompt: PromptFn, *, auto_approve: bool = False) -> None:
    self._prompt = prompt
    self._auto_approve = auto_approve
    self._approved: set[tuple[str, str]] = set()

  def _covered(self, permission: str, target: str) -> bool:
    return any(
      matches(permission, name) and matches(target, scope) for name, scope in self._approved
    )

  async def ask(
    self,
    *,
    permission: str,
    patterns: list[str],
    metadata: dict[str, Any],
    always: list[str] | None = None,
  ) -> None:
    if self._auto_approve or all(self._covered(permission, target) for target in patterns):
      return

    scopes = list(always or [])
    detail = [f"{permission}: {', '.join(patterns)}"]

    for key in ("command", "workdir", "diff"):
      value = metadata.get(key)
      if isinstance(value, str) and value:
        detail.append(f"{key}: {value}")

    detail.append("Session scopes: " + (", ".join(scopes) if scopes else "none"))
    decision = await self._prompt(
      permission=permission,
      detail="\n\n".join(detail),
    )

    if decision == "yes":
      return

    if decision == "always":
      self._approved.update((permission, scope) for scope in scopes)
      return

    raise PermissionDenied("The user rejected permission to use this specific tool call.")
