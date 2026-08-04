"""End-to-end workflow smoke over real Docker containers.

The chain the CLIs will run — producer task → store → consumer task — with
nothing faked: two real containers (one per entry), a real script writing the
artifact, the engine fetching it out, ``run_task`` persisting it, and the
edge staging it into the second container read-only. Auto-skipped where
Docker is absent (see ``conftest.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any, final, override

from etils import epath
import pytest

from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.swebench_pro.unit_test import SweBenchProVerdict
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import (
    ArtifactSchema,
    Contribution,
    DockerHostSandboxConfig,
    ExecResult,
    FilesystemStore,
    Inline,
    Mount,
    Mounts,
    SandboxConfig,
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
)
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.workflow import (
    EntryStatus,
    Task,
    Workflow,
    WorkflowEntry,
)


def _on(wf: Workflow, sandbox: SandboxConfig) -> Workflow:
  """Run every entry of ``wf`` on ``sandbox`` — entries declare where they run.

  Args:
    wf: The workflow to place.
    sandbox: The config (and therefore the backend) every entry gets.

  Returns:
    A copy whose entries all declare ``sandbox``.
  """
  return replace(wf, entries=[replace(e, sandbox=sandbox) for e in wf.entries])


_IMAGE = "debian:stable-slim"
SPEC = SandboxSpec("workflow-smoke", _IMAGE, "/", "none")


@final
@dataclass(frozen=True)
class _Instance(TaskInstance[SweBenchProVerdict]):
  instance_id: str = "workflow-smoke"

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return SPEC

  @override
  def prompt(self) -> str:
    return "SOLVE THIS"

  @override
  def gold_patch(self) -> str | None:
    return None

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
  ) -> UnitTestSpec[SweBenchProVerdict]:
    raise NotImplementedError


@final
@dataclass
class _Collect(SandboxObserver):
  """Declares ``name`` and registers it from the workspace after the run."""

  name: str

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    return (ArtifactSchema(self.name, description="a produced file"),)

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    # File-based on purpose (the engine must fetch it out of the container),
    # and best-effort like the production observers: register only what
    # landed, so a failed run yields fewer artifacts, never a broken fetch.
    if not sb.exists(self.name):
      return None
    return Contribution(artifacts={self.name: self.name})


@final
@dataclass
class _ScriptTask(Task):
  """Stages a script, runs it, collects the file it wrote."""

  script: str
  produces: str
  consumes: str = ""

  @override
  def mounts(self, instance: TaskInstance[Any]) -> Mounts:
    del instance
    return {"main.sh": Mount(Inline(self.script.encode()), executable=True)}

  @override
  def observers(
      self, instance: TaskInstance[Any]
  ) -> tuple[SandboxObserver, ...]:
    del instance
    return (_Collect(name=self.produces),)

  @override
  def input_schema(self) -> tuple[ArtifactSchema, ...]:
    if not self.consumes:
      return ()
    return (ArtifactSchema(self.consumes, description="the upstream file"),)

  @override
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    del instance
    return sb.run_script("main.sh", timeout=timeout)


@pytest.mark.docker
def test_live_two_container_chain_through_the_store(tmp_path: Path):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  producer = _ScriptTask(
      script='printf HELLO > "$SANDBOX_WORKSPACE"/thing.txt\n',
      produces="thing.txt",
  )
  consumer = _ScriptTask(
      # proves the input landed AND is the exact bytes: transform it so the
      # output could only come from the mounted upstream artifact
      script=(
          'tr "A-Z" "a-z" < "$SANDBOX_WORKSPACE"/thing.txt'
          ' > "$SANDBOX_WORKSPACE"/consumed.txt\n'
      ),
      produces="consumed.txt",
      consumes="thing.txt",
  )
  wf = Workflow(
      store=store,
      sweep_id="smoke",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              "producer",
              producer,
              timeout=60.0,
          ),
          WorkflowEntry(
              "consumer",
              consumer,
              timeout=60.0,
          ),
      ],
  )
  outcome = _on(wf, DockerHostSandboxConfig()).execute(
      _Instance(),
      output_dir=tmp_path / "out",
      run_ts="ts-live",
  )
  assert outcome.succeeded is True
  assert [e.status for e in outcome.entries] == [
      EntryStatus.SUCCEEDED,
      EntryStatus.SUCCEEDED,
  ]
  # the consumer's output exists in the store and is the transformed bytes —
  # it can only have come from the producer's artifact via the edge
  assert (
      store.get_bytes("smoke/workflow-smoke/r0/consumer/a0/consumed.txt")
      == b"hello"
  )
  # both terminal markers and the workflow record are durable
  assert store.get_bytes("smoke/workflow-smoke/r0/producer/complete.json")
  assert store.get_bytes("smoke/workflow-smoke/r0/consumer/complete.json")
  assert outcome.record_key is not None
  record = json.loads(store.get_bytes(outcome.record_key))
  assert record["edges"]["consumer"] == {"thing.txt": "producer"}


@pytest.mark.docker
def test_live_failed_producer_persists_its_evidence_and_blocks(
    tmp_path: Path,
):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  # declares thing.txt but exits before writing it → invalid attempt
  broken = _ScriptTask(
      script='echo "boom: disk exploded" >&2\nexit 7\n',
      produces="thing.txt",
  )
  consumer = _ScriptTask(
      script="true\n",
      produces="consumed.txt",
      consumes="thing.txt",
  )
  wf = Workflow(
      store=store,
      sweep_id="smoke",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              "producer",
              broken,
              timeout=60.0,
          ),
          WorkflowEntry(
              "consumer",
              consumer,
              timeout=60.0,
          ),
      ],
  )
  outcome = _on(wf, DockerHostSandboxConfig()).execute(
      _Instance(),
      output_dir=tmp_path / "out",
      run_ts="ts-live",
  )
  assert outcome.succeeded is False
  assert [e.status for e in outcome.entries] == [
      EntryStatus.FAILED,
      EntryStatus.BLOCKED,
  ]
  # the failed attempt is fully debuggable FROM THE STORE: its shard exists,
  # says the outputs were invalid, and the marker is terminal-failed
  shards = store.read_manifest("smoke", "workflow-smoke", 0, task="producer")
  assert len(shards) == 1
  assert shards[0].extra["outputs_valid"] is False
  marker = json.loads(
      store.get_bytes("smoke/workflow-smoke/r0/producer/complete.json")
  )
  assert marker["outcome"] == "failed"
  # the roll-up is written for a failed run too, and says so (ADR-0009)
  assert outcome.record_key is not None
  assert json.loads(store.get_bytes(outcome.record_key))["succeeded"] is False
