"""The config contract, checked against the parser that actually reads it.

`native_supervision.py` renders a document by hand from `config.rs`. Nothing
mechanical has ever compared the two, and four independent defects came out of
that gap in four review rounds — a missing field, an unknown field, a changed
type, a changed value domain. This is the check that closes it: **render here,
hand it to the binary's own parser, and assert what it does.**

Both directions, because only one of them proves nothing. That a valid document
is accepted is satisfied by a parser that accepts anything; that a broken one
is refused is satisfied by a parser that refuses everything. The negative cases
are drawn from the four shapes that actually happened, not invented ones — a
real history makes a better negative set than imagination, because it is the
distribution the next defect is drawn from.

**The observable is a side effect, not an exit code.** Every refusal the
wrapper makes before the actor exists — a bad config, an unreadable criterion,
a missing endpoint, an unspawnable actor — exits 3, so the exit code cannot
distinguish "the config was rejected" from "the config was fine and something
later failed". What can: `config::load` is the *first* thing `run` does and
`Actor::spawn` comes after it, so **if the actor ran, the config was accepted.**
The actor here writes a sentinel file, and the two arms differ in whether that
file exists.
"""

from __future__ import annotations

from collections.abc import Callable
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from swe_lab.trace_synthesis.native_supervision import (
    API_KEY_ENV,
    BASE_URL_ENV,
    Blocking,
    NativeSupervision,
)
from swe_lab.trace_synthesis.supervisor_binary import local_build

#: Set this to ``require`` where a binary is supposed to exist — a machine that
#: provisioned one, or a job that built it. Absence is then a **failure**
#: rather than a skip.
#:
#: The gate exists because a test that skips when its subject is missing is
#: green forever and green for the wrong reason: in a CI summary a skip and a
#: pass are the same line. The gate's own two behaviors are asserted below, in
#: a test that needs no binary and therefore always runs.
REQUIRE_ENV = "SWE_LAB_SUPERVISOR_ROUNDTRIP"

_SENTINEL_NAME = "actor-ran"

_SUPERVISION = NativeSupervision(
    model="anthropic/claude-sonnet-5",
    budget=3,
    cooldown=4,
    window=8,
    judge_every_n_assistant_messages=3,
    block_actor_while_judging=Blocking.STDOUT,
)


def _binary_or_skip() -> Path:
  """Return the wrapper to test against, skipping or failing when absent.

  Returns:
    The local build named by the environment.

  Raises:
    AssertionError: A binary was declared to be required and is not there.
  """
  build = local_build()
  if build is not None:
    return Path(str(build))
  if os.environ.get(REQUIRE_ENV) == "require":
    raise AssertionError(
        f"{REQUIRE_ENV}=require, but no wrapper binary is configured; the"
        " round-trip check cannot run and must not report as passed"
    )
  return pytest.skip(
      "no wrapper binary configured; see this module's docstring"
  )


def _run_wrapper(binary: Path, document: object, workdir: Path) -> Path:
  """Hand ``document`` to the wrapper and return the sentinel's path.

  The sentinel exists afterwards if and only if the wrapper got as far as
  launching the actor, which it does only once the config has been read and
  accepted.

  Args:
    binary: The wrapper.
    document: The config to write, already a JSON-ready object.
    workdir: A scratch directory for this invocation.

  Returns:
    Where the actor would have written its sentinel.
  """
  config = workdir / "supervisor-config.json"
  _ = config.write_text(json.dumps(document))
  sentinel = workdir / _SENTINEL_NAME
  _ = subprocess.run(
      [
          str(binary),
          "run",
          "--config",
          str(config),
          "--actor-event-log",
          str(workdir / "events.jsonl"),
          "--supervisor-log",
          str(workdir / "supervisor.jsonl"),
          "--summary",
          str(workdir / "summary.json"),
          "--actor-stderr",
          str(workdir / "actor.stderr"),
          "--",
          "/bin/sh",
          "-c",
          f"touch {sentinel}",
      ],
      capture_output=True,
      timeout=120,
      check=False,
      # A minimal environment, as everywhere the wrapper is invoked from the
      # host: the endpoint is needed to get past `Endpoint::from_env`, and no
      # model call happens because the actor exits before any boundary. The
      # key is a placeholder and reaches nothing.
      env={
          "PATH": os.defpath,
          BASE_URL_ENV: "http://127.0.0.1:9/v1",
          API_KEY_ENV: "not-a-real-key",
      },
  )
  return sentinel


def test_the_document_this_repo_renders_is_accepted_by_the_binary(
    tmp_path: Path,
):
  """The accept arm, and the one that a refuse-everything parser fails.

  This is the whole point of the round-trip: the hand-written mirror in
  `native_supervision.py` is checked against the parser it mirrors, rather than
  against another reading of `config.rs` by the same pair of eyes.
  """
  binary = _binary_or_skip()
  document = _SUPERVISION.config_document(task="Fix the failing test.")

  sentinel = _run_wrapper(binary, document, tmp_path)

  assert sentinel.exists(), (
      "the wrapper never reached the actor, so it did not accept the document"
      " this repo renders"
  )


def _drop_a_field(document: dict[str, Any]) -> None:
  del document["limits"]["max_actor_stdout_bytes"]


def _add_a_field_the_binary_does_not_know(document: dict[str, Any]) -> None:
  document["policy"]["speak_twice"] = True


def _change_a_fields_type(document: dict[str, Any]) -> None:
  document["policy"]["block_actor_while_judging"] = True


def _leave_a_fields_domain(document: dict[str, Any]) -> None:
  document["policy"]["window"] = 0


@pytest.mark.parametrize(
    "break_it,label",
    [
        (_drop_a_field, "missing-field"),
        (_add_a_field_the_binary_does_not_know, "unknown-field"),
        (_change_a_fields_type, "wrong-type"),
        (_leave_a_fields_domain, "out-of-domain"),
    ],
    ids=["missing-field", "unknown-field", "wrong-type", "out-of-domain"],
)
def test_a_document_broken_the_way_ours_have_broken_is_refused(
    break_it: Callable[[dict[str, Any]], None], label: str, tmp_path: Path
):
  """The reject arms, one per shape that has actually happened here.

  Each mirrors a real defect from this contract's review history: a field added
  on the Rust side and not mirrored; a field this side kept that the binary
  does not know (`deny_unknown_fields` on every struct); a field whose type
  changed under us (`block_actor_while_judging` was a bool before #387 made it
  an enum); and a value outside the domain the binary requires.

  Args:
    break_it: Mutates a valid document into the broken shape.
    label: What is being broken, for the failure message.
    tmp_path: A scratch directory.
  """
  binary = _binary_or_skip()
  document = copy.deepcopy(
      dict(_SUPERVISION.config_document(task="Fix the failing test."))
  )
  break_it(document)

  sentinel = _run_wrapper(binary, document, tmp_path)

  assert not sentinel.exists(), (
      f"the wrapper launched the actor with a {label} document: it accepted a"
      " config it should have refused"
  )


def test_the_gate_fails_rather_than_skips_where_a_binary_is_required(
    monkeypatch: pytest.MonkeyPatch,
):
  """The gate is asserted, because a silent skip is green for the wrong reason.

  This runs with no binary — in CI, always — so the thing protecting the
  round-trip is itself covered even where the round-trip cannot be.

  Args:
    monkeypatch: Used to clear the binary and set the requirement.
  """
  monkeypatch.setattr(sys.modules[__name__], "local_build", lambda: None)

  monkeypatch.setenv(REQUIRE_ENV, "require")
  with pytest.raises(AssertionError, match=REQUIRE_ENV):
    _ = _binary_or_skip()

  # …and the other behavior, so this test cannot be satisfied by a gate that
  # always raises — which would fail every machine without a binary and be
  # "fixed" by deleting the gate.
  monkeypatch.delenv(REQUIRE_ENV, raising=False)
  with pytest.raises(pytest.skip.Exception, match="no wrapper binary"):
    _ = _binary_or_skip()
