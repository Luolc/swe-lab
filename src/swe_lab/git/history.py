"""Purge future git history from a task repo, and prove it is gone.

The SWE-Bench Pro images ship the **entire upstream history**, including the
commit that fixes the issue: ``git show <sha>`` hands the agent the reference
solution, and ``git log --all`` finds it without knowing any sha. Measured at a
100% exposure rate upstream (`SWE-bench_Pro-os#93`), and reproduced here — see
the task-25 plan for the numbers.

This module is the pure half, mirroring its sibling :mod:`~swe_lab.git.patch`:
it *builds* the in-container shell and *parses* what that shell reports. The
observer that runs it lives in :mod:`swe_lab.sandbox.observers.git_history`.

Two functions, matching the two halves of ADR-0010 §3b/§4:

- :func:`build_purge_script` — remove the future, **keep the past**. Future
  commits are reachable only through refs (branches, remote-tracking refs,
  tags), the reflog, and bare shas of unpruned objects; ancestors of the base
  commit are reachable through ``HEAD``. So deleting the first set and pruning
  removes exactly the future.
- :func:`build_report_script` — emit the before/after counts and the three
  assertions as JSON, so the caller decides rather than the script.

**Why tags are date-filtered rather than deleted.** ``git log`` / ``blame`` /
``show`` on commits *before* the base is legitimate research a human engineer
would have, and some regression tasks are solvable only through a tag that
predates the base. SWE-bench Verified preserves those deliberately; we match it,
which also keeps our numbers comparable with theirs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import shlex

# Ambient-config isolation, same rationale as the extraction side: a run must
# not inherit a ~/.gitconfig, a pager (which would hang a headless run), or an
# external diff helper.
_ISOLATED_ENV = (
    "GIT_CONFIG_GLOBAL=/dev/null",  # ignore the user's ~/.gitconfig
    "GIT_CONFIG_SYSTEM=/dev/null",  # ignore /etc/gitconfig (system-wide)
    "GIT_CONFIG_NOSYSTEM=1",  # belt-and-suspenders: no system config at all
    "GIT_PAGER=cat",  # never open a pager (would hang a headless run)
    "GIT_TERMINAL_PROMPT=0",  # never block asking for credentials
)


def _preamble(workdir: str) -> list[str]:
  """Return the header: strict mode, config isolation, and a *guarded* ``cd``.

  ``set -e`` comes **first**, and the ``cd`` is checked explicitly on top of it.
  Both, because this is the one line in the script that must never be
  best-effort: every command after it is destructive and none of them names a
  directory. A ``cd`` that fails silently leaves the shell in whatever
  directory it started in, and the purge then deletes *that* repo's branches,
  remotes and reflog. Demonstrated, not hypothesized — an unguarded version of
  this script wiped the branches and origin of the checkout it was launched
  from.

  The git-repo check closes the same hole one step further along: a ``cd`` that
  succeeds into a path that is not a repo would otherwise let ``git`` walk *up*
  to an enclosing one.

  Args:
    workdir: In-container path of the repo to operate on.

  Returns:
    The shared header lines.
  """
  quoted = shlex.quote(workdir)
  return [
      "set -eu",
      *(f"export {assignment}" for assignment in _ISOLATED_ENV),
      f'cd {quoted} || {{ echo "FATAL: no such directory: {workdir}" >&2;'
      " exit 78; }",
      # Refuse to touch anything unless *this* directory is itself a repo: with
      # no check, git would happily walk up to an enclosing one.
      'test "$(git rev-parse --show-toplevel 2>/dev/null)" = "$(pwd -P)"'
      f' || {{ echo "FATAL: {workdir} is not a git repository root" >&2;'
      " exit 78; }",
  ]


def build_purge_script(*, workdir: str) -> str:
  """Build the in-container shell that removes future history.

  POSIX-``sh`` compatible on purpose: some instance images are Alpine with
  busybox rather than the distro their Dockerfile names (upstream issue #75), so
  the script must not depend on bash or on GNU ``date``.

  The base commit is read from ``HEAD`` rather than passed in: the image has
  already checked it out, and reading it here means the script cannot disagree
  with the repo it is running against.

  Args:
    workdir: In-container path of the instance's repo.

  Returns:
    The shell script text, newline-terminated.
  """
  lines = [
      *_preamble(workdir),
      'BASE="$(git rev-parse HEAD)"',
      'BASE_TS="$(git show -s --format=%ct "$BASE")"',
      # HEAD must not be a branch ref we are about to delete.
      'git checkout --detach --quiet "$BASE"',
      # Symbolic refs FIRST, individually. `refs/remotes/origin/HEAD` is a
      # symref to `origin/main`, and `update-ref --stdin` refuses to delete a
      # symref together with its target ("multiple updates for ...") — which
      # aborts the whole transaction and purges *nothing*. Verified against a
      # real image; the upstream reference implementation has this bug.
      "git for-each-ref --format='%(refname) %(symref)'"
      " refs/heads refs/remotes refs/tags |"
      " while read -r ref target; do"
      ' [ -n "${target:-}" ] && git symbolic-ref --delete "$ref" || true;'
      " done",
      # Every remaining branch and remote-tracking ref, atomically.
      "git for-each-ref --format='delete %(refname)' refs/heads refs/remotes"
      " | git update-ref --stdin",
      # Tags: only those whose commit postdates the base. `^{}` dereferences an
      # annotated tag to its commit, so tag-object indirection is not a hiding
      # place (`git show-ref --dereference` would resolve it otherwise).
      "git for-each-ref --format='%(refname) %(objectname)' refs/tags |"
      " while read -r ref obj; do"
      ' ctime="$(git show -s --format=%ct "${obj}^{}" 2>/dev/null || echo 0)";'
      ' [ "$ctime" -gt "$BASE_TS" ] && printf \'delete %s\\n\' "$ref";'
      " done | git update-ref --stdin",
      # Remotes (the config URL leaks where to look), and the stray HEAD files.
      'for r in $(git remote); do git remote remove "$r"; done',
      "rm -f .git/FETCH_HEAD .git/ORIG_HEAD",
      # The reflog leaks commit messages after the refs are gone; the prune is
      # what stops an unreferenced object answering to a bare sha.
      "git reflog expire --expire=now --all",
      # `--aggressive` is deliberately omitted: measured at ~2.5x the time for
      # ~10% more space, and it blocks nothing extra — bare-sha access is
      # already dead without it.
      "git gc --prune=now --quiet",
  ]
  return "\n".join(lines) + "\n"


def build_report_script(
    *, workdir: str, solution_sha: str | None = None
) -> str:
  """Build the shell that reports repo state and the three assertions as JSON.

  Run it before *and* after the purge: the same script produces the "before"
  and "after" halves of a report, so a passing instance still carries evidence
  ("3444 commits ahead → 0") rather than only a boolean.

  The assertions are computed here but **not enforced** here — the script always
  exits 0 and the caller decides. A shell that exits non-zero mid-observer would
  turn a policy decision into a stack trace.

  ``base_reachable`` and ``no_future_commits`` are always computed;
  ``solution_reachable`` is ``null`` when no ``solution_sha`` is known, which is
  why ``no_future_commits`` is the load-bearing check — it catches leaks whose
  sha we never knew.

  Args:
    workdir: In-container path of the instance's repo.
    solution_sha: The fix commit that must be unreachable, when known.

  Returns:
    The shell script text, newline-terminated. Its stdout is one JSON object.
  """
  lines = [
      *_preamble(workdir),
      # Past the guard, a probe that cannot answer must report rather than
      # abort — the caller decides what a missing number means.
      "set +e",
      'BASE="$(git rev-parse HEAD 2>/dev/null || echo "")"',
      'BASE_TS="$(git show -s --format=%ct "$BASE" 2>/dev/null || echo 0)"',
      "REFS=$(git for-each-ref 2>/dev/null | wc -l)",
      "TAGS=$(git for-each-ref refs/tags 2>/dev/null | wc -l)",
      "HEADS=$(git for-each-ref refs/heads 2>/dev/null | wc -l)",
      "REMOTE_REFS=$(git for-each-ref refs/remotes 2>/dev/null | wc -l)",
      "REMOTES=$(git remote 2>/dev/null | wc -l)",
      "REFLOG=$(git reflog 2>/dev/null | wc -l)",
      # Not an ancestor of HEAD. Reported for context only — it is NOT the
      # assertion: a correct purge that keeps past tags legitimately leaves
      # thousands of these (ansible: 9630), all past-dated.
      "AHEAD=$(git log --oneline --all ^HEAD 2>/dev/null | wc -l)",
      # THE assertion: reachable commits that postdate the base. Integer
      # comparison of committer timestamps — no `date -d`, which is GNU-only
      # and absent from the Alpine images this dataset ships.
      "FUTURE=$(git log --all --format=%ct 2>/dev/null"
      " | awk -v b=\"$BASE_TS\" '$1>b' | wc -l)",
      'if [ -n "$BASE" ] && git cat-file -e "$BASE" 2>/dev/null;'
      " then BASE_OK=true; else BASE_OK=false; fi",
  ]
  if solution_sha:
    lines += [
        f"if git cat-file -e {shlex.quote(solution_sha)} 2>/dev/null;"
        " then SOL=true; else SOL=false; fi",
    ]
  else:
    lines.append("SOL=null")
  lines += [_emit_json(), "exit 0"]
  return "\n".join(lines) + "\n"


# Report field → the shell variable holding it, in emission order. The single
# place the two halves meet: the `printf` format, its arguments and the JSON
# keys are all derived from this, and ``test_the_report_script_emits_exactly_
# the_dataclass_fields`` fails if it ever drifts from ``GitHistoryReport``.
_SHELL_VARS: tuple[tuple[str, str], ...] = (
    ("base_sha", "BASE"),
    ("refs", "REFS"),
    ("tags", "TAGS"),
    ("heads", "HEADS"),
    ("remote_refs", "REMOTE_REFS"),
    ("remotes", "REMOTES"),
    ("reflog", "REFLOG"),
    ("non_ancestor_commits", "AHEAD"),
    ("future_commits", "FUTURE"),
    ("base_reachable", "BASE_OK"),
    ("solution_reachable", "SOL"),
)


def _emit_json() -> str:
  """Build the ``printf`` that prints the report as one JSON object.

  Generated rather than written out, so the key list exists **once**. Whether a
  value is JSON-quoted is read off the dataclass — ``str`` fields get quotes,
  numbers and booleans do not — which is why adding a field means touching the
  dataclass and this table, and nothing else.

  Returns:
    The ``printf`` command line.
  """
  # `from __future__ import annotations` makes `field.type` the *source text*
  # of the annotation, not the type object — so this compares strings. Getting
  # that wrong silently drops the quotes around `base_sha` and emits invalid
  # JSON, which is what `test_the_report_script_quotes_only_its_string_fields`
  # is there to catch.
  types = {field.name: str(field.type) for field in fields(GitHistoryReport)}
  pairs = [
      f'"{name}":"%s"' if types[name] == "str" else f'"{name}":%s'
      for name, _ in _SHELL_VARS
  ]
  args = " ".join(f'"${var}"' for _, var in _SHELL_VARS)
  return f"printf '{{{','.join(pairs)}}}\\n' {args}"


@dataclass(frozen=True, slots=True)
class GitHistoryReport:
  """One side (before or after) of the purge, as the in-container shell saw it.

  Attributes:
    base_sha: The commit ``HEAD`` pointed at.
    refs: Total refs.
    tags: Refs under ``refs/tags``.
    heads: Refs under ``refs/heads``.
    remote_refs: Refs under ``refs/remotes``.
    remotes: Configured remotes.
    reflog: Reflog entries.
    non_ancestor_commits: Reachable commits that are not ancestors of ``HEAD``.
      **Context, not a verdict** — a correct purge keeps past side-history, so
      this is legitimately non-zero (ansible: 9630, all past-dated).
    future_commits: Reachable commits whose committer date postdates the base.
      This is the load-bearing measurement: it should be ``0`` after a purge.
    base_reachable: Whether the base commit still exists (ADR-0001 needs it).
    solution_reachable: Whether the fix commit still exists; ``None`` when no
      solution sha was supplied.
  """

  base_sha: str
  refs: int
  tags: int
  heads: int
  remote_refs: int
  remotes: int
  reflog: int
  non_ancestor_commits: int
  future_commits: int
  base_reachable: bool
  solution_reachable: bool | None

  def to_dict(self) -> dict[str, object]:
    """Render for the JSON artifact — field order follows the declaration."""
    return asdict(self)

  @classmethod
  def from_json(cls, text: str) -> GitHistoryReport:
    """Parse the report script's stdout.

    Splats the object straight into the constructor, matching
    ``AttemptRecord.from_json``: the field list then lives **only** in the
    dataclass, so adding one never means editing a parser too. It is also
    stricter than hand-mapping — a missing or unexpected key is a ``TypeError``
    rather than a silently defaulted field. Types are trusted because the
    producer is :func:`build_report_script` — our own shell, not outside input.

    A key that is missing, or one nobody declared, therefore surfaces as the
    constructor's own ``TypeError`` — deliberately, since a report we cannot
    read is treated exactly like a contaminated repo by the caller.

    Args:
      text: The script's stdout — one JSON object, possibly with stray output
        around it (a git warning, say), so only the last JSON line is read.

    Returns:
      The parsed report.

    Raises:
      ValueError: If no JSON object is present, or the line does not parse.
    """
    line = next(
        (
            candidate
            for candidate in reversed(text.strip().splitlines())
            if candidate.startswith("{")
        ),
        None,
    )
    if line is None:
      raise ValueError(f"no JSON object in report output: {text!r}")
    return cls(**json.loads(line))

  def violations(self) -> tuple[str, ...]:
    """Return the assertion failures, empty when the repo is clean.

    The three postconditions of ADR-0010 §4, in the order they matter: the base
    must survive (extraction and grading depend on it), the known solution must
    be gone, and nothing reachable may postdate the base.

    Returns:
      One human-readable string per violated assertion.
    """
    failures: list[str] = []
    if not self.base_reachable:
      failures.append(
          f"base commit {self.base_sha or '<unknown>'} is unreachable"
          " (extraction and grading depend on it)"
      )
    if self.solution_reachable:
      failures.append("the solution commit is still reachable")
    if self.future_commits:
      failures.append(
          f"{self.future_commits} reachable commit(s) postdate the base commit"
      )
    return tuple(failures)
