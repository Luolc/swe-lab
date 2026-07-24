"""The ``swe_lab`` command-line interface.

One Typer app; each subcommand is a typed function in its own module (the
dispatcher stays a thin table so it never grows into one giant file). Run it as
``python -m swe_lab <subcommand>``.
"""

import typer

from .eval import eval_cmd
from .rollout import rollout_cmd

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def root() -> None:
  """swe-lab: build, run, and evaluate SWE-agent evaluation data."""
  # A top-level callback keeps this a multi-command group: subcommands are
  # required even while `eval` is the only one registered (Typer otherwise
  # collapses a single-command app into that command).


_ = app.command("eval")(eval_cmd)
_ = app.command("rollout")(rollout_cmd)
# _ = app.command("verify")(verify_cmd)    # 10b

__all__ = ["app"]
