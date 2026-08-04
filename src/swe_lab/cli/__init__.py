"""The ``swe_lab`` command-line interface.

One Typer app; each subcommand is a typed function in its own module (the
dispatcher stays a thin table so it never grows into one giant file). Run it as
``python -m swe_lab <subcommand>``.
"""

import typer

from .eval import eval_cmd
from .promote import promote_cmd
from .rollout import rollout_in_docker
from .run import run_cmd

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def root() -> None:
  """swe-lab: build, run, and evaluate SWE-agent evaluation data."""
  # A top-level callback keeps this a multi-command group, so an explicit
  # subcommand (eval / rollout / promote) is always required — Typer otherwise
  # collapses a single-command app into that one command.


_ = app.command(
    "run",
    context_settings={
        # Overrides are options this command cannot declare ahead of time —
        # they name fields of whatever workflow was asked for. Click hands
        # them over as extra args, and `swe_lab.cli.overrides` parses them.
        "ignore_unknown_options": True,
        "allow_extra_args": True,
    },
)(run_cmd)
_ = app.command("eval")(eval_cmd)
_ = app.command("rollout")(rollout_in_docker)
_ = app.command("promote")(promote_cmd)

__all__ = ["app"]
