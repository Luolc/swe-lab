# Task 01 — Google-style readability lift (full repo)

**Status: ✅ Complete (P0–P2 + gates; P3 leftovers below)** · Decided & executed
2026-07-18 · Preceded the SandboxRun plan.

Bring the whole repo up to the
[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html),
with docstrings as the center of gravity, and leave standing mechanical gates so
it never regresses. Research grounding: the guide itself (fetched 2026-07-18)
and a tooling survey (ruff 0.15.22 source, pydoclint 0.9.1, PyPI pages) — key
verified facts inline below.

## Starting point

The repo already stands on Google-style ground: pyink (Google's Black fork;
2-space = Google-internal style), 80 cols, isort with Google-ish settings,
strict-camelCase acronyms, module docstrings everywhere, `Error`-suffixed
exceptions, no mutable defaults. The measured gap:

- **0** `Args:` / `Returns:` / `Raises:` / `Attributes:` sections across all
  218 defs (house style is narrative prose + RST roles).
- Verbosity imbalance: 9-param `evaluate()` behind a one-line summary; trivial
  helpers behind long prose.
- No docstring/naming/complexity lint families enabled; nothing checks that
  `Args:` match signatures.

## Decisions (settled 2026-07-18, recorded in `docs/conventions.md`)

| Knob | Decision |
|---|---|
| Docstring mood | **Imperative** ("Fetch rows…"), repo-wide. Guide permits either with per-file consistency (§3.8.3); we fix it globally and enforce with D401. |
| §2.2 import-modules-not-symbols | **Waived entirely** (highest-churn rule, low value for this repo). |
| Indentation | 2-space stays (Google-internal style via pyink; public guide says 4 — deliberate deviation, already load-bearing). |
| Docstring section hanging indent | 2 spaces (match code indent). |
| Docstring prose width | 80 cols, enforced (W505 + `max-doc-length = 80`). |
| Quotes | pyink majority-quotes, unchanged. |
| Tests | exempt from docstring-presence rules (D1xx) per §3.8.2.1. |

> **Dropped rule — TODO format (§3.12).** The guide's current form
> (`# TODO: <issue-link> - <text>`) presumes an issue tracker; this repo does
> not use GitHub issues, so the rule is a no-op today (removed 2026-07-18).
> Whether to manage plans/bugs via issues is a future discussion — short-term
> no. Revisit this rule if issues are ever adopted.

## Toolchain (verified 2026-07-18)

- **ruff** (config-only): `select += ["D", "D401", "N", "C90", "W505"]` with
  `[tool.ruff.lint.pydocstyle] convention = "google"`. The google convention
  disables exactly D203/D204/D213/D215/D400/D401/D404/D406–D409/D413; we
  re-select **D401** explicitly (imperative mood is our repo-wide choice) —
  verify empirically that explicit select beats the convention's ignore.
  **D417** (params missing from an existing docstring) is on under google.
  `[tool.ruff.lint.mccabe] max-complexity = 10`;
  `[tool.ruff.lint.pycodestyle] max-doc-length = 80`;
  per-file-ignores: `"tests/**" = ["D1"]`.
- **pydoclint 0.9.1** (the one new tool, pre-commit hook `jsh9/pydoclint`):
  enforces *Args ↔ signature* and *Returns ↔ return annotation* consistency —
  the check ruff cannot do yet. Flags: `--style=google
  --arg-type-hints-in-docstring=False --check-return-types=False` (types belong
  to basedpyright; docstrings never repeat them). No baseline needed — verified
  clean on the pre-rewrite tree (it validates *existing* sections; "sections
  must exist at all" is D417's job, flipped on at the end of P2).
- **Rejected** (with reasons): pylint + Google pylintrc (redundant with
  ruff+basedpyright; second slow linter); docformatter / pyment /
  pydocstringformatter (none can synthesize Google sections — the conversion is
  semantic, i.e. Claude's job; the tools only gate it); interrogate (D1xx
  suffices; no need for a percentage ratchet at 5.3k LOC); ruff `DOC` family
  (preview-only and DOC101 unimplemented as of 0.15.22 — revisit when
  astral-sh/ruff#12434 lands, then pydoclint can likely be dropped).

## Phases

- **P0 — decisions** ✅ (table above; conventions.md updated in the gates PR).
- **P1 — mechanical gates, one PR** ✅: ruff config + pydoclint hook + the 18
  hand-fixes the new rules surfaced (13× D401 mood, 2× D301 raw-string, 2× W505
  rewrap, 1× D205). CI (pre-commit) enforces from then on. Temporary ignores
  only where P2 removes them (D102/D103/D107/D417 off until the rewrite lands).
- **P2 — docstring rewrite** ✅ (landed as one PR, not 6–8: the four module
  batches were rewritten and verified together on one tree, and CI runs
  `--all-files`, so slicing them into interdependent PRs added conflict risk
  with no review value; ~90 docstrings added/rewritten): every public API gets
  imperative summary + `Args:`/`Returns:`/`Raises:` where warranted (omit
  sections when the one-liner suffices — §3.8.3 explicitly allows it; that is
  the cure for verbosity imbalance, not more prose); classes get `Attributes:`;
  `@property` docstrings become noun phrases; keep the RST cross-ref roles.
  Suggested batch order (stable → refactor-adjacent):
  1. `core/datasets/` (incl. `swebench_pro/`) + `core/patch.py`
  2. `pipelines/related_files/` (schema, validator, aggregator, storage, …)
  3. `core/agent/` + `core/repo/` + `core/docker/`
  4. `evaluation/` + `rollout/` — **light touch only** (compliance, no deep
     polish): the SandboxRun refactor rewrites most of this glue (~15–20% of
     LOC); deep polish there is done as part of the migration itself.
  5. `paths.py`, `__main__`s, leftovers; drop the temporary D102/D103/D107/D417
     ignores.
- **P3 — judgment readability pass** (🔶 remaining, deliberately deferred):
  naming descriptiveness beyond N-rules; >~40-line functions (§3.18) — e.g.
  `validate_snippet_dict`; error-message precision (§3.10.2). These change
  code (not just docs), so they are done under review — the function splits
  most naturally during the SandboxRun migration, which rewrites the biggest
  offenders anyway. Comment style conformance happened alongside P2.
  **Do-not-touch:** the long `git diff`-related comments in `core/patch.py` are
  **deliberately verbose** (owner's explicit call, 2026-07-18 — they encode the
  ADR-0001 patch-extraction subtleties). The comment pass must not trim them.
- **P4 — standing guardrail (long-term)**: gates live in pre-commit/CI forever;
  the SandboxRun migration DoD gains "moved code lands Google-docstring'd";
  new code is born compliant.

## DoD

- `uv run pre-commit run --all-files` green **with** D+D401+D417, N, C90, W505,
  pydoclint enabled and **no temporary ignores left** in `pyproject.toml`.
- Docstring coverage: every public def/class passes D1xx; no `Args:` drift
  (pydoclint clean).
- `uv run pytest` green throughout; docstring-only PRs never change behavior.
- `docs/conventions.md` carries the style decisions; the 2026-07-18 audit doc's
  documentation section points here.

## Relation to SandboxRun

This task **precedes** `/plan` for the SandboxRun spec. Docstrings travel with
functions, so the refactor does not waste this work except in the glue files
listed under P2-4 — which get compliance-only treatment now and real polish
during migration.
