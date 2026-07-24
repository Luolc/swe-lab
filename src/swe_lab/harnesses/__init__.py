"""Harness plugs: how each off-the-shelf agent CLI is run as an engine body.

One subpackage per harness (``claude_code`` now; ``codex`` / ``grok_build``
next), all implementing the :class:`~swe_lab.harnesses.base.Harness` contract.
"""

from .base import Harness

__all__ = ["Harness"]
