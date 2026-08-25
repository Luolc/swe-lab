"""The dataset-agnostic runnable-instance interface.

Nothing downstream of a dataset should import a concrete one. Instead, a
dataset's record exposes *what this instance is and where it runs* through
:class:`TaskInstance`: the sandbox context, the prompt stating the task, the
reference solution if the dataset has one, and its compiled unit-test eval.
Callers resolve an instance by name (``load_dataset(name).require(id)``) and use
these methods polymorphically — supporting a new dataset is a new record type
implementing this ABC, with no change to anything built on top.

What a caller *does* with an instance is its own business: drive an agent
against the task, grade a candidate solution, generate or annotate data from the
same run context. This interface only says what every dataset must be able to
answer for any of that to be possible.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from swe_lab.evaluation.verdict import UnitTestSpec, Verdict
from swe_lab.sandbox import Mounts, SandboxSpec
from swe_lab.sandbox.observers import PATCH_NAME


class TaskInstance[V: Verdict](ABC):
  """A dataset instance that can be run in a sandbox (ADR-0002 ABC).

  A behavior interface implemented by a dataset's record type (which is also a
  ``DatasetRecord`` for the loader). Generic over the dataset's verdict type so
  ``unit_test_spec`` returns a correctly-typed spec; a consumer that only reads
  a result uses the base ``Verdict`` surface (``score`` / ``resolved`` /
  ``summary``).

  Only ``unit_test_spec`` is specific to grading. The rest describes the
  instance itself and is equally what a generation, annotation, or agent-driving
  workflow needs.
  """

  instance_id: str  # provided by the concrete record

  @abstractmethod
  def sandbox_spec(self) -> SandboxSpec:
    """Return the run context (image / workdir / base commit) to run in."""
    ...

  def required_tests(self) -> Sequence[str]:
    """Return the tests this instance is graded on, when the dataset knows them.

    Used only by the integrity rules, to notice a patch that hardcodes a
    required test's name instead of implementing the behavior. Default: empty,
    which disables that one rule and nothing else.

    Returns:
      Every test that must pass, or empty when the dataset records none.
    """
    return ()

  def solution_sha(self) -> str | None:
    """Return the upstream commit that fixed this instance, if it is known.

    Used to *assert its absence*: the images ship the whole upstream history,
    so an agent that reaches this commit can read the reference solution
    (ADR-0010). The history purge deletes it and then checks it is gone, and
    this is the only exact way to make that check.

    Default: ``None``, which is honest for a dataset that does not record one.
    The purge's other assertion — that nothing reachable postdates the base
    commit — still runs, and is the load-bearing one precisely because it does
    not need this.

    Returns:
      The 40-character fix commit sha, or ``None`` when the dataset has none.
    """
    return None

  def mounts(self) -> Mounts:
    """Return the dataset's own material to stage for a run of this instance.

    The instance is one of the mount sources (ADR-0007 §2) and decides for
    itself whether it stages anything. Default: nothing — right for a
    solving-only dataset, or one whose material is baked into the image.

    Names are workspace-relative, like every other contributor's;
    ``merge_mounts`` refuses duplicate targets across sources.

    Returns:
      Workspace-relative name → mount; empty by default.
    """
    return {}

  @abstractmethod
  def prompt(self) -> str:
    """Return the dataset-derived prompt stating this instance's task."""
    ...

  @abstractmethod
  def gold_patch(self) -> str | None:
    """Return the instance's own reference (gold) patch.

    Returns:
      The reference diff, or ``None`` when the dataset carries no reference
      solution — an unsolved corpus, or one collected for annotation rather
      than for grading. A caller that requires one has to say so: ``None`` is
      *not* interchangeable with the ``apply_patch=False`` that
      :meth:`unit_test_spec` accepts, which means "grade the base commit" and
      is a perfectly gradeable request.
    """
    ...

  @abstractmethod
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
      patch_baseline: bool = False,
  ) -> UnitTestSpec[V]:
    """Compile this instance's unit-test evaluation spec.

    Pair it with :meth:`sandbox_spec` for the run context (that is not returned
    here — it is the same context solving uses).

    The **bytes of the patch are not this method's business**: they arrive in
    the workspace as the eval task's declared input, from wherever the caller
    got them (a workflow edge, a file, the gold patch). What is compiled here
    is only whether the script applies one, and which file it reads.

    Args:
      apply_patch: Compile the script to apply the workspace file named
        ``patch_name``. ``False`` grades the base commit untouched (a
        self-check that the required tests fail without a fix).
      patch_name: Which workspace file the script applies; the default is the
        store name the extraction side produces.
      checkout_golden_tests: Restore the held-out golden test files after the
        reset (so a candidate patch cannot game them).
      patch_baseline: Grade against the recomputed **pre-agent baseline**
        instead of resetting to the base commit — the grading half of
        ``patch_baseline`` on the rollout side (ADR-0001, 2026-08-25
        amendment). The compiled script must recompute the baseline with the
        shared pinned commands, verify its sha against the run's recorded
        ``patch.base_ref.txt``, and reset to *it* before applying. A dataset
        that has not implemented this mode should refuse it loudly rather
        than accept-and-ignore it.

    Returns:
      The compiled unit-test spec.
    """
    ...

  def run_provenance(self) -> dict[str, object]:
    """Return facts a reader needs to interpret this instance's result.

    Anything that makes a verdict mean something other than "the patch worked"
    — an environment fix the harness applied, a measured flake rate the
    instance is known to have. It travels into the run record and the CLI
    summary, because a reader has only those to go on and cannot recover from
    a bare ``resolved: false`` that the instance fails a quarter of the time
    regardless.

    Datasets with nothing to declare inherit the empty default.

    Returns:
      JSON-serializable facts, empty when there are none.
    """
    return {}
