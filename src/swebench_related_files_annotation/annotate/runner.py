"""Run the annotation agent for a single instance, end to end.

Wires together the workspace, a per-instance reverse proxy, a headless Claude
Code invocation, output parsing/validation, and storage of the two committed
artifacts (the annotation and the extracted final proxy record).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import subprocess
import time

from ..datasets.loader import Dataset, load_dataset
from ..datasets.swebench_pro import SweBenchProInstance
from ..paths import annotations_dir, cache_root, find_repo_root
from ..repo.provider import GitCheckoutProvider
from .agent_validator import validate_output
from .errors import (
    AnnotationError,
    cli_failure,
    MissingOutputError,
    RetryableError,
)
from .prompt import build_prompt
from .proxy import (
    build_proxy,
    DEFAULT_BASE_PORT,
    port_for_index,
    ReverseProxy,
)
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
  """Outcome of annotating one instance."""

  instance_id: str
  annotation: Annotation
  annotation_path: Path
  last_exchange_path: Path
  proxy_log_path: Path
  complete: bool
  validation_problems: dict[str, list[str]] = field(default_factory=dict)

  @property
  def is_valid(self) -> bool:
    return self.complete and not self.validation_problems


def annotate_instance(
    instance: SweBenchProInstance,
    index: int,
    *,
    repo_root: Path | None = None,
    provider: GitCheckoutProvider | None = None,
    model: str = DEFAULT_MODEL,
    base_port: int = DEFAULT_BASE_PORT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    claude_timeout: float = DEFAULT_CLAUDE_TIMEOUT_S,
) -> RunResult:
  """Annotate one instance and persist its artifacts.

  Transient failures (network, rate limit, overload, timeout) are retried with
  backoff up to ``max_attempts``. A usage/quota exhaustion raises
  ``UsageLimitError`` immediately (no retry) so the caller can stop and wait for
  the window to refresh. Raw diagnostics for any failure are appended under
  ``.cache/annotate-failures/<instance_id>.log``.
  """
  root = repo_root or find_repo_root()
  provider = provider or GitCheckoutProvider()
  binary = build_proxy(root)

  workspace = prepare_workspace(instance, provider)
  port = port_for_index(index, base_port=base_port)
  proxy_log = cache_root(root) / "proxy-logs" / f"{instance.instance_id}.jsonl"
  diag_path = (
      cache_root(root) / "annotate-failures" / f"{instance.instance_id}.log"
  )

  cli_result = _invoke_with_retries(
      prompt=build_prompt(instance),
      cwd=workspace.checkout,
      port=port,
      proxy_log=proxy_log,
      binary=binary,
      model=model,
      timeout=claude_timeout,
      diag_path=diag_path,
      max_attempts=max_attempts,
  )

  snippets = _read_output(workspace)
  validation_problems = _validate(workspace)
  last_record = _last_proxy_record(proxy_log)
  complete = bool(last_record.get("complete", False))

  model_usage = cli_result.get("modelUsage")
  model_ids = list(model_usage) if isinstance(model_usage, dict) else []
  model_used = ", ".join(model_ids) if model_ids else model

  metadata: dict[str, object] = {
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
  annotation = Annotation(instance.instance_id, snippets, metadata)

  annotation_path, last_exchange_path = _store(root, annotation, last_record)
  return RunResult(
      instance_id=instance.instance_id,
      annotation=annotation,
      annotation_path=annotation_path,
      last_exchange_path=last_exchange_path,
      proxy_log_path=proxy_log,
      complete=complete,
      validation_problems=validation_problems,
  )


def annotate_by_id(
    instance_id: str,
    *,
    dataset: Dataset | None = None,
    model: str = DEFAULT_MODEL,
    base_port: int = DEFAULT_BASE_PORT,
) -> RunResult:
  """Look an instance up by id and annotate it (using its dataset index)."""
  dataset = dataset or load_dataset()
  record = dataset.require(instance_id)
  if not isinstance(record, SweBenchProInstance):
    raise TypeError(f"Unexpected record type: {type(record).__name__}")
  index = dataset.index_of(instance_id)
  return annotate_instance(record, index, model=model, base_port=base_port)


def _invoke_with_retries(
    *,
    prompt: str,
    cwd: Path,
    port: int,
    proxy_log: Path,
    binary: Path,
    model: str,
    timeout: float,
    diag_path: Path,
    max_attempts: int,
) -> dict[str, object]:
  """Run the proxy + claude call, retrying only transient failures."""
  for attempt in range(1, max_attempts + 1):
    try:
      with ReverseProxy(port, proxy_log, binary) as proxy:
        return _invoke_claude(
            prompt=prompt,
            cwd=cwd,
            base_url=proxy.base_url,
            model=model,
            timeout=timeout,
            diag_path=diag_path,
        )
    except RetryableError:
      if attempt >= max_attempts:
        raise
      backoff = _RETRY_BACKOFFS_S[min(attempt - 1, len(_RETRY_BACKOFFS_S) - 1)]
      time.sleep(backoff)
  # Unreachable: the loop either returns or raises.
  raise AnnotationError("retry loop exited without a result")


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

  if result.returncode != 0:
    _save_diagnostics(
        diag_path, result.returncode, result.stdout, result.stderr
    )
    raise cli_failure(stderr=result.stderr)

  try:
    parsed = json.loads(result.stdout)
  except json.JSONDecodeError as exc:
    _save_diagnostics(
        diag_path, result.returncode, result.stdout, result.stderr
    )
    raise AnnotationError(
        f"could not parse claude output as JSON: {exc}"
    ) from exc

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
        result_text=str(parsed.get("result", "")),
        api_error_status=parsed.get("api_error_status"),
    )
  return parsed


def _save_diagnostics(
    diag_path: Path | None,
    returncode: object,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
) -> None:
  """Append raw CLI output for a failed run, to study unknown errors later."""
  if diag_path is None:
    return
  diag_path.parent.mkdir(parents=True, exist_ok=True)
  stamp = datetime.now(UTC).isoformat()
  with diag_path.open("a") as handle:
    _ = handle.write(f"=== {stamp} returncode={returncode} ===\n")
    _ = handle.write(f"--- stdout ---\n{_as_text(stdout)[:5000]}\n")
    _ = handle.write(f"--- stderr ---\n{_as_text(stderr)[:5000]}\n\n")


def _as_text(value: str | bytes | None) -> str:
  if isinstance(value, bytes):
    return value.decode("utf-8", "replace")
  return value or ""


def _read_output(workspace: Workspace) -> tuple[Snippet, ...]:
  if not workspace.output_path.is_file():
    raise MissingOutputError(
        f"agent did not write {workspace.output_path.name} in the working"
        " directory"
    )
  return parse_agent_output(workspace.output_path.read_text())


def _validate(workspace: Workspace) -> dict[str, list[str]]:
  """Post-hoc check via the same validator the agent runs (single source)."""
  problems = validate_output(workspace.output_path, workspace.checkout)
  return {f"{p.index}:{p.file_path}": p.messages for p in problems}


def _last_proxy_record(proxy_log: Path) -> dict[str, object]:
  if not proxy_log.is_file():
    return {}
  last_line = ""
  with proxy_log.open() as handle:
    for line in handle:
      if line.strip():
        last_line = line
  if not last_line:
    return {}
  record = json.loads(last_line)
  return record if isinstance(record, dict) else {}


def _store(
    root: Path, annotation: Annotation, last_record: dict[str, object]
) -> tuple[Path, Path]:
  out_dir = annotations_dir(root)
  out_dir.mkdir(parents=True, exist_ok=True)
  annotation_path = out_dir / f"{annotation.instance_id}.json"
  _ = annotation_path.write_text(annotation.to_json())

  last_exchange_path = out_dir / f"{annotation.instance_id}.last_exchange.json"
  _ = last_exchange_path.write_text(
      json.dumps(last_record, indent=2, ensure_ascii=False) + "\n"
  )
  return annotation_path, last_exchange_path
