"""DeepSWE unit-test grading: run Datacurve's verifier verbatim (task-30 §2).

The whole eval side is *their* code: mount the four verifier files at
``/tests``, stage the candidate patch where their grader reads it, run
``bash /tests/test.sh``, and read back ``reward.json``. Nothing of their
patch-application or scoring logic is reimplemented — proven equivalent to
their baked verifier image by the gold round trip in task-30 §1 (their
``tests/Dockerfile`` is the task image plus a COPY of exactly these files).

Attribution maps 1:1 onto swe-lab's rules, because upstream already separates
the cases:

- ``reward.json`` with ``apply_failed: 1`` — the patch did not apply. That is
  **graded** (reward 0, the patch's fault, suites never ran).
- no ``reward.json`` at all — the verifier crashed (their trap writes
  ``reward.txt = -1``). That is an **infrastructure failure**: the grader
  raises, the attempt fails ungraded, and the runner may retry it.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import shlex
from typing import override

from swe_lab.evaluation.verdict import Grader, UnitTestSpec, Verdict
from swe_lab.sandbox import Inline, Mount, Mounts, SandboxError, SandboxFs
from swe_lab.sandbox.observers import PATCH_NAME

# Their grader's default in-container layout (grader.py reads these; each is
# env-overridable there, but keeping the defaults keeps our run byte-identical
# to the upstream verifier's).
TESTS_DIR = "/tests"
ARTIFACTS_DIR = "/logs/artifacts"
VERIFIER_DIR = "/logs/verifier"
MODEL_PATCH_NAME = "model.patch"

# The verifier outputs copied back into the workspace for grading + audit.
REWARD_JSON_NAME = "reward.json"
CTRF_JSON_NAME = "ctrf.json"
RUN_LOG_NAME = "run.log"

ENTRYSCRIPT_NAME = "entryscript.sh"


@dataclass(frozen=True, slots=True)
class DeepSweVerdict(Verdict):
  """The graded outcome of one DeepSWE run, read from ``reward.json``.

  Attributes:
    reward: Upstream's binary verdict — 1 iff there is at least one
      fail-to-pass test, every one passes, and no pass-to-pass fails.
    f2p_total: Fail-to-pass tests the run was held to.
    f2p_passed: How many of those passed.
    p2p_total: Pass-to-pass tests the run was held to.
    p2p_passed: How many of those passed.
    partial: Upstream's combined pass fraction (over both buckets).
    apply_failed: The submitted patch did not apply; suites never ran and the
      counts are the whitelists with zero passes. Graded — a patch that does
      not apply is the patch's fault (unlike a crashed verifier, which raises
      in the grader instead of producing a verdict at all).
  """

  reward: int
  f2p_total: int
  f2p_passed: int
  p2p_total: int
  p2p_passed: int
  partial: float
  apply_failed: bool = False

  @property
  @override
  def score(self) -> float:
    """1.0 iff upstream's binary reward is 1, else 0.0."""
    return 1.0 if self.reward == 1 else 0.0

  @override
  def summary(self) -> dict[str, object]:
    """Return the report detail: the counts and the apply flag."""
    return {
        "f2p": f"{self.f2p_passed}/{self.f2p_total}",
        "p2p": f"{self.p2p_passed}/{self.p2p_total}",
        "partial": self.partial,
        "apply_failed": self.apply_failed,
    }

  @override
  def metrics(self) -> dict[str, float]:
    """Return the aggregatable scalars."""
    return {
        "f2p_total": float(self.f2p_total),
        "f2p_passed": float(self.f2p_passed),
        "p2p_total": float(self.p2p_total),
        "p2p_passed": float(self.p2p_passed),
        "partial": self.partial,
        "apply_failed": float(self.apply_failed),
    }


@dataclass(frozen=True)
class DeepSweGrader(Grader[DeepSweVerdict]):
  """Read the verdict their verifier already computed.

  No judgment happens here — ``grader.py grade`` wrote ``reward.json`` in the
  container and the eval script copied it into the workspace; this parses it.
  """

  @override
  def grade(self, sb: SandboxFs) -> DeepSweVerdict:
    """Parse the copied ``reward.json`` into a verdict.

    Args:
      sb: The workspace to read through.

    Returns:
      The verdict.

    Raises:
      SandboxError: If ``reward.json`` is absent or unreadable — upstream's
        verifier-crash case (their trap writes ``reward.txt = -1`` for it),
        an infrastructure failure that must fail the attempt ungraded rather
        than score an agent zero for a broken verifier.
    """
    if not sb.exists(REWARD_JSON_NAME):
      raise SandboxError(
          "the DeepSWE verifier produced no reward.json — upstream's crash"
          " sentinel (reward.txt = -1), an infrastructure failure, not a"
          " grade; see run.log / the exec logs for what broke"
      )
    raw = sb.read(REWARD_JSON_NAME).decode("utf-8", "backslashreplace")
    try:
      data = json.loads(raw)
    except json.JSONDecodeError as error:
      raise SandboxError(
          f"the DeepSWE verifier's reward.json is unreadable: {error}"
      ) from error
    return DeepSweVerdict(
        reward=int(data["reward"]),
        f2p_total=int(data.get("f2p_total", 0)),
        f2p_passed=int(data.get("f2p_passed", 0)),
        p2p_total=int(data.get("p2p_total", 0)),
        p2p_passed=int(data.get("p2p_passed", 0)),
        partial=float(data.get("partial", 0.0)),
        apply_failed=bool(data.get("apply_failed", 0)),
    )


def _build_eval_script(*, apply_patch: bool, patch_name: str) -> str:
  """Build the entryscript that drives their verifier.

  No reset of any kind: their ``grader.py prepare`` owns patch application,
  per-file resets included. Our script only moves files across the boundary —
  the candidate patch in, the verdict and logs out.

  Args:
    apply_patch: Stage the workspace patch where their grader reads it.
      ``False`` runs the verifier against the base state, which their design
      grades reward-0 by construction — the self-check mode.
    patch_name: The workspace file the patch arrives as.

  Returns:
    The entryscript text, newline-terminated.
  """
  ws = '"$SANDBOX_WORKSPACE"'
  lines = [
      "set -e",
      # An image that sets no HOME breaks toolchains that need one (same
      # hazard the swebench_pro script guards; a fallback, so an image's own
      # HOME wins).
      'export HOME="${HOME:-/root}"',
      f"mkdir -p {ARTIFACTS_DIR} {VERIFIER_DIR}",
  ]
  if apply_patch:
    lines.append(
        f"cp {ws}/{shlex.quote(patch_name)} {ARTIFACTS_DIR}/{MODEL_PATCH_NAME}"
    )
  lines += [
      # The verifier owns its own error handling (its trap writes the crash
      # sentinel); a failing test suite must reach OUR copy-back either way.
      "set +e",
      f"bash {TESTS_DIR}/test.sh",
      "verifier_status=$?",
      "set -e",
      *(
          f"cp {VERIFIER_DIR}/{name} {ws}/ 2>/dev/null || true"
          for name in (REWARD_JSON_NAME, CTRF_JSON_NAME, RUN_LOG_NAME)
      ),
      # Recorded, never gated on — the grader reads reward.json, not this.
      'exit "$verifier_status"',
  ]
  return "\n".join(lines) + "\n"


def compile_unit_test(
    *,
    apply_patch: bool,
    patch_name: str = PATCH_NAME,
    patch_baseline: bool = False,
    test_sh: str,
    grader_py: str,
    config_json: str,
    test_patch: str,
) -> UnitTestSpec[DeepSweVerdict]:
  """Compile one DeepSWE instance's unit-test spec.

  The four verifier files are mounted read-only at their upstream paths, so
  the container state matches their baked verifier image exactly (task-30 §1:
  that image is the task image plus a COPY of these files).

  Args:
    apply_patch: Grade the workspace patch; ``False`` grades the base state
      (reward 0 by construction upstream — the self-check).
    patch_name: The workspace file the script stages for their grader.
    patch_baseline: Refused. Their grader per-file resets touched files to
      ``base_commit``, so it consumes ``base_commit``-relative patches only —
      a baseline-relative patch would hit restored preimages and mis-grade as
      ``apply_failed`` (task-30 §3).
    test_sh: The task's ``tests/test.sh``.
    grader_py: The shared ``tests/grader.py``.
    config_json: The task's ``tests/config.json``.
    test_patch: The held-out tests, ``tests/test.patch``.

  Returns:
    The compiled spec.

  Raises:
    ValueError: If ``patch_baseline`` is requested — accepting-and-ignoring
      it would silently mis-grade every run that overlaps image state.
  """
  if patch_baseline:
    raise ValueError(
        "DeepSWE's verifier grades base_commit-relative patches (per-file"
        " reset to base_commit, then apply); baseline mode would mis-grade —"
        " use the default extraction (task-30 §3)"
    )
  mounts: Mounts = {
      f"{TESTS_DIR}/test.sh": Mount(Inline(test_sh.encode())),
      f"{TESTS_DIR}/grader.py": Mount(Inline(grader_py.encode())),
      f"{TESTS_DIR}/config.json": Mount(Inline(config_json.encode())),
      f"{TESTS_DIR}/test.patch": Mount(Inline(test_patch.encode())),
  }
  return UnitTestSpec(
      eval_script=_build_eval_script(
          apply_patch=apply_patch, patch_name=patch_name
      ),
      mounts=mounts,
      grader=DeepSweGrader(),
      patch_name=patch_name,
      native_outputs={
          REWARD_JSON_NAME: REWARD_JSON_NAME,
          CTRF_JSON_NAME: CTRF_JSON_NAME,
          RUN_LOG_NAME: RUN_LOG_NAME,
      },
  )
