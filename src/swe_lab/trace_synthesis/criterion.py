"""The judge's criterion: one committed artifact, identical for every instance.

:class:`~swe_lab.trace_synthesis.supervisor.Observation` guards the entrance
facing the actor. The judge has a second one — the standard it measures
against — and material derived from the answer pierces the barrier from behind:
a criterion written for one instance by someone who has read its fix steers the
actor down that fix's path without ever quoting it, and no field check on the
observation can see it, because it never travels that channel.

The rule this module enforces is therefore mechanical rather than a statement
about anyone's care: **the criterion is a named, committed artifact that is
byte-identical for every instance**, and a material identical across instances
cannot encode instance-specific knowledge.

When the check fires, that is the design working. The day a per-instance
criterion is genuinely wanted, the digest stops matching and the run stops —
the moment a person has to re-examine the barrier, deliberately loud so that
routing around it takes a visible decision rather than a quiet edit.
"""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib
import re

CRITERION_PATH = (
    pathlib.Path(__file__).parent / "criteria" / "general-practice.md"
)
"""The artifact. One file, in the repository, shipped with the package."""

CRITERION_SHA256 = (
    "ffb2dadfe2b36eb3f44f28c4282a8d51e84e1c943558500787cbb0518e2900a1"
)
"""The pinned digest of :data:`CRITERION_PATH`.

Changing the criterion without changing this constant stops every run, which is
the point: the two move together only by someone's decision.
"""

# Long enough that ordinary English prose does not collide by chance, short
# enough to catch a quoted line of a patch.
SHINGLE_WORDS = 8

_DIFF_PATH = re.compile(r"^(?:\+\+\+|---) [ab]/(.+)$", re.MULTILINE)
_WORD = re.compile(r"\S+")


class CriterionRejectedError(RuntimeError):
  """Raised when the criterion is not the pinned, instance-independent one.

  Deliberately not a recorded gap: with the barrier broken there is no
  experiment left to run, so the run refuses to start.
  """


@dataclasses.dataclass(frozen=True)
class Criterion:
  """The criterion text and an honest account of what was checked.

  Attributes:
    text: The artifact's contents.
    overlap_checked: Whether the redundant gold-patch overlap check ran. It
      needs the gold patch to be available where the check runs; for a dataset
      that records none it cannot run, and the digest carries the invariant
      alone. That is a weaker state, and a run says so rather than reporting a
      check it did not perform.
  """

  text: str
  overlap_checked: bool


def _shingles(text: str) -> set[tuple[str, ...]]:
  """Return the set of word n-grams in a text.

  Args:
    text: Any text.

  Returns:
    Every window of :data:`SHINGLE_WORDS` consecutive lowercased words.
  """
  words = [match.group().lower() for match in _WORD.finditer(text)]
  return {
      tuple(words[index : index + SHINGLE_WORDS])
      for index in range(len(words) - SHINGLE_WORDS + 1)
  }


def load_criterion(
    *,
    gold_patch: str | None = None,
    path: pathlib.Path = CRITERION_PATH,
    digest: str = CRITERION_SHA256,
) -> Criterion:
  """Load the criterion, or refuse to start.

  Args:
    gold_patch: This instance's gold patch, when the dataset records one. Given
      it, the redundant half runs: the criterion may share no changed file path
      and no ``SHINGLE_WORDS``-word run with the patch.
    path: The artifact to load. A parameter so a test can point at a forged
      criterion; production never passes it.
    digest: The digest to require. A parameter for the same reason.

  Returns:
    The criterion, with a flag saying whether the overlap half ran.

  Raises:
    CriterionRejectedError: The artifact is missing, its digest does not match,
      or it overlaps the gold patch.
  """
  try:
    raw = path.read_bytes()
  except OSError as error:
    raise CriterionRejectedError(
        f"criterion unreadable at {path}: {error}"
    ) from error

  found = hashlib.sha256(raw).hexdigest()
  if found != digest:
    raise CriterionRejectedError(
        f"criterion digest {found} does not match the pinned {digest}; the"
        " criterion must be byte-identical for every instance"
    )

  text = raw.decode("utf-8")
  if gold_patch is None:
    return Criterion(text=text, overlap_checked=False)

  shared_paths = sorted(
      changed
      for changed in set(_DIFF_PATH.findall(gold_patch))
      if changed in text
  )
  if shared_paths:
    raise CriterionRejectedError(
        f"criterion names {len(shared_paths)} path(s) changed by the gold patch"
    )

  shared = _shingles(text) & _shingles(gold_patch)
  if shared:
    raise CriterionRejectedError(
        f"criterion shares {len(shared)} {SHINGLE_WORDS}-word run(s) with the"
        " gold patch"
    )

  return Criterion(text=text, overlap_checked=True)
