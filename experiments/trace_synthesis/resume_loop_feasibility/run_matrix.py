"""Drives the arms of Phase 0, one question per subcommand.

Execution is strictly sequential and each stage can void the ones after it, so
each subcommand is run and read before the next is started. The kill conditions
are the ones registered in README.md before any run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import socket
import subprocess
import time
import uuid

import driver
import toy_task

HERE = pathlib.Path(__file__).resolve().parent
RUNS = HERE / "runs"
WORKDIR = HERE / "workdir"

# Flags whose existence and declared argument shape the probe establishes, plus
# one control arm that is known not to exist. Two arms printing different errors
# is what makes this a check rather than an observation.
PROBE_FLAGS = (
    "--max-turns",
    "--definitely-not-a-flag",
    "--resume-session-at",
    "--resume-drops-turn",
    "--task-budget",
    "--rewind-files",
    "--max-budget-usd",
    "--system-prompt-snapshot",
)


def q0(_: argparse.Namespace) -> int:
  """Records the build coordinates and the two-arm flag probe. Costs nothing.

  Args:
    _: unused parsed arguments.

  Returns:
    A process exit code.
  """
  out_dir = RUNS / "q0"
  out_dir.mkdir(parents=True, exist_ok=True)
  arms: list[dict[str, str]] = []
  for flag in PROBE_FLAGS:
    completed = subprocess.run(
        ["claude", "-p", flag], capture_output=True, text=True, check=False
    )
    arms.append({
        "flag": flag,
        "stderr_first_line": completed.stderr.strip().splitlines()[0]
        if completed.stderr.strip()
        else "",
        "exit_code": str(completed.returncode),
    })
  record = {
      "claude_version": driver.claude_version(),
      "git_commit": driver.git_commit(),
      "arms": arms,
  }
  _ = (out_dir / "evidence.json").write_text(json.dumps(record, indent=2))
  print(json.dumps(record, indent=2))
  return 0


def q2a(args: argparse.Namespace) -> int:
  """Q2'.1 — what does `--max-turns` count?

  Runs the identical toy task at several `--max-turns` values and unbounded,
  and reports assistant messages, `tool_use` blocks and `tool_result` blocks
  separately, so a model batching several tool calls into one message cannot be
  confused with a different counting unit.

  Args:
    args: parsed arguments; `args.turns` selects which arms to run.

  Returns:
    A process exit code.
  """
  summary: list[dict[str, object]] = []
  for label in args.turns:
    max_turns = None if label == "none" else int(label)
    workdir = WORKDIR / f"q2a-{label}"
    _ = toy_task.build(workdir)
    out_dir = RUNS / f"q2a-maxturns-{label}"
    meta = driver.run_segment(
        out_dir,
        workdir,
        toy_task.PROMPT,
        session_id=str(uuid.uuid4()),
        max_turns=max_turns,
        timeout=900,
    )
    ledger = toy_task.ledger(workdir)
    _ = (out_dir / "ledger.json").write_text(json.dumps(ledger, indent=2))
    row = {
        "arm": label,
        "exit_code": meta["exit_code"],
        "assistant_messages": meta["assistant_messages"],
        "tool_use_blocks": meta["tool_use_blocks"],
        "tool_result_blocks": meta["tool_result_blocks"],
        "result_subtype": meta["result_subtype"],
        "result_stop_reason": meta["result_stop_reason"],
        "result_terminal_reason": meta["result_terminal_reason"],
        "assistant_events": meta["assistant_events"],
        "result_is_error": meta["result_is_error"],
        "result_num_turns": meta["result_num_turns"],
        "total_cost_usd": meta["total_cost_usd"],
        "task_complete": ledger["result_exists"],
        "nonces_in_result": len(ledger["nonces_found_in_result"]),
        "elapsed_s": meta["elapsed_s"],
    }
    summary.append(row)
    print(json.dumps(row, indent=2), flush=True)
  print(json.dumps({"ledger_total_usd": round(driver.ledger_total(), 4)}))
  _ = (RUNS / "q2a-summary.json").write_text(json.dumps(summary, indent=2))
  return 0


RECALL_PROMPT = (
    "Without using any tools, state the TOKEN value that was written in"
    " step2.txt. Reply with only the 32-character token and nothing else."
)


def project_dirs(workdir: pathlib.Path) -> list[pathlib.Path]:
  """Returns the Claude Code project directories that hold this cwd's sessions.

  The harness derives the directory name from the absolute working directory by
  replacing path separators and underscores; rather than reimplementing that
  mapping, this globs for the session files themselves, which is what
  `streamjson_input/transcripts.py` already does.

  Args:
    workdir: the agent's working directory.

  Returns:
    Matching project directories, possibly empty.
  """
  root = pathlib.Path(
      os.environ.get("CLAUDE_CONFIG_DIR") or (pathlib.Path.home() / ".claude")
  )
  resolved = str(workdir.resolve())
  for separator in ("/", "_"):
    resolved = resolved.replace(separator, "-")
  candidate = root / "projects" / resolved
  return [candidate] if candidate.exists() else []


def session_files(workdir: pathlib.Path) -> dict[str, dict[str, object]]:
  """Snapshots the session files for a working directory.

  Args:
    workdir: the agent's working directory.

  Returns:
    A mapping of session-file stem to its size and sha256, so a later snapshot
    can be diffed for growth against a fork.
  """
  snapshot: dict[str, dict[str, object]] = {}
  for directory in project_dirs(workdir):
    for path in sorted(directory.glob("*.jsonl")):
      body = path.read_bytes()
      snapshot[path.stem] = {
          "bytes": len(body),
          "sha256": hashlib.sha256(body).hexdigest(),
      }
  return snapshot


def read_paths(events_path: pathlib.Path) -> list[str]:
  """Lists the basenames every tool call in a run touched.

  Args:
    events_path: the run's `events.jsonl`.

  Returns:
    Basenames appearing in tool inputs, in call order.
  """
  names: list[str] = []
  for event in driver.read_events(events_path):
    if event.get("type") != "assistant":
      continue
    message = event.get("message") or {}
    if not isinstance(message, dict):
      continue
    for block in message.get("content") or []:
      if isinstance(block, dict) and block.get("type") == "tool_use":
        names.append(json.dumps(block.get("input"))[:400])
  return names


def q1(args: argparse.Namespace) -> int:
  """Q1 + Q2'.3-.5 — does a `--max-turns` cut resume, and does the count reset?

  The positive chain is registered in README.md: the nonce must be recalled
  verbatim, from a file that no longer exists, with zero tool calls, while a
  fresh-session control arm asked the same question fails to produce it.

  Args:
    args: parsed arguments; `args.segments` sets how deep the loop goes.

  Returns:
    A process exit code.
  """
  workdir = WORKDIR / "q1"
  nonces = toy_task.build(workdir)
  session_id = str(uuid.uuid4())
  before = session_files(workdir)

  first = driver.run_segment(
      RUNS / "q1-seg1",
      workdir,
      toy_task.PROMPT,
      session_id=session_id,
      max_turns=5,
      timeout=900,
  )
  after_first = session_files(workdir)
  touched = read_paths(RUNS / "q1-seg1" / "events.jsonl")
  saw_step2 = any("step2.txt" in entry for entry in touched)

  # Delete the file the nonce lived in, so segment 2 cannot re-read it.
  step2 = workdir / "step2.txt"
  if step2.exists():
    step2.unlink()

  second = driver.run_segment(
      RUNS / "q1-seg2",
      workdir,
      RECALL_PROMPT,
      resume=session_id,
      max_turns=1,
      timeout=600,
  )
  after_second = session_files(workdir)
  answer = final_text(RUNS / "q1-seg2" / "events.jsonl")

  # Negative control: a fresh session, same question, same deleted file.
  control_dir = RUNS / "q1-control"
  control = driver.run_segment(
      control_dir,
      workdir,
      RECALL_PROMPT,
      session_id=str(uuid.uuid4()),
      max_turns=1,
      timeout=600,
  )
  control_answer = final_text(control_dir / "events.jsonl")

  nonce = nonces["step2"]
  chain = {
      "1_segment1_exit_zero_or_maxturns": first["exit_code"] in (0, 1)
      and first["result_subtype"] in ("success", "error_max_turns"),
      "2_segment1_read_step2": saw_step2,
      "3_step2_absent_at_segment2": not step2.exists(),
      "4_segment2_ran": second["result_subtype"] is not None,
      "5_nonce_verbatim_in_answer": nonce in answer,
      "6_segment2_zero_tool_use": second["tool_use_blocks"] == 0,
      "7_control_lacks_nonce": nonce not in control_answer,
  }
  record = {
      "chain": chain,
      "chain_holds": all(chain.values()),
      "segment1": {k: first[k] for k in (
          "exit_code", "assistant_messages", "tool_use_blocks",
          "tool_result_blocks", "result_subtype", "result_terminal_reason",
          "total_cost_usd")},
      "segment2": {k: second[k] for k in (
          "exit_code", "assistant_messages", "tool_use_blocks",
          "result_subtype", "result_terminal_reason", "total_cost_usd")},
      "segment2_usage": second["usage"],
      "control": {k: control[k] for k in (
          "exit_code", "assistant_messages", "tool_use_blocks",
          "result_subtype", "total_cost_usd")},
      "session_files_before": before,
      "session_files_after_segment1": after_first,
      "session_files_after_segment2": after_second,
      "session_ids_seen": sorted(
          set(after_second) | set(after_first) | set(before)
      ),
      "ledger_total_usd": round(driver.ledger_total(), 4),
  }
  _ = (RUNS / "q1-summary.json").write_text(json.dumps(record, indent=2))
  print(json.dumps(record, indent=2))
  return 0


def final_text(events_path: pathlib.Path) -> str:
  """Concatenates every assistant text block in a run.

  Args:
    events_path: the run's `events.jsonl`.

  Returns:
    The assistant's text, joined with newlines.
  """
  chunks: list[str] = []
  for event in driver.read_events(events_path):
    if event.get("type") != "assistant":
      continue
    message = event.get("message") or {}
    if not isinstance(message, dict):
      continue
    for block in message.get("content") or []:
      if isinstance(block, dict) and block.get("type") == "text":
        chunks.append(str(block.get("text", "")))
  return "\n".join(chunks)


CONTINUE_PROMPT = "Continue."


def first_text(events_path: pathlib.Path) -> str:
  """Returns the first assistant text block of a run, for Q4's labelling.

  Args:
    events_path: the run's `events.jsonl`.

  Returns:
    The text, or the empty string when the segment produced none.
  """
  for event in driver.read_events(events_path):
    if event.get("type") != "assistant":
      continue
    message = event.get("message") or {}
    if not isinstance(message, dict):
      continue
    for block in message.get("content") or []:
      if isinstance(block, dict) and block.get("type") == "text":
        return str(block.get("text", ""))
  return ""


def tool_calls(events_path: pathlib.Path) -> list[dict[str, object]]:
  """Lists each tool call a run made, as name plus input.

  Args:
    events_path: the run's `events.jsonl`.

  Returns:
    One record per `tool_use` block, in order.
  """
  calls: list[dict[str, object]] = []
  for event in driver.read_events(events_path):
    if event.get("type") != "assistant":
      continue
    message = event.get("message") or {}
    if not isinstance(message, dict):
      continue
    for block in message.get("content") or []:
      if isinstance(block, dict) and block.get("type") == "tool_use":
        calls.append({"name": block.get("name"), "input": block.get("input")})
  return calls


def step_of(call: dict[str, object]) -> str | None:
  """Returns the step file a tool call touched, when it touched one.

  Args:
    call: a record from `tool_calls`.

  Returns:
    A basename like `step3.txt`, or None.
  """
  blob = json.dumps(call.get("input"))
  for index in range(1, toy_task.STEPS + 1):
    if f"step{index}.txt" in blob:
      return f"step{index}.txt"
  return None


def label_segment(
    text: str,
    calls: list[dict[str, object]],
    already_read: set[str],
    complete: bool,
) -> str:
  """Applies Q4's labels, which were fixed in README.md before any run.

  REDO and DONE are decided mechanically against the side-effect ledger.
  CONTINUE and RESTATE are separated by whether the segment opened with prose
  instead of an action, which is stated in the report as a judgement.

  Args:
    text: the segment's first assistant text block.
    calls: the segment's tool calls in order.
    already_read: step files read in earlier segments.
    complete: whether the task was actually finished.

  Returns:
    One of CONTINUE, RESTATE, REDO, DONE, OTHER.
  """
  repeats = [
      step for step in (step_of(call) for call in calls)
      if step is not None and step in already_read
  ]
  if repeats:
    return "REDO"
  if not calls and not complete and text:
    return "DONE"
  if calls and not text.strip():
    return "CONTINUE"
  if calls and text.strip():
    return "RESTATE"
  return "OTHER"


def loop_arm(
    tag: str,
    segments: int,
    *,
    perturb: bool = False,
    delay_before_last: float = 0.0,
) -> dict[str, object]:
  """Runs one segmented arm: `--max-turns 1` per segment, neutral continue.

  Args:
    tag: run-directory prefix, also the fixture directory name.
    segments: how many segments to run.
    perturb: whether to change the working tree between segments, which is the
      control arm that shows a cache miss is visible to this instrument.
    delay_before_last: seconds to wait before the final segment, for Q5b.

  Returns:
    The arm's per-segment records.
  """
  workdir = WORKDIR / tag
  _ = toy_task.build(workdir)
  session_id = str(uuid.uuid4())
  rows: list[dict[str, object]] = []
  already_read: set[str] = set()
  for index in range(1, segments + 1):
    out_dir = RUNS / f"{tag}-seg{index}"
    if index == segments and delay_before_last:
      time.sleep(delay_before_last)
    if perturb and index > 1:
      _ = (workdir / f"perturb{index}.txt").write_text("x" * 64)
    meta = driver.run_segment(
        out_dir,
        workdir,
        toy_task.PROMPT if index == 1 else CONTINUE_PROMPT,
        session_id=session_id if index == 1 else None,
        resume=None if index == 1 else session_id,
        max_turns=1,
        timeout=600,
    )
    calls = tool_calls(out_dir / "events.jsonl")
    ledger = toy_task.ledger(workdir)
    text = first_text(out_dir / "events.jsonl")
    label = label_segment(
        text, calls, already_read, bool(ledger["result_exists"])
    )
    for call in calls:
      step = step_of(call)
      if step:
        already_read.add(step)
    usage = meta["usage"]
    rows.append({
        "segment": index,
        "label": label,
        "first_text": text[:400],
        "tools": [c["name"] for c in calls],
        "steps_touched": [step_of(c) for c in calls],
        "exit_code": meta["exit_code"],
        "result_subtype": meta["result_subtype"],
        "input_tokens": usage.get("input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_creation": usage.get("cache_creation"),
        "total_cost_usd": meta["total_cost_usd"],
        "elapsed_s": meta["elapsed_s"],
    })
    print(json.dumps(rows[-1], indent=2)[:700], flush=True)
  return {
      "tag": tag,
      "session_id": session_id,
      "segments": rows,
      "ledger": toy_task.ledger(workdir),
      "seam_total_cost_usd": round(
          sum(float(r["total_cost_usd"] or 0) for r in rows), 6
      ),
  }


def q45(args: argparse.Namespace) -> int:
  """Q4 + Q5 at depth, plus the unsegmented control on the same task.

  Args:
    args: parsed arguments.

  Returns:
    A process exit code.
  """
  out: dict[str, object] = {"arms": []}
  for repeat in range(1, args.repeats + 1):
    out["arms"].append(loop_arm(f"q45-R{repeat}", args.segments))
  for repeat in range(1, args.repeats + 1):
    workdir = WORKDIR / f"q45-Z{repeat}"
    _ = toy_task.build(workdir)
    out_dir = RUNS / f"q45-Z{repeat}"
    meta = driver.run_segment(
        out_dir,
        workdir,
        toy_task.PROMPT,
        session_id=str(uuid.uuid4()),
        timeout=900,
    )
    out["arms"].append({
        "tag": f"q45-Z{repeat}",
        "unsegmented": True,
        "assistant_messages": meta["assistant_messages"],
        "tool_use_blocks": meta["tool_use_blocks"],
        "result_subtype": meta["result_subtype"],
        "usage": meta["usage"],
        "total_cost_usd": meta["total_cost_usd"],
        "elapsed_s": meta["elapsed_s"],
        "ledger": toy_task.ledger(workdir),
    })
    print(json.dumps(out["arms"][-1], indent=2)[:500], flush=True)
  out["ledger_total_usd"] = round(driver.ledger_total(), 4)
  _ = (RUNS / "q45-summary.json").write_text(json.dumps(out, indent=2))
  print(json.dumps({"ledger_total_usd": out["ledger_total_usd"]}))
  return 0


# The Go build is the only one with header redaction; the two python ports have
# none and leaked a live token once. Located by name rather than by listing the
# directory it was expected in — `ls <expected parent>` prints the same thing
# whether a binary is elsewhere or absent, which is how this experiment briefly
# concluded it did not exist.
PROXY_GLOB = ".cache/bin/**/cc-reverse-proxy"


def find_proxy() -> pathlib.Path | None:
  """Locates the redacting wire-capture proxy on this machine.

  Returns:
    The binary's path, or None when no build is present.
  """
  for root in (HERE.parents[2], pathlib.Path.home() / "dev" / "swe-lab"):
    hits = sorted(root.glob(PROXY_GLOB))
    if hits:
      return hits[0]
  return None


def q3wire(args: argparse.Namespace) -> int:
  """Q3b's deciding measurement: do the seam records reach the API request?

  The transcript is not the wire. A record can sit in the session file and
  never be sent; only a capture of the request body can tell those apart, and
  they are indistinguishable in every other observation this experiment makes.

  Capture goes to a scratch directory outside the repository and is never
  committed; only the structural conclusion is reported.

  Args:
    args: parsed arguments; `args.out` is the scratch directory.

  Returns:
    A process exit code.
  """
  proxy = find_proxy()
  if proxy is None:
    print(json.dumps({"wire": "could not determine", "reason": "no proxy build"}))
    return 1
  scratch = pathlib.Path(args.out)
  scratch.mkdir(parents=True, exist_ok=True)
  log = scratch / "proxy.jsonl"
  handle = subprocess.Popen(
      [
          str(proxy),
          "--port",
          str(args.port),
          "--target",
          "https://api.anthropic.com",
          "--output",
          str(log),
      ],
      stdout=(scratch / "proxy.stderr.log").open("w", encoding="utf-8"),
      stderr=subprocess.STDOUT,
  )
  try:
    base_url = f"http://127.0.0.1:{args.port}"
    for _ in range(100):
      try:
        with socket.create_connection(("127.0.0.1", args.port), timeout=0.2):
          break
      except OSError:
        time.sleep(0.1)
    workdir = WORKDIR / "q3wire"
    _ = toy_task.build(workdir)
    session_id = str(uuid.uuid4())
    _ = driver.run_segment(
        scratch / "seg1",
        workdir,
        toy_task.PROMPT,
        session_id=session_id,
        max_turns=2,
        timeout=600,
        base_url=base_url,
    )
    _ = driver.run_segment(
        scratch / "seg2",
        workdir,
        CONTINUE_PROMPT,
        resume=session_id,
        max_turns=1,
        timeout=600,
        base_url=base_url,
    )
  finally:
    handle.terminate()
    _ = handle.wait(timeout=30)
  print(json.dumps({"log": str(log), "exists": log.exists()}))
  return 0


def q5b(args: argparse.Namespace) -> int:
  """Q5b — how long may the supervisor deliberate before the seam costs a miss?

  Two arms identical but for the pause before the final segment. The reportable
  quantity is that segment's `cache_read_input_tokens`: a collapse names the
  supervisor's latency budget, and its staying high raises that budget by the
  TTL actually in force.

  Args:
    args: parsed arguments; `args.delay` is the long arm's pause in seconds.

  Returns:
    A process exit code.
  """
  fast = loop_arm("q5b-nodelay", 2)
  slow = loop_arm("q5b-delay", 2, delay_before_last=args.delay)
  record = {
      "delay_seconds": args.delay,
      "nodelay_segment2": fast["segments"][1],
      "delay_segment2": slow["segments"][1],
      "ledger_total_usd": round(driver.ledger_total(), 4),
  }
  _ = (RUNS / "q5b-summary.json").write_text(json.dumps(record, indent=2))
  print(json.dumps(record, indent=2))
  return 0


def wire_shapes(log: pathlib.Path) -> list[dict[str, object]]:
  """Reduces a proxy capture to role sequences and seam counts.

  Only structure is returned: no request bodies and no headers leave the
  scratch directory the capture lives in.

  Args:
    log: the proxy's JSONL output.

  Returns:
    One record per captured request.
  """
  out: list[dict[str, object]] = []
  for line in log.read_text(encoding="utf-8").splitlines():
    if not line.strip():
      continue
    body = json.loads(line)["request"]["body"]
    messages = body.get("messages") or []
    blob = json.dumps(messages)
    out.append({
        "messages": len(messages),
        "role_sequence": [m.get("role") for m in messages],
        "seam_user_text_blocks": blob.count("Continue from where you left off."),
        "seam_synthetic_assistant": blob.count("No response requested."),
        "system_reminder_blocks": blob.count("<system-reminder>"),
    })
  return out


def credential_gate(log: pathlib.Path) -> dict[str, object]:
  """Checks a capture in both directions before anything is read from it.

  A zero on the negative arm alone is undiscriminating: it reads the same
  whether redaction worked or the search looked in the wrong place. The
  positive arm is what makes the negative one informative.

  Args:
    log: the proxy's JSONL output.

  Returns:
    The two arms' counts. Values are never included.
  """
  raw = log.read_text(encoding="utf-8")
  return {
      "redaction_marker_occurrences": raw.lower().count("[redacted]"),
      "credential_shapes": {
          "sk_ant": raw.count("sk-ant-"),
          "bearer_jwt": len(re.findall(r"(?i)bearer\s+ey", raw)),
          "jwt_shaped": len(re.findall(r"eyJ[A-Za-z0-9_-]{20,}", raw)),
      },
  }


def q7(args: argparse.Namespace) -> int:
  """Q7 — do `--resume-session-at` / `--resume-drops-turn` control the seam?

  Resumes an existing session at a chosen message id, on the wire, and reports
  whether the seam records still appear. The outcome is reported as either
  "observed: <what changed>" or "could not determine"; nothing between.

  Args:
    args: parsed arguments.

  Returns:
    A process exit code.
  """
  proxy = find_proxy()
  if proxy is None:
    print(json.dumps({"q7": "could not determine", "reason": "no proxy build"}))
    return 1
  scratch = pathlib.Path(args.out)
  scratch.mkdir(parents=True, exist_ok=True)
  log = scratch / "proxy.jsonl"
  handle = subprocess.Popen(
      [str(proxy), "--port", str(args.port), "--target",
       "https://api.anthropic.com", "--output", str(log)],
      stdout=(scratch / "proxy.stderr.log").open("w", encoding="utf-8"),
      stderr=subprocess.STDOUT,
  )
  try:
    for _ in range(100):
      try:
        with socket.create_connection(("127.0.0.1", args.port), timeout=0.2):
          break
      except OSError:
        time.sleep(0.1)
    extra = ("--resume-session-at", args.at)
    if args.drops_turn:
      extra += ("--resume-drops-turn", args.drops_turn)
    meta = driver.run_segment(
        scratch / "seg",
        WORKDIR / args.workdir,
        CONTINUE_PROMPT,
        resume=args.session,
        max_turns=1,
        extra=extra,
        timeout=600,
        base_url=f"http://127.0.0.1:{args.port}",
    )
  finally:
    handle.terminate()
    _ = handle.wait(timeout=30)
  record = {
      "extra_flags": list(extra),
      "exit_code": meta["exit_code"],
      "result_subtype": meta["result_subtype"],
      "credential_gate": credential_gate(log) if log.exists() else None,
      "wire": wire_shapes(log) if log.exists() else None,
  }
  print(json.dumps(record, indent=2))
  return 0


def last_message_uuid(session_id: str) -> str | None:
  """Returns the uuid of the last real message record in a session.

  Attachment, latch and queue records are skipped: `--resume-session-at` takes
  a message id.

  Args:
    session_id: the session to read.

  Returns:
    The uuid, or None when no transcript is found.
  """
  root = pathlib.Path(
      os.environ.get("CLAUDE_CONFIG_DIR") or (pathlib.Path.home() / ".claude")
  )
  hits = sorted((root / "projects").glob(f"*/{session_id}.jsonl"))
  if not hits:
    return None
  found: str | None = None
  for line in hits[0].read_text(encoding="utf-8").splitlines():
    if not line.strip():
      continue
    record = json.loads(line)
    if record.get("type") in ("user", "assistant") and record.get("uuid"):
      found = str(record["uuid"])
  return found


def q7loop(args: argparse.Namespace) -> int:
  """The production shape: a segmented loop that resumes at the latest message.

  Segment 2 resumes with `--resume-session-at <last message id>`, which is the
  loop as it would actually be built. The wire is captured so the seam can be
  compared against the unsegmented control's role sequence.

  Args:
    args: parsed arguments.

  Returns:
    A process exit code.
  """
  proxy = find_proxy()
  if proxy is None:
    print(json.dumps({"q7loop": "could not determine", "reason": "no proxy"}))
    return 1
  scratch = pathlib.Path(args.out)
  scratch.mkdir(parents=True, exist_ok=True)
  log = scratch / "proxy.jsonl"
  handle = subprocess.Popen(
      [str(proxy), "--port", str(args.port), "--target",
       "https://api.anthropic.com", "--output", str(log)],
      stdout=(scratch / "proxy.stderr.log").open("w", encoding="utf-8"),
      stderr=subprocess.STDOUT,
  )
  rows: list[dict[str, object]] = []
  try:
    for _ in range(100):
      try:
        with socket.create_connection(("127.0.0.1", args.port), timeout=0.2):
          break
      except OSError:
        time.sleep(0.1)
    base_url = f"http://127.0.0.1:{args.port}"
    workdir = WORKDIR / "q7loop"
    _ = toy_task.build(workdir)
    session_id = str(uuid.uuid4())
    for index in range(1, args.segments + 1):
      anchor = None if index == 1 else last_message_uuid(session_id)
      meta = driver.run_segment(
          scratch / f"seg{index}",
          workdir,
          toy_task.PROMPT if index == 1 else CONTINUE_PROMPT,
          session_id=session_id if index == 1 else None,
          resume=None if index == 1 else session_id,
          max_turns=1,
          extra=() if anchor is None else ("--resume-session-at", anchor),
          timeout=600,
          base_url=base_url,
      )
      rows.append({
          "segment": index,
          "anchor_used": anchor is not None,
          "exit_code": meta["exit_code"],
          "result_subtype": meta["result_subtype"],
          "tools": meta["tool_names"],
          "cache_read_input_tokens": meta["usage"].get("cache_read_input_tokens"),
          "cache_creation_input_tokens": meta["usage"].get(
              "cache_creation_input_tokens"
          ),
          "total_cost_usd": meta["total_cost_usd"],
      })
  finally:
    handle.terminate()
    _ = handle.wait(timeout=30)
  record = {
      "segments": rows,
      "credential_gate": credential_gate(log) if log.exists() else None,
      "wire": wire_shapes(log) if log.exists() else None,
      "ledger_total_usd": round(driver.ledger_total(), 4),
  }
  _ = (RUNS / "q7loop-evidence.json").write_text(json.dumps(record, indent=2))
  print(json.dumps(record, indent=2))
  return 0


def main() -> int:
  """Dispatches a subcommand.

  Returns:
    A process exit code.
  """
  parser = argparse.ArgumentParser()
  sub = parser.add_subparsers(dest="cmd", required=True)
  _ = sub.add_parser("q0").set_defaults(func=q0)
  parser_q2a = sub.add_parser("q2a")
  _ = parser_q2a.add_argument("--turns", nargs="+", default=["1", "2", "3", "none"])
  _ = parser_q2a.set_defaults(func=q2a)
  parser_q1 = sub.add_parser("q1")
  _ = parser_q1.add_argument("--segments", type=int, default=2)
  _ = parser_q1.set_defaults(func=q1)
  parser_q45 = sub.add_parser("q45")
  _ = parser_q45.add_argument("--segments", type=int, default=5)
  _ = parser_q45.add_argument("--repeats", type=int, default=1)
  _ = parser_q45.set_defaults(func=q45)
  parser_wire = sub.add_parser("q3wire")
  _ = parser_wire.add_argument("--port", type=int, default=20411)
  _ = parser_wire.add_argument("--out", required=True)
  _ = parser_wire.set_defaults(func=q3wire)
  parser_q5b = sub.add_parser("q5b")
  _ = parser_q5b.add_argument("--delay", type=float, default=380.0)
  _ = parser_q5b.set_defaults(func=q5b)
  parser_q7 = sub.add_parser("q7")
  _ = parser_q7.add_argument("--port", type=int, default=20413)
  _ = parser_q7.add_argument("--out", required=True)
  _ = parser_q7.add_argument("--session", required=True)
  _ = parser_q7.add_argument("--at", required=True)
  _ = parser_q7.add_argument("--drops-turn", default=None)
  _ = parser_q7.add_argument("--workdir", default="q3wire")
  _ = parser_q7.set_defaults(func=q7)
  parser_q7l = sub.add_parser("q7loop")
  _ = parser_q7l.add_argument("--port", type=int, default=20415)
  _ = parser_q7l.add_argument("--out", required=True)
  _ = parser_q7l.add_argument("--segments", type=int, default=4)
  _ = parser_q7l.set_defaults(func=q7loop)
  args = parser.parse_args()
  return int(args.func(args))


if __name__ == "__main__":
  raise SystemExit(main())
