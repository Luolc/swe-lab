"""Shared machinery for running a headless snippet-annotation agent.

Both the single-instance annotator (`annotator`) and the sample aggregator
(`aggregator`) do the same thing: provision an isolated workspace, invoke a
headless Claude Code agent (capturing its trace via ``stream-json`` by default,
or a per-call reverse proxy — see ``CAPTURE_MODES``), with retries and failure
classification, then read / validate / store the result. That flow lives here as
:func:`run_agent`; the two callers differ only in the prompt and in a couple of
extra context files.

Per-run isolation: pass a ``variant`` (and optionally an explicit ``port``) so
several runs of the same instance can execute concurrently without sharing a
checkout, proxy port, or log path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from swe_lab.core.agent.errors import (
    AnnotationError,
    cli_failure,
    MissingOutputError,
    RetryableError,
)
from swe_lab.core.agent.proxy import (
    build_proxy,
    DEFAULT_BASE_PORT,
    port_for_index,
    ReverseProxy,
)

# The stream/proxy trace parsing + unified exchange record now live in
# ``core.agent.trace`` so ``rollout`` (agent runs in-container) reuses them
# without a core→tasks dependency. Re-exported here for back-compat: existing
# callers and tests still import these names from ``agent_run``.
from swe_lab.core.agent.trace import (
    CAPTURE_MODES,
    CAPTURE_PROXY,
    DEFAULT_CAPTURE,
    final_result_event,
    last_assistant_stop_reason,
    last_proxy_record,
    last_stream_record,
    parse_stream_events,
)
from swe_lab.core.datasets.swebench_pro import SweBenchProInstance
from swe_lab.core.paths import cache_root, find_repo_root
from swe_lab.core.repo.provider import GitCheckoutProvider

from .agent_validator import validate_output
from .schema import Annotation, parse_agent_output, Snippet
from .workspace import prepare_workspace, Workspace

DEFAULT_MODEL = "sonnet"
# A run may explore a large repo for several minutes; cap it generously.
DEFAULT_CLAUDE_TIMEOUT_S = 1800.0
# Total attempts for transient (retryable) failures, with backoff between them.
DEFAULT_MAX_ATTEMPTS = 3
_RETRY_BACKOFFS_S = (5.0, 20.0, 60.0)


@dataclass
class RunResult:
  """Outcome of one agent run (annotation or aggregate).

  Carries the parsed annotation and the extracted final proxy record
  (``last_record``); persisting them is the caller's job (see ``storage``).
  """

  instance_id: str
  annotation: Annotation
  last_record: dict[str, object]
  proxy_log_path: Path
  complete: bool
  validation_problems: dict[str, list[str]] = field(default_factory=dict)

  @property
  def is_valid(self) -> bool:
    return self.complete and not self.validation_problems


def run_agent(
    instance: SweBenchProInstance,
    index: int,
    *,
    prompt: str,
    kind: str = "annotation",
    context_files: Mapping[str, str] | None = None,
    extra_metadata: Mapping[str, object] | None = None,
    repo_root: Path | None = None,
    provider: GitCheckoutProvider | None = None,
    model: str = DEFAULT_MODEL,
    base_port: int = DEFAULT_BASE_PORT,
    port: int | None = None,
    variant: str = "",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    claude_timeout: float = DEFAULT_CLAUDE_TIMEOUT_S,
    capture: str = DEFAULT_CAPTURE,
) -> RunResult:
  """Run an agent in an isolated workspace and return its validated result.

  ``instance`` must be a repo record (``repo`` / ``base_commit`` /
  ``instance_id``). Transient failures are retried with backoff; usage-limit
  failures raise immediately. ``variant`` isolates concurrent runs of one
  instance (distinct checkout, proxy port, and log paths). ``capture`` selects
  the trace source (see ``CAPTURE_MODES``): ``proxy`` keeps a reverse proxy in
  front of the API; ``stream`` reads the CLI's own ``stream-json`` output and
  needs no proxy.
  """
  if capture not in CAPTURE_MODES:
    raise ValueError(
        f"unknown capture mode {capture!r}; use one of {CAPTURE_MODES}"
    )
  started = time.monotonic()
  instance_id = instance.instance_id
  root = repo_root or find_repo_root()
  provider = provider or GitCheckoutProvider()
  binary = build_proxy(root) if capture == CAPTURE_PROXY else None

  workspace = prepare_workspace(instance, provider, variant=variant)
  for name, content in (context_files or {}).items():
    _ = (workspace.context_dir / name).write_text(content)

  run_port = (
      port if port is not None else port_for_index(index, base_port=base_port)
  )
  tag = instance_id if not variant else f"{instance_id}__{variant}"
  if capture == CAPTURE_PROXY:
    trace_log = cache_root(root) / "proxy-logs" / f"{tag}.jsonl"
  else:
    trace_log = cache_root(root) / "agent-traces" / f"{tag}.stream.jsonl"
  diag_path = cache_root(root) / "annotate-failures" / f"{tag}.log"

  cli_result, snippets = invoke_with_retries(
      prompt=prompt,
      cwd=workspace.checkout,
      port=run_port,
      trace_log=trace_log,
      binary=binary,
      capture=capture,
      model=model,
      timeout=claude_timeout,
      diag_path=diag_path,
      max_attempts=max_attempts,
      workspace=workspace,
  )

  validation_problems = validate_workspace(workspace)
  if capture == CAPTURE_PROXY:
    last_record = last_proxy_record(trace_log)
  else:
    last_record = last_stream_record(trace_log)
  complete = bool(last_record.get("complete", False))

  metadata = _build_metadata(
      cli_result, model, run_port, complete, snippets, validation_problems, kind
  )
  metadata["capture"] = capture
  # Wall-clock of this whole run (provision + agent + validate + store) vs the
  # agent's own duration reveals stalls: if wall_clock >> the run's active time,
  # the box was starved (e.g. swap-thrashing under too much concurrency).
  metadata["wall_clock_s"] = round(time.monotonic() - started, 1)
  metadata.update(extra_metadata or {})
  annotation = Annotation(instance_id, snippets, metadata)

  return RunResult(
      instance_id=instance_id,
      annotation=annotation,
      last_record=last_record,
      proxy_log_path=trace_log,
      complete=complete,
      validation_problems=validation_problems,
  )


def _build_metadata(
    cli_result: dict[str, object],
    model: str,
    port: int,
    complete: bool,
    snippets: tuple[Snippet, ...],
    validation_problems: dict[str, list[str]],
    kind: str,
) -> dict[str, object]:
  model_usage = cli_result.get("modelUsage")
  model_ids = list(model_usage) if isinstance(model_usage, dict) else []
  model_used = ", ".join(model_ids) if model_ids else model
  return {
      "kind": kind,
      "model": model_used,
      "model_requested": model,
      "run_id": cli_result.get("session_id"),
      "timestamp": datetime.now(UTC).isoformat(),
      "proxy_port": port,
      "num_turns": cli_result.get("num_turns"),
      "cost_usd": cli_result.get("total_cost_usd"),
      "usage": cli_result.get("usage"),
      "stop_reason": cli_result.get("stop_reason"),
      "complete": complete,
      "snippet_count": len(snippets),
      "invalid_snippet_count": len(validation_problems),
  }


def invoke_with_retries(
    *,
    prompt: str,
    cwd: Path,
    port: int,
    trace_log: Path,
    binary: Path | None,
    capture: str,
    model: str,
    timeout: float,
    diag_path: Path,
    max_attempts: int,
    workspace: Workspace,
) -> tuple[dict[str, object], tuple[Snippet, ...]]:
  """Run the claude call and read the output, retrying flaky attempts.

  Retries transient CLI failures *and* the case where the agent ends without
  writing its output file (``MissingOutputError``) — a known flaky behavior that
  a fresh attempt usually fixes. In ``proxy`` capture the call goes through a
  reverse proxy that writes ``trace_log``; in ``stream`` capture the CLI's own
  ``stream-json`` output is written to ``trace_log`` directly. Returns
  ``(cli_result, snippets)``.
  """
  for attempt in range(1, max_attempts + 1):
    try:
      if capture == CAPTURE_PROXY:
        assert binary is not None  # built by run_agent for proxy capture
        with ReverseProxy(port, trace_log, binary) as proxy:
          cli_result = _invoke_claude(
              prompt=prompt,
              cwd=cwd,
              base_url=proxy.base_url,
              model=model,
              timeout=timeout,
              diag_path=diag_path,
          )
      else:
        cli_result = _invoke_claude_stream(
            prompt=prompt,
            cwd=cwd,
            model=model,
            timeout=timeout,
            diag_path=diag_path,
            stream_log=trace_log,
        )
      return cli_result, read_snippets(workspace)
    except (RetryableError, MissingOutputError):
      if attempt >= max_attempts:
        raise
      backoff = _RETRY_BACKOFFS_S[min(attempt - 1, len(_RETRY_BACKOFFS_S) - 1)]
      time.sleep(backoff)
  # Unreachable: the loop either returns or raises.
  raise AnnotationError("retry loop exited without a result")


def _invoke_claude_stream(
    *,
    prompt: str,
    cwd: Path,
    model: str,
    timeout: float,
    diag_path: Path | None,
    stream_log: Path,
) -> dict[str, object]:
  """Invoke headless claude with ``stream-json`` and persist the event stream.

  No proxy: ``claude --output-format stream-json --verbose`` emits every event
  (assistant turns, tool results, a final ``result``) as JSONL on stdout, which
  we save to ``stream_log`` for auditing. Returns the final ``result`` event as
  the ``cli_result`` (with ``stop_reason`` filled in from the last assistant
  turn so it carries the same fields ``_build_metadata`` reads).
  """
  env = os.environ.copy()
  argv = [
      "claude",
      "-p",
      prompt,
      "--model",
      model,
      "--output-format",
      "stream-json",
      "--verbose",
      "--dangerously-skip-permissions",
  ]
  # Stream stdout straight to the log FILE (not a pipe) and run claude in its
  # own process group. On timeout we SIGKILL the WHOLE group: killing only the
  # direct child leaves claude's node grandchildren orphaned, still holding the
  # stdout pipe open, which makes the post-timeout communicate() block forever
  # (this once hung an aggregate call for ~3h). File output + group-kill make
  # the timeout actually terminate the run.
  stream_log.parent.mkdir(parents=True, exist_ok=True)
  timed_out = False
  with stream_log.open("w") as out:
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdout=out,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
      _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
      _kill_process_group(proc)
      _, stderr = proc.communicate()
      timed_out = True
  exit_code = proc.returncode

  if timed_out:
    _save_diagnostics(diag_path, "TIMEOUT", "(stdout streamed to log)", stderr)
    raise RetryableError(f"claude timed out after {timeout:.0f}s")

  stdout = stream_log.read_text()
  events = parse_stream_events(stdout)
  final = final_result_event(events)
  result_text = str(final.get("result", "")) if final else ""
  api_error_status = final.get("api_error_status") if final else None

  if exit_code != 0 or (final is not None and final.get("is_error")):
    _save_diagnostics(diag_path, exit_code, stdout, stderr)
    raise cli_failure(
        stderr=stderr,
        result_text=result_text,
        api_error_status=api_error_status,
    )

  if final is None:
    _save_diagnostics(diag_path, exit_code, stdout, stderr)
    raise AnnotationError("claude stream produced no result event")

  cli_result = dict(final)
  _ = cli_result.setdefault("stop_reason", last_assistant_stop_reason(events))
  return cli_result


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
  """SIGKILL the whole process group (claude + its node grandchildren)."""
  try:
    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
  except (ProcessLookupError, PermissionError):
    proc.kill()


def _invoke_claude(
    *,
    prompt: str,
    cwd: Path,
    base_url: str,
    model: str,
    timeout: float,
    diag_path: Path | None = None,
) -> dict[str, object]:
  env = os.environ.copy()
  env["ANTHROPIC_BASE_URL"] = base_url
  argv = [
      "claude",
      "-p",
      prompt,
      "--model",
      model,
      "--output-format",
      "json",
      "--dangerously-skip-permissions",
  ]
  try:
    result = subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
  except subprocess.TimeoutExpired as exc:
    _save_diagnostics(diag_path, "TIMEOUT", exc.stdout, exc.stderr)
    raise RetryableError(f"claude timed out after {timeout:.0f}s") from exc

  # `claude --output-format json` writes its result object — including any
  # error, with `is_error` / `result` / `api_error_status` — to STDOUT, even
  # when it exits nonzero (e.g. a mid-session API 401). So parse stdout first
  # and build a well-classified error; stderr is usually empty for these.
  parsed: object = None
  parse_error: json.JSONDecodeError | None = None
  try:
    parsed = json.loads(result.stdout)
  except json.JSONDecodeError as exc:
    parse_error = exc

  result_text = ""
  api_error_status: object = None
  if isinstance(parsed, dict):
    result_text = str(parsed.get("result", ""))
    api_error_status = parsed.get("api_error_status")

  if result.returncode != 0:
    _save_diagnostics(
        diag_path, result.returncode, result.stdout, result.stderr
    )
    raise cli_failure(
        stderr=result.stderr,
        result_text=result_text,
        api_error_status=api_error_status,
    )

  if parse_error is not None:
    _save_diagnostics(
        diag_path, result.returncode, result.stdout, result.stderr
    )
    raise AnnotationError(
        f"could not parse claude output as JSON: {parse_error}"
    ) from parse_error

  if not isinstance(parsed, dict):
    _save_diagnostics(
        diag_path, result.returncode, result.stdout, result.stderr
    )
    raise AnnotationError("claude output was not a JSON object")

  if parsed.get("is_error"):
    _save_diagnostics(
        diag_path, result.returncode, result.stdout, result.stderr
    )
    raise cli_failure(
        stderr=result.stderr,
        result_text=result_text,
        api_error_status=api_error_status,
    )
  return parsed


def _save_diagnostics(
    diag_path: Path | None,
    exit_code: object,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
) -> None:
  """Append raw CLI output for a failed run, to study unknown errors later."""
  if diag_path is None:
    return
  diag_path.parent.mkdir(parents=True, exist_ok=True)
  stamp = datetime.now(UTC).isoformat()
  with diag_path.open("a") as handle:
    _ = handle.write(f"=== {stamp} exit_code={exit_code} ===\n")
    _ = handle.write(f"--- stdout ---\n{_as_text(stdout)[:5000]}\n")
    _ = handle.write(f"--- stderr ---\n{_as_text(stderr)[:5000]}\n\n")


def _as_text(value: str | bytes | None) -> str:
  if isinstance(value, bytes):
    return value.decode("utf-8", "replace")
  return value or ""


def read_snippets(workspace: Workspace) -> tuple[Snippet, ...]:
  if not workspace.output_path.is_file():
    raise MissingOutputError(
        f"agent did not write {workspace.output_path.name} in the working"
        " directory"
    )
  return parse_agent_output(workspace.output_path.read_text())


def validate_workspace(workspace: Workspace) -> dict[str, list[str]]:
  """Post-hoc check via the same validator the agent runs (single source)."""
  problems = validate_output(workspace.output_path, workspace.checkout)
  return {f"{p.index}:{p.file_path}": p.messages for p in problems}
