"""The DeepSWE instance record: one parquet row, runnable (task-30 §2).

Both a ``DatasetRecord`` (the loader parses it) and a ``TaskInstance`` (the
CLIs run it), mirroring ``SweBenchProInstance``. ``COLUMNS`` is imported from
the builder — producer and consumer assert against the same list, so the
schema cannot drift between them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, override

from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import SandboxSpec
from swe_lab.sandbox.observers import PATCH_NAME

from .build_parquet import COLUMNS as _BUILDER_COLUMNS
from .unit_test import compile_unit_test, DeepSweVerdict

# The repo path inside every task image — Harbor's convention, verified on a
# real image in task-30 §1.
WORKDIR = "/app"


@dataclass(frozen=True)
class DeepSweInstance(TaskInstance[DeepSweVerdict]):
  """A single DeepSWE task with normalized, typed fields.

  Attributes mirror the parquet columns (see the builder, the schema's one
  home). ``base_commit`` is the normalized full sha; ``base_commit_hash``
  keeps the upstream value verbatim, different only for the three tasks the
  census caught with abbreviated values.
  """

  COLUMNS: ClassVar[tuple[str, ...]] = _BUILDER_COLUMNS

  # The ABC's identity attribute; fed from the parquet's `task_id` column
  # (upstream's name for the same thing), which stays readable as `task_id`.
  instance_id: str
  ext_id: str
  display_title: str
  display_description: str
  category: str
  language: str
  repository_url: str
  base_commit_hash: str
  base_commit: str
  docker_image: str
  agent_timeout_sec: float
  verifier_timeout_sec: float
  cpus: int
  memory_mb: int
  storage_mb: int
  instruction: str
  test_sh: str
  grader_py: str
  config_json: str
  test_patch: str
  solution_patch: str
  solve_sh: str
  f2p: tuple[str, ...]
  p2p: tuple[str, ...]
  upstream_repo: str
  upstream_license: str

  @property
  def task_id(self) -> str:
    """Upstream's name for :attr:`instance_id` — the parquet column."""
    return self.instance_id

  @classmethod
  def from_raw(cls, raw: Mapping[str, Any]) -> DeepSweInstance:
    """Parse one parquet row.

    Args:
      raw: The row, keyed by column name.

    Returns:
      The typed record.
    """
    fields = {name: raw[name] for name in cls.COLUMNS}
    fields["instance_id"] = fields.pop("task_id")
    fields["f2p"] = tuple(fields["f2p"])
    fields["p2p"] = tuple(fields["p2p"])
    return cls(**fields)

  @override
  def sandbox_spec(self) -> SandboxSpec:
    """Return the run context — image, ``/app``, the normalized base sha."""
    return SandboxSpec(
        self.instance_id, self.docker_image, WORKDIR, self.base_commit
    )

  @override
  def prompt(self) -> str:
    """Return ``instruction.md`` verbatim, upstream footer included."""
    return self.instruction

  @override
  def gold_patch(self) -> str | None:
    """Return the held-out reference solution."""
    return self.solution_patch

  @override
  def required_tests(self) -> Sequence[str]:
    """Return the graded test ids — fail-to-pass plus pass-to-pass."""
    return (*self.f2p, *self.p2p)

  @override
  def solution_sha(self) -> str | None:
    """Return ``None``: DeepSWE tasks are original, no upstream fix exists."""
    return None

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
      patch_baseline: bool = False,
  ) -> UnitTestSpec[DeepSweVerdict]:
    """Compile this instance's unit-test spec (their verifier, verbatim).

    Args:
      apply_patch: Grade the workspace patch; ``False`` grades the base state
        (reward 0 by construction upstream).
      patch_name: The workspace file the patch arrives as.
      checkout_golden_tests: Accepted for signature compatibility and
        irrelevant here: the held-out tests arrive via ``test.patch``, which
        their grader applies after the candidate patch, resetting the files
        it touches first — the same tamper protection the checkout gives
        SWE-Bench Pro.
      patch_baseline: Refused by the compiler — their grader consumes
        ``base_commit``-relative patches only (task-30 §3).

    Returns:
      The compiled spec.
    """
    del checkout_golden_tests
    return compile_unit_test(
        apply_patch=apply_patch,
        patch_name=patch_name,
        patch_baseline=patch_baseline,
        test_sh=self.test_sh,
        grader_py=self.grader_py,
        config_json=self.config_json,
        test_patch=self.test_patch,
    )

  @override
  def run_provenance(self) -> dict[str, object]:
    """Return the facts a reader needs to interpret a result."""
    return {
        "dataset": "deepswe",
        "language": self.language,
        "upstream_repo": self.upstream_repo,
        "upstream_license": self.upstream_license,
        "repository_url": self.repository_url,
        "ext_id": self.ext_id,
    }
