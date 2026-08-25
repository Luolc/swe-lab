"""Shared diff-extract observer: the agent's patch via git diff vs base_commit.

Runs in ``before_destroy`` against the still-live container, so it works for
**any** harness that edits the repo — extraction is not baked into the agent
script. Reuses ``swe_lab.git.patch``'s extraction contract (ADR-0001: worktree
diff vs ``base_commit``, ``git add -N``, no ``--binary``, residual
``Binary files … differ`` stripped host-side) byte-for-byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import override

from swe_lab.git.patch import (
    build_baseline_script,
    build_extraction_script,
    is_effectively_empty,
    strip_binary_hunks,
)
from swe_lab.sandbox.errors import SandboxError
from swe_lab.sandbox.observer import ArtifactSchema, SandboxObserver
from swe_lab.sandbox.result import Contribution
from swe_lab.sandbox.sandbox import SandboxFs

RAW_PATCH_NAME = "patch.raw.diff"  # raw git-diff bytes (audit)
PATCH_NAME = "patch.diff"  # clean, text-only patch that gets graded
EXTRACT_SCRIPT_NAME = "extract.sh"  # persisted for audit
BASELINE_SCRIPT_NAME = "baseline.sh"  # persisted for audit (baseline mode only)
# The base the patch was taken against, as an artifact — emitted only in
# baseline mode, where the base is a per-run sha that exists nowhere else. A
# patch is only interpretable together with its base, so they travel the same
# way: the grading side declares this name as an input and the workflow wires
# it along the same edge as the patch.
BASE_REF_NAME = "patch.base_ref.txt"
# What the extraction found, on the run's metrics (and so on its record).
EMPTY_METRIC = "patch_is_empty"
BINARY_STRIPPED_METRIC = "patch_binary_stripped"
_EXTRACT_TIMEOUT_S = 120.0
_BASELINE_TIMEOUT_S = 120.0


def _read_patch(sb: SandboxFs, name: str) -> str:
  """Read an extracted patch file as text, tolerant of odd bytes.

  The extractor writes raw bytes; decode with ``backslashreplace`` so an
  exotic-encoding hunk can never crash the read (ported from the runner).

  Args:
    sb: The live sandbox to read from.
    name: The workspace-relative patch filename.

  Returns:
    The decoded patch text, or ``""`` when the file is absent.
  """
  if not sb.exists(name):
    return ""
  return sb.read(name).decode("utf-8", "backslashreplace")


@dataclass
class DiffExtractObserver(SandboxObserver):
  """Extract the worktree diff vs ``base_commit``, strip binary hunks host-side.

  Single-run (holds the extracted patch + flags): construct a fresh one per run.

  Attributes:
    exclude_globs: Build-noise denylist passed to the extraction script.
    baseline: Diff against the tree **as the agent found it** rather than
      against ``spec.base_commit``, by committing that tree in ``after_create``
      (ADR-0001, 2026-08-25 amendment). For images whose worktree ships
      already different from ``base_commit`` — build-time edits that were never
      committed — where the default would fold those into every agent's patch.

      **Half of a pair**: the base ref is a contract with the grader, which
      has to grade a tree matching it, so the composing task sets this
      together with the grading side's ``patch_baseline`` (see
      ``UnitTestTask``) — that side recomputes the baseline with the same
      pinned commands, verifies the sha against :attr:`base_ref`, and resets
      to it. Against a plain ``base_commit``-resetting grader, a baseline
      patch fails to apply exactly when the agent touched a path the image had
      mutated (measured: a file recreated after an image-time delete is a
      ``new file`` hunk against a tree that already has it) — closed, not
      wrong, but confusingly.
    patch: The clean, text-only diff vs :attr:`base_ref` (may be ``""``).
    is_empty: Whether the clean patch is effectively empty.
    binary_stripped: Whether a residual binary hunk was stripped host-side.
    base_ref: The base the patch was actually taken against — the baseline sha
      in baseline mode, else ``""`` until ``before_destroy`` resolves it to
      ``spec.base_commit``. Read back by the task onto the attempt's record, so
      the question is answerable from the manifest alone.
  """

  exclude_globs: tuple[str, ...] = ()
  baseline: bool = False
  patch: str = ""
  is_empty: bool = True
  binary_stripped: bool = False
  base_ref: str = field(default="", init=False)

  @override
  def after_create(self, sb: SandboxFs) -> None:
    """Commit the tree as the agent found it, when asked to.

    A no-op unless :attr:`baseline`. Runs *after* the history purge, which
    ``CodingAgentTask.observers`` orders first — a baseline made before
    ``git gc --prune=now`` would be at that purge's mercy.

    Args:
      sb: The live sandbox, whose repo is at ``sb.spec.workdir``.

    Raises:
      SandboxError: If the baseline could not be created or read back.
        Deliberately **fails closed**: falling back to ``spec.base_commit``
        would silently produce exactly the contaminated patch this mode exists
        to prevent, and ``after_create``'s contract is that a raise aborts the
        run.
    """
    if not self.baseline:
      return
    body = build_baseline_script(
        workdir=sb.spec.workdir, output_path=BASE_REF_NAME
    )
    # `> patch.base_ref.txt` is relative to the shell cwd, which run_script
    # does NOT put in the workspace — cd there first, exactly as the
    # extraction script below does. Caught by the first live run: without it
    # the sha lands in the container's own cwd, the workspace read comes back
    # empty, and the fail-closed path aborts a perfectly healthy run.
    script = f'cd "$SANDBOX_WORKSPACE"\n{body}'
    sb.write(BASELINE_SCRIPT_NAME, script.encode("utf-8"))
    result = sb.run_script(BASELINE_SCRIPT_NAME, timeout=_BASELINE_TIMEOUT_S)
    if result.exit_code != 0:
      detail = (result.stderr or result.stdout).strip()[-500:]
      raise SandboxError(
          "could not commit the pre-agent baseline (exit"
          f" {result.exit_code}): {detail}"
      )
    sha = _read_patch(sb, BASE_REF_NAME).strip()
    if not sha:
      raise SandboxError(
          "the pre-agent baseline commit produced no sha; refusing to fall"
          " back to base_commit, which would fold the image's own worktree"
          " changes into the agent's patch"
      )
    self.base_ref = sha

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    """Declare the clean patch (the deliverable) and the raw diff (audit)."""
    base = (
        (
            ArtifactSchema(
                BASE_REF_NAME,
                description="the sha the patch was diffed against",
            ),
        )
        if self.baseline
        else ()
    )
    return (
        ArtifactSchema(PATCH_NAME, description="the extracted clean patch"),
        ArtifactSchema(
            RAW_PATCH_NAME,
            required=False,
            description="the raw in-sandbox git diff, kept for audit",
        ),
        *base,
    )

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Run the extraction in-container, then clean + register the patch."""
    self.base_ref = self.base_ref or sb.spec.base_commit
    body = build_extraction_script(
        workdir=sb.spec.workdir,
        base_ref=self.base_ref,
        output_path=RAW_PATCH_NAME,  # relative; cd below lands it in-workspace
        exclude_globs=self.exclude_globs,
    )
    # `git … > patch.raw.diff` is relative to the shell cwd, so cd into the
    # workspace ($SANDBOX_WORKSPACE, set on every backend) — one script text
    # works on A-host and A-ghjob alike, and the persisted extract.sh lands
    # in the workspace for audit.
    script = f'cd "$SANDBOX_WORKSPACE"\n{body}'
    sb.write(EXTRACT_SCRIPT_NAME, script.encode("utf-8"))
    _ = sb.run_script(EXTRACT_SCRIPT_NAME, timeout=_EXTRACT_TIMEOUT_S)

    raw = _read_patch(sb, RAW_PATCH_NAME)
    self.patch = strip_binary_hunks(raw)
    self.binary_stripped = self.patch != raw
    self.is_empty = is_effectively_empty(self.patch)

    # The raw diff was produced *in* the sandbox, so it is fetched out; the
    # clean one was derived here, so it is handed over inline rather than
    # written back into the sandbox only to be fetched again.
    #
    # Both are named for their file: these are shared, cross-harness artifacts,
    # so nothing namespaces them and the artifact name simply *is* the filename
    # (which already carries the format).
    artifacts = (
        {RAW_PATCH_NAME: RAW_PATCH_NAME} if sb.exists(RAW_PATCH_NAME) else {}
    )
    # The base ref is handed over from memory, not fetched from the workspace:
    # the file has sat in a directory the agent can write to for the whole
    # run, and the copy captured in `after_create` — before the agent started —
    # is the one that cannot have been tampered with.
    base_ref = (
        {BASE_REF_NAME: f"{self.base_ref}\n".encode()} if self.baseline else {}
    )
    return Contribution(
        artifacts=artifacts,
        inline_artifacts={
            PATCH_NAME: self.patch.encode("utf-8"),
            **base_ref,
        },
        # What the extraction *found*, as metrics: a persisted attempt has to
        # be readable on its own, and "the patch is empty" is the difference
        # between an agent that failed and one that changed nothing.
        metrics={
            EMPTY_METRIC: float(self.is_empty),
            BINARY_STRIPPED_METRIC: float(self.binary_stripped),
        },
    )
