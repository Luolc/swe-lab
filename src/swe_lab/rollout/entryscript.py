"""Build the in-container rollout entryscript.

The script runs inside the instance's prebuilt image (the repo is checked out
at ``base_commit`` in ``workdir``). It: prepares a writable HOME, runs the
headless Claude Code agent on the repo (streaming its ``stream-json`` trajectory
to a mounted file), then extracts the agent's edits as a text-only,
``git apply``-able patch via the shared
:func:`~swe_lab.core.patch.build_extraction_script` (written to the
raw patch file; the runner strips any residual binary marker section host-side).

**Base for the diff (MVP choice).** We diff against the instance's original
``base_commit``, not a fresh post-setup commit. That guarantees the grading
round-trip — the grader does ``git reset --hard base_commit`` then
``git apply`` — and a patch relative to ``base_commit`` applies cleanly there
(the dataset's own gold patches are diffs vs ``base_commit`` and apply, so the
convention is proven). The post-setup-commit alternative is deferred (see
ADR-0001).
"""

from __future__ import annotations

import shlex

from swe_lab.core.patch import build_extraction_script

from .constants import (
    AGENT_HOME_AT,
    AGENT_STDERR_NAME,
    CLAUDE_BIN_AT,
    MOUNT_AT,
    PROMPT_NAME,
    RAW_PATCH_NAME,
    TRAJECTORY_NAME,
)


def build_rollout_script(
    *,
    workdir: str,
    base_commit: str,
    model: str,
    claude_bin: str = CLAUDE_BIN_AT,
    home_dir: str = AGENT_HOME_AT,
    mount_at: str = MOUNT_AT,
    exclude_globs: tuple[str, ...] = (),
) -> str:
  """Build the bash that runs the agent in-container and extracts the patch.

  ``claude_bin`` is the read-only bind-mount path of the pinned native binary;
  the workspace is mounted at ``mount_at`` (prompt in, trajectory + patch out).
  The agent invocation is guarded with ``|| true`` so a nonzero agent exit still
  lets us extract whatever partial edits it made (an empty patch is caught later
  by the runner, not here).
  """
  home = shlex.quote(home_dir)
  wd = shlex.quote(workdir)
  bin_path = shlex.quote(claude_bin)
  model_arg = shlex.quote(model)
  prompt_path = shlex.quote(f"{mount_at}/{PROMPT_NAME}")
  trajectory_path = shlex.quote(f"{mount_at}/{TRAJECTORY_NAME}")
  stderr_path = shlex.quote(f"{mount_at}/{AGENT_STDERR_NAME}")

  preamble = [
      "set -u",  # bash: error on any unset variable
      f"export HOME={home}",
      f"mkdir -p {home}",
      # Some Claude Code builds refuse --dangerously-skip-permissions as root
      # unless a sandbox is signalled; the throwaway container is our sandbox.
      "export IS_SANDBOX=1",
      f"cd {wd}",
      (
          f'{bin_path} -p "$(cat {prompt_path})"'
          f" --model {model_arg}"
          " --output-format stream-json --verbose"
          " --dangerously-skip-permissions"
          f" > {trajectory_path} 2> {stderr_path} || true"
      ),
  ]
  extraction = build_extraction_script(
      workdir=workdir,
      base_ref=base_commit,
      output_path=f"{mount_at}/{RAW_PATCH_NAME}",
      exclude_globs=exclude_globs,
  )
  return "\n".join(preamble) + "\n" + extraction
