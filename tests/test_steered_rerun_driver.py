"""The steered re-run driver must still load against the shipped harness API.

`run_steered.py` is a runnable instrument, not a library, so nothing imported
it — and #264 (the capture proxy moving into the sandbox) broke it at import
while every test stayed green. The break surfaced when a paid pilot tried to
start, which is the most expensive place to find it.

So this loads the driver the way the shell does and asserts the two things the
migration turns on: it imports at all, and the harness it builds routes the
agent through the *in-sandbox* proxy at the actor's own upstream. No Docker, no
network, no key — the invocation script is a string the harness produces.

The module lives under `experiments/`, which is exempt from the code-quality
hooks and is not an importable package, so it is loaded by path — the same way
`test_steered_rerun_gates.py` loads its own.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

from swe_lab.harnesses.claude_code.constants import (
    AGENT_SCRIPT_NAME,
    BINARY_AT,
    PROXY_BASE_URL,
    PROXY_BINARY_AT,
)

_DRIVER = (
    Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/steered_rerun/run_steered.py"
)


@pytest.fixture(scope="module")
def driver() -> ModuleType:
  """Import the driver by path, with an inert argv."""
  spec = importlib.util.spec_from_file_location("steered_run_driver", _DRIVER)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  # Registered before execution: the module defines dataclasses, and
  # `@dataclass` resolves annotations through `sys.modules[cls.__module__]`.
  sys.modules[spec.name] = module
  argv = sys.argv
  sys.argv = [_DRIVER.name]
  try:
    spec.loader.exec_module(module)
  finally:
    sys.argv = argv
    del sys.modules[spec.name]
  return module


def test_the_driver_runs_the_actor_through_the_in_sandbox_proxy(
    driver: ModuleType,
):
  """The staged script starts the proxy at the actor's own upstream."""
  harness = driver.SteeredClaudeCodeHarness(
      model=driver.ACTOR_MODEL,
      bare=False,
      capture="proxy",
      proxy_target=driver.ACTOR_BASE_URL,
      hook_source="#!/usr/bin/env python3\n",
      settings_json="{}\n",
  )
  script = harness.mounts("/repo")[AGENT_SCRIPT_NAME].resource.content.decode()
  # The target is not cosmetic: cc-reverse-proxy gates its OpenRouter
  # behaviour on this string, and the Anthropic default silently drops the
  # X-Anthropic-Beta mirroring the whole capture exists for.
  assert f"--target {driver.ACTOR_BASE_URL}" in script
  assert f"export ANTHROPIC_BASE_URL={PROXY_BASE_URL}" in script
  assert [asset.path for asset in harness.assets()] == [
      BINARY_AT,
      PROXY_BINARY_AT,
  ]
