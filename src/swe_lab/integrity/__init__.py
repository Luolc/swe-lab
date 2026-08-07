"""Benchmark-integrity detection: signals that a result was not earned.

The environment controls (ADR-0010 §3a/§3b) prevent what they can — future git
history is purged before the agent starts, egress is a per-entry policy. This
package covers what an environment *cannot* prevent, because it arrives through
the agent's one legitimate channel: the patch. Plus the audit of those controls
themselves, which is the highest-value check of the three.

:mod:`~swe_lab.integrity.rules` is the pure core — functions over parsed values,
so the same rules run in-flight and **replay** over stored runs
(:mod:`~swe_lab.integrity.replay`). The observer that drives them in-flight is
``ResultVerifyObserver``, in :mod:`swe_lab.sandbox.observers`.

**Detection, never a gate** (ADR-0010 §3c/§6): a flag is a reason to look, not
a verdict, and the verifier never fails a run — including on its own bugs.
"""

from .replay import replay_run
from .rules import (
    check_controls,
    check_patch,
    check_trace,
    HIGH_CONFIDENCE_RULES,
    merge,
    VerifierFindings,
)

__all__ = [
    "HIGH_CONFIDENCE_RULES",
    "VerifierFindings",
    "check_controls",
    "check_patch",
    "check_trace",
    "merge",
    "replay_run",
]
