#!/usr/bin/env python3
"""The host-side Supervisor: guidebook, belief state, and every hint logged.

Phase C of the [spec](../../../docs/trace-synthesis/spec.md): after each tool
call completes, judge whether the blind actor is on track against the Oracle's
guidebook and, when it is not, produce **one short directional hint**.

Everything privileged is here and only here. The guidebook never enters the
container; the belief state is this process's memory; the hint log is a host
file. What crosses into the sandbox is a single sentence of hint text, and
nothing else — which is the spec's "the belief state lives host-side, outside
the sandbox" made mechanical rather than promised.

The hook cannot import this: it runs inside the container and must be able to
hold nothing. It cannot call it over HTTP either — this box runs `ufw` with
`default deny (incoming)`, so nothing on the Docker bridge can reach a host
port at all (measured 2026-09-01; the same wall breaks `capture="proxy"`, whose
agent dials `host.docker.internal`). So the transport is the **bind-mounted
workspace**: the hook drops a request file, this polls for it, and the answer
comes back as a file beside it. No network leaves the sandbox, which is a
better fit for the isolation argument than the HTTP version was — the sandbox
gets no route to the host at all, only a shared directory it already had.

**Every judgement is logged, hint or no hint** (``hint_log.jsonl``), with its
tool boundary, the model's verdict and the hint text in full. Whether the
channel then preserved a hint is a separate question, answered after the run by
``analyze.py`` against the converted conversation — the spec's hard constraint
is that a lost hint be *detectable*, and it is detectable only if the host
recorded the hint independently of the trace.

Not production code: `docs/trace-synthesis/plans/README.md` task 05 is what
turns any of this into something the repo keeps.
"""

from __future__ import annotations

from collections.abc import Sequence
import argparse
import hashlib
from dataclasses import dataclass, field
import json
import os
import pathlib
import re
import threading
import time
import urllib.request

# The marker the hint is wrapped in. Task 02 measured the 2x2 of tag name
# against hint body and this is the only cell that drew no objection from the
# actor; the *why* is unresolved, so the recommendation is empirical.
HINT_TAG = "oracle_hint"

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_MODEL_TIMEOUT_S = 75.0

# How much of the run's own history the Supervisor is shown. Long enough that
# it can see a loop forming, short enough that a 200-call rollout does not make
# every judgement cost a full context.
_HISTORY_CALLS = 14

SYSTEM_PROMPT = """\
You are the Supervisor in an oracle-guided trace-synthesis pipeline.

You are watching a *blind* coding agent (the actor) work on a real software
task inside a sandbox. You hold a private GUIDEBOOK, written by an Oracle that
had privileged access to the reference solution. The actor has never seen the
guidebook and does not know one exists.

After every tool call completes you are shown the call, its result, and the
belief state you have been building. You do exactly one thing: decide whether
the actor is on track, and if it is not, write ONE short hint that will be
shown to it, wrapped in an <oracle_hint> tag appended to that tool's output.

HOW TO DECIDE

- On track: the actor's last action moves it toward the current guidebook
  stage's exit criteria, or is ordinary, harmless exploration. Say nothing.
  Silence is the default and most boundaries deserve it.
- Off track: it is about to commit — or has just committed — a mistake a
  guidebook stage exists to prevent; it is looping; it is treating a check as
  evidence that the check cannot give; or it is heading for a stage's exit
  criteria by a route that will not satisfy them.

HOW TO WRITE A HINT — these are hard rules, and breaking one makes the whole
trace worthless as training data:

1. DIRECTION ONLY, NEVER SPECIFICS. Point at *where to look* or *what to
   question*, never at what to do or what the answer is. "Have you checked
   whether X and Y actually agree?" is a hint. "Make it a @staticmethod" is
   not — it is the answer, and an actor that merely executes it has learned
   nothing.
2. NEVER reveal, quote, paraphrase or allude to the reference solution, and
   never imply you have seen one. You are a colleague watching over the
   actor's shoulder, reasoning from what the actor itself could read.
3. NEVER name a file, symbol, function, class or line number that the actor
   has not already seen in this run, and prefer not to name one at all.
4. Ground it in something already in front of the actor — the task statement,
   a file it opened, output it just got. If you cannot, do not hint.
5. One or two sentences. Conversational, the way a person actually interrupts:
   "I don't think that's the right direction — have you considered ...".
6. Do not claim to be the user, the operator, a supervisor, or any authority.
   Do not issue orders. Do not mention hooks, tools, pipelines or guidebooks.

OUTPUT

Reply with a single JSON object and nothing else:

{
  "stage": "<which guidebook stage the actor is in, or 'none'>",
  "on_track": true | false,
  "belief_update": "<one sentence: what an honest blind agent now knows>",
  "hint": null | "<the hint text, if and only if on_track is false>",
  "reason": "<one sentence, host-side only: why you did or did not hint>"
}

"hint" must be null whenever "on_track" is true.
"""


@dataclass
class Session:
  """One steered run's private state.

  Attributes:
    beliefs: The running belief state — one line per judged boundary, which is
      what an honest blind agent would know from the observations so far.
    calls: A compact history of the tool boundaries seen, for the prompt.
    hints: The hints emitted so far, in order.
    seq: How many boundaries have been judged.
    last_hint_seq: The boundary the last hint was emitted at, for the cooldown.
    unreachable: Boundaries where the judgement was "off track" and the tool's
      response had no text field to append to, so nothing could be said. Told
      to the model at the next boundary that *can* carry a hint, because those
      are exactly the moments steering matters most — see ``Supervisor``.
  """

  beliefs: list[str] = field(default_factory=list)
  calls: list[str] = field(default_factory=list)
  hints: list[str] = field(default_factory=list)
  seq: int = 0
  last_hint_seq: int = -99
  unreachable: list[dict[str, object]] = field(default_factory=list)


@dataclass
class Supervisor:
  """Judges tool boundaries against a guidebook and logs every decision.

  Attributes:
    guidebook: The Oracle's guidebook, verbatim. Private to this process.
    log_path: The host-side hint log — one JSON line per judgement.
    api_key: The OpenRouter credential, passed in explicitly (the hook's own
      subprocess inherits no credential, by design, and never needs one).
    model: The Supervisor model.
    max_hints: Ceiling on hints per session. A hint at every boundary would be
      dictation, not steering.
    cooldown: Boundaries that must pass after a hint before another is
      allowed, so the actor gets room to act on the one it has.
  """

  guidebook: str
  log_path: pathlib.Path
  api_key: str
  model: str
  max_hints: int = 8
  cooldown: int = 2
  _sessions: dict[str, Session] = field(default_factory=dict)
  _lock: threading.Lock = field(default_factory=threading.Lock)

  def judge(self, request: dict[str, object]) -> dict[str, object]:
    """Judge one tool boundary and return the hook's decision.

    Args:
      request: The hook's description of the boundary.

    Returns:
      The decision: ``seq``, ``tag``, and ``hint`` (``None`` for no hint).
    """
    with self._lock:
      session = self._sessions.setdefault(str(request.get("session", "")), Session())
      session.seq += 1
      seq = session.seq
      history = list(session.calls[-_HISTORY_CALLS:])
      beliefs = list(session.beliefs[-_HISTORY_CALLS:])
      hints_so_far = list(session.hints)
      cooling = seq - session.last_hint_seq <= self.cooldown
      spent = len(session.hints) >= self.max_hints
      unreachable = list(session.unreachable)

    event = str(request.get("event", ""))
    tool = str(request.get("tool_name", ""))
    summary = f"#{seq} {event} {tool}: {_one_line(str(request.get('tool_input', '')))}"

    started = time.monotonic()
    verdict, error, usage = self._ask_model(
        history=history,
        beliefs=beliefs,
        hints_so_far=hints_so_far,
        event=event,
        tool=tool,
        tool_input=str(request.get("tool_input", "")),
        tool_response=str(request.get("tool_response", "")),
        unreachable=unreachable,
    )
    latency = round(time.monotonic() - started, 2)

    hint = verdict.get("hint")
    hint = hint.strip() if isinstance(hint, str) and hint.strip() else None
    carried_from: list[int] = []
    # The event guard is the hook's too, but it belongs here as well: what the
    # log says was emitted has to be what could actually have been injected.
    suppressed = None
    appendable = bool(request.get("appendable"))
    if hint and event != "PostToolUse":
      suppressed = "event cannot carry updatedToolOutput"
    elif hint and not appendable:
      # Measured 2026-09-01: `Edit`'s tool_response is a structured object with
      # no free-text field, and `updatedToolOutput` is validated against the
      # tool's declared schema — so a hint cannot be appended there at all. The
      # actor is unreachable at exactly the boundary it is committing a mistake
      # at. Carried forward instead of dropped.
      suppressed = "tool response has no text field to append to"
    elif hint and spent:
      suppressed = "hint budget spent"
    elif hint and cooling:
      suppressed = "cooldown"
    if suppressed:
      hint = None

    with self._lock:
      session = self._sessions[str(request.get("session", ""))]
      session.calls.append(summary)
      belief = verdict.get("belief_update")
      if isinstance(belief, str) and belief.strip():
        session.beliefs.append(f"#{seq}: {belief.strip()}")
      if hint:
        session.hints.append(hint)
        session.last_hint_seq = seq
        carried_from = [int(item["seq"]) for item in session.unreachable]
        session.unreachable.clear()
      elif suppressed == "tool response has no text field to append to":
        session.unreachable.append(
            {"seq": seq, "tool": tool, "reason": verdict.get("reason")}
        )

    self._log({
        "ts": time.time(),
        "session": request.get("session"),
        "seq": seq,
        "event": event,
        "tool": tool,
        # Carried through from the hook so the host's record and the converted
        # conversation share an identity, not just an ordering — see
        # `reconcile.py`.
        "tool_use_id": request.get("tool_use_id"),
        "tool_input": _one_line(str(request.get("tool_input", "")), 400),
        "stage": verdict.get("stage"),
        "on_track": verdict.get("on_track"),
        "belief_update": verdict.get("belief_update"),
        "reason": verdict.get("reason"),
        "hint": hint,
        "hint_emitted": bool(hint),
        "suppressed": suppressed,
        "appendable": appendable,
        # Which earlier boundaries this hint is finally delivering for. The
        # gap is the deferral latency, and an intervention that never gets a
        # `carried_from` line is one the actor never heard.
        "carried_from": carried_from,
        "unreachable_pending": [int(item["seq"]) for item in unreachable],
        "raw_hint": verdict.get("hint"),
        "model": self.model,
        "model_error": error,
        "usage": usage,
        "latency_s": latency,
    })
    return {"seq": seq, "tag": HINT_TAG, "hint": hint}

  def _ask_model(
      self,
      *,
      history: Sequence[str],
      beliefs: Sequence[str],
      hints_so_far: Sequence[str],
      event: str,
      tool: str,
      tool_input: str,
      tool_response: str,
      unreachable: Sequence[str],
  ) -> tuple[dict[str, object], str | None, dict[str, object] | None]:
    """Put the boundary to the Supervisor model.

    Returns:
      The parsed verdict, an error string when the call or the parse failed,
      and the provider's usage record.
    """
    user = "\n\n".join([
        f"# GUIDEBOOK (private — the actor has never seen this)\n\n{self.guidebook}",
        "# TOOL CALLS SO FAR\n\n" + ("\n".join(history) or "(none yet)"),
        "# BELIEF STATE\n\n" + ("\n".join(beliefs) or "(nothing established yet)"),
        "# HINTS ALREADY GIVEN\n\n"
        + ("\n".join(f"- {h}" for h in hints_so_far) or "(none)"),
        "# BOUNDARIES YOU WANTED TO INTERVENE AT AND COULD NOT REACH\n\n"
        + (
            "\n".join(
                f"#{item['seq']} ({item['tool']}): {item['reason']}"
                for item in unreachable
            )
            + "\n\nThis boundary can carry a hint. If any of those still"
            " stands, say it now."
            if unreachable
            else "(none)"
        ),
        f"# THE BOUNDARY TO JUDGE\n\nevent: {event}\ntool: {tool}\n\n"
        f"## tool input\n{tool_input}\n\n## tool result\n{tool_response}",
    ])
    body = json.dumps({
        "model": self.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "max_tokens": 1200,
        "temperature": 0.3,
    }).encode()
    request = urllib.request.Request(
        _OPENROUTER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
      with urllib.request.urlopen(request, timeout=_MODEL_TIMEOUT_S) as response:
        payload = json.load(response)
    except Exception as error:  # noqa: BLE001 — a Supervisor failure is data
      return {}, f"{type(error).__name__}: {error}", None
    try:
      text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
      return {}, f"unexpected response shape: {_one_line(json.dumps(payload), 300)}", None
    # `content` is present and **null** when the model spent its budget on
    # reasoning without emitting a message — measured 2026-09-01, three
    # boundaries in one run. The key exists, so the shape check above passes;
    # only the type catches it.
    if not isinstance(text, str):
      return (
          {},
          f"null message content: {_one_line(json.dumps(payload), 300)}",
          payload.get("usage"),
      )
    verdict = _parse_json_object(text)
    if verdict is None:
      return {}, f"unparseable verdict: {_one_line(text, 300)}", payload.get("usage")
    return verdict, None, payload.get("usage")

  def log_error(self, error: str, request_file: str) -> None:
    """Record a boundary the judge could not judge.

    It belongs in the same log as the judgements: the log is the host's
    independent account of what happened at every boundary, and a boundary that
    was skipped is exactly the kind of gap that is invisible from the trace.

    Args:
      error: What went wrong.
      request_file: The request this concerns.
    """
    self._log({
        "ts": time.time(),
        "watcher_error": error,
        "request_file": request_file,
    })

  def _log(self, record: dict[str, object]) -> None:
    """Append one judgement to the host-side hint log."""
    with self._lock:
      with self.log_path.open("a") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def _one_line(text: str, limit: int = 200) -> str:
  """Collapse text to a single bounded line, for a log or a history entry."""
  flat = " ".join(text.split())
  return flat if len(flat) <= limit else flat[:limit] + "…"


def _parse_json_object(text: str) -> dict[str, object] | None:
  """Parse the model's reply, tolerating a fenced or prefixed JSON object.

  Args:
    text: The model's reply.

  Returns:
    The object, or ``None`` when no JSON object could be read out of it.
  """
  try:
    parsed = json.loads(text)
  except json.JSONDecodeError:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
      return None
    try:
      parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
      return None
  return parsed if isinstance(parsed, dict) else None


# Where the hook and this process exchange files, inside the run's workspace.
# Staged as a mount by the driver so the directory is owned by the *host* user:
# the container runs as root and can write anywhere, but the host cannot write
# into a root-created directory, and only one of the two can be fixed.
IO_DIR = "steer_io"
_POLL_S = 0.15


@dataclass
class Watcher:
  """Answers request files under a run directory until it is stopped.

  Attributes:
    supervisor: The judge.
    root: The run's output directory; request files are found anywhere under
      it, because the workflow allocates the per-attempt workspace itself and
      its path is not known when this starts.
  """

  supervisor: Supervisor
  root: pathlib.Path
  _stop: threading.Event = field(default_factory=threading.Event)
  _thread: threading.Thread | None = None

  def start(self) -> None:
    """Begin polling on a background thread."""
    self._thread = threading.Thread(target=self._loop, daemon=True)
    self._thread.start()

  def shutdown(self) -> None:
    """Stop polling and wait briefly for the thread to finish."""
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=5)

  def _loop(self) -> None:
    while not self._stop.is_set():
      for path in sorted(self.root.rglob(f"{IO_DIR}/*.req.json")):
        # Nothing this thread does may kill it. A judgement that raises costs
        # one boundary; a dead poller costs **every boundary after it**, and it
        # costs them invisibly from the actor's side — the hook just waits out
        # its deadline and fails open. Measured 2026-09-01: a `null` message
        # content raised out of `judge`, the thread died at boundary 13, and the
        # remaining boundaries of that rollout were never judged at all.
        try:
          self._answer(path)
        except Exception as error:  # noqa: BLE001 — a dead poller is worse
          self._refuse(path, f"{type(error).__name__}: {error}")
      self._stop.wait(_POLL_S)

  def _answer(self, path: pathlib.Path) -> None:
    """Judge one request file and land its answer beside it.

    Args:
      path: The request file. Removed once answered, so it is judged once.
    """
    try:
      request = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
      return  # a partially written file; the rename makes this rare, not never
    try:
      path.unlink()
    except OSError:
      pass
    self._reply(path, self.supervisor.judge(request))

  def _refuse(self, path: pathlib.Path, error: str) -> None:
    """Answer a request the judge could not, and say so host-side.

    The hook is waiting on a file. Leaving it to time out turns one broken
    judgement into a stalled tool call, so the answer is always written; what
    changes is that it carries no hint and the log carries the reason.

    Args:
      path: The request file.
      error: What went wrong.
    """
    self.supervisor.log_error(error, path.name)
    try:
      path.unlink()
    except OSError:
      pass
    self._reply(path, {"hint": None})

  def _reply(self, path: pathlib.Path, decision: dict[str, object]) -> None:
    """Land one answer beside its request, atomically.

    Args:
      path: The request file the answer belongs to.
      decision: What to tell the hook.
    """
    answer = path.with_name(path.name.removesuffix(".req.json") + ".resp.json")
    temporary = answer.with_suffix(".tmp")
    _ = temporary.write_text(json.dumps(decision))
    temporary.rename(answer)


def key_pool() -> list[str]:
  """Return the configured key pool.

  Returns:
    The keys, in the order ``OPENROUTER_API_KEYS`` lists them.

  Raises:
    RuntimeError: If the variable is unset.
  """
  keys = [k for k in os.environ.get("OPENROUTER_API_KEYS", "").split(",") if k]
  if not keys:
    raise RuntimeError("OPENROUTER_API_KEYS is unset; source .envrc.local")
  return keys


def key_fingerprint(key: str) -> str:
  """Return a short, non-reversible name for a key.

  The report has to say *which* key a run spent, and the pool's index alone
  stops meaning that the moment the pool is reordered. A hash prefix names the
  key without being the key.

  Args:
    key: The credential.

  Returns:
    The first eight hex characters of its sha256.
  """
  return hashlib.sha256(key.encode()).hexdigest()[:8]


def key_credits(key: str) -> dict[str, float]:
  """Return one key's own balance.

  **Each key in the pool is a separate OpenRouter account with its own
  balance** — measured 2026-09-01 across all 25, which answered with remaining
  balances spread over $117–$278. Reading one key's ``total_credits`` and
  calling it an account-level pool, as an earlier version of this file's
  accounting did, understates the budget by more than an order of magnitude.
  So accounting is per key, and there is no total to report.

  Args:
    key: The credential.

  Returns:
    ``total``, ``used`` and ``remaining``, in dollars.
  """
  request = urllib.request.Request(
      "https://openrouter.ai/api/v1/credits",
      headers={"Authorization": f"Bearer {key}"},
  )
  with urllib.request.urlopen(request, timeout=20) as response:
    data = json.load(response)["data"]
  total = float(data["total_credits"])
  used = float(data["total_usage"])
  return {"total": total, "used": used, "remaining": total - used}


def openrouter_key(start: int = 0) -> tuple[int, str]:
  """Return a usable key from the pool, preferring the one asked for.

  Not every key in the pool is live, so this asks OpenRouter before a run is
  spent on one. **No key value is ever returned in a message, logged, or
  printed** — the caller gets the key, and the errors name only how many were
  tried.

  ``start`` exists for concurrency, not for thrift: OpenRouter rate-limits per
  key, so parallel runs are given different ones. (That the limit is per key is
  OpenRouter's documented model; we have not hit one, so treat "three parallel
  runs need three keys" as a precaution, not as a measurement.)

  Args:
    start: Index in the pool to try first; later keys are the fallback.

  Returns:
    The index that answered, and its key.

  Raises:
    RuntimeError: If the variable is unset, or no key from ``start`` on
      answers.
  """
  keys = key_pool()
  for index in range(start, len(keys)):
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {keys[index]}"},
    )
    try:
      with urllib.request.urlopen(request, timeout=20) as response:
        _ = json.load(response)
      return index, keys[index]
    except Exception:  # noqa: BLE001 — a dead key is data, not an error
      continue
  raise RuntimeError(
      f"no key from index {start} on answered ({len(keys)} in the pool)"
  )


def main() -> None:
  """Run the Supervisor standalone, for a manual probe."""
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.add_argument("--guidebook", required=True)
  _ = parser.add_argument("--log", required=True)
  _ = parser.add_argument("--watch", required=True, help="run directory to poll")
  _ = parser.add_argument("--model", default="anthropic/claude-opus-5")
  args = parser.parse_args()

  supervisor = Supervisor(
      guidebook=pathlib.Path(args.guidebook).read_text(),
      log_path=pathlib.Path(args.log),
      api_key=openrouter_key()[1],
      model=args.model,
  )
  watcher = Watcher(supervisor, pathlib.Path(args.watch))
  watcher.start()
  print(f"supervisor watching {args.watch}; ctrl-c to stop")
  try:
    while True:
      time.sleep(3600)
  except KeyboardInterrupt:
    watcher.shutdown()


if __name__ == "__main__":
  main()
