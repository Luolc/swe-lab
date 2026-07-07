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

from ..datasets.loader import Dataset, load_dataset
from ..datasets.swebench_pro import SweBenchProInstance
from ..paths import annotations_dir, cache_root, find_repo_root
from ..repo.provider import GitCheckoutProvider
from .agent_validator import validate_output
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
) -> RunResult:
  """Annotate one instance and persist its artifacts."""
  root = repo_root or find_repo_root()
  provider = provider or GitCheckoutProvider()
  binary = build_proxy(root)

  workspace = prepare_workspace(instance, provider)
  port = port_for_index(index, base_port=base_port)
  proxy_log = cache_root(root) / "proxy-logs" / f"{instance.instance_id}.jsonl"

  with ReverseProxy(port, proxy_log, binary) as proxy:
    cli_result = _invoke_claude(
        prompt=build_prompt(instance),
        cwd=workspace.checkout,
        base_url=proxy.base_url,
        model=model,
    )

  snippets = _read_output(workspace)
  validation_problems = _validate(workspace)
  last_record = _last_proxy_record(proxy_log)
  complete = bool(last_record.get("complete", False))

  metadata: dict[str, object] = {
      "model": model,
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


def _invoke_claude(
    *, prompt: str, cwd: Path, base_url: str, model: str
) -> dict[str, object]:
  env = os.environ.copy()
  env["ANTHROPIC_BASE_URL"] = base_url
  result = subprocess.run(
      [
          "claude",
          "-p",
          prompt,
          "--model",
          model,
          "--output-format",
          "json",
          "--dangerously-skip-permissions",
      ],
      cwd=str(cwd),
      env=env,
      capture_output=True,
      text=True,
      check=False,
  )
  if result.returncode != 0:
    raise RuntimeError(
        f"claude exited {result.returncode}:\n{result.stderr.strip()}"
    )
  try:
    parsed = json.loads(result.stdout)
  except json.JSONDecodeError as exc:
    raise RuntimeError(
        f"Could not parse claude output as JSON: {exc}\n{result.stdout[:500]}"
    ) from exc
  return parsed if isinstance(parsed, dict) else {}


def _read_output(workspace: Workspace) -> tuple[Snippet, ...]:
  if not workspace.output_path.is_file():
    raise FileNotFoundError(
        f"Agent did not write {workspace.output_path.name} in the working"
        " directory."
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
