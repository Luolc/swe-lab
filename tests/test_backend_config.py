"""Tests for the split sandbox configs: semantics vs backend mechanics."""

from __future__ import annotations

from pathlib import Path

from etils import epath
import pytest

from swe_lab.sandbox import (
    build_sandbox,
    DockerHostSandbox,
    DockerHostSandboxConfig,
    GhjobSandboxConfig,
    sandbox_config_type,
    sandbox_factory,
    SandboxConfig,
    SandboxError,
    SandboxSpec,
)

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")


def test_flat_settings_become_the_backends_own_config(tmp_path: Path):
  sandbox = build_sandbox(
      "host", SPEC, workspace=tmp_path, network=False, pull=False
  )
  assert isinstance(sandbox, DockerHostSandbox)


def test_an_unsupported_knob_is_refused_loudly(tmp_path: Path):
  # ghjob has no `pull`: a knob the backend cannot honor is an error at
  # construction, never a silent no-op.
  with pytest.raises(SandboxError, match="rejects these settings"):
    _ = build_sandbox("ghjob", SPEC, workspace=tmp_path, pull=False)


def test_ghjob_refuses_offline_semantics(tmp_path: Path):
  # network is a BASE semantic every backend must honor or refuse: the job
  # container is already live, so ghjob refuses rather than no-ops.
  with pytest.raises(SandboxError, match="network"):
    _ = build_sandbox("ghjob", SPEC, workspace=tmp_path, network=False)


def test_a_factory_narrows_its_config_by_ownership(tmp_path: Path):
  del tmp_path
  factory = sandbox_factory("host")
  with pytest.raises(SandboxError, match="DockerHostSandboxConfig"):
    _ = factory(SPEC, SandboxConfig())  # base semantics alone cannot build


def test_the_config_type_is_the_override_seam():
  assert sandbox_config_type("host") is DockerHostSandboxConfig
  assert sandbox_config_type("ghjob") is GhjobSandboxConfig


def test_the_object_path_carries_a_ready_config(tmp_path: Path):
  config = DockerHostSandboxConfig(
      workspace=epath.Path(tmp_path), network=False, pull=False
  )
  sandbox = sandbox_factory("host")(SPEC, config)
  assert isinstance(sandbox, DockerHostSandbox)
