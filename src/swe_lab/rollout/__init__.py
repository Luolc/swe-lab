"""Rollout: run a headless coding agent inside an instance's container.

Runs Claude Code (pinned native binary, bind-mounted) against a task's prebuilt
image, captures its trajectory as a unified exchange record, and extracts the
resulting edits as a ``git apply``-able patch (a diff vs ``base_commit``) ready
for ``evaluation`` to grade. Deliberately *not* called "solver": trajectory
generation is general (solving is one use). The agent harness is pluggable
(Claude Code first); the harness-agnostic contract is patch = ``git diff`` of
the workdir. See ``PLAN.md`` → Workstream 2.
"""

from __future__ import annotations

from .runner import rollout, RolloutResult

__all__ = ["RolloutResult", "rollout"]
