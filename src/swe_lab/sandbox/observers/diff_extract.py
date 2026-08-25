"""Shared diff-extract observer: the agent's patch via git diff vs base_commit.

Runs in ``before_destroy`` against the still-live container, so it works for
**any** harness that edits the repo — extraction is not baked into the agent
script. Reuses ``swe_lab.git.patch``'s extraction contract (ADR-0001: worktree
diff vs ``base_commit``, ``git add -N``, no ``--binary``, residual
``Binary files … differ`` stripped host-side) byte-for-byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import shlex
from typing import override

from swe_lab.git.patch import (
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
# What the extraction found, on the run's metrics (and so on its record).
EMPTY_METRIC = "patch_is_empty"
BINARY_STRIPPED_METRIC = "patch_binary_stripped"
_EXTRACT_TIMEOUT_S = 120.0
_BASELINE_TIMEOUT_S = 120.0

# A task container often carries no git identity, and `git commit` refuses
# without one. Set per-invocation (`-c`), not in the repo's config, so the
# baseline leaves no trace the agent could read as a hint.
_BASELINE_IDENTITY = (
    "-c user.email=baseline@swe-lab.invalid",
    "-c user.name=swe-lab",
)
# Pinned so the baseline sha is reproducible across attempts of one instance:
# an unpinned date makes every attempt's base different, and "which base
# produced this patch" stops being a comparable answer.
_BASELINE_DATE = "1970-01-01T00:00:00+00:00"


def build_baseline_script(*, workdir: str, output_path: str) -> str:
  """Build the bash that commits the tree as the agent found it.

  Everything present is committed — tracked modifications, deletions and
  untracked files alike (``add -A``) — because the point is a base that
  *matches the tree the agent starts from*, and a file left out would show up
  later as if the agent had created it.

  ``--allow-empty`` so a clean worktree still yields a baseline: the caller
  asked for one, and returning ``base_commit`` instead would make the base
  depend on whether the image happened to be dirty.

  Args:
    workdir: In-container path of the instance's repo.
    output_path: Where the resolved sha is written, one line, no newline fuss.

  Returns:
    The bash script text, newline-terminated.
  """
  wd = shlex.quote(workdir)
  out = shlex.quote(output_path)
  identity = " ".join(_BASELINE_IDENTITY)
  dates = (
      f"GIT_AUTHOR_DATE={_BASELINE_DATE} GIT_COMMITTER_DATE={_BASELINE_DATE}"
  )
  return (
      "\n".join(
          [
              "set -eu",
              f"git -C {wd} {identity} add -A -- :/",
              f"{dates} git -C {wd} {identity} commit --allow-empty -q"
              " -m 'swe-lab: pre-agent baseline'",
              f"git -C {wd} rev-parse HEAD > {out}",
          ]
      )
      + "\n"
  )


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

      **Off by default, and not free to turn on**: the base ref is a contract
      with the grader, which has to grade a tree matching it. The shipped
      ``swebench_pro`` grader resets hard to ``base_commit``, which wipes the
      very mutations a baseline captures — measured, a baseline-relative patch
      then applies *unless* the agent touched a path the image had mutated, at
      which point it fails (a file recreated after an image-time delete is a
      ``new file`` hunk against a tree that already has it). Fails closed, not
      wrong, but only sometimes. Turn this on for a dataset whose grader grades
      the image's tree as shipped.
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
    sha_file = "patch.base.txt"
    script = build_baseline_script(
        workdir=sb.spec.workdir, output_path=sha_file
    )
    sb.write(BASELINE_SCRIPT_NAME, script.encode("utf-8"))
    result = sb.run_script(BASELINE_SCRIPT_NAME, timeout=_BASELINE_TIMEOUT_S)
    if result.exit_code != 0:
      detail = (result.stderr or result.stdout).strip()[-500:]
      raise SandboxError(
          "could not commit the pre-agent baseline (exit"
          f" {result.exit_code}): {detail}"
      )
    sha = _read_patch(sb, sha_file).strip()
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
    return (
        ArtifactSchema(PATCH_NAME, description="the extracted clean patch"),
        ArtifactSchema(
            RAW_PATCH_NAME,
            required=False,
            description="the raw in-sandbox git diff, kept for audit",
        ),
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
    return Contribution(
        artifacts=artifacts,
        inline_artifacts={PATCH_NAME: self.patch.encode("utf-8")},
        # What the extraction *found*, as metrics: a persisted attempt has to
        # be readable on its own, and "the patch is empty" is the difference
        # between an agent that failed and one that changed nothing.
        metrics={
            EMPTY_METRIC: float(self.is_empty),
            BINARY_STRIPPED_METRIC: float(self.binary_stripped),
        },
    )
