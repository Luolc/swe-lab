"""Tests for the provisioning seam: declared assets, backend-chosen transfer.

The property under test is a *negative* one and easy to lose: neither side
enumerates the other. A backend must place an agent it has never heard of, and
a harness must not care how the bytes travel (task-28 §7).
"""

from __future__ import annotations

from pathlib import Path

from etils import epath
import pytest

from swe_lab.sandbox import (
    AgentAsset,
    InstalledAssetsObserver,
    MountedAssetsObserver,
    SandboxSpec,
)
from swe_lab.sandbox.backends.ghjob import GitHubJobSandbox
from swe_lab.sandbox.backends.host import DockerHostSandbox

SPEC = SandboxSpec("acme__widget-1", "img:tag", "/app", "base")


def _asset(
    tmp_path: Path, name: str = "agent"
) -> tuple[AgentAsset, list[object]]:
  """Build an asset whose materializer records where it was asked to put it."""
  calls: list[object] = []
  cached = tmp_path / f"{name}-cached"
  _ = cached.write_bytes(b"BIN")

  def materialize(dest: epath.Path | None) -> epath.Path:
    calls.append(dest)
    if dest is None:
      return epath.Path(cached)
    _ = epath.Path(dest).parent.mkdir(parents=True, exist_ok=True)
    _ = epath.Path(dest).write_bytes(b"BIN")
    return epath.Path(dest)

  return AgentAsset(path=f"/opt/{name}/{name}", materialize=materialize), calls


def test_a_container_backend_takes_a_host_copy(tmp_path: Path):
  # `dest=None` means "put it in the host cache and tell me where" — the only
  # thing a container backend can use, since it must hand bytes over.
  asset, calls = _asset(tmp_path)
  sandbox = DockerHostSandbox(spec=SPEC, workspace=epath.Path(tmp_path / "ws"))
  observer = sandbox.asset_observer((asset,))
  assert isinstance(observer, MountedAssetsObserver)

  mounts = observer.mounts()
  assert calls == [None]  # host cache, not the final path
  assert set(mounts) == {"/opt/agent/agent"}
  assert mounts["/opt/agent/agent"].executable is True
  assert mounts["/opt/agent/agent"].read_only is True


def test_a_job_backend_installs_in_place_and_moves_no_bytes(tmp_path: Path):
  # The case a mount cannot express: the job's filesystem IS the sandbox, so
  # the asset is fetched straight to its final path.
  asset, calls = _asset(tmp_path)
  final = tmp_path / "opt" / "agent" / "agent"
  asset = AgentAsset(path=str(final), materialize=asset.materialize)
  sandbox = GitHubJobSandbox(spec=SPEC, workspace=epath.Path(tmp_path / "ws"))
  observer = sandbox.asset_observer((asset,))
  assert isinstance(observer, InstalledAssetsObserver)

  observer.after_create(sandbox)
  assert calls == [epath.Path(str(final))]  # the final path, not the cache
  assert final.read_bytes() == b"BIN"
  # ...and it contributes no mounts: nothing travels.
  assert observer.mounts() == {}


def test_neither_side_enumerates_the_other(tmp_path: Path):
  """The point of the seam, stated as a test.

  A backend places an asset it has never heard of — no import of any harness,
  no name it recognizes — which is what lets a downstream backend provision a
  downstream agent, and what makes adding an agent cost zero backend edits.
  """
  asset, _ = _asset(tmp_path, name="an-agent-swe-lab-never-heard-of")
  sandbox = DockerHostSandbox(spec=SPEC, workspace=epath.Path(tmp_path / "ws"))
  observer = sandbox.asset_observer((asset,))
  assert observer is not None
  name = "an-agent-swe-lab-never-heard-of"
  assert f"/opt/{name}/{name}" in observer.mounts()


def test_a_task_that_runs_no_agent_gets_nothing(tmp_path: Path):
  # A grading run and an audit used to be handed an agent binary they never
  # execed, because the backend provisioned one unconditionally.
  sandbox = DockerHostSandbox(spec=SPEC, workspace=epath.Path(tmp_path / "ws"))
  assert sandbox.asset_observer(()) is None
  assert (
      GitHubJobSandbox(
          spec=SPEC, workspace=epath.Path(tmp_path / "ws2")
      ).asset_observer(())
      is None
  )


def test_several_assets_are_placed_together(tmp_path: Path):
  # Codex needs two binaries in one directory (the second is spawned from a
  # path derived as a sibling), and the seam carries that with no special case.
  first, _ = _asset(tmp_path, name="one")
  second, _ = _asset(tmp_path, name="two")
  sandbox = DockerHostSandbox(spec=SPEC, workspace=epath.Path(tmp_path / "ws"))
  observer = sandbox.asset_observer((first, second))
  assert observer is not None
  assert set(observer.mounts()) == {"/opt/one/one", "/opt/two/two"}


def test_the_materializer_is_kept_out_of_repr(tmp_path: Path):
  # A closure in a repr is noise at best; the path is the useful part.
  asset, _ = _asset(tmp_path)
  assert "materialize" not in repr(asset)
  assert "/opt/agent/agent" in repr(asset)


@pytest.mark.parametrize("harness_name", ["claude_code", "codex", "grok_build"])
def test_every_shipped_harness_declares_its_own_binaries(harness_name: str):
  # The declaration is what the backend consumes, so a harness that forgot it
  # would run against a container with no agent in it.
  from swe_lab.harnesses import build_harness
  import swe_lab.workflow.definitions as definitions

  assert definitions.ROLLOUT_KEY  # imported for its registrations
  assets = build_harness(harness_name).assets()
  assert assets, harness_name
  for asset in assets:
    assert asset.path.startswith("/"), asset.path  # absolute: machinery


def test_the_rollout_task_forwards_its_harness_declaration():
  # The one line that joins the two halves.
  from swe_lab.harnesses.codex import CodexHarness
  from swe_lab.rollout import CodingAgentTask

  harness = CodexHarness()
  task = CodingAgentTask(harness=harness)
  assert [a.path for a in task.assets()] == [a.path for a in harness.assets()]
  # Codex is the two-binary case, carried with no special case anywhere.
  assert len(task.assets()) == 2


def test_a_grading_task_declares_nothing():
  from swe_lab.evaluation.unit_test import UnitTestTask

  assert list(UnitTestTask().assets()) == []


# ─── the shared harness helpers (one implementation, three agents) ───────────


def test_every_harness_uses_the_shared_info_observer():
  """Three harnesses had grown their own, near-identical copy.

  What legitimately differs is *what to probe* and *what to call the
  artifact*, so those are parameters; the capture, the never-fail guarantee
  and the registration are shared.
  """
  from swe_lab.harnesses import build_harness
  from swe_lab.harnesses.common import AgentInfoObserver
  import swe_lab.workflow.definitions as definitions

  assert definitions.ROLLOUT_KEY  # imported for its registrations
  seen: dict[str, tuple[str, ...]] = {}
  for name in ("claude_code", "codex", "grok_build"):
    harness = build_harness(name)
    (info,) = [
        o for o in harness.observers() if isinstance(o, AgentInfoObserver)
    ]
    assert info.artifact.startswith(name.split("_")[0])
    seen[name] = tuple(info.probes)

  # Version and help everywhere; Codex adds the subcommand that IS its
  # surface, since the generic --help does not explain an `exec` run.
  assert seen["claude_code"] == ("--version", "--help")
  assert seen["grok_build"] == ("--version", "--help")
  assert seen["codex"] == ("--version", "--help", "exec --help")


def test_the_env_renderer_is_shared_and_refuses_a_bad_name():
  from swe_lab.harnesses.common import env_exports
  from swe_lab.sandbox import SandboxError

  assert env_exports({"A": "1", "B": "x y"}) == "export A=1\nexport B='x y'\n"
  with pytest.raises(SandboxError, match="invalid environment variable"):
    _ = env_exports({"not a name": "v"})


def test_the_workspace_reader_is_absence_tolerant(tmp_path: Path):
  # A crashed run leaves no trace file, and the callers must report an outcome
  # rather than raise.
  from swe_lab.harnesses.common import read_text
  from swe_lab.sandbox import SandboxSpec
  from swe_lab.sandbox.testing import FakeSandbox

  sb = FakeSandbox(
      spec=SandboxSpec("x", "img", "/app", "base"),
      workspace=epath.Path(tmp_path),
  )
  assert read_text(sb, "nope.txt") == ""
  sb.write("there.txt", b"hi")
  assert read_text(sb, "there.txt") == "hi"
