"""The guard on the anchored seam — the one check this path's argument rests on.

The segmented loop resumes with ``--resume-session-at``, and the whole reason it
is allowed to exist is that this flag **produces no synthetic assistant record**
(measured, feasibility report §9.1). That matters because the fabricated
``assistant`` turn violates criterion **(a)** of
``docs/trace-synthesis/spec.md`` — tokens the model never wrote — and (a) is
*not* what the owner relaxed on 2026-09-03; (b) is. The spec's own §6 then
closes the other exit: the trace is the conversation with **nothing removed**,
so the artifact cannot be post-processed away either. Producing it disqualifies
the trace; deleting it disqualifies the trace. Not producing it is the only
open door, and this module is what checks the door is still shut.

**The flag is undocumented** (``hideHelp()``), so it carries no compatibility
promise. If a build changes it, the seam does not go red — it quietly reverts to
the dirty one, and the run keeps producing traces that look fine and are
ineligible. **A silent, distant failure on the one property an argument rests
on is exactly the case that needs a check rather than a claim**, which is why
this runs on every resumed segment rather than living in a document.

The reading fails **closed**. "No synthetic assistant records" is worth nothing
from an instrument that saw no assistant records at all, so
:func:`seam_is_clean` requires the positive premises first — a parsed capture,
at least one main-loop request, at least one assistant message in it — and only
then the zeros. That the instrument fires at all is a separate arm, asserted in
``tests/test_seam_shape.py`` against a committed dirty-seam fixture.
"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import json
from typing import Any

from swe_lab.sandbox import SandboxError

#: The text of the fabricated ``assistant`` turn the plain ``--resume`` repair
#: writes. Matched literally **as a detector of a known artifact**, never as a
#: filter: the asymmetry is deliberate, since a build that changed the wording
#: makes this report a false *clean*, and the segment log's own record of every
#: seam is what covers that (task 22 §6.5).
SYNTHETIC_ASSISTANT_TEXT = "No response requested."

#: Its sibling, a synthetic **user** turn. Counted because it travels with the
#: other one and its presence is the loudest sign the anchored seam has
#: reverted — but it is **not** a disqualifier on its own: ``spec.md`` §7 says a
#: synthetic user turn violates neither criterion.
RESUME_CONTINUATION_TEXT = "Continue from where you left off."


class DirtySeamError(SandboxError):
  """Raised when a resumed segment's wire shows the artifacts (or cannot say).

  A run that raises this is recorded as a run error with its artifacts intact,
  which is the point: the alternative is a trace that reads as ordinary and is
  ineligible under ``spec.md`` §7.
  """


@dataclasses.dataclass(frozen=True)
class SeamReading:
  """What one capture says about the seam, premises included.

  The premises are fields rather than assumptions because the zeros below are
  only meaningful once they hold — a capture the instrument could not read
  reports the same zeros as a clean one.

  Attributes:
    records: Parsed records in the capture.
    main_loop_requests: Of those, the ones carrying a ``tools`` array — the
      agent-loop calls, as opposed to the auxiliary ones. The feasibility
      report's own selection rule.
    assistant_messages: Assistant messages seen in the fullest main-loop
      request. Zero means the instrument had nothing to look at.
    synthetic_assistants: Assistant messages carrying
      :data:`SYNTHETIC_ASSISTANT_TEXT`, in that same request.
    resume_continuations: User messages carrying
      :data:`RESUME_CONTINUATION_TEXT`, in that same request.
  """

  records: int
  main_loop_requests: int
  assistant_messages: int
  synthetic_assistants: int
  resume_continuations: int


def _text_blocks(content: Any) -> list[str]:
  """Return the text of every text block in a message's content.

  Args:
    content: A message's ``content`` — a string or a list of blocks.

  Returns:
    The texts, in order.
  """
  if isinstance(content, str):
    return [content]
  if not isinstance(content, list):
    return []
  texts: list[str] = []
  for block in content:
    if isinstance(block, Mapping) and block.get("type") == "text":
      text = block.get("text")
      if isinstance(text, str):
        texts.append(text)
  return texts


def _main_loop_bodies(raw: str) -> tuple[int, list[Mapping[str, Any]]]:
  """Split a capture into its record count and its agent-loop request bodies.

  A request carrying a ``tools`` array is an agent-loop call; one without is
  auxiliary. That is the feasibility report's own selection rule, recorded in
  the capture rather than guessed from message counts.

  Args:
    raw: The capture's contents.

  Returns:
    How many records parsed, and the main-loop request bodies among them.
  """
  records = 0
  bodies: list[Mapping[str, Any]] = []
  for line in raw.splitlines():
    line = line.strip()
    if not line:
      continue
    try:
      record = json.loads(line)
    except json.JSONDecodeError:
      continue
    if not isinstance(record, Mapping):
      continue
    records += 1
    request = record.get("request")
    body = request.get("body") if isinstance(request, Mapping) else None
    if isinstance(body, Mapping) and body.get("tools"):
      bodies.append(body)
  return records, bodies


def read_seam(raw: str) -> SeamReading:
  """Read a ``cc-reverse-proxy`` capture for the two resume artifacts.

  Counted over the **fullest** main-loop request rather than summed across all
  of them: every request re-serializes the whole conversation, so a sum counts
  the same message once per later request and would report a much larger sample
  than exists.

  Args:
    raw: The capture's contents (``""`` when the run wrote none).

  Returns:
    The reading, premises included.
  """
  records, bodies = _main_loop_bodies(raw)
  fullest: list[Any] = []
  for body in bodies:
    messages = body.get("messages")
    if isinstance(messages, list) and len(messages) > len(fullest):
      fullest = messages

  assistants = 0
  synthetic = 0
  continuations = 0
  for message in fullest:
    if not isinstance(message, Mapping):
      continue
    texts = [text.strip() for text in _text_blocks(message.get("content"))]
    if message.get("role") == "assistant":
      assistants += 1
      synthetic += sum(text == SYNTHETIC_ASSISTANT_TEXT for text in texts)
    elif message.get("role") == "user":
      continuations += sum(text == RESUME_CONTINUATION_TEXT for text in texts)

  return SeamReading(
      records=records,
      main_loop_requests=len(bodies),
      assistant_messages=assistants,
      synthetic_assistants=synthetic,
      resume_continuations=continuations,
  )


def seam_is_clean(reading: SeamReading) -> bool:
  """Report whether the anchored seam held, as a positive chain.

  Every link must hold, and the premises come first:

  1. the capture parsed into at least one record;
  2. at least one of them is a main-loop request;
  3. that request holds at least one assistant message — so the instrument had
     something to look at;
  4. **then** the fabricated assistant turn is absent;
  5. and so is its sibling, whose presence means the seam reverted even though
     it is not itself a disqualifier.

  Args:
    reading: What :func:`read_seam` found.

  Returns:
    Whether the seam can be *shown* to be clean. A capture this cannot read
    reports ``False`` — the honest answer is "cannot say", and on a check whose
    whole job is to notice a silent reversion, "cannot say" must not pass.
  """
  return (
      reading.records > 0
      and reading.main_loop_requests > 0
      and reading.assistant_messages > 0
      and reading.synthetic_assistants == 0
      and reading.resume_continuations == 0
  )
