"""Shared pytest configuration.

Auto-skips tests marked ``@pytest.mark.docker`` when no usable Docker daemon
is reachable, so ``uv run pytest`` never fails just because Docker is not
installed or not running locally. CI runners have Docker, so those tests run
there.

Also stubs the pinned agent binary suite-wide (:func:`fake_claude_binary`) —
provisioning it is a *backend's* job now, so any test driving a real backend
would otherwise download ~100 MB and write to ``/opt``.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cache
import os
import shutil
import subprocess

from etils import epath
import pytest


@cache
def _docker_usable() -> bool:
  """Return whether a Docker daemon is installed and reachable."""
  if shutil.which("docker") is None:
    return False
  try:
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        timeout=15,
        check=False,
    )
  except (OSError, subprocess.TimeoutExpired):
    return False
  return result.returncode == 0


@dataclass
class FakeClaudeBinary:
  """What the stubbed provisioner did, for a test to assert on.

  Attributes:
    cached: The stand-in host-cache path returned when no ``dest`` was asked
      for — what a backend that hands a copy over would mount.
    destinations: One entry per ``ensure_claude_binary`` call, in order: the
      requested ``dest`` as a string, or ``None`` when the caller took the
      default host cache. Distinguishes "fetched for itself" (a job) from
      "took a host copy" (a container).
  """

  cached: epath.Path
  destinations: list[str | None] = field(default_factory=list)


@pytest.fixture(autouse=True)
def fake_claude_binary(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> FakeClaudeBinary:
  """Stand in for the pinned agent binary: no download, no ``/opt`` write.

  Autouse because provisioning moved *into the backends*: every run on a real
  ``GitHubJobSandbox`` / ``DockerHostSandbox`` provisions the agent, including
  tests that have nothing to do with Claude Code. The stub records where each
  call was told to put it and never writes there.
  """
  cached = tmp_path_factory.mktemp("claude-code-cache") / "claude"
  _ = cached.write_bytes(b"BIN")
  os.chmod(cached, 0o755)
  record = FakeClaudeBinary(cached=epath.Path(cached))

  def _stub(*, dest: epath.PathLike | None = None, **kwargs: object):
    del kwargs  # version / platform / repo_root / refresh: irrelevant here
    record.destinations.append(None if dest is None else str(dest))
    return record.cached if dest is None else epath.Path(dest)

  monkeypatch.setattr(
      "swe_lab.harnesses.claude_code.binary.ensure_claude_binary", _stub
  )
  return record


def pytest_collection_modifyitems(
    config: pytest.Config, items: Iterable[pytest.Item]
) -> None:
  """Skip Docker-marked tests when no Docker daemon is reachable."""
  del config
  if _docker_usable():
    return
  skip = pytest.mark.skip(reason="no usable Docker daemon")
  for item in items:
    if "docker" in item.keywords:
      item.add_marker(skip)
