"""Harness plugs: how each off-the-shelf agent CLI is run as an engine body.

One subpackage per harness (``claude_code`` now; ``codex`` / ``grok_build``
next), all implementing the :class:`~swe_lab.harnesses.base.Harness` contract,
and each registering itself by name at import of its own package (see
``registry``) so an invocation can select it without swe-lab knowing it exists.
"""

from .base import Harness
from .observer import COMPLETE_METRIC, HarnessOutcomeObserver
from .registry import (
    build_harness,
    HarnessFactory,
    register_harness,
    registered_harnesses,
)

__all__ = [
    "COMPLETE_METRIC",
    "Harness",
    "HarnessFactory",
    "HarnessOutcomeObserver",
    "build_harness",
    "register_harness",
    "registered_harnesses",
]
