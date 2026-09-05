"""Replay the repo's supervisor over one recorded rollout, varying only `N`.

`N` is how many of the actor's **assistant messages** accumulate before the
policy is consulted once. Today's supervisor consults it at every stream event
(`N` = "every event", the `replicate` arm). Everything else the real run used is
carried over verbatim: model, budget, cooldown, window, criterion, transport.

Nothing here re-runs the actor. The event stream is a recording, so a correction
this replay produces is never delivered and never changes what happens next.

Usage:
  python replay.py shape                 # deterministic batch shapes, no calls
  python replay.py self-check            # driver == repo Supervisor, no calls
  python replay.py run --pass a          # one full pass over every arm
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping, Sequence
import dataclasses
import datetime
import hashlib
import json
import pathlib
import subprocess
import time
from typing import Any

from swe_lab.conversation import Message
from swe_lab.harnesses.claude_code.convert import event_to_message
from swe_lab.trace_synthesis.judge import (
    JUDGE_INSTRUCTIONS,
    supervising_policy,
    Transport,
)
from swe_lab.trace_synthesis.supervisor import (
    EvidenceFilter,
    Intervention,
    LOG_KIND_GAP,
    LOG_KIND_LAPSE,
    LOG_KIND_SILENT,
    LOG_KIND_SPOKE,
    LOG_KIND_UNJUDGED,
    Observation,
    PolicyLapseError,
    SpeakWhenOffTrack,
    Unjudged,
)
from swe_lab.workflow.definitions import SUPERVISOR_MODEL, SUPERVISOR_TRANSPORT

HERE = pathlib.Path(__file__).parent
RUNS = HERE / "runs"

#: How the corpus is named in committed artifacts. A runtime path carries the
#: operator's home directory, which `docs/conventions.md` forbids in committed
#: evidence, and it identifies nothing a reader needs: what pins the corpus is
#: the digest and the event count recorded beside this.
CORPUS_ID = "~/corpora/swe-lab/first-e2e-2026-09-02/r0/rollout/a0"

# The recorded rollout, resolved at run time. Read-only: nothing here writes
# under it.
CORPUS = pathlib.Path(CORPUS_ID).expanduser()
EVENT_STREAM = CORPUS / "claude_code.event_stream.jsonl"

#: The instance the recorded rollout was drawn from (`run.json` in the
#: corpus's own store), used only by `verify-task`.
INSTANCE_ID = (
    "instance_internetarchive__openlibrary"
    "-5de7de19211e71b29b2f2ba3b1dff2fe065d660f"
    "-v08d8e8889ec945ab821fb156c04c7d2e2810debb"
)
CONVERSATION = CORPUS / "conversation.json"

# Carried over from the real run without change (AGENTS-level: `N` is the only
# variable). Model: `swe_lab.workflow.definitions.SUPERVISOR_MODEL`. Budget:
# `SUPERVISOR_BUDGET`. Cooldown and window: `supervising_policy` defaults.
MODEL = SUPERVISOR_MODEL
BUDGET = 3
COOLDOWN = 4
WINDOW = 8


@dataclasses.dataclass(frozen=True)
class Arm:
  """One configuration of the sweep.

  Attributes:
    name: Directory name under `runs/`.
    n: Assistant messages per batch, or `None` for "every event" (today).
    window: How many admitted records the judge sees.
    budget: How many corrections this arm may deliver. Only the post-hoc
      control arm moves it; see `PREREGISTRATION.md`.
  """

  name: str
  n: int | None
  window: int = WINDOW
  budget: int = BUDGET


# The pre-registered arms, in the pre-registered execution order.
ARMS: tuple[Arm, ...] = (
    Arm("replicate", None),
    Arm("n1", 1),
    Arm("n3", 3),
    Arm("n5", 5),
    Arm("n6", 6),
    Arm("n10", 10),
    Arm("n10_w15", 10, window=15),
    # Added after both registered passes had run, and labelled post-hoc in
    # PREREGISTRATION.md. It is the repo's own control arm
    # (`definitions.CONTROL_BUDGET`): every event is a boundary, so it differs
    # from `replicate` only in whether speech happened, and it never speaks, so
    # it differs from `n1` only in the boundary set. It changes no registered
    # reading; it exists to separate two differences the registered arms move
    # together.
    Arm("replicate_budget0", None, budget=0),
)


def load_events() -> list[dict[str, Any]]:
  """Return the recorded stream events in order.

  Returns:
    Every decoded event of the rollout, oldest first.
  """
  with EVENT_STREAM.open(encoding="utf-8") as handle:
    return [json.loads(line) for line in handle if line.strip()]


def load_task() -> str:
  """Return the task text the actor and the supervisor were both given.

  The supervisor's `task` is `instance.prompt()`
  (`swe_lab.rollout` builds it that way), which for SWE-bench Pro is the problem
  statement, the requirements and the interfaces under fixed headers. It is not
  stored as its own artifact, so it is recovered from the recorded conversation:
  the first user message's last text block, which is what the harness sent.

  Returns:
    The task text, verbatim.
  """
  conversation = json.loads(CONVERSATION.read_text(encoding="utf-8"))
  for message in conversation["messages"]:
    if message["role"] != "user":
      continue
    texts = [b["text"] for b in message["content"] if b.get("type") == "text"]
    # The first block is the SDK's own <system-reminder>; the task is the last.
    return texts[-1]
  raise SystemExit("no user message in the recorded conversation")


def assistant_event_indices(events: Sequence[Mapping[str, Any]]) -> list[int]:
  """Return the 1-based positions of the actor's assistant events.

  Args:
    events: The recorded stream, in order.

  Returns:
    Every index `i` (1-based) where `events[i - 1]` is an assistant event.
  """
  return [i for i, e in enumerate(events, 1) if e.get("type") == "assistant"]


def boundaries_for(arm: Arm, events: Sequence[Mapping[str, Any]]) -> list[int]:
  """Return the cursors at which this arm consults the policy.

  Args:
    arm: The configuration.
    events: The recorded stream.

  Returns:
    For `replicate`, every event position. Otherwise the position of every
    `n`-th assistant event; a trailing partial batch is not judged.
  """
  if arm.n is None:
    return list(range(1, len(events) + 1))
  turns = assistant_event_indices(events)
  return [turns[k] for k in range(arm.n - 1, len(turns), arm.n)]


@dataclasses.dataclass
class RecordingTransport:
  """A transport, with what answered each call kept beside it.

  `ModelJudge` records the response model and the sampling sent; latency,
  finish reason and usage are not on that path and are unrecoverable
  afterwards, so they are captured here. The payload is passed through
  unchanged — this adds no request field.

  Attributes:
    send: What actually sends the request. Injectable so `self-check` drives
      the real driver with no network; a run leaves it at the default.
    prompts: The user half of every request, in order. What the self-check
      compares, because two runs that built the same prompts accumulated the
      same evidence in the same windows.
    calls: Number of transport calls attempted. Counted before the payload is
      inspected so a stale recorder cannot turn every call into an unobserved
      exception and still make the self-check look healthy.
    seen: One entry per call made since the last `drain`.
  """

  send: Transport = SUPERVISOR_TRANSPORT
  prompts: list[str] = dataclasses.field(default_factory=list)
  calls: int = 0
  seen: list[dict[str, Any]] = dataclasses.field(default_factory=list)

  def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Send one request and record how it answered.

    Args:
      payload: The request body built by the judge or the writer.

    Returns:
      The decoded response.

    Raises:
      Exception: Whatever the real transport raises, unchanged — the policy
        turns it into a lapse exactly as it does in a live run.
    """
    self.calls += 1
    self.prompts.append(payload["messages"][0]["content"])
    prompt_chars = len(payload["system"]) + sum(
        len(message["content"]) for message in payload["messages"]
    )
    started = time.monotonic()
    try:
      response = self.send(payload)
    except Exception as error:  # noqa: BLE001 - recorded, then re-raised
      self.seen.append(
          {
              "seconds": round(time.monotonic() - started, 3),
              "transport_error": repr(error),
              "prompt_chars": prompt_chars,
          }
      )
      raise
    content = response.get("content", [{}])[0].get("text")
    self.seen.append(
        {
            "seconds": round(time.monotonic() - started, 3),
            "response_model": response.get("model"),
            "provider": response.get("provider"),
            "finish_reason": response.get("stop_reason"),
            "content_is_null": content is None,
            "content_chars": len(content) if isinstance(content, str) else 0,
            "usage": response.get("usage"),
            "prompt_chars": prompt_chars,
        }
    )
    return response

  def drain(self) -> list[dict[str, Any]]:
    """Return and clear the calls recorded since the last drain.

    Returns:
      The call records, in order: the judge's first, the writer's after it.
    """
    out, self.seen = self.seen, []
    return out


def replay(
    *,
    arm: Arm,
    events: Sequence[Mapping[str, Any]],
    task: str,
    policy: Any,
    boundaries: Sequence[int],
    criterion: Any,
) -> Iterator[dict[str, Any]]:
  """Drive one arm over the recorded stream, yielding one row per boundary.

  Mirrors `Supervisor.observe`: the same `EvidenceFilter`, the same accumulation
  order, the same two-tier failure handling (a `PolicyLapseError` bounds the
  failure to this boundary; anything else is an unbounded gap). What it changes
  is *when* the policy is consulted — `Supervisor` consults it on every event.

  Args:
    arm: The configuration, for the arm name recorded on each row. The window
      is **not** taken from here: it is read off the policy, which is what
      actually slices the evidence, so the recorded `evidence_in_window` cannot
      drift from what the judge saw.
    events: The recorded stream, in order.
    task: What the actor was asked to do.
    policy: The `SpeakWhenOffTrack` under test.
    boundaries: The cursors at which to consult it.
    criterion: The loaded criterion, for measuring the prompt.

  Yields:
    One row per boundary.
  """
  transport: RecordingTransport = policy.judge.transport
  window: int = policy.window
  evidence_filter = EvidenceFilter()
  evidence: list[Message] = []
  said: list[Intervention] = []
  due = set(boundaries)
  previous_boundary = 0
  admitted_at_previous = 0

  for cursor, event in enumerate(events, 1):
    record, disposition = evidence_filter.admit(event_to_message(event))
    if record is not None:
      evidence.append(record)
    if cursor not in due:
      continue

    windowed = policy.selector.select(evidence, limit=window)
    observation = Observation(
        task=task,
        evidence=tuple(evidence),
        cursor=cursor,
        said=tuple(said),
    )
    prompt_builder = policy.judge.prompt_builder
    empty_prompt = prompt_builder.build(
        dataclasses.replace(observation, evidence=()), criterion
    )
    window_prompt = prompt_builder.build(
        dataclasses.replace(observation, evidence=tuple(windowed)), criterion
    )
    row: dict[str, Any] = {
        "arm": arm.name,
        "cursor": cursor,
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
        "evidence_disposition": disposition,
        "assistant_events_so_far": sum(
            1 for e in events[:cursor] if e.get("type") == "assistant"
        ),
        "cursor_gap": cursor - previous_boundary,
        "window": window,
        "admitted_total": len(evidence),
        "admitted_new_since_last_boundary": len(evidence) - admitted_at_previous,
        "evidence_in_window": len(windowed),
        "evidence_dropped_by_window": len(evidence) - len(windowed),
        "new_evidence_dropped_by_window": max(
            0, len(evidence) - len(windowed) - admitted_at_previous
        ),
        "rendered_nonempty_in_window": sum(
            prompt_builder.build(
                dataclasses.replace(observation, evidence=(record,)), criterion
            )
            != empty_prompt
            for record in windowed
        ),
        "rendered_evidence_chars": max(
            0, len(window_prompt) - len(empty_prompt)
        ),
        "prompt_chars": len(window_prompt),
    }
    previous_boundary = cursor
    admitted_at_previous = len(evidence)

    try:
      intervention = policy.consider(observation)
    except PolicyLapseError as error:
      row |= {"kind": LOG_KIND_LAPSE, "reason": repr(error)}
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
      row |= {"kind": LOG_KIND_GAP, "reason": repr(error)}
    else:
      # Classified by type, and with the shipped `Supervisor`'s own row
      # vocabulary: `Unjudged` is neither a silence nor a correction, and a
      # witness that collapses a distinction its subject makes is not a
      # witness.
      if isinstance(intervention, Unjudged):
        row |= {
            "kind": LOG_KIND_UNJUDGED,
            "reason": intervention.reason,
            "text": None,
        }
      elif intervention is None:
        row |= {"kind": LOG_KIND_SILENT, "text": None}
      else:
        said.append(intervention)
        row |= {"kind": LOG_KIND_SPOKE, "text": intervention.text}
    calls = transport.drain()
    judge_call = policy.judge.calls[-1] if policy.judge.calls else None
    row |= {
        "calls": calls,
        "judge_raw": judge_call.raw if judge_call else None,
        "judge_response_model": judge_call.response_model if judge_call else None,
        "judge_sampling_sent": (
            dict(judge_call.sampling_sent) if judge_call else None
        ),
        "markers_total": len(policy.markers),
    }
    yield row


def _verdict_fields(row: Mapping[str, Any]) -> dict[str, Any]:
  """Parse the judge's own answer out of a row, for the analysis.

  Args:
    row: One replay row.

  Returns:
    `off_track`, `self_correcting` and `reason` when the answer parsed, else
    `None`s. Never coerced: a non-boolean is left as `None`.
  """
  raw = row.get("judge_raw")
  if not isinstance(raw, str):
    return {"off_track": None, "self_correcting": None, "judge_reason": None}
  try:
    answer = json.loads(raw)
  except (json.JSONDecodeError, TypeError):
    return {"off_track": None, "self_correcting": None, "judge_reason": None}
  if not isinstance(answer, dict):
    return {"off_track": None, "self_correcting": None, "judge_reason": None}
  return {
      "off_track": (
          answer["off_track"]
          if type(answer.get("off_track")) is bool
          else None
      ),
      "self_correcting": (
          answer["self_correcting"]
          if type(answer.get("self_correcting")) is bool
          else None
      ),
      "judge_reason": str(answer.get("reason", "")),
  }


def git_sha() -> str:
  """Return the commit this ran at.

  Returns:
    The short sha, with `-dirty` when the tree has changes.
  """
  sha = subprocess.run(
      ["git", "rev-parse", "--short", "HEAD"],
      capture_output=True,
      text=True,
      check=True,
      cwd=HERE,
  ).stdout.strip()
  dirty = subprocess.run(
      ["git", "status", "--porcelain"],
      capture_output=True,
      text=True,
      check=True,
      cwd=HERE,
  ).stdout.strip()
  return f"{sha}-dirty" if dirty else sha


def cmd_shape(_: argparse.Namespace) -> None:
  """Print the deterministic batch shape of every arm — no model calls."""
  events = load_events()
  turns = assistant_event_indices(events)
  print(f"events={len(events)} assistant={len(turns)}")
  print(
      f"{'N':>4} {'judgments':>10} {'first@event':>12} {'max batch':>10}"
      f" {'mean batch':>11} {'overflow w=8':>13}"
  )
  for n in (1, 2, 3, 4, 5, 6, 7, 8, 10, 20):
    bounds = boundaries_for(Arm(f"n{n}", n), events)
    if not bounds:
      continue
    evidence_filter = EvidenceFilter()
    admitted = 0
    counts, previous = [], 0
    for cursor, event in enumerate(events, 1):
      record, _ = evidence_filter.admit(event_to_message(event))
      if record is not None:
        admitted += 1
      if cursor in set(bounds):
        counts.append(admitted - previous)
        previous = admitted
    overflow = sum(1 for c in counts if c > WINDOW) / len(counts)
    print(
        f"{n:>4} {len(bounds):>10} {bounds[0]:>12} {max(counts):>10}"
        f" {sum(counts) / len(counts):>11.1f} {overflow:>12.0%}"
    )


def _canned(payload: Mapping[str, Any]) -> Mapping[str, Any]:
  """Answer a judge or writer request without a network call.

  Args:
    payload: The request body the real call would have sent.

  Returns:
    A response of the shape `ModelJudge` and `ModelWriter` read. The judge is
    always told the actor is off track and not recovering, so the policy's
    speaking path — writer call, budget, cooldown — is exercised too.
  """
  judging = payload["system"] == JUDGE_INSTRUCTIONS
  content = (
      '{"off_track": true, "self_correcting": false, "reason": "stub",'
      ' "running_state": "Current checkpoint: replay stub"}'
      if judging
      else "a stub line"
  )
  return {
      "model": "stub-model",
      "content": [{"type": "text", "text": content}],
      "stop_reason": "end_turn",
      "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0},
  }


def _stub_policy() -> SpeakWhenOffTrack:
  """Build the real policy with a transport that makes no network call.

  Returns:
    A `SpeakWhenOffTrack` holding the pinned criterion, the real `ModelJudge`
    and `ModelWriter`, and a `RecordingTransport` over :func:`_canned`.
  """
  return supervising_policy(
      model="stub-model",
      transport=RecordingTransport(send=_canned),
      budget=BUDGET,
      cooldown=COOLDOWN,
      window=WINDOW,
  )


def driver_matches_supervisor() -> dict[str, Any]:
  """Run this file's driver and the shipped `Supervisor` over the same stream.

  The driver is the newer half of every comparison in `REPORT.md`, so it is
  cleared against the shipped consumer before any of its numbers are read.
  Both sides run the **real** `SpeakWhenOffTrack`, `ModelJudge` and
  `ModelWriter` over the real 170 events; only the network is replaced.

  What is compared is deliberately two things, because either alone is weak:
  the row sequence — cursor, kind, evidence disposition — and **the exact
  prompt text of every model call**, which is the observable carrying the
  accumulation and the window. A driver that admitted the wrong record, sliced
  the window differently or judged at the wrong moment changes the prompts even
  where it leaves the row kinds intact.

  Returns:
    The two row sequences and the two prompt sequences, for a caller to assert
    on.
  """
  from swe_lab.trace_synthesis.supervisor import Supervisor

  events = load_events()
  task = load_task()

  shipped_policy = _stub_policy()
  shipped_rows: list[Mapping[str, Any]] = []
  supervisor = Supervisor(
      policy=shipped_policy,
      task=task,
      sink=lambda _: None,
      log=shipped_rows.append,
      now=lambda: datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
  )
  for event in events:
    _ = supervisor.observe(event)

  every_event = Arm("self-check", None)
  driver_policy = _stub_policy()
  driver_rows = list(
      replay(
          arm=every_event,
          events=events,
          task=task,
          policy=driver_policy,
          boundaries=boundaries_for(every_event, events),
          criterion=driver_policy.criterion,
      )
  )

  return {
      "shipped_rows": [
          {"cursor": r["cursor"], "kind": r["kind"], "evidence": r["evidence"]}
          for r in shipped_rows
      ],
      "driver_rows": [
          {
              "cursor": r["cursor"],
              "kind": r["kind"],
              "evidence": r["evidence_disposition"],
          }
          for r in driver_rows
      ],
      "shipped_prompts": list(shipped_policy.judge.transport.prompts),
      "driver_prompts": list(driver_policy.judge.transport.prompts),
      "shipped_calls": shipped_policy.judge.transport.calls,
      "driver_calls": driver_policy.judge.transport.calls,
  }


def committed_home_paths() -> list[str]:
  """Return every committed artifact of this experiment carrying a home path.

  `docs/conventions.md` forbids operator PII — a home path included — in any
  committed record, and the manifests are the file that most wants to write
  one, since the corpus is read from under `$HOME`.

  Returns:
    The offending paths, relative to this directory; empty when clean.
  """
  offenders: list[str] = []
  for path in sorted(RUNS.rglob("*")):
    if not path.is_file():
      continue
    if "/home/" in path.read_text(encoding="utf-8", errors="replace"):
      offenders.append(str(path.relative_to(HERE)))
  return offenders


def cmd_self_check(_: argparse.Namespace) -> None:
  """Assert the driver reproduces `Supervisor`, and that no artifact leaks."""
  found = driver_matches_supervisor()
  assert found["shipped_rows"] == found["driver_rows"], (
      "driver diverges from Supervisor"
  )
  assert found["shipped_prompts"] == found["driver_prompts"], (
      "driver builds different prompts from Supervisor"
  )
  assert found["shipped_calls"] > 0 and found["driver_calls"] > 0, (
      "self-check exercised no model calls"
  )
  assert found["shipped_prompts"] and found["driver_prompts"], (
      "self-check recorded no model prompts"
  )
  leaks = committed_home_paths()
  assert not leaks, f"committed artifacts carry a home path: {leaks}"
  print(
      f"self-check OK: {len(found['driver_rows'])} rows identical,"
      f" {len(found['driver_prompts'])} prompts byte-identical,"
      f" no home path in committed artifacts"
  )


def cmd_verify_task(_: argparse.Namespace) -> None:
  """Assert the recovered task text is byte-identical to `instance.prompt()`.

  `load_task` recovers the task from the recorded conversation because no
  artifact stores it. That recovery is an assumption until it is checked
  against the dataset the run was drawn from, so this checks it. Needs the
  gitignored SWE-bench Pro parquet present (`datasets/swebench_pro/README.md`).
  """
  from swe_lab.datasets.loader import load_dataset

  expected = load_dataset("swebench_pro").require(INSTANCE_ID).prompt()
  found = load_task()
  assert expected == found, "recovered task text is not instance.prompt()"
  print(
      f"verify-task OK: {len(found)} chars,"
      f" sha256 {hashlib.sha256(found.encode()).hexdigest()}"
  )


def cmd_run(args: argparse.Namespace) -> None:
  """Execute one full pass over every arm, in the pre-registered order."""
  events = load_events()
  task = load_task()
  for arm in ARMS:
    out = RUNS / arm.name / args.pass_id
    if (out / "manifest.json").exists():
      print(f"skip {arm.name}/{args.pass_id}: already run")
      continue
    out.mkdir(parents=True, exist_ok=True)
    transport = RecordingTransport()
    policy = supervising_policy(
        model=MODEL,
        transport=transport,
        budget=arm.budget,
        cooldown=COOLDOWN,
        window=arm.window,
    )
    bounds = boundaries_for(arm, events)
    started = datetime.datetime.now(datetime.UTC)
    print(f"{arm.name}/{args.pass_id}: {len(bounds)} boundaries", flush=True)
    rows: list[dict[str, Any]] = []
    with (out / "judgments.jsonl").open("w", encoding="utf-8") as handle:
      for row in replay(
          arm=arm,
          events=events,
          task=task,
          policy=policy,
          boundaries=bounds,
          criterion=policy.criterion,
      ):
        row |= _verdict_fields(row)
        rows.append(row)
        _ = handle.write(json.dumps(row) + "\n")
        handle.flush()
    finished = datetime.datetime.now(datetime.UTC)
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "arm": arm.name,
                "pass": args.pass_id,
                "n": arm.n,
                # From the policy, not the arm: the policy is what slices the
                # evidence, so the manifest records what the judge actually saw.
                "window": policy.window,
                "budget": arm.budget,
                "cooldown": COOLDOWN,
                "model_requested": MODEL,
                "criterion_digest": policy.criterion.digest,
                "criterion_overlap_checked": policy.criterion.overlap_checked,
                "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
                "task_chars": len(task),
                "corpus": CORPUS_ID,
                "event_stream_sha256": hashlib.sha256(
                    EVENT_STREAM.read_bytes()
                ).hexdigest(),
                "events": len(events),
                "boundaries": len(bounds),
                "git_sha": git_sha(),
                "started": started.isoformat(),
                "finished": finished.isoformat(),
                "wall_seconds": round(
                    (finished - started).total_seconds(), 1
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"  done in {(finished - started).total_seconds():.0f}s;"
        f" spoke={sum(1 for r in rows if r['kind'] == 'spoke')}"
        f" lapse={sum(1 for r in rows if r['kind'] == 'lapse')}",
        flush=True,
    )


def main() -> None:
  """Parse arguments and dispatch."""
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(required=True)
  sub.add_parser("shape").set_defaults(func=cmd_shape)
  sub.add_parser("self-check").set_defaults(func=cmd_self_check)
  sub.add_parser("verify-task").set_defaults(func=cmd_verify_task)
  run = sub.add_parser("run")
  run.add_argument("--pass", dest="pass_id", required=True, choices=["a", "b"])
  run.set_defaults(func=cmd_run)
  args = parser.parse_args()
  args.func(args)


if __name__ == "__main__":
  main()
