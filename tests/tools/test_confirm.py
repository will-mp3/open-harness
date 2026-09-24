from __future__ import annotations

import pytest

from open_harness.tools.base import PermissionDenied
from open_harness.tools.confirm import ConfirmGate, Decision, bash_prefix


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


async def test_always_remembers_the_supplied_wildcard() -> None:
  prompt = _ScriptedPrompt(["always", "yes"])
  gate = ConfirmGate(prompt)

  await gate.ask(
    permission="bash",
    patterns=["git status --short"],
    metadata={},
    always=["git status *"],
  )
  await gate.ask(
    permission="bash",
    patterns=["git status --branch"],
    metadata={},
  )

  assert len(prompt.calls) == 1


async def test_approval_does_not_leak_across_permissions() -> None:
  prompt = _ScriptedPrompt(["always", "no"])
  gate = ConfirmGate(prompt)

  await gate.ask(permission="edit", patterns=["a.py"], metadata={})

  with pytest.raises(PermissionDenied):
    await gate.ask(permission="read", patterns=["a.py"], metadata={})

  assert len(prompt.calls) == 2


async def test_an_unapproved_target_requires_a_new_decision() -> None:
  prompt = _ScriptedPrompt(["always", "no"])
  gate = ConfirmGate(prompt)

  await gate.ask(permission="edit", patterns=["a.py"], metadata={})

  with pytest.raises(PermissionDenied):
    await gate.ask(
      permission="edit",
      patterns=["a.py", "b.py"],
      metadata={},
    )

  assert len(prompt.calls) == 2


async def test_approval_does_not_leak_into_a_new_gate() -> None:
  prompt = _ScriptedPrompt(["always", "no"])
  first_gate = ConfirmGate(prompt)
  second_gate = ConfirmGate(prompt)

  await first_gate.ask(permission="edit", patterns=["a.py"], metadata={})

  with pytest.raises(PermissionDenied):
    await second_gate.ask(permission="edit", patterns=["a.py"], metadata={})

  assert len(prompt.calls) == 2


async def test_auto_approve_never_prompts() -> None:
  prompt = _ScriptedPrompt([])
  gate = ConfirmGate(prompt, auto_approve=True)

  await gate.ask(
    permission="bash",
    patterns=["git status --short"],
    metadata={},
  )

  assert prompt.calls == []


async def test_details_includes_the_supplied_diff() -> None:
  prompt = _ScriptedPrompt(["yes"])
  gate = ConfirmGate(prompt)

  await gate.ask(
    permission="edit",
    patterns=["a.py"],
    metadata={"diff": "- old\n+ new"},
  )

  assert prompt.calls == [
    ("edit", "edit: a.py\n\n- old\n+ new"),
  ]


async def test_detail_without_a_diff_shows_permission_and_targets() -> None:
  prompt = _ScriptedPrompt(["yes"])
  gate = ConfirmGate(prompt)

  await gate.ask(
    permission="edit",
    patterns=["a.py", "b.py"],
    metadata={},
  )

  assert prompt.calls == [
    ("edit", "edit: a.py, b.py"),
  ]


@pytest.mark.parametrize(
  ("command", "expected"),
  [
    ("git status --short", "git status *"),
    ("git diff HEAD", "git diff *"),
    ("npm run build", "npm run build *"),
    ("uv run pytest -q", "uv run pytest *"),
    ("ls -la", "ls *"),
    ("git -c core.pager=cat status", "git status *"),
    ("echo 'unterminated", "echo *"),
  ],
)
def test_bash_prefix(command: str, expected: str) -> None:
  assert bash_prefix(command) == expected


@pytest.mark.parametrize("command", ["git status", "git status --branch"])
async def test_generated_prefix_covers_the_command_and_its_arguments(
  command: str,
) -> None:
  prompt = _ScriptedPrompt(["always", "yes"])
  gate = ConfirmGate(prompt)
  original = "git status --short"

  await gate.ask(
    permission="bash",
    patterns=[original],
    metadata={},
    always=[bash_prefix(original)],
  )
  await gate.ask(permission="bash", patterns=[command], metadata={})

  assert len(prompt.calls) == 1


@pytest.mark.parametrize(
  "command",
  ["git diff HEAD", "git status-other --short"],
)
async def test_generated_prefix_does_not_cover_other_subcommands(
  command: str,
) -> None:
  prompt = _ScriptedPrompt(["always", "no"])
  gate = ConfirmGate(prompt)
  original = "git status --short"

  await gate.ask(
    permission="bash",
    patterns=[original],
    metadata={},
    always=[bash_prefix(original)],
  )

  with pytest.raises(PermissionDenied):
    await gate.ask(permission="bash", patterns=[command], metadata={})

  assert len(prompt.calls) == 2
