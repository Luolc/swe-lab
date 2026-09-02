"""The barrier's second half: what the judge may measure against.

§3.1 of the plan
(``docs/trace-synthesis/plans/task-05-supervisor-the-component.md``) states the
rule in the form that can be checked — the criterion is a named, committed
artifact, byte-identical for every instance — and names the test that must land
with it.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from swe_lab.trace_synthesis.criterion import (
    Criterion,
    CRITERION_PATH,
    CRITERION_SHA256,
    CriterionRejectedError,
    load_criterion,
)

GOLD_PATCH = """diff --git a/src/pkg/parser.py b/src/pkg/parser.py
--- a/src/pkg/parser.py
+++ b/src/pkg/parser.py
@@ -10,7 +10,7 @@ def parse(text):
-    return text.split(",")
+    return [part.strip() for part in text.split(",") if part.strip()]
"""


def forged(tmp_path: pathlib.Path, text: str) -> tuple[pathlib.Path, str]:
  """Write a criterion that is not the committed one.

  Args:
    tmp_path: Pytest's temporary directory.
    text: The forged criterion's contents.

  Returns:
    Its path and its own digest, so a test can isolate the overlap half from
    the digest half.
  """
  path = tmp_path / "forged.md"
  path.write_text(text, encoding="utf-8")
  return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_committed_criterion_matches_its_pinned_digest() -> None:
  """The artifact and the constant move together, or the loader rejects."""
  assert (
      hashlib.sha256(CRITERION_PATH.read_bytes()).hexdigest()
      == CRITERION_SHA256
  )
  assert isinstance(load_criterion(), Criterion)


def test_a_criterion_quoting_the_gold_patch_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
  """§3.1's named test: a criterion carrying the fix fails the check.

  The loader rejects rather than returning a degraded criterion. This is a
  loader-level refusal; no run is stopped by it until a rollout path calls in.
  """
  path, digest = forged(
      tmp_path,
      "# Criterion\n\nPrefer this shape:\n\n"
      '    return [part.strip() for part in text.split(",") if part.strip()]\n',
  )
  with pytest.raises(CriterionRejectedError, match="word run"):
    load_criterion(gold_patch=GOLD_PATCH, path=path, digest=digest)


def test_a_criterion_naming_a_changed_file_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
  """The cheap redundant half: the criterion may not name where the fix went.

  Not even when it quotes none of it.
  """
  path, digest = forged(
      tmp_path, "# Criterion\n\nLook closely at src/pkg/parser.py.\n"
  )
  with pytest.raises(CriterionRejectedError, match="path"):
    load_criterion(gold_patch=GOLD_PATCH, path=path, digest=digest)


def test_an_edited_criterion_is_rejected_by_the_loader(
    tmp_path: pathlib.Path,
) -> None:
  """Instance-independence is enforced by the digest, not by inspection.

  Any edit at all, benign or not, is rejected until someone re-pins it.
  """
  path = tmp_path / "edited.md"
  path.write_text(
      CRITERION_PATH.read_text(encoding="utf-8") + "\nand one more thing\n",
      encoding="utf-8",
  )
  with pytest.raises(CriterionRejectedError, match="digest"):
    load_criterion(path=path)


def test_a_missing_criterion_is_rejected_by_the_loader(
    tmp_path: pathlib.Path,
) -> None:
  """An absent artifact is a refusal, never a silently empty criterion."""
  with pytest.raises(CriterionRejectedError):
    load_criterion(path=tmp_path / "nope.md")


def test_without_a_gold_patch_the_run_says_the_overlap_half_did_not_run() -> (
    None
):
  """The redundant half degrades honestly.

  A dataset that records no patch leaves the digest carrying the invariant
  alone, and the run says so rather than reporting a check it did not perform.
  """
  assert load_criterion().overlap_checked is False
  assert load_criterion(gold_patch=GOLD_PATCH).overlap_checked is True
