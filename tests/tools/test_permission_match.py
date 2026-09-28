from __future__ import annotations

import pytest

from open_harness.tools.permission_match import matches


@pytest.mark.parametrize(
  ("value", "pattern", "expected"),
  [
    ("file1.txt", "file?.txt", True),
    ("file12.txt", "file?.txt", False),
    ("foo+bar", "foo+bar", True),
    ("ls", "ls *", True),
    ("ls -la", "ls *", True),
    ("ls foo bar", "ls *", True),
    ("lstmeval", "ls *", False),
    ("lstmeval", "ls*", True),
    ("git status", "git status *", True),
    ("git status-other", "git status *", False),
    ("git diff", "git status *", False),
    ("build[x]", "build[?]", True),
    ("buildx", "build[?]", False),
    ("a\nb", "a*b", True),
    ("a\nb", "a?b", True),
    ("ls\n", "ls", False),
    ("GIT", "git", False),
    (r"a\b", "a/b", True),
    ("a/b", r"a\b", True),
    ("\U00020000", "?", False),
    ("\U00020000", "??", True),
    ("", "*", True),
    ("x.a+(b)$", "x.a+(b)$", True),
  ],
)
def test_matches(value: str, pattern: str, expected: bool) -> None:
  assert matches(value, pattern) is expected
