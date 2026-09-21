from __future__ import annotations

import pytest
from open_harness.tools.confirm import ConfirmGate, Decision

from open_harness.tools.base import PermissionDenied


class _ScriptedPrompt:
  def __init__(self, answers: list[Decision]) -> None:
    self._answers = answers
    self.calls: list[tuple[str, str]] = []

  async def __call__(self, *, permission: str, detail: str) -> Decision:
    self.calls.append((permission, detail))
    return self._answers.pop(0)


async def test_yes_allpws_a_single_call_without_persisting() -> None:
  prompt = _ScriptedPrompt(["yes", "yes"])
  gate = ConfirmGate(prompt)

  await gate.ask(permission="edit", patterns=["a.py"], metadata={})
  await gate.ask(permission="edit", patterns=["a.py"], metadata={})

  assert len(prompt.calls) == 2


async def test_no_raises_permission_denied() -> None:
  prompt = _ScriptedPrompt(["no"])
  gate = ConfirmGate(prompt)

  with pytest.raises(PermissionDenied):
    await gate.ask(permission="edit", patterns=["a.py"], metadata={})

  assert len(prompt.calls) == 1


async def test_always_remembers_the_requested_pattern_when_none_supplied() -> None:
  prompt = _ScriptedPrompt(["always"])
  gate = ConfirmGate(prompt)

  await gate.ask(permission="edit", patterns=["a.py"], metadata={})
  await gate.ask(permission="edit", patterns=["a.py"], metadata={})

  assert len(prompt.calls) == 1
