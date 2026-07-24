"""Shared diff-extract observer: the agent's patch via git diff vs base_commit.

Runs in ``before_destroy`` against the still-live container, so it works for
**any** harness that edits the repo — extraction is not baked into the agent
script (spec §core model). Reuses ``core/patch.py``'s extraction contract
(ADR-0001: worktree diff vs ``base_commit``, ``git add -N``, no ``--binary``,
residual ``Binary files … differ`` stripped host-side) byte-for-byte.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import override

from swe_lab.core.patch import (
    build_extraction_script,
    is_effectively_empty,
    strip_binary_hunks,
)
from swe_lab.sandbox.manager import Sandbox
from swe_lab.sandbox.observer import SandboxObserver
from swe_lab.sandbox.result import Contribution

RAW_PATCH_NAME = "patch.raw.diff"  # raw git-diff bytes (audit)
PATCH_NAME = "patch.diff"  # clean, text-only patch that gets graded
EXTRACT_SCRIPT_NAME = "extract.sh"  # persisted for audit
_EXTRACT_TIMEOUT_S = 120.0


def _read_patch(path: Path) -> str:
  """Read the extracted patch as text, tolerant of odd bytes.

  The extractor writes raw bytes; decode with ``backslashreplace`` so an
  exotic-encoding hunk can never crash the read (ported from the runner).

  Args:
    path: The host-side raw patch file.

  Returns:
    The decoded patch text, or ``""`` when the file is absent.
  """
  if not path.is_file():
    return ""
  return path.read_bytes().decode("utf-8", "backslashreplace")


@dataclass
class DiffExtractObserver(SandboxObserver):
  """Extract the worktree diff vs ``base_commit``, strip binary hunks host-side.

  Single-run (holds the extracted patch + flags): construct a fresh one per run.

  Attributes:
    exclude_globs: Build-noise denylist passed to the extraction script.
    patch: The clean, text-only diff vs ``base_commit`` (may be ``""``).
    is_empty: Whether the clean patch is effectively empty.
    binary_stripped: Whether a residual binary hunk was stripped host-side.
  """

  exclude_globs: tuple[str, ...] = ()
  patch: str = ""
  is_empty: bool = True
  binary_stripped: bool = False

  @override
  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    """Run the extraction in-container, then clean + register the patch."""
    body = build_extraction_script(
        workdir=sb.spec.workdir,
        base_ref=sb.spec.base_commit,
        output_path=RAW_PATCH_NAME,  # relative; cd below lands it in-workspace
        exclude_globs=self.exclude_globs,
    )
    # `git … > patch.raw.diff` is relative to the shell cwd, so cd into the
    # workspace ($SANDBOX_WORKSPACE, set on every backend) — one script text
    # works on A-host and A-ghjob alike, and the persisted extract.sh lands
    # in the workspace for audit.
    script = f'cd "$SANDBOX_WORKSPACE"\n{body}'
    _ = (sb.workspace / EXTRACT_SCRIPT_NAME).write_text(script)
    _ = sb.run(EXTRACT_SCRIPT_NAME, timeout=_EXTRACT_TIMEOUT_S)

    raw = _read_patch(sb.workspace / RAW_PATCH_NAME)
    self.patch = strip_binary_hunks(raw)
    self.binary_stripped = self.patch != raw
    self.is_empty = is_effectively_empty(self.patch)
    _ = (sb.workspace / PATCH_NAME).write_text(self.patch)

    artifacts = {"patch": sb.workspace / PATCH_NAME}
    raw_path = sb.workspace / RAW_PATCH_NAME
    if raw_path.is_file():
      artifacts["patch_raw"] = raw_path
    return Contribution(artifacts=artifacts)
