"""The ``swe_lab`` command-line interface.

One Typer app; each subcommand is a typed function in its own module (the
dispatcher stays a thin table so it never grows into one giant file). Run it as
``python -m swe_lab <subcommand>``.
"""

import typer

from .host_env import adopt_host_scoped_token
from .promote import promote_cmd
from .run import run_cmd

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def root() -> None:
  """swe-lab: build, run, and evaluate SWE-agent evaluation data."""
  # A top-level callback keeps this a multi-command group, so an explicit
  # subcommand (run / promote) is always required — Typer otherwise collapses
  # a single-command app into that one command.
  #
  # It is also the one place every subcommand passes through, which is where
  # the repo-scoped OAuth token gets handed back to the name a run reads —
  # called explicitly here rather than run as an import side effect, so
  # importing any part of this package never edits the process environment.
  adopt_host_scoped_token()


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
_ = app.command("promote")(promote_cmd)

__all__ = ["app"]
