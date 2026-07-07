"""CLI: annotate a single instance by id.

python -m swebench_related_files_annotation.annotate <instance_id>
"""

from __future__ import annotations

import argparse

from .proxy import DEFAULT_BASE_PORT
from .runner import annotate_by_id, DEFAULT_MODEL


def main() -> int:
  parser = argparse.ArgumentParser(
      prog="python -m swebench_related_files_annotation.annotate",
      description="Annotate one SWE-bench instance's relevant code snippets.",
  )
  _ = parser.add_argument("instance_id", help="Instance id to annotate.")
  _ = parser.add_argument(
      "--model", default=DEFAULT_MODEL, help="Claude model (default: sonnet)."
  )
  _ = parser.add_argument(
      "--base-port",
      type=int,
      default=DEFAULT_BASE_PORT,
      help=f"Base proxy port (default: {DEFAULT_BASE_PORT}).",
  )
  args = parser.parse_args()

  result = annotate_by_id(
      args.instance_id, model=args.model, base_port=args.base_port
  )

  status = "OK" if result.is_valid else "NEEDS REVIEW"
  print(f"[{status}] {result.instance_id}")
  print(f"  snippets:     {len(result.annotation.snippets)}")
  print(f"  complete:     {result.complete}")
  print(f"  annotation:   {result.annotation_path}")
  print(f"  last exchange:{result.last_exchange_path}")
  if result.validation_problems:
    print("  validation problems:")
    for key, problems in result.validation_problems.items():
      print(f"    {key}: {'; '.join(problems)}")
  return 0 if result.is_valid else 1


if __name__ == "__main__":
  raise SystemExit(main())
