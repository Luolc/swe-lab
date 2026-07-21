"""Shared pytest configuration.

Auto-skips tests marked ``@pytest.mark.docker`` when no usable Docker daemon
is reachable, so ``uv run pytest`` never fails just because Docker is not
installed or not running locally. CI runners have Docker, so those tests run
there.
"""

from collections.abc import Iterable
from functools import cache
import shutil
import subprocess

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
