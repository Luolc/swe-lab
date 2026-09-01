"""Ending a spawned command must end what it spawned, not just the child.

The defect this closes is a *grandchild*: `ReverseProxy` started its proxy
without `start_new_session`, so anything the proxy itself started outlived the
context that owned it. A test that only watches the direct child cannot see
that, which is why these spawn a real tree and check the grandchild.

Parent-alive deaths only — normal exit, exception, timeout. A parent that is
killed sends no signal at all, and nothing here should be read as covering it.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
import signal
import subprocess
import time

import pytest

from swe_lab.process_group import end_process_group

# A shell that backgrounds a long sleep, prints its pid, and then waits: the
# grandchild is what outlives a terminate() aimed at the direct child.
_SPAWNS_A_GRANDCHILD = "sleep 300 & echo $!; wait"


def _alive(pid: int) -> bool:
  """Whether ``pid`` exists (signal 0 tests for existence)."""
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    return False
  return True


def _wait_gone(pid: int, timeout: float = 5.0) -> bool:
  """Wait for ``pid`` to disappear, so the assert is not a race."""
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if not _alive(pid):
      return True
    time.sleep(0.05)
  return False


@pytest.fixture
def tree() -> Iterator[subprocess.Popen[str]]:
  """Start a shell in its own group, holding a backgrounded grandchild."""
  process = subprocess.Popen(
      ["/bin/bash", "-c", _SPAWNS_A_GRANDCHILD],
      stdout=subprocess.PIPE,
      text=True,
      start_new_session=True,
  )
  yield process
  if process.poll() is None:  # a failing test must not leak the tree
    end_process_group(process)


def test_the_whole_tree_dies_not_just_the_child(tree: subprocess.Popen[str]):
  assert tree.stdout is not None
  grandchild = int(tree.stdout.readline().strip())
  assert _alive(grandchild)

  end_process_group(tree)

  assert _wait_gone(grandchild), "the grandchild outlived its group"
  assert tree.poll() is not None


def test_terminating_only_the_child_is_what_leaked(tree: subprocess.Popen[str]):
  """The old behaviour, pinned as the reason the helper exists."""
  assert tree.stdout is not None
  grandchild = int(tree.stdout.readline().strip())

  tree.terminate()
  _ = tree.wait(timeout=5)

  assert _alive(grandchild), "if this fails the premise changed, not the fix"
  os.kill(grandchild, signal.SIGKILL)


def test_ending_an_already_dead_group_is_silent(tree: subprocess.Popen[str]):
  """Teardown runs on paths where the child is already gone."""
  os.killpg(tree.pid, signal.SIGKILL)
  _ = tree.wait(timeout=5)
  end_process_group(tree)  # must not raise
