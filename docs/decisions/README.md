# Decisions

Architectural / design decisions for `swe-lab` that are worth recording so a
future session (human or agent) understands the *why*, not just the *what*.

## How we record decisions

- **A standalone, settled decision** gets its own numbered file:
  `NNNN-short-slug.md` (e.g. `0001-execution-on-github-actions.md`), following
  the lightweight ADR shape — **Context → Decision → Consequences**, plus a
  status line (`Proposed` / `Accepted` / `Superseded by NNNN`). See the
  `documentation-and-adrs` skill for the format.
- **A tightly-coupled *set* of decisions** on one topic may live in a single
  decision-log file (like `patch-extraction-decisions.md`) rather than being
  scattered across many ADRs. Split it into individual ADRs only once the set
  has stabilized and is trusted.

## Source-of-truth rule

A decision doc records intent at a point in time; it can drift from the code.
When a doc and the code disagree, **the code is authoritative** unless the doc is
explicitly marked as the spec being implemented. Docs that are known to have
drifted carry a **Provisional** banner at the top.

## Index

| Decision | Status | Notes |
| --- | --- | --- |
| [patch-extraction-decisions.md](patch-extraction-decisions.md) | ⚠️ Provisional | D1–D8 on patch extraction / diffing / grading. **Not source of truth — the code is.** Pending a joint re-review before it is trusted or split into ADRs. |
