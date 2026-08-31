---
name: swelab-python-review
description: Repo-level Python review increments for swe-lab — toolchain facts, where this repo's lint blind spots are declared, the `etils.epath` path rule, interface and record shapes, docstring pointers, subprocess hazards. Stacks on the user-level `python-review` (`~/.agents/skills/python-review`), which owns the general language judgment. Load both; findings go back to `pr-review` for grading.
---

# swe-lab Python review (repo increments)

## Loading

Depends on the user-level `python-review` at `~/.agents/skills/python-review`;
not loaded → **stop and report**, this file is only the repo delta.
`pr-review`'s code track mounts it after "1. Correctness"; findings are graded
by `pr-review` into its single comment — **no separate Verdict**. A P0 still
needs a failure scenario; LGTM still needs non-empty `## Evidence`. Item ids are
repo-prefixed `SLP*` so they never collide with the user-level `P*`;
append-only, never renumbered or reused, and independent of where the item sits
in this file. A supersede names the *user-level* id.

## Supersede

- `SLP1` **`supersede P21`**: paths use `etils.epath`, not `pathlib` — a
  parameter is `epath.PathLike`, a return / dataclass field / stored attribute
  is the concrete `epath.Path`, and a `PathLike` is coerced with
  `epath.Path(p)` before any path op or before being stored; known gaps
  (`.chmod()`, recursive `.rglob()`, `.stat().st_size`, `.cwd()` / `.home()`)
  reach for `os` / `pathlib` at that spot; Typer entry-point parameters stay
  `pathlib.Path` (Typer rejects a union). **Reason:** a cloud-URI-ready path
  API (`gs://`, `s3://`), see the Style section of `docs/conventions.md`. The
  rest of `P21` (`with` for files, locks, temp dirs, HTTP clients) stands.

## Increments

- `SLP2` **Toolchain facts** (a comment on the root `pyproject.toml`; where they
  disagree, the config wins): pyink formats at 80 columns, 2-space indent,
  majority quotes, `py313`, and ruff's formatter is off for `.py`; isort runs
  the black profile, so ruff `I` is off; ruff lints
  `B C D D401 E F ISC001 N W W505 RUF008 UP SIM`, mccabe max-complexity 10;
  pydoclint checks Google `Args:` against the signature with type checks off
  (basedpyright owns types); basedpyright runs its default mode over `src` +
  `tests` with a set of `report*` diagnostics switched off — read
  `[tool.basedpyright]` for the current set rather than any copy of it, and
  treat every switch it disables as a blind spot; `tests/` is exempt from `D1`
  and `reportUnusedCallResult`, and `experiments/` from every pre-commit hook
  (the file-level `exclude: ^experiments/` is global, so it applies to all of
  them); Python is pinned to 3.13.
- `SLP3` **Blind spots: point at the config, don't keep a list.** The
  authoritative enabled set is `[tool.ruff.lint] select` in the root
  `pyproject.toml`. `S`, `DTZ`, `PTH`, `T20`, `G`, `PT` and `RUF012` are **not**
  enabled, so user-level items carrying those numbers are review work here, not
  lint. Wanting one enforced is a separate PR against the config.
- `SLP4` **Interfaces and records.** A behavior interface with in-repo
  implementers is an `abc.ABC` with `@abstractmethod`, implemented as
  `class Impl(Base)` + `@override` (ADR-0002; `Verdict` per ADR-0006);
  `typing.Protocol` is only for a structural data shape with no shared
  derivation to own. A field-shaped class is a `@dataclass`, a record is
  `frozen=True`, a hand-written `__init__` needs a reason. Aliases use the
  PEP 695 `type` statement and generics the bracket form
  (`class Grader[V: Verdict](ABC)`), never `TypeVar`.
- `SLP5` **Docstrings are self-contained.** The only sanctioned doc pointer in
  code is a stable `ADR-NNNN`; a `task-NN §x.y` citation is a finding (those
  files move and renumber). Google style §2.2 (import modules, not symbols) is
  **waived** here — symbol imports are fine, don't report them.
- `SLP6` **Subprocess and agent-runner hazards** (violating one is P1): stream a
  subprocess's stdout to a file rather than `capture_output=True`, `killpg` the
  group on timeout, respect the `MAXJOBS=2` memory ceiling. Details are in
  `docs/conventions.md#hazards-learned-the-hard-way` — link, don't copy.
