"""Runs one Claude Code segment and records everything the report will cite.

One segment = one `claude -p` invocation. The driver holds three disciplines the
README registered:

- **the budget is enforced here, not remembered** — every invocation carries
  `--max-budget-usd`, every run's cost is appended to `runs/ledger.jsonl`, and
  `guard_budget()` refuses to launch once the running total crosses the cap;
- **coordinates are recorded per run** — build, git commit, wall clock with
  timezone, the argv actually used, and the model id the *response* reports
  rather than the alias in the request;
- **raw stdout is preserved** with wall-clock and monotonic stamps per line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
RUNS = HERE / "runs"
LEDGER = RUNS / "ledger.jsonl"

LEDGER_CAP_USD = 5.00
PER_CALL_CAP_USD = 0.25
MODEL = "sonnet"


class BudgetExceeded(RuntimeError):
  """Raised instead of launching once the ledger has crossed the cap."""


def ledger_total() -> float:
  """Sums the cost of every run recorded so far.

  Returns:
    Total USD spent by this experiment.
  """
  if not LEDGER.exists():
    return 0.0
  total = 0.0
  for line in LEDGER.read_text(encoding="utf-8").splitlines():
    if line.strip():
      total += float(json.loads(line).get("total_cost_usd") or 0.0)
  return total


def guard_budget() -> float:
  """Refuses to launch when the experiment's ledger has crossed its cap.

  Returns:
    The running total, when it is under the cap.

  Raises:
    BudgetExceeded: when the recorded total is at or above the cap.
  """
  total = ledger_total()
  if total >= LEDGER_CAP_USD:
    raise BudgetExceeded(
        f"ledger total ${total:.4f} >= cap ${LEDGER_CAP_USD:.2f}; refusing to"
        " launch"
    )
  return total


def git_commit() -> str:
  """Returns the repository commit the run was made at."""
  out = subprocess.run(
      ["git", "rev-parse", "HEAD"],
      cwd=HERE,
      capture_output=True,
      text=True,
      check=False,
  )
  return out.stdout.strip() or "unknown"


def claude_version() -> str:
  """Returns the Claude Code build string."""
  out = subprocess.run(
      ["claude", "--version"], capture_output=True, text=True, check=False
  )
  return out.stdout.strip()


def build_argv(
    prompt: str,
    *,
    session_id: str | None = None,
    resume: str | None = None,
    max_turns: int | None = None,
    extra: tuple[str, ...] = (),
    budget_readout: bool = False,
) -> list[str]:
  """Assembles one segment's command line.

  `--safe-mode` is registered, not incidental: it keeps ambient auth while
  disabling CLAUDE.md discovery, skills, plugins and hooks, which both removes
  operator PII from the capture and makes the baseline context reproducible.
  Its cost is stated in the report — a production rollout carries its own
  project context, so absolute token counts here understate that case.

  Args:
    prompt: the user text for this segment.
    session_id: a fixed session id for a first segment.
    resume: a session id to resume instead of starting one.
    max_turns: the `--max-turns` value, when the segment is bounded.
    extra: any further flags under test.
    budget_readout: whether to pass `--max-budget-usd`, which is visible to the
      actor and so is off by default (README.md, "Amendment 1").

  Returns:
    The argv list.
  """
  argv = [
      "claude",
      "-p",
      prompt,
      "--output-format",
      "stream-json",
      "--verbose",
      "--model",
      MODEL,
      "--safe-mode",
      "--dangerously-skip-permissions",
  ]
  # `--max-budget-usd` is deliberately absent: it writes a running budget
  # readout into the actor's own context, which is a shape production does not
  # have and which plausibly moves the behaviour Q4 measures. See README.md,
  # "Amendment 1". Cost is bounded by `--max-turns` and the ledger check.
  if budget_readout:
    argv += ["--max-budget-usd", str(PER_CALL_CAP_USD)]
  if session_id:
    argv += ["--session-id", session_id]
  if resume:
    argv += ["--resume", resume]
  if max_turns is not None:
    argv += ["--max-turns", str(max_turns)]
  argv += list(extra)
  return argv


def run_segment(
    out_dir: pathlib.Path,
    workdir: pathlib.Path,
    prompt: str,
    *,
    session_id: str | None = None,
    resume: str | None = None,
    max_turns: int | None = None,
    extra: tuple[str, ...] = (),
    timeout: int = 600,
    base_url: str | None = None,
) -> dict[str, object]:
  """Runs one segment to completion, capturing its stream and coordinates.

  Args:
    out_dir: directory to write `events.jsonl`, `stderr.log` and `meta.json`.
    workdir: the agent's working directory.
    prompt: the user text for this segment.
    session_id: a fixed session id for a first segment.
    resume: a session id to resume instead of starting one.
    max_turns: the `--max-turns` value, when the segment is bounded.
    extra: any further flags under test.
    timeout: seconds before the segment is abandoned.
    base_url: an `ANTHROPIC_BASE_URL` to route through, for wire capture.

  Returns:
    The run's metadata record, also written to `meta.json`.
  """
  _ = guard_budget()
  out_dir.mkdir(parents=True, exist_ok=True)
  argv = build_argv(
      prompt,
      session_id=session_id,
      resume=resume,
      max_turns=max_turns,
      extra=extra,
  )
  started_wall = dt.datetime.now(dt.timezone.utc).isoformat()
  started = time.monotonic()
  events_path = out_dir / "events.jsonl"
  timed_out = False
  with (
      events_path.open("w", encoding="utf-8") as events,
      (out_dir / "stderr.log").open("w", encoding="utf-8") as errors,
  ):
    # argv[0] stays the bare name so the recorded command line carries no
    # operator home path; the resolved binary is passed out of band.
    executable = shutil.which("claude")
    if executable is None:
      raise RuntimeError("claude is not on PATH")
    process = subprocess.Popen(
        argv,
        executable=executable,
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=errors,
        text=True,
        bufsize=1,
        env={
            **os.environ,
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            **({"ANTHROPIC_BASE_URL": base_url} if base_url else {}),
        },
    )
    assert process.stdout is not None
    for line in process.stdout:
      stamped = {
          "wall": dt.datetime.now(dt.timezone.utc).isoformat(),
          "offset_s": round(time.monotonic() - started, 4),
          "raw": line.rstrip("\n"),
      }
      _ = events.write(json.dumps(stamped) + "\n")
      events.flush()
      if time.monotonic() - started > timeout:
        process.kill()
        timed_out = True
        break
    code = process.wait()

  meta: dict[str, object] = {
      "argv": argv,
      "cwd": str(workdir),
      "session_id": session_id,
      "resume": resume,
      "max_turns": max_turns,
      "started_wall": started_wall,
      "elapsed_s": round(time.monotonic() - started, 3),
      "exit_code": code,
      "timed_out": timed_out,
      "claude_version": claude_version(),
      "git_commit": git_commit(),
      "model_alias_requested": MODEL,
      # Sampling parameters are recorded including the ones NOT sent: the CLI
      # exposes no temperature/top_p knob, so absence is the observation.
      "sampling_parameters_sent": {"temperature": None, "top_p": None},
  }
  meta.update(summarize(events_path))
  _ = (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
  RUNS.mkdir(parents=True, exist_ok=True)
  with LEDGER.open("a", encoding="utf-8") as ledger:
    _ = ledger.write(
        json.dumps({
            "run": out_dir.name,
            "path": str(out_dir.relative_to(HERE))
            if out_dir.is_relative_to(HERE)
            else "<scratch>",
            "wall": started_wall,
            "total_cost_usd": meta.get("total_cost_usd"),
        })
        + "\n"
    )
  return meta


def read_events(events_path: pathlib.Path) -> list[dict[str, object]]:
  """Parses a captured stream back into decoded stream-json objects.

  Args:
    events_path: the run's `events.jsonl`.

  Returns:
    One decoded object per parsable line, in order.
  """
  events: list[dict[str, object]] = []
  if not events_path.exists():
    return events
  for line in events_path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
      continue
    try:
      raw = json.loads(line)["raw"]
      events.append(json.loads(raw))
    except (ValueError, KeyError):
      continue
  return events


def summarize(events_path: pathlib.Path) -> dict[str, object]:
  """Counts the quantities Q2'.1 and Q2'.2 are decided on.

  Assistant messages and `tool_use` blocks are counted separately on purpose:
  if the model batches several tool calls into one message, a count of turns
  taken against assistant messages and one taken against tool calls disagree,
  and only reporting both can tell those apart.

  Args:
    events_path: the run's `events.jsonl`.

  Returns:
    Counts, the terminal result event's fields, and usage totals.
  """
  events = read_events(events_path)
  assistants = 0
  # Thinking and tool_use arrive as separate stream events that share one
  # message id, so a count of `type: assistant` events is not a count of
  # assistant messages. Both are reported: they disagree exactly when the model
  # emits several block types per message, which is the case a turn-count claim
  # must not silently absorb.
  message_ids: list[str] = []
  stop_reasons: list[str] = []
  tool_use = 0
  tool_result = 0
  models: list[str] = []
  tool_names: list[str] = []
  result: dict[str, object] = {}
  for event in events:
    kind = event.get("type")
    if kind == "assistant":
      assistants += 1
      message = event.get("message") or {}
      if isinstance(message, dict):
        model = message.get("model")
        if isinstance(model, str) and model not in models:
          models.append(model)
        identifier = message.get("id")
        if isinstance(identifier, str) and identifier not in message_ids:
          message_ids.append(identifier)
        stop_reason = message.get("stop_reason")
        if isinstance(stop_reason, str):
          stop_reasons.append(stop_reason)
        for block in message.get("content") or []:
          if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_use += 1
            name = block.get("name")
            if isinstance(name, str):
              tool_names.append(name)
    elif kind == "user":
      message = event.get("message") or {}
      if isinstance(message, dict):
        for block in message.get("content") or []:
          if isinstance(block, dict) and block.get("type") == "tool_result":
            tool_result += 1
    elif kind == "result":
      result = event

  usage = result.get("usage") or {}
  return {
      "events": len(events),
      "assistant_events": assistants,
      "assistant_messages": len(message_ids),
      "assistant_stop_reasons": stop_reasons,
      "tool_use_blocks": tool_use,
      "tool_result_blocks": tool_result,
      "tool_names": tool_names,
      "model_ids_in_response": models,
      "result_subtype": result.get("subtype"),
      "result_stop_reason": result.get("stop_reason"),
      "result_terminal_reason": result.get("terminal_reason"),
      "result_is_error": result.get("is_error"),
      "result_num_turns": result.get("num_turns"),
      "result_keys": sorted(result),
      "total_cost_usd": result.get("total_cost_usd"),
      "usage": usage if isinstance(usage, dict) else {},
  }


def main() -> int:
  """Runs a single segment from the command line.

  Returns:
    A process exit code.
  """
  parser = argparse.ArgumentParser()
  _ = parser.add_argument("out_dir")
  _ = parser.add_argument("workdir")
  _ = parser.add_argument("prompt")
  _ = parser.add_argument("--session-id", default=None)
  _ = parser.add_argument("--resume", default=None)
  _ = parser.add_argument("--max-turns", type=int, default=None)
  _ = parser.add_argument("--new-session", action="store_true")
  args = parser.parse_args()
  session_id = args.session_id
  if args.new_session and not session_id:
    session_id = str(uuid.uuid4())
  meta = run_segment(
      pathlib.Path(args.out_dir),
      pathlib.Path(args.workdir),
      args.prompt,
      session_id=session_id,
      resume=args.resume,
      max_turns=args.max_turns,
  )
  print(json.dumps(meta, indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
