"""Tests for the host-side OAuth token shim at the CLI entry point."""

from __future__ import annotations

import os

import pytest

from swe_lab.cli.host_env import adopt_host_scoped_token, HOST_OAUTH_TOKEN_ENV
from swe_lab.harnesses.claude_code.constants import OAUTH_TOKEN_ENV


def test_the_repo_scoped_token_is_adopted_under_the_name_a_run_reads(
    monkeypatch: pytest.MonkeyPatch,
):
  # The whole point of the repo-scoped name: `.envrc.local` exports it so an
  # interactive `claude` in this directory never picks the token up, and the
  # run still finds it under the only name the harness and the sandbox know.
  monkeypatch.delenv(OAUTH_TOKEN_ENV, raising=False)
  monkeypatch.setenv(HOST_OAUTH_TOKEN_ENV, "from-envrc")
  adopt_host_scoped_token()
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
  adopt_host_scoped_token()
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
  adopt_host_scoped_token()
  assert os.environ[OAUTH_TOKEN_ENV] == ""


def test_an_empty_repo_scoped_token_is_not_adopted(
    monkeypatch: pytest.MonkeyPatch,
):
  # The source side is the other way round: there is nothing to copy, and
  # copying it would manufacture the empty canonical variable the branch above
  # exists to respect.
  monkeypatch.delenv(OAUTH_TOKEN_ENV, raising=False)
  monkeypatch.setenv(HOST_OAUTH_TOKEN_ENV, "")
  adopt_host_scoped_token()
  assert OAUTH_TOKEN_ENV not in os.environ


def test_nothing_to_adopt_leaves_the_environment_alone(
    monkeypatch: pytest.MonkeyPatch,
):
  monkeypatch.delenv(OAUTH_TOKEN_ENV, raising=False)
  monkeypatch.delenv(HOST_OAUTH_TOKEN_ENV, raising=False)
  adopt_host_scoped_token()
  assert OAUTH_TOKEN_ENV not in os.environ
