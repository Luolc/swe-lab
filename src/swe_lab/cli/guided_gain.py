"""The ``guided-gain`` subcommand: the 2×2 reading of a from-scratch sweep."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Annotated

from etils import epath
import typer

from swe_lab.cli.persist_wiring import local_store
from swe_lab.paths import find_repo_root
from swe_lab.sandbox import build_store
from swe_lab.trace_synthesis.guided_gain import guided_gain
from swe_lab.workflow.definitions import (
    BASELINE_UNIT_TEST_KEY,
    GUIDED_UNIT_TEST_KEY,
)


def guided_gain_cmd(
    sweep: Annotated[
        str,
        typer.Argument(
            help="The sweep id the from_scratch_guided_trace runs were keyed"
            " under (`run … --persist --sweep <id>`)."
        ),
    ],
    store_root: Annotated[
        Path | None,
        typer.Option(
            help="A filesystem store root to read instead of the repo's T1"
            " store (.cache/store/runs) — e.g. a run directory's own"
            " `store/`, or a copy taken off the box."
        ),
    ] = None,
    baseline_key: Annotated[
        str, typer.Option(help="Entry key of the blind grading.")
    ] = BASELINE_UNIT_TEST_KEY,
    guided_key: Annotated[
        str, typer.Option(help="Entry key of the guided grading.")
    ] = GUIDED_UNIT_TEST_KEY,
) -> None:
  """Read a sweep's blind and guided verdicts into a 2×2 (JSON + a table).

  The JSON goes to stdout, the table to stderr, so the numbers can be piped
  while a person still sees them. A sweep with no records at all is refused
  rather than rendered as four zeros: nothing measured is not a measurement
  of zero.
  """
  store = (
      build_store("filesystem", root=epath.Path(store_root))
      if store_root is not None
      else local_store(find_repo_root())
  )
  records = store.read_manifests(sweep)
  if not records:
    print(
        f"sweep {sweep!r} has no attempt records in the store; nothing to"
        " read (a run lands there with `--persist --sweep <id>`)",
        file=sys.stderr,
    )
    raise typer.Exit(1)
  reading = guided_gain(
      records,
      sweep_id=sweep,
      baseline_key=baseline_key,
      guided_key=guided_key,
  )
  print(reading.render(), end="", file=sys.stderr)
  print(json.dumps(reading.to_json(), indent=2))
