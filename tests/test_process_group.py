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
import contextlib
import os
import signal
import subprocess
import time

import pytest

from swe_lab.process_group import end_process_group

# A shell that backgrounds a long sleep, prints its pid, and then waits: the
# grandchild is what outlives a terminate() aimed at the direct child.
_SPAWNS_A_GRANDCHILD = "sleep 300 & echo $!; wait"

# Captured before any test can monkeypatch `os.killpg`, so teardown reaches the
# kernel rather than a recorder.
_REAL_KILLPG = os.killpg


def _alive(pid: int) -> bool:
  """Whether ``pid`` exists (signal 0 tests for existence)."""
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    return False
  return True


def _wait_gone_from_group(pid: int, timeout: float = 5.0) -> bool:
  """Wait for ``pid`` to exit without reaping it."""
  deadline = time.monotonic() + timeout
  options = os.WEXITED | os.WNOWAIT | os.WNOHANG
  while time.monotonic() < deadline:
    if os.waitid(os.P_PID, pid, options) is not None:
      return True
    time.sleep(0.05)
  return False


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
  # Teardown deliberately does **not** call the code under test: several of
  # these tests monkeypatch the very calls it makes, and pytest finalizes
  # `monkeypatch` after this fixture — so a teardown routed through
  # `end_process_group` met a faked `waitid`, refused to signal (correctly),
  # and leaked the tree.
  #
  # Gated on `returncode`, for the same reason the helper gates on it: once a
  # test has reaped the leader, this number may name somebody else's group. A
  # test that reaps it owns its own descendants — see
  # `test_terminating_only_the_child_is_what_leaked`, which kills the
  # grandchild it deliberately orphaned.
  if process.returncode is None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
      _REAL_KILLPG(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
      _ = process.wait(timeout=5)


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


def test_nothing_is_reaped_before_the_last_group_signal(
    tree: subprocess.Popen[str], monkeypatch: pytest.MonkeyPatch
):
  """The pid must stay reserved while the group is still being signalled.

  Reaping the leader releases its pid, and the group is addressed *by* that
  number — so a `wait()` in between would leave the final `SIGKILL` resolving
  a number the kernel may have reissued. This is the ordering, not the effect,
  so it is asserted at the seam.
  """
  order: list[str] = []
  real_killpg, real_wait = os.killpg, subprocess.Popen.wait

  def killpg(pid: int, sig: int) -> None:
    order.append(f"killpg:{signal.Signals(sig).name}")
    real_killpg(pid, sig)

  def wait(self: subprocess.Popen[str], timeout: float | None = None) -> int:
    order.append("reap")
    return real_wait(self, timeout=timeout)

  monkeypatch.setattr(os, "killpg", killpg)
  monkeypatch.setattr(subprocess.Popen, "wait", wait)

  end_process_group(tree, grace_s=1.0)

  assert order[-1] == "reap", order
  assert order.index("reap") > max(
      index for index, call in enumerate(order) if call.startswith("killpg")
  ), order


def test_reaping_is_what_releases_the_group_number():
  """The measurement the ordering rests on, pinned so a platform change shows.

  An exited-but-unreaped leader keeps its pid — and therefore its group id —
  reserved. Reap it with no member left behind and the number is free, which
  is why a `killpg` after a `wait()` is a lookup of a name that may since have
  been reissued.
  """
  process = subprocess.Popen(["/bin/true"], start_new_session=True)
  assert _wait_gone_from_group(process.pid)

  os.killpg(process.pid, 0)  # still addressable: the zombie holds the number

  _ = process.wait(timeout=5)
  with pytest.raises(ProcessLookupError):
    os.killpg(process.pid, 0)


def test_a_leader_reaped_by_someone_else_before_entry_is_never_signalled(
    tree: subprocess.Popen[str], monkeypatch: pytest.MonkeyPatch
):
  """The pre-flight's *external* half: a live `Popen`, no child to wait on.

  `returncode` is still None — this parent has not reaped anything — so the
  only thing that can say the leader is gone is `waitid`, and it must be
  consulted before the first signal rather than after it.
  """
  signalled: list[str] = []

  def killpg(pid: int, sig: int) -> None:
    del pid
    signalled.append(signal.Signals(sig).name)

  def reaped_elsewhere(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise ChildProcessError(10, "No child processes")

  monkeypatch.setattr(os, "killpg", killpg)
  monkeypatch.setattr(os, "waitid", reaped_elsewhere)

  end_process_group(tree, grace_s=1.0)

  assert signalled == [], signalled
  assert tree.returncode is None  # nothing here reaped it either


def test_a_leader_reaped_elsewhere_stops_the_group_signal(
    tree: subprocess.Popen[str], monkeypatch: pytest.MonkeyPatch
):
  """Ownership unknown is not permission, whenever it becomes unknown.

  Here the leader is still held when the helper is entered — so SIGTERM goes
  out — and is reaped by something else while the group is draining. From that
  point this parent can no longer say what the number names, so the
  conservative move is to stop signalling rather than send one more.
  """
  signalled: list[str] = []
  real_killpg = os.killpg

  def killpg(pid: int, sig: int) -> None:
    signalled.append(signal.Signals(sig).name)
    real_killpg(pid, sig)

  calls: list[int] = []

  def reaped_after_the_first_signal(*args: object, **kwargs: object) -> None:
    del args, kwargs
    calls.append(1)
    if len(calls) == 1:
      return None  # the pre-flight check: still ours, so SIGTERM is sent
    raise ChildProcessError(10, "No child processes")

  monkeypatch.setattr(os, "killpg", killpg)
  monkeypatch.setattr(os, "waitid", reaped_after_the_first_signal)

  end_process_group(tree, grace_s=1.0)

  assert signalled == ["SIGTERM"], signalled


def test_a_leader_reaped_before_entry_is_never_signalled(
    monkeypatch: pytest.MonkeyPatch,
):
  """The check has to precede the *first* signal, not only the last.

  If the leader was already reaped when this was called, the number stopped
  naming that tree before we touched it — so even the SIGTERM is unsafe.
  """
  signalled: list[str] = []
  process = subprocess.Popen(["/bin/true"], start_new_session=True)
  _ = process.wait(timeout=5)  # reaped by us: the reservation is gone

  def killpg(pid: int, sig: int) -> None:
    del pid
    signalled.append(signal.Signals(sig).name)

  monkeypatch.setattr(os, "killpg", killpg)
  end_process_group(process, grace_s=1.0)

  assert signalled == [], signalled


def test_ending_an_already_dead_group_is_silent(tree: subprocess.Popen[str]):
  """Teardown runs on paths where the child is already gone."""
  os.killpg(tree.pid, signal.SIGKILL)
  _ = tree.wait(timeout=5)
  end_process_group(tree)  # must not raise
