"""Re-run the rules over a stored run, without a container or an agent.

The reason the core is pure. A rule set's first version mostly detects its own
bugs — task-26 §4 measured exactly that — so a corrected rule has to be
re-measurable against runs that already happened. Reading three files off disk
does that; re-running the agent would not.
"""

from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Any

from etils import epath

from .rules import (
    check_controls,
    check_patch,
    check_trace,
    merge,
    VerifierFindings,
)

# The artifact names a persisted rollout leaves behind.
PATCH_NAME = "patch.diff"
CONVERSATION_NAME = "conversation.json"
INTEGRITY_NAME = "git_integrity.json"


def _read_json(path: epath.Path) -> dict[str, Any] | None:
  """Parse a JSON artifact, or ``None`` when it is absent or unreadable."""
  if not path.exists():
    return None
  try:
    parsed = json.loads(path.read_text())
  except (ValueError, UnicodeDecodeError):
    return None
  return parsed if isinstance(parsed, dict) else None


def replay_run(
    run_dir: epath.PathLike,
    *,
    required_tests: Sequence[str] = (),
    workdir: str = "/",
) -> VerifierFindings:
  """Apply every rule to one persisted run directory.

  Missing artifacts are not an error: an old run predating a control simply has
  no report for it, and the point of replay is to cover exactly those.

  Args:
    run_dir: A directory holding ``patch.diff`` / ``conversation.json`` /
      ``git_integrity.json`` — a run's output dir or its store prefix.
    required_tests: The instance's required tests, when the caller has them.
    workdir: The repo path inside the sandbox, for the outside-reads rule.

  Returns:
    The merged findings for that run.
  """
  root = epath.Path(run_dir)
  patch_path = root / PATCH_NAME
  patch = patch_path.read_text() if patch_path.exists() else ""
  conversation = _read_json(root / CONVERSATION_NAME) or {}
  messages = conversation.get("messages") or []
  return merge(
      check_patch(patch, required_tests),
      check_trace(messages, workdir=workdir),
      check_controls(_read_json(root / INTEGRITY_NAME)),
  )
