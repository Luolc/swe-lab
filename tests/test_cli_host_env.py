"""Tests for the host-side credential shims the CLI entry point runs."""

from __future__ import annotations

from collections.abc import Iterator
import os

import pytest

from swe_lab.cli.host_env import (
    adopt_host_scoped_credentials,
    HOST_OAUTH_TOKEN_ENV,
    HOST_SUPERVISOR_API_KEY_ENV,
)
from swe_lab.credential_sources import (
    adopted_credential_sources,
    forget_adoptions,
)
from swe_lab.harnesses.claude_code.constants import OAUTH_TOKEN_ENV
from swe_lab.trace_synthesis.native_supervision import (
    API_KEY_ENV as SUPERVISOR_API_KEY_ENV,
)


@pytest.fixture(autouse=True)
def _forget() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
  """Keep the adoption record from outliving the test that made it.

  It is process-global, like the environment it describes. A test here that
  left an adoption behind would surface as a failure in the *run record* tests,
  which assert an exact `record_extra` dict — a confusing failure far from its
  cause, and one that depends on collection order.
  """
  yield
  forget_adoptions()


def test_the_repo_scoped_token_is_adopted_under_the_name_a_run_reads(
    monkeypatch: pytest.MonkeyPatch,
):
  # The whole point of the repo-scoped name: `.envrc.local` exports it so an
  # interactive `claude` in this directory never picks the token up, and the
  # run still finds it under the only name the harness and the sandbox know.
  monkeypatch.delenv(OAUTH_TOKEN_ENV, raising=False)
  monkeypatch.setenv(HOST_OAUTH_TOKEN_ENV, "from-envrc")
  adopt_host_scoped_credentials()
  assert os.environ[OAUTH_TOKEN_ENV] == "from-envrc"


def test_an_existing_canonical_token_is_never_overwritten(
    monkeypatch: pytest.MonkeyPatch,
):
  # CI sets the canonical name straight from the repository secret, and a
  # developer may export it for one command. Either way the caller has already
  # answered the question; a shim that overwrote it would be silently choosing
  # a different credential than the one asked for.
  monkeypatch.setenv(OAUTH_TOKEN_ENV, "from-ci")
  monkeypatch.setenv(HOST_OAUTH_TOKEN_ENV, "from-envrc")
  adopt_host_scoped_credentials()
  assert os.environ[OAUTH_TOKEN_ENV] == "from-ci"


def test_an_explicitly_emptied_canonical_token_is_left_empty(
    monkeypatch: pytest.MonkeyPatch,
):
  # `CLAUDE_CODE_OAUTH_TOKEN= swe-lab run …` is someone blanking the credential
  # for one command. It is *set*, so the shim has nothing to decide: restoring
  # it from `.envrc.local` would override an explicit instruction with an
  # ambient one, and the run would authenticate against the developer's word.
  monkeypatch.setenv(OAUTH_TOKEN_ENV, "")
  monkeypatch.setenv(HOST_OAUTH_TOKEN_ENV, "from-envrc")
  adopt_host_scoped_credentials()
  assert os.environ[OAUTH_TOKEN_ENV] == ""


def test_an_empty_repo_scoped_token_is_not_adopted(
    monkeypatch: pytest.MonkeyPatch,
):
  # The source side is the other way round: there is nothing to copy, and
  # copying it would manufacture the empty canonical variable the branch above
  # exists to respect.
  monkeypatch.delenv(OAUTH_TOKEN_ENV, raising=False)
  monkeypatch.setenv(HOST_OAUTH_TOKEN_ENV, "")
  adopt_host_scoped_credentials()
  assert OAUTH_TOKEN_ENV not in os.environ


def test_nothing_to_adopt_leaves_the_environment_alone(
    monkeypatch: pytest.MonkeyPatch,
):
  monkeypatch.delenv(OAUTH_TOKEN_ENV, raising=False)
  monkeypatch.delenv(HOST_OAUTH_TOKEN_ENV, raising=False)
  adopt_host_scoped_credentials()
  assert OAUTH_TOKEN_ENV not in os.environ


def test_the_supervisor_key_is_adopted_from_the_machine_wide_pool(
    monkeypatch: pytest.MonkeyPatch,
):
  # The other direction from the OAuth shim: the shell exports the machine-wide
  # name every OpenRouter consumer on the box shares, and the run reads the
  # repo-scoped one the sandbox passes by reference to the wrapper.
  monkeypatch.delenv(SUPERVISOR_API_KEY_ENV, raising=False)
  monkeypatch.setenv(HOST_SUPERVISOR_API_KEY_ENV, "key-one,key-two")
  adopt_host_scoped_credentials()
  # Copied whole. Splitting belongs to the wrapper's `api_key_from_env`, which
  # does it in-process; a shim that split it would put a key somewhere visible.
  assert os.environ[SUPERVISOR_API_KEY_ENV] == "key-one,key-two"


def test_an_existing_supervisor_key_is_never_overwritten(
    monkeypatch: pytest.MonkeyPatch,
):
  # Same rule as the OAuth side: a caller who set the canonical name has
  # already answered the question this shim exists to answer.
  monkeypatch.setenv(SUPERVISOR_API_KEY_ENV, "set-for-this-run")
  monkeypatch.setenv(HOST_SUPERVISOR_API_KEY_ENV, "from-envrc")
  adopt_host_scoped_credentials()
  assert os.environ[SUPERVISOR_API_KEY_ENV] == "set-for-this-run"


def test_the_run_record_names_the_source_and_carries_no_credential(
    monkeypatch: pytest.MonkeyPatch,
):
  # Adoption is otherwise invisible, so the record says which name a value came
  # from. The second half is the credential boundary: a provenance record that
  # carried the value would put a key in every persisted run.
  secret = "key-one,key-two"
  monkeypatch.delenv(SUPERVISOR_API_KEY_ENV, raising=False)
  monkeypatch.setenv(HOST_SUPERVISOR_API_KEY_ENV, secret)
  adopt_host_scoped_credentials()
  sources = adopted_credential_sources()
  assert sources[SUPERVISOR_API_KEY_ENV] == HOST_SUPERVISOR_API_KEY_ENV
  assert secret not in repr(sources)


def test_nothing_adopted_is_recorded_as_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
  # What CI looks like: it sets the canonical names itself, so there is no
  # adoption to report and the run record carries no such field at all.
  monkeypatch.setenv(OAUTH_TOKEN_ENV, "from-ci")
  monkeypatch.setenv(SUPERVISOR_API_KEY_ENV, "from-ci")
  adopt_host_scoped_credentials()
  assert adopted_credential_sources() == {}
