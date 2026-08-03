"""The ``promote`` subcommand: push a debug run's workspace into the T1 store.

The misclassification safety valve (task 12): a run left at T0 (its workspace
under ``.cache/``) can be moved into T1 after the fact — every file uploaded
under the run key, plus a manifest shard — so the debug/formal split never has
to be perfect at launch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from swe_lab.cli.persist_wiring import local_store, new_record
from swe_lab.paths import find_repo_root
from swe_lab.sandbox import promote


def promote_cmd(
    instance_id: Annotated[
        str, typer.Argument(help="The instance the workspace belongs to.")
    ],
    workspace: Annotated[
        Path,
        typer.Option(help="The debug run's workspace directory to promote."),
    ],
    sweep: Annotated[
        str, typer.Option(help="Sweep id the promoted run is keyed under.")
    ] = "adhoc",
    status: Annotated[
        str, typer.Option(help="Run status to record in the manifest.")
    ] = "promoted",
    backend: Annotated[
        str, typer.Option(help="Backend the run used (recorded only).")
    ] = "host",
) -> None:
  """Promote a debug workspace into T1: upload every file + a manifest shard."""
  if not workspace.is_dir():
    raise typer.BadParameter(f"workspace {workspace} is not a directory")
  root = find_repo_root()
  record = new_record(
      sweep=sweep,
      instance_id=instance_id,
      task="promoted",
      status=status,
      backend=backend,
      extra={"promoted": True},
  )
  written = promote(local_store(root), record, workspace)
  print(
      json.dumps(
          {
              "promoted": instance_id,
              "run_ts": written.run_ts,
              "keys": written.artifact_keys,
          },
          indent=2,
      )
  )
