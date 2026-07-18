"""CLI: annotate a single instance via the sample-and-aggregate pipeline.

    python -m swe_lab.pipelines.related_files <instance_id>

Runs N samples + an aggregate and stores every artifact under
``outputs/related_files/<dataset>/<instance_id>/`` (see ``storage``).
"""

from __future__ import annotations

import argparse

from swe_lab.core.agent.trace import CAPTURE_MODES, DEFAULT_CAPTURE

from .agent_run import DEFAULT_MODEL
from .pipeline import annotate_by_id_with_aggregation, DEFAULT_SAMPLES
from .storage import DEFAULT_DATASET


def main() -> int:
  """Run the annotation CLI and return the process exit status."""
  parser = argparse.ArgumentParser(
      prog="python -m swe_lab.pipelines.related_files",
      description="Annotate one SWE-Bench instance's relevant code snippets.",
  )
  _ = parser.add_argument("instance_id", help="Instance id to annotate.")
  _ = parser.add_argument(
      "--model", default=DEFAULT_MODEL, help="Claude model (default: sonnet)."
  )
  _ = parser.add_argument(
      "--dataset", default=DEFAULT_DATASET, help="Dataset name."
  )
  _ = parser.add_argument(
      "--samples",
      type=int,
      default=DEFAULT_SAMPLES,
      help=f"Independent samples to aggregate (default: {DEFAULT_SAMPLES}).",
  )
  _ = parser.add_argument(
      "--capture",
      choices=CAPTURE_MODES,
      default=DEFAULT_CAPTURE,
      help=(
          "Trace source: 'proxy' (reverse proxy, raw wire record) or 'stream'"
          f" (claude stream-json, no proxy). Default: {DEFAULT_CAPTURE}."
      ),
  )
  args = parser.parse_args()

  result = annotate_by_id_with_aggregation(
      args.instance_id,
      dataset=args.dataset,
      samples=args.samples,
      model=args.model,
      capture=args.capture,
  )

  agg = result.aggregate
  status = "OK" if agg.is_valid else "NEEDS REVIEW"
  counts = [len(c.annotation.snippets) for c in result.candidates]
  print(f"[{status}] {result.instance_id}")
  print(f"  candidates:   {counts} snippets")
  print(f"  aggregate:    {len(agg.annotation.snippets)} snippets")
  print(f"  complete:     {agg.complete}")
  print(f"  stored under: {result.directory}")
  if agg.validation_problems:
    print("  aggregate validation problems:")
    for key, problems in agg.validation_problems.items():
      print(f"    {key}: {'; '.join(problems)}")
  return 0 if agg.is_valid else 1


if __name__ == "__main__":
  raise SystemExit(main())
