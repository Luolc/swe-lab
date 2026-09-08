""":class:`~swe_lab.trace_synthesis.supervisor.SpeakPolicy` stand-ins for tests.

Test doubles, and deliberately not shipped code. The library shipped a pair
like these for the streaming carrier, which is gone (ADR-0026) along with their
only production callers — the stale-module guard now holds their names down, so
they are not repeated here. What is left is what they always really were: what
a test uses when it is asserting something about a *carrier* and wants no model
call in the way, or wants a correction to arrive at a cursor it chose.

Here rather than copied into each file, because four want them: the harness's
segment tests, the loop's own tests, the upstream tests and the gate tests.
"""

from __future__ import annotations

import dataclasses

from swe_lab.trace_synthesis.supervisor import Intervention, Observation


@dataclasses.dataclass(frozen=True)
class SilentPolicy:
  """Consults nothing and says nothing."""

  @property
  def name(self) -> str:
    """Return the policy's name.

    Returns:
      ``"silent"``.
    """
    return "silent"

  def consider(self, observation: Observation) -> Intervention | None:
    """Stay silent.

    Args:
      observation: Ignored.

    Returns:
      ``None``, always.
    """
    del observation
    return None


@dataclasses.dataclass(frozen=True)
class SpeaksAt:
  """Says a fixed line at fixed cursors, consulting no judge.

  The timing knob in isolation: it varies *when* while holding *what* and
  *whether* constant, which is what lets a test about delivery say nothing
  about judgement.

  Attributes:
    cursors: The cursor values at which to speak.
    text: The line, identical at every one of them.
  """

  cursors: frozenset[int]
  text: str

  @property
  def name(self) -> str:
    """Return the policy's name.

    Returns:
      ``"speaks-at"``.
    """
    return "speaks-at"

  def consider(self, observation: Observation) -> Intervention | None:
    """Speak if this cursor is one of the fixed points.

    Args:
      observation: Read only for its cursor.

    Returns:
      The fixed line, or ``None``.
    """
    if observation.cursor not in self.cursors:
      return None
    return Intervention(text=self.text)
