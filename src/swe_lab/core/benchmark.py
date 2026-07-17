"""Dataset-agnostic contracts for running the benchmark (eval + rollout).

The general flows (``evaluation``, ``rollout``, ``core.docker``) depend only on
these contracts; each dataset provides an *adapter* that builds them from its
own records (see ``core.datasets.swebench_pro.execution``). Adding a dataset is
adding an adapter — the flows never learn a dataset's specifics (image location,
harness source, path conventions).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar


@dataclass(frozen=True)
class EvalSpec:
  """Everything the eval flow needs to grade one instance in its container.

  Dataset-agnostic: the SWE-Bench-Pro-specific bits (which image, where the repo
  lives, which test runner) are already resolved into these fields by the
  dataset's adapter.
  """

  instance_id: str
  image_ref: str
  workdir: str  # where the repo lives inside the image (e.g. /app)
  base_commit: str
  before_repo_set_cmd: str  # extra prep to run after checkout (may be empty)
  run_script: str  # test-invocation script, content (bash)
  parser: str  # test-output -> structured results, content (python)
  fail_to_pass: tuple[str, ...]
  pass_to_pass: tuple[str, ...]
  selected_tests: tuple[str, ...]

  @property
  def required_tests(self) -> frozenset[str]:
    return frozenset(self.fail_to_pass) | frozenset(self.pass_to_pass)

  def is_resolved(self, passed: frozenset[str] | set[str]) -> bool:
    """Resolved iff every FAIL_TO_PASS and PASS_TO_PASS test passed."""
    return self.required_tests <= frozenset(passed)


_Record = TypeVar("_Record", contravariant=True)


class BenchmarkAdapter(Protocol[_Record]):
  """Builds executable specs from a dataset's records; one impl per dataset."""

  def eval_spec(self, instance: _Record) -> EvalSpec:
    """The eval spec for one instance (resolves image, fetches harness, …)."""
    ...
