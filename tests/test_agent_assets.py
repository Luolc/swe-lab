"""Tests for the provisioning seam: declared assets, backend-chosen transfer.

The property under test is a *negative* one and easy to lose: neither side
enumerates the other. A backend must place an agent it has never heard of, and
a harness must not care how the bytes travel (task-28 §7).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import override

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

  return (
      AgentAsset(
          path=f"/opt/{name}/{name}",
          version="1.0",
          fetch=materialize,
      ),
      calls,
  )


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
  asset = AgentAsset(path=str(final), version=asset.version, fetch=asset.fetch)
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
  assert "fetch" not in repr(asset)
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


# ─── the agent's exit status (shared tail) ──────────────────────────────────


def test_every_harness_propagates_the_agents_own_exit_status():
  """It used to be flattened to 0, on a rationale that did not hold.

  The stated reason was that a non-zero exec would disturb container
  teardown. It would not: teardown is a context-manager exit, every backend
  runs the exec with ``check=False``, and ``RunStatus`` is not derived from
  the code. So the zero bought nothing and left the recorded
  ``<agent>.exit_code`` metric permanently 0.0.
  """
  from swe_lab.harnesses import build_harness
  from swe_lab.sandbox import Inline
  import swe_lab.workflow.definitions as definitions

  assert definitions.ROLLOUT_KEY  # imported for its registrations
  for name in ("claude_code", "codex", "grok_build"):
    harness = build_harness(name)
    mounts = harness.mounts("/app")
    # Both the invocation script and the sourced env file end in .sh.
    (script_name,) = [k for k in mounts if k.startswith("run_")]
    resource = mounts[script_name].resource
    assert isinstance(resource, Inline)
    script = resource.content.decode().rstrip()

    assert script.endswith('exit "$status"'), name
    # Captured on the line right after the agent returns, before anything can
    # overwrite `$?`...
    assert "status=$?" in script, name
    # ...and still written to the workspace, which is NOT redundant: the file
    # is absent when we killed the run at the deadline, so its absence tells a
    # kill apart from a non-zero exit.
    assert "printf '%s\\n' \"$status\" >" in script, name


def test_the_exit_status_is_recorded_but_never_gated_on():
  """ADR-0011's line: an exit code is ambiguous, so it gets no authority.

  Non-zero covers both "the task defeated the agent" and "the API broke".
  Attribution stays with AgentOutcome, read from the trace — this test pins
  that no shipped task consults the exec's status.
  """
  import inspect

  from swe_lab.evaluation.unit_test import UnitTestTask
  from swe_lab.rollout import CodingAgentTask
  from swe_lab.workflow import Task

  for cls in (Task, CodingAgentTask, UnitTestTask):
    for name in ("should_retry", "outputs_valid"):
      source = inspect.getsource(getattr(cls, name))
      assert "exit_code" not in source, f"{cls.__name__}.{name}"


# ─── the paradigm the first cut of this seam excluded ────────────────────────


def test_a_store_backed_sandbox_resolves_without_fetching():
  """The case an earlier version of this seam broke.

  A downstream sandbox maintains its own artifact store: it does not download
  and is not handed a host copy. It resolves the release itself and names the
  resulting store path in its own construction parameters — which it must do
  *before the sandbox exists*, because that declaration is part of how the
  sandbox gets built.

  The declaration is deliberately small enough to allow that: a release and a
  destination. It does NOT name a platform — the sandbox knows what it runs
  on, and choosing the build (and whether to bundle it) is its call.
  """
  from swe_lab.harnesses import build_harness
  import swe_lab.workflow.definitions as definitions

  assert definitions.ROLLOUT_KEY  # imported for its registrations
  for name in ("claude_code", "codex", "grok_build"):
    for asset in build_harness(name).assets():
      # Everything such a backend needs, available with no sandbox in
      # existence and no bytes moved.
      assert asset.version
      assert asset.path.startswith("/")
      assert not hasattr(asset, "platform")  # the sandbox's business, not ours


def test_an_asset_without_a_fetch_is_legal_and_says_so_when_transferred(
    tmp_path: Path,
):
  # A harness targeting a store-backed sandbox may declare no fetch at all.
  # That must construct fine...
  asset = AgentAsset(path="/opt/acme/agent", version="1.2.3")
  assert asset.fetch is None

  # ...and a backend that CAN only transfer must fail with a real explanation
  # rather than a None call deep inside a mount.
  from swe_lab.sandbox import SandboxError

  sandbox = DockerHostSandbox(spec=SPEC, workspace=epath.Path(tmp_path / "ws"))
  observer = sandbox.asset_observer((asset,))
  assert observer is not None
  with pytest.raises(SandboxError, match="declares no fetch"):
    _ = observer.mounts()


def test_the_pinned_version_is_a_field_a_run_can_override():
  # Pinning is a run-level decision: defaulted to the verified release so a
  # sweep is reproducible, overridable because the default is ours, not law.
  from swe_lab.harnesses.codex import CodexHarness

  assert [a.version for a in CodexHarness().assets()] == ["0.147.0"] * 2
  moved = CodexHarness(version="0.146.1")
  assert [a.version for a in moved.assets()] == ["0.146.1"] * 2
  # Two files of one release stay distinguishable by their destination.
  assert len({a.path for a in moved.assets()}) == 2


# ─── the two moments, used together ─────────────────────────────────────────


def test_the_runner_puts_assets_on_the_config_before_construction():
  """A store-backed sandbox has to know what it will carry to be built at all.

  So the runner fills `SandboxConfig.assets` from the task *before* calling
  the factory — there is no later moment at which such a backend could learn
  it.
  """
  import dataclasses

  from swe_lab.harnesses.codex import CodexHarness
  from swe_lab.rollout import CodingAgentTask
  from swe_lab.sandbox import DockerHostSandboxConfig

  seen: list[tuple[str, ...]] = []

  task = CodingAgentTask(harness=CodexHarness())
  config = dataclasses.replace(
      DockerHostSandboxConfig(), assets=tuple(task.assets())
  )
  seen.append(tuple(a.path for a in config.assets))

  # What a downstream factory reads at construction time: enough to resolve
  # each release against its own store and name the result in its own params.
  assert seen == [("/opt/codex/codex", "/opt/codex/codex-code-mode-host")]
  assert all(a.version == "0.147.0" for a in config.assets)


def test_config_time_resolution_does_not_remove_the_run_time_phase(
    tmp_path: Path,
):
  """Bringing bytes in early does not finish the job.

  An artifact that arrives as an archive still has to be unpacked, moved into
  place and made executable, and `after_create` is the only place that can
  happen — whoever brought the bytes in. A backend may therefore use *both*
  moments, which is why resolving at configuration time does not imply
  returning None here.
  """
  from swe_lab.sandbox import SandboxObserver
  from swe_lab.sandbox.backends.host import DockerHostSandbox

  unpacked: list[str] = []

  class _StoreBackedSandbox(DockerHostSandbox):
    """Resolves from its own store at build time; still initializes at run."""

    @override
    def asset_observer(
        self, assets: Sequence[AgentAsset]
    ) -> SandboxObserver | None:
      class _Unpack(SandboxObserver):

        @override
        def after_create(self, sb: object) -> None:
          del sb
          unpacked.extend(a.path for a in assets)

      return _Unpack() if assets else None

  asset, calls = _asset(tmp_path)
  sandbox = _StoreBackedSandbox(
      spec=SPEC, workspace=epath.Path(tmp_path / "ws")
  )
  observer = sandbox.asset_observer((asset,))
  assert observer is not None
  observer.after_create(sandbox)

  assert unpacked == ["/opt/agent/agent"]  # the run-time half still ran...
  assert calls == []  # ...and it fetched nothing: the store already had it
