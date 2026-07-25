"""The ``eval`` subcommand: grade one instance's patch in a container."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Annotated

import typer

from swe_lab.core.datasets.loader import load_dataset
from swe_lab.core.datasets.swebench_pro import SweBenchProInstance
from swe_lab.core.datasets.swebench_pro.unit_test import compile_unit_test
from swe_lab.core.paths import cache_root, find_repo_root
from swe_lab.evaluation.methods.unit_test import run_unit_test
from swe_lab.sandbox import BackendKind, build_backend

_WORKSPACES_SUBDIR = "eval_workspaces"


def eval_cmd(
    instance_id: str,
    dataset: str = "swebench_pro",
    gold: Annotated[
        bool, typer.Option(help="Grade the instance's own gold patch.")
    ] = False,
    patch_file: Annotated[
        Path | None, typer.Option(help="Path to a candidate .diff to grade.")
    ] = None,
    timeout: Annotated[
        float, typer.Option(help="Seconds before the eval run is killed.")
    ] = 1800.0,
    network: Annotated[
        bool, typer.Option(help="Give the container network access.")
    ] = True,
    pull: Annotated[
        bool, typer.Option(help="Pull the image before running.")
    ] = True,
    backend: Annotated[
        BackendKind,
        typer.Option(help="Sandbox backend (host Docker, or the GH job)."),
    ] = BackendKind.HOST,
) -> None:
  """Grade one instance by running its tests in its container.

  Applies the patch (``--gold`` for the instance's own gold patch, or a
  candidate via ``--patch-file``), runs the instance's test suite, and reports
  the verdict. Exit code is 0 iff the patch resolves the instance.
  """
  if gold == (patch_file is not None):
    raise typer.BadParameter("pass exactly one of --gold / --patch-file")

  instance = load_dataset(dataset).require(instance_id)
  if not isinstance(instance, SweBenchProInstance):
    raise typer.BadParameter(f"dataset {dataset!r} is not wired for eval yet")

  if gold:
    patch = instance.patch
  else:
    assert patch_file is not None  # guaranteed by the exactly-one check above
    patch = patch_file.read_text()

  root = find_repo_root()
  sandbox_spec, unit_spec = compile_unit_test(
      instance, patch=patch, repo_root=root
  )
  workspace = cache_root(root) / _WORKSPACES_SUBDIR / instance.instance_id
  # The manager refuses a non-empty workspace; a fresh grade starts clean.
  shutil.rmtree(workspace, ignore_errors=True)

  result, verdict = run_unit_test(
      sandbox_spec,
      unit_spec,
      backend=build_backend(backend, network=network, pull=pull),
      workspace=workspace,
      timeout=timeout,
  )

  summary: dict[str, object] = {
      "instance_id": instance.instance_id,
      "status": result.status.value,
      "resolved": bool(verdict and verdict.resolved),
  }
  if verdict is not None:
    summary |= {
        "score": verdict.score,
        "output_state": verdict.output_state.value,
        "passed": sorted(verdict.passed),
        "missing": sorted(verdict.missing),
    }
  if result.error is not None:
    summary["error"] = repr(result.error)
  print(json.dumps(summary, indent=2))
  raise typer.Exit(0 if summary["resolved"] else 1)
