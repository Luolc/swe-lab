"""Run one rollout: a headless agent solves an instance inside its container.

Dataset-agnostic: takes an :class:`~swe_lab.core.benchmark.EvalSpec`
(which image, workdir, base_commit) plus a ready-made solve ``prompt``, and
returns the extracted patch + the agent's trajectory as a unified exchange
record. The dataset-specific prompt construction lives in the CLI (``__main__``)
so this runner never learns a dataset's fields.

Flow: provision the pinned agent binary → stage a workspace (prompt +
entryscript) → ``docker run`` the instance image with the binary bind-mounted
and the OAuth token inherited by reference → read back the raw extraction, strip
any residual binary marker section to a clean text patch, read the
``stream-json`` trajectory → guard the empty patch. The produced patch is a
text diff vs ``base_commit``, meant to be handed straight to ``evaluation``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from swe_lab.core.agent.binary import ensure_claude_binary
from swe_lab.core.agent.trace import last_stream_record
from swe_lab.core.benchmark import EvalSpec
from swe_lab.core.docker.provider import DockerProvider, Mount
from swe_lab.core.patch import (
    is_effectively_empty,
    strip_binary_hunks,
)
from swe_lab.core.paths import cache_root, find_repo_root

from .constants import (
    AGENT_STDERR_NAME,
    CLAUDE_BIN_AT,
    DEFAULT_MODEL,
    ENTRYSCRIPT_NAME,
    MOUNT_AT,
    OAUTH_TOKEN_ENV,
    PATCH_NAME,
    PROMPT_NAME,
    RAW_PATCH_NAME,
    TRAJECTORY_NAME,
)
from .entryscript import build_rollout_script

# Gitignored cache subdir holding one workspace per rollout run.
ROLLOUT_WORKSPACES_SUBDIR = "rollout_workspaces"
# Claude Code runs for minutes exploring + editing + (maybe) testing; cap well
# above the annotation cap since solving is heavier than read-only annotation.
DEFAULT_TIMEOUT_S = 3600.0


@dataclass(frozen=True)
class RolloutResult:
  """Outcome of one rollout run."""

  instance_id: str
  patch: str  # clean text diff vs base_commit, binary stripped (may be empty)
  is_empty: bool  # no applyable content (failed attempt, never graded as pass)
  binary_stripped: bool  # a residual binary marker section was removed
  complete: bool  # the agent's stream ended cleanly (terminal success event)
  exchange: dict[str, object]  # unified exchange record (trajectory)
  exit_code: int
  timed_out: bool
  workspace: Path


def rollout(
    spec: EvalSpec,
    *,
    prompt: str,
    model: str = DEFAULT_MODEL,
    provider: DockerProvider | None = None,
    binary_path: Path | None = None,
    workspace: Path | None = None,
    repo_root: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    pull: bool = True,
    exclude_globs: tuple[str, ...] = (),
) -> RolloutResult:
  """Run the agent in ``spec``'s image and return its patch + trajectory.

  ``binary_path`` defaults to the pinned native Claude Code binary (downloaded +
  checksum-verified on first use). The workspace defaults to a per-instance dir
  under the gitignored cache. The container inherits ``CLAUDE_CODE_OAUTH_TOKEN``
  from this process's environment by reference (never in the docker argv), so
  that variable must be set. Network is on (the agent must reach the API).
  """
  provider = provider or DockerProvider()
  root = repo_root or find_repo_root()
  binary_path = binary_path or ensure_claude_binary(repo_root=root)
  if workspace is None:
    workspace = cache_root(root) / ROLLOUT_WORKSPACES_SUBDIR / spec.instance_id
  workspace.mkdir(parents=True, exist_ok=True)

  # Clear stale outputs so a crashed run can't be read off a previous run's
  # artifacts.
  for name in (RAW_PATCH_NAME, PATCH_NAME, TRAJECTORY_NAME, AGENT_STDERR_NAME):
    (workspace / name).unlink(missing_ok=True)

  _ = (workspace / PROMPT_NAME).write_text(prompt)
  _ = (workspace / ENTRYSCRIPT_NAME).write_text(
      build_rollout_script(
          workdir=spec.workdir,
          base_commit=spec.base_commit,
          model=model,
          exclude_globs=exclude_globs,
      )
  )

  if pull:
    provider.pull(spec.image_ref)
  run = provider.run_script(
      spec.image_ref,
      workspace,
      ENTRYSCRIPT_NAME,
      mount_at=MOUNT_AT,
      timeout=timeout,
      network=True,
      extra_mounts=[Mount(binary_path, CLAUDE_BIN_AT, read_only=True)],
      pass_env=[OAUTH_TOKEN_ENV],
  )

  # The container wrote the raw git-diff extraction; strip any residual binary
  # marker section so the graded patch is cleanly text-only (see
  # strip_binary_hunks). Keep the raw file for audit; write the clean patch as
  # the canonical PATCH_NAME artifact.
  raw_patch = _read_patch(workspace / RAW_PATCH_NAME)
  patch = strip_binary_hunks(raw_patch)
  _ = (workspace / PATCH_NAME).write_text(patch)

  exchange = last_stream_record(workspace / TRAJECTORY_NAME)
  return RolloutResult(
      instance_id=spec.instance_id,
      patch=patch,
      is_empty=is_effectively_empty(patch),
      binary_stripped=patch != raw_patch,
      complete=bool(exchange.get("complete", False)),
      exchange=exchange,
      exit_code=run.exit_code,
      timed_out=run.timed_out,
      workspace=workspace,
  )


def _read_patch(patch_path: Path) -> str:
  """Read the extracted patch as text, tolerant of odd bytes.

  The extractor writes raw bytes (patch-transport correctness); we decode with
  ``backslashreplace`` so an exotic-encoding hunk can never crash the read
  (matches SWE-agent's ``errors="backslashreplace"`` guard).
  """
  if not patch_path.is_file():
    return ""
  return patch_path.read_bytes().decode("utf-8", "backslashreplace")
