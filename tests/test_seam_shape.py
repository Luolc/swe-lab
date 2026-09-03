"""The guard behind the anchored seam, and the proof it can fire.

This check is load-bearing in a way most are not: the segmented loop is allowed
to exist because `--resume-session-at` fabricates no assistant turn, that flag
is undocumented, and a build that changed it would not go red — it would quietly
revert to the dirty seam and keep producing traces that `spec.md` §7
disqualifies. So the guard needs both arms *and* its premises:

- the **fires** arm reads a capture carrying the artifacts and reports them, so
  a clean reading elsewhere means something;
- the **clean** arm reads an anchored capture and passes, so a guard that
  refused everything would not be mistaken for a working one;
- the **premise** arms show that an empty or unreadable capture reads as *not
  clean*, because "the check could not run" must not look like "the seam held".

Fixtures are built to the shapes the feasibility report measured on the wire —
§6.2 for the dirty seam, §9.1 for the anchored one — not captured here.
"""

from __future__ import annotations

from pathlib import Path

from swe_lab.trace_synthesis.seam_shape import (
    read_seam,
    seam_is_clean,
)

_DATA = Path(__file__).resolve().parent / "data"
_DIRTY = _DATA / "proxy_seam_dirty.jsonl"
_ANCHORED = _DATA / "proxy_seam_anchored.jsonl"


def test_the_guard_fires_on_a_dirty_seam():
  """The arm that makes every zero elsewhere informative."""
  reading = read_seam(_DIRTY.read_text(encoding="utf-8"))

  assert reading.synthetic_assistants == 1
  assert reading.resume_continuations == 1
  assert not seam_is_clean(reading)


def test_an_anchored_seam_reads_clean():
  """The control arm: a guard that refused everything would fail here."""
  reading = read_seam(_ANCHORED.read_text(encoding="utf-8"))

  assert reading.synthetic_assistants == 0
  assert reading.resume_continuations == 0
  assert seam_is_clean(reading)


def test_the_premises_are_part_of_the_chain():
  """An unreadable capture is *not clean*, because it cannot say it is clean.

  Failing closed is the whole design: the artifact this guards against arrives
  silently, so an instrument that saw nothing must not report the same thing as
  one that looked and found nothing.
  """
  empty = read_seam("")
  assert empty.records == 0
  assert not seam_is_clean(empty)

  # Records that parse, but none of them an agent-loop call.
  auxiliary = read_seam('{"request": {"body": {"messages": []}}}\n')
  assert auxiliary.records == 1
  assert auxiliary.main_loop_requests == 0
  assert not seam_is_clean(auxiliary)

  # A main-loop call the instrument can read, holding no assistant message: it
  # has nothing to look at, so its zero is not a finding.
  no_actor = read_seam(
      '{"request": {"body": {"tools": [{"name": "Bash"}], "messages":'
      ' [{"role": "user", "content": [{"type": "text", "text": "go"}]}]}}}\n'
  )
  assert no_actor.main_loop_requests == 1
  assert no_actor.assistant_messages == 0
  assert not seam_is_clean(no_actor)


def test_the_count_is_over_the_fullest_request_not_summed():
  """Every request re-serializes the conversation; a sum inflates the sample.

  The dirty fixture carries the artifacts in its second record only, and the
  first record's messages are a prefix of it — so a summing reader would report
  a larger sample than exists, which is the mistake the report's §9.3 had to
  make explicit about its own denominators.
  """
  reading = read_seam(_DIRTY.read_text(encoding="utf-8"))

  assert reading.main_loop_requests == 2
  # Two assistant messages appear across the two records; only one
  # conversation holds them, and that is the one counted.
  assert reading.assistant_messages == 2
  assert reading.synthetic_assistants == 1
