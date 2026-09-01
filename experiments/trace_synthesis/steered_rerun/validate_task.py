#!/usr/bin/env python3
"""The task-quality gate: is this a reasoning failure, or a broken task?

A harvested exit-2 rollout is only raw material for trace synthesis if the
actor actually *erred*. On SWE-bench Pro that is not the common case: OpenAI's
2026-07-08 audit of the public 731 estimates **~30% of tasks are broken** and
retracts their earlier recommendation to adopt the dataset
([survey](../../../docs/research/swebench-pro-task-quality.md)). Feeding a
broken task into "agent erred, a hint recovers it" teaches the hint to fix the
dataset, not the agent.

**The criterion is determinacy** (kimjune01's Pro audit, the sharpest of the
published ones): *is the behavior the hidden tests judge pinned down by what
the solver was given* — the problem statement, the requirements, the interface,
and the repository at ``base_commit``? Not "is it reachable" and not "did some
rollout pass": a coin flip between two internally consistent readings is
reachable and still not pinned down. Issue #261 selects on **mixed outcome**,
so "one sample resolved" is already true of every candidate and screens
nothing.

This program does not decide. It assembles the evidence a decision needs and
prints it side by side, plus the one screen that is mechanical:

**The unpinned-token screen** (ICLR 2026 submission ``aNUVttHlU8``): a lexical
element that the hidden tests require, that the gold patch introduces, and that
appears **nowhere** in the four things the solver holds, cannot be derived —
only guessed. Non-empty output is an alarm, not a verdict. Its blind spot is
named in the survey and matters here: it cannot catch *method* versus
*Function*, because both words do appear in the prompt. That case needs the
second, manual check — the **requirements/interface diagonal** — for which the
survey found no published detector at all.

Usage::

  direnv exec . uv run python experiments/trace_synthesis/steered_rerun/validate_task.py <instance-id>
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess

from swe_lab.datasets.loader import load_dataset

_HERE = pathlib.Path(__file__).resolve().parent

# Lexical elements worth screening: identifiers, dotted paths, quoted strings
# and numeric literals. Deliberately generous — a false alarm costs a reader a
# few seconds, a miss costs a whole experiment.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STRING = re.compile(r"""["']([^"'\n]{3,60})["']""")
_NUMBER = re.compile(r"\b\d{2,}\b")

# Tokens every test file carries; screening them is pure noise.
_BORING = frozenset("""
assert assertEqual assertTrue assertFalse assertRaises assertIn assertIsNone
import from def class self return None True False and not for in with test
tests pytest unittest mark parametrize raises fixture expected actual result
value values args kwargs setUp tearDown mock patch MagicMock lambda print len
str int float bool list dict tuple set type object Exception ValueError
TypeError KeyError AttributeError RuntimeError NotImplementedError staticmethod
classmethod property super init main module package the and or if else elif
""".split())


def added_lines(patch: str) -> str:
  """Return only the lines a diff adds.

  What the hidden tests *require* lives in what the test patch adds; the
  context lines are pre-existing code the solver can already read.

  Args:
    patch: A unified diff.

  Returns:
    The added lines, newline-joined, without their ``+`` markers.
  """
  return "\n".join(
      line[1:]
      for line in patch.splitlines()
      if line.startswith("+") and not line.startswith("+++")
  )


def tokens(text: str) -> set[str]:
  """Return the screenable lexical elements of a text.

  Args:
    text: Any source or prose.

  Returns:
    Identifiers, quoted string bodies and multi-digit numbers, minus the ones
    every test file carries.
  """
  found = set(_IDENTIFIER.findall(text))
  found |= {match.strip() for match in _STRING.findall(text)}
  found |= set(_NUMBER.findall(text))
  return {token for token in found if token not in _BORING}


def unpinned(instance: object, repo_source: str) -> list[str]:
  """Return tokens the tests require that the solver's inputs never mention.

  Args:
    instance: The dataset row.
    repo_source: The repository text the solver can read at ``base_commit``.
      Empty when it was not gathered — the screen then over-reports, and says
      so rather than silently passing.

  Returns:
    The unpinned tokens, sorted.
  """
  required = tokens(added_lines(instance.test_patch))
  given = tokens(
      "\n".join([
          instance.problem_statement,
          instance.requirements,
          instance.interface,
          repo_source,
      ])
  )
  # The gold patch is *not* part of what the solver holds — it is the answer.
  # A token is only interesting when the tests require it and the gold patch
  # introduces it, which is what makes it a thing the solver had to invent.
  introduced = tokens(added_lines(instance.patch))
  return sorted((required & introduced) - given)


def repo_text(instance: object, paths: list[str]) -> tuple[str, int]:
  """Read the touched files out of the instance image at ``base_commit``.

  The determinacy question includes "and the repository source", so a token
  that is already in the code the solver would read is pinned even when the
  prose never names it. Read from the image rather than a checkout, because the
  image *is* what the rollout sees.

  Args:
    instance: The dataset row.
    paths: Repository-relative paths to read.

  A path the gold patch *creates* does not exist at ``base_commit``, which is
  normal and not a failure: it is read as absent, and the count of files
  actually found is reported so an empty read is never mistaken for a clean
  screen.

  Returns:
    The text of whatever existed, and how many of ``paths`` were found.
  """
  workdir = instance.sandbox_spec().workdir
  script = "; ".join(
      f"cat {workdir}/{path} 2>/dev/null && echo '@@FOUND@@'" for path in paths
  )
  done = subprocess.run(
      [
          "docker", "run", "--rm", "--platform", "linux/amd64",
          "--entrypoint", "/bin/bash", instance.sandbox_spec().image_ref,
          "-c", f"{{ {script}; }}; exit 0",
      ],
      capture_output=True,
      text=True,
      check=False,
  )
  return done.stdout.replace("@@FOUND@@", ""), done.stdout.count("@@FOUND@@")


def touched_paths(patch: str) -> list[str]:
  """Return the repository-relative paths a diff touches.

  Args:
    patch: A unified diff.

  Returns:
    The paths, in order of first appearance.
  """
  seen: list[str] = []
  for match in re.finditer(r"^\+\+\+ b/(.+)$", patch, re.MULTILINE):
    path = match.group(1).strip()
    if path not in seen:
      seen.append(path)
  return seen


def report(instance: object) -> str:
  """Assemble the evidence a determinacy judgement needs.

  Args:
    instance: The dataset row.

  Returns:
    The Markdown dossier.
  """
  paths = touched_paths(instance.patch) + touched_paths(instance.test_patch)
  source, found = repo_text(instance, paths)
  alarms = unpinned(instance, source)
  nouns = sorted(set(re.findall(r"\bThe (\w+)\b", instance.requirements)))
  types = sorted(set(re.findall(r"Type:\s*(\w+)", instance.interface)))
  return "\n".join([
      f"# Task-quality dossier — `{instance.instance_id}`",
      "",
      "Assembled by [`validate_task.py`](../validate_task.py). The criterion is",
      "**determinacy**: is the behavior the hidden tests judge pinned down by the",
      "problem statement, the requirements, the interface, and the repository at",
      "`base_commit`? See",
      "[`docs/research/swebench-pro-task-quality.md`](../../../../docs/research/swebench-pro-task-quality.md)",
      "for the criterion's source and OpenAI's four categories.",
      "",
      "## The requirements/interface diagonal",
      "",
      "The survey found **no published detector** that diffs these two Pro-specific",
      "fields against each other, and the failure it catches — one field calling a",
      "unit a *method* while the other types it a *Function* — is invisible to the",
      "token screen, because both words are in the prompt.",
      "",
      f"- nouns the requirements use (`The <noun>`): {nouns or '(none)'}",
      f"- types the interface assigns (`Type: <t>`): {types or '(none)'}",
      "",
      "## The unpinned-token screen",
      "",
      "Tokens the added test lines require **and** the gold patch introduces, that",
      "appear in none of the four things the solver holds. Non-empty is an alarm,",
      "not a verdict — read each one and say whether it was derivable.",
      "",
      f"- repository source read: {len(source)} characters;"
      f" {found} of {len(paths)} touched paths exist at `base_commit`"
      + (
          " — the rest are files the gold patch creates, which is normal"
          if found < len(paths)
          else ""
      ),
      "",
      "```",
      "\n".join(alarms) if alarms else "(none)",
      "```",
      "",
      "## Required tests",
      "",
      "```",
      "\n".join(instance.fail_to_pass),
      "```",
      "",
      "## problem_statement",
      "",
      instance.problem_statement,
      "",
      "## requirements",
      "",
      instance.requirements,
      "",
      "## interface",
      "",
      instance.interface,
      "",
      "## test_patch (added lines only)",
      "",
      "```diff",
      added_lines(instance.test_patch),
      "```",
      "",
      "## Verdict",
      "",
      "_Filled in by hand. State the evidence, not just the call._",
      "",
      "- [ ] **Determinate** — the graded behavior follows from the solver's inputs.",
      "- [ ] **Broken: misleading prompt** — points at the wrong behavior, or",
      "      contradicts what the tests require.",
      "- [ ] **Broken: overly strict tests** — forces implementation detail the",
      "      prompt never specifies.",
      "- [ ] **Broken: underspecified prompt** — omits a requirement the hidden",
      "      tests enforce and that cannot reasonably be inferred.",
      "- [ ] **Broken: low-coverage tests** — an incomplete fix would pass.",
      "",
  ])


def main() -> None:
  """Write one instance's dossier under ``task-validation/``."""
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.add_argument("instance")
  _ = parser.add_argument("--dataset", default="swebench_pro")
  args = parser.parse_args()

  instance = load_dataset(args.dataset).require(args.instance)
  out = _HERE / "task-validation"
  out.mkdir(exist_ok=True)
  # Owner, repo *and* commit prefix. Two instances of one repo appear in the
  # candidate list, so the repo alone silently overwrote one dossier with the
  # other's; a fixed-width slice of the rest then did the same for qutebrowser,
  # whose owner and repo are the same word and ate the whole budget. Parse the
  # id's actual shape instead: `<owner>__<repo>-<sha>-v<sha>`.
  stem = args.instance.removeprefix("instance_")
  owner, _, rest = stem.partition("__")
  repo, _, tail = rest.partition("-")
  path = out / f"{owner}__{repo}-{tail[:8]}.md"
  _ = path.write_text(report(instance))
  print(f"wrote {path}")


if __name__ == "__main__":
  main()
