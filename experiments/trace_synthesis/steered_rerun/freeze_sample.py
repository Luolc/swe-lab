#!/usr/bin/env python3
"""Freeze one harvested failure as a **self-contained, mountable sample**.

This is the shape the phase-C workflow consumes. The pipeline does not re-run a
rollout to find a failure: a full rollout + test sweep has already happened and
its traces are cached, so the workflow starts at *write the guidebook*, with a
hand-assembled dataset row as its input. That row is a directory, and this
program writes it.

**It is a contract, not a convenience.** The guidebook agent and the workflow
that mounts this are built against the layout below, so the invariant is
self-containment: a consumer needs the directory and nothing else — no parquet,
no dataset loader, no access to the frozen run tree it came from.

::

  <instance_id>/
    instance.json            every field of the dataset row
    failed_conversation.json the typed Conversation of the failing rollout
    verdict.json             which required tests failed, and on what
    patch.diff               the patch that rollout submitted
    raw/                     the unconverted trace, kept as corroboration
    PROVENANCE.json          the run's provenance + how it was produced
    MANIFEST.sha256          a checksum of every file above

``failed_conversation.json`` is the **typed** conversation, deliberately: the
raw trace is kept beside it as evidence, but a consumer that parses the raw
stream is re-implementing a converter this repo already owns and has tested.

Usage::

  direnv exec . uv run python experiments/trace_synthesis/steered_rerun/freeze_sample.py \\
      --frozen /home/ubuntu/dev/swe-lab-artifacts/trace_synthesis/<label>-rollout-<n> \\
      --summary experiments/trace_synthesis/steered_rerun/runs/<label>/summary-r<n>.json
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import pathlib
import shutil

from swe_lab.datasets.loader import load_dataset

_OUT_ROOT = pathlib.Path("/home/ubuntu/dev/swe-lab-artifacts/trace_synthesis")


def instance_fields(instance: object) -> dict[str, object]:
  """Return every dataset field of a record, JSON-ready.

  Args:
    instance: The dataset row.

  Returns:
    The row's fields, with tuples flattened to lists.
  """
  raw = dataclasses.asdict(instance)  # pyright: ignore[reportArgumentType]
  return {
      key: list(value) if isinstance(value, (tuple, frozenset)) else value
      for key, value in raw.items()
  }


def verdict(
    frozen: pathlib.Path, instance: object, resolved: bool
) -> dict[str, object]:
  """Return what the grading suite decided, and how firmly.

  The suite runs once per attempt and the entry retries, so a per-attempt
  breakdown is the cheap flakiness check: a required test that fails in every
  attempt is a property of the patch, one that moves is a property of the
  suite. A sample whose verdict is not stable across attempts is not a
  reasoning failure worth steering.

  Args:
    frozen: The frozen run directory.
    instance: The dataset row.
    resolved: What the grading entry actually decided, read from the run's own
      ``unit_test.resolved`` metric. Taken as an argument rather than assumed:
      this field used to be hard-coded ``False``, so a resolved run would have
      been written out as a sample asserting it had failed.

  Returns:
    The verdict record.
  """
  required = list(instance.fail_to_pass) + list(instance.pass_to_pass)
  attempts: dict[str, dict[str, object]] = {}
  for path in sorted(frozen.glob("unit_test/a*/unit_test.output.json")):
    seen = {
        str(test["name"]): str(test["status"])
        for test in json.loads(path.read_text())["tests"]
    }
    attempts[path.parent.name] = {
        "failed": [t for t in required if seen.get(t) not in (None, "PASSED")],
        "missing": [t for t in required if t not in seen],
        "tests_seen": len(seen),
    }
  failures = [set(a["failed"]) for a in attempts.values()]  # pyright: ignore[reportArgumentType]
  return {
      "resolved": resolved,
      "required": len(required),
      "fail_to_pass": list(instance.fail_to_pass),
      "attempts": attempts,
      # The intersection, not the union: what every attempt agreed on.
      "failed_in_every_attempt": sorted(set.intersection(*failures))
      if failures
      else [],
      "stable_across_attempts": len({frozenset(f) for f in failures}) == 1,
  }


def manifest(root: pathlib.Path) -> str:
  """Return a ``sha256sum``-format manifest of a directory tree.

  Args:
    root: The directory to checksum.

  Returns:
    One ``<sha256>  <relative path>`` line per file, sorted.
  """
  lines: list[str] = []
  for path in sorted(root.rglob("*")):
    if path.is_file() and path.name != "MANIFEST.sha256":
      digest = hashlib.sha256(path.read_bytes()).hexdigest()
      lines.append(f"{digest}  {path.relative_to(root)}")
  return "\n".join(lines) + "\n"


def check_gates(
    metrics: dict[str, float],
    unit_test_metrics: dict[str, float],
    decision: dict[str, object],
) -> None:
  """Refuse a run that is unresolved for any reason other than reasoning.

  The workflow's own exit code distinguishes none of these: an unresolved
  verdict is reported the same way whether the actor reasoned badly, was killed
  at its timeout, never started, or was graded by a suite that disagreed with
  itself.

  Args:
    metrics: The rollout entry's metrics.
    unit_test_metrics: The grading entry's metrics.
    decision: The verdict record from ``verdict``.

  Raises:
    SystemExit: If the run is not attributable to the actor's reasoning.
  """
  # The first three are about the actor's own process. Measured 2026-09-01 —
  # the protonmail/webclients image cannot execute the mounted linux-x64 binary
  # (`cannot execute: required file not found`, exit 127 after 0.7 s), and the
  # run still came back unresolved with `timed_out == 0`.
  for name, want in (
      ("claude_code.timed_out", 0.0),
      ("agent_complete", 1.0),
      ("claude_code.exit_code", 0.0),
  ):
    if metrics.get(name) != want:
      raise SystemExit(
          f"refusing to freeze: {name} is {metrics.get(name)!r}, not {want!r}."
          " The rollout did not end in the actor finishing its work, so its"
          " unresolved verdict is not evidence the actor erred."
      )

  # Before anything about *how* it failed: that it failed at all. The sample's
  # whole claim is "the actor erred here", and nothing above reads the grade,
  # so a run whose required tests all passed would otherwise be written out as
  # a failure sample asserting `resolved: false`.
  if unit_test_metrics.get("unit_test.resolved") != 0.0:
    raise SystemExit(
        "refusing to freeze: unit_test.resolved is"
        f" {unit_test_metrics.get('unit_test.resolved')!r}, so the grading"
        " suite resolved this instance. A resolved run is not a failure"
        " sample, whatever else is stable about it."
    )

  # The fourth gate is about the *grader*, and the three above cannot see it.
  # It is **not** that a resolved attempt can sit beside an unresolved one:
  # `UnitTest.should_retry` stops at the first resolved attempt, so a run that
  # ends unresolved had every executed attempt unresolved. What those attempts
  # can still disagree about is *which* required tests failed — and that
  # disagreement is a property of the suite, not of the patch. `verdict()` has
  # always computed this; refusing on it is what makes computing it worth
  # anything.
  attempts = decision["attempts"]
  if not attempts:
    raise SystemExit(
        "refusing to freeze: no grading attempt left an output file, so there"
        " is no verdict to attribute to anything."
    )
  if not decision["stable_across_attempts"]:
    per_attempt = {
        name: sorted(attempt["failed"]) + sorted(attempt["missing"])
        for name, attempt in attempts.items()  # pyright: ignore[reportAttributeAccessIssue]
    }
    raise SystemExit(
        "refusing to freeze: the grading attempts disagree about which"
        f" required tests failed — {per_attempt}. An unstable verdict is a"
        " property of the suite, not of the patch, so this is not a reasoning"
        " failure however the last attempt happened to land."
    )


def main() -> None:
  """Write one sample directory."""
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.add_argument("--frozen", required=True)
  _ = parser.add_argument("--summary", required=True)
  _ = parser.add_argument("--dataset", default="swebench_pro")
  _ = parser.add_argument("--out-root", default=str(_OUT_ROOT))
  args = parser.parse_args()

  frozen = pathlib.Path(args.frozen)
  summary = json.loads(pathlib.Path(args.summary).read_text())
  instance = load_dataset(args.dataset).require(str(summary["instance_id"]))

  metrics = {
      name: value
      for name, value in (summary["entries"]["rollout"]["metrics"] or {}).items()
  }
  unit_test_metrics = {
      name: value
      for name, value in (
          summary["entries"].get("unit_test", {}).get("metrics") or {}
      ).items()
  }
  decision = verdict(
      frozen, instance, unit_test_metrics.get("unit_test.resolved") == 1.0
  )
  check_gates(metrics, unit_test_metrics, decision)

  # Only now: a refusal must not leave a directory behind that looks like a
  # sample. Nothing is created until the run has earned it.
  out = pathlib.Path(args.out_root) / str(summary["instance_id"])
  if out.exists():
    shutil.rmtree(out)
  (out / "raw").mkdir(parents=True)

  _ = (out / "instance.json").write_text(
      json.dumps(instance_fields(instance), indent=2, ensure_ascii=False) + "\n"
  )

  conversations = sorted(frozen.glob("rollout/a*/conversation.json"))
  if not conversations:
    raise SystemExit(
        f"no typed conversation under {frozen}/rollout/a*/ — the sample's"
        " primary input is the typed Conversation, so this is not a usable"
        " failure sample"
    )
  _ = (out / "failed_conversation.json").write_text(conversations[0].read_text())

  _ = (out / "verdict.json").write_text(json.dumps(decision, indent=2) + "\n")
  _ = shutil.copy(frozen / "rollout/a0/patch.diff", out / "patch.diff")

  # The unconverted trace, as corroboration for the typed conversation. Already
  # redacted in place by `run_steered.py` before the run was frozen; copying an
  # unredacted one here would put a credential in the deliverable.
  for path in sorted(frozen.glob("rollout/a*/*.jsonl")) + sorted(
      frozen.glob("rollout/a*/*.log")
  ):
    if path.stat().st_size:
      _ = shutil.copy(path, out / "raw" / path.name)

  provenance = json.loads((frozen / "PROVENANCE.json").read_text())
  provenance["produced_by"] = {
      "how": "harvested by experiments/trace_synthesis/steered_rerun",
      "frozen_run": str(frozen),
      "capture": summary.get("capture"),
      "actor_model": summary.get("actor_model"),
      "actor_base_url": summary.get("actor_base_url"),
      "auth": summary.get("auth"),
      "bare": summary.get("bare"),
      "subagents": "denied (--disallowedTools ...,Task)",
      "concurrency": summary.get("concurrency"),
      "openrouter_key_index": summary.get("key_index"),
      "openrouter_key": summary.get("key"),
      "credits_before": summary.get("credits_before"),
      "credits_after": summary.get("credits_after"),
      "wall_s": summary.get("wall_s"),
      "agent_timeout_s": 3600.0,
  }
  _ = (out / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2) + "\n")

  _ = (out / "MANIFEST.sha256").write_text(manifest(out))
  print(out)


if __name__ == "__main__":
  main()
