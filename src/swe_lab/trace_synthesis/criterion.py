"""The judge's criterion: one committed artifact, identical for every instance.

:class:`~swe_lab.trace_synthesis.supervisor.Observation` guards the entrance
facing the actor. The judge has a second one — the standard it measures
against — and material derived from the answer pierces the barrier from behind:
a criterion written for one instance by someone who has read its fix steers the
actor down that fix's path without ever quoting it, and no field check on the
observation can see it, because it never travels that channel.

**What the digest check does and does not establish**, because a barrier
described more strongly than it is, is worse than none:

- It **does** guarantee that no run selects a different criterion per instance.
  One artifact, one digest, every instance — a per-instance criterion cannot be
  swapped in without the digest stopping the run.
- It does **not** establish that the one shared artifact is free of solution
  knowledge. A single committed criterion could carry the fixes for every
  instance and still be byte-identical everywhere, and the optional
  path/n-gram check is neither exhaustive nor always runnable. **Whether the
  content is general is a review and provenance question**, settled by reading
  the artifact, not by this module.

The digest is therefore what makes the content question *answerable once*:
review the artifact in its pull request, and the check keeps that reviewed text
in force until someone re-pins it deliberately and visibly.

**This is not yet a startup gate.** Nothing in a production path calls
:func:`load_criterion`; the run-level refusal lands with the judge, which is
where the criterion is consumed. What is enforced today is narrower and stated
where it is claimed: ``SpeakWhenOffTrack`` cannot be constructed without a
:class:`Criterion`.
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
  """Raised when the artifact is not the pinned criterion.

  Deliberately not a recorded gap: a criterion that is not the reviewed one
  leaves nothing to judge against, so the caller refuses rather than degrades.
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
