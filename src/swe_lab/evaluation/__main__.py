"""CLI: grade a patch for one instance by running its tests in the container.

    # gold self-test (apply the dataset's own gold patch -> should resolve)
    python -m swe_lab.evaluation <instance_id> --gold

    # grade a candidate patch from a file
    python -m swe_lab.evaluation <instance_id> --patch-file fix.diff

Needs Docker available. Prints a JSON result; exit 0 iff resolved.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from swe_lab.core.datasets.loader import load_dataset
from swe_lab.core.datasets.swebench_pro import (
    evaluate,
    SweBenchProAdapter,
    SweBenchProInstance,
)


def main() -> int:
  parser = argparse.ArgumentParser(
      prog="python -m swe_lab.evaluation",
      description="Grade a patch by running an instance's tests in its image.",
  )
  _ = parser.add_argument("instance_id")
  _ = parser.add_argument("--dataset", default="swebench_pro")
  _ = parser.add_argument(
      "--gold",
      action="store_true",
      help="grade the dataset's own gold patch (harness self-test).",
  )
  _ = parser.add_argument(
      "--patch-file", help="path to a candidate patch (.diff) to grade."
  )
  _ = parser.add_argument("--timeout", type=float, default=1800.0)
  _ = parser.add_argument(
      "--no-network", action="store_true", help="run the container offline."
  )
  args = parser.parse_args()

  instance = load_dataset(args.dataset).require(args.instance_id)
  if not isinstance(instance, SweBenchProInstance):
    raise TypeError(f"Unexpected record type: {type(instance).__name__}")
  spec = SweBenchProAdapter().eval_spec(instance)

  if args.gold:
    patch = instance.patch
  elif args.patch_file:
    with open(args.patch_file) as handle:
      patch = handle.read()
  else:
    parser.error("pass --gold or --patch-file")

  result = evaluate(
      spec, patch=patch, timeout=args.timeout, network=not args.no_network
  )
  print(json.dumps(asdict(result), indent=2))
  return 0 if result.resolved else 1


if __name__ == "__main__":
  raise SystemExit(main())
