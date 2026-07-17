"""Constants for the rollout flow: in-container paths and workspace file names.

Single source of truth for every literal the entryscript builder and the runner
share, so an in-container path is defined once rather than re-typed in both.
"""

from __future__ import annotations

# Where DockerProvider bind-mounts the per-run workspace (entryscript reads/
# writes files here by their in-container path). Matches the ``mount_at`` the
# runner passes to ``run_script``.
MOUNT_AT = "/workspace"

# The pinned native Claude Code binary is bind-mounted read-only here.
CLAUDE_BIN_AT = "/opt/claude-code/claude"

# A writable HOME for the agent inside the container (the instance images run as
# root with no guaranteed-writable home; the native binary wants to create a
# config dir). Set in the entryscript, created before the agent runs.
AGENT_HOME_AT = "/tmp/rollout-home"

# Workspace files (host side, mounted into the container at MOUNT_AT).
ENTRYSCRIPT_NAME = "entryscript.sh"
PROMPT_NAME = "prompt.txt"
TRAJECTORY_NAME = "trajectory.jsonl"  # the agent's stream-json events
AGENT_STDERR_NAME = "agent.stderr"
# The container writes the raw git-diff extraction here; the runner strips any
# residual binary marker sections and writes the clean, graded patch to
# PATCH_NAME. Both are kept (raw for audit, clean for grading).
RAW_PATCH_NAME = "patch.raw.diff"
PATCH_NAME = "patch.diff"  # the clean, text-only patch that gets graded

# Auth: the subscription OAuth token, inherited by reference into the container
# (never placed in the docker argv). See DockerProvider.run_script pass_env.
OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

DEFAULT_MODEL = "sonnet"
