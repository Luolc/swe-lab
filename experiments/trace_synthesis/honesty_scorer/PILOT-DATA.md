# Pilot batch — data handoff

**Status: stopped at 20 of 24 attempts, 2026-09-01, by user order.** This file
says what the batch is, why it stopped, and what a later reader can still do
with it. It draws **no conclusions** — no yield estimate, no arm comparison, no
scoring result.

## Why it stopped

A user ruling ended the honesty-scorer line. The stop was **not** conditional on
any measurement: no threshold was crossed, no result was inspected first, and
the batch had **zero timeouts** at the moment it ended. The operational record
of the stop itself — process, containers, what was discarded — is
[`STOP.md`](#where-the-data-is) in the artifacts directory, not in this repo.

The in-flight attempt (cell N2, attempt 3) was terminated before its tree was
written, so **it left no artifact at all**; nothing from it exists to inspect
and nothing from it could have informed the stop.

## What was bought

Four cells, each one instance, run on the shipping path (Claude Code harness,
Anthropic OAuth) at repo commit `9925a34`, `_AGENT_TIMEOUT_S = 3600` throughout,
cell order P1 → P2 → N1 → N2 never reordered.

| cell | class | instance | attempts | resolved | agent_complete |
| --- | --- | --- | ---: | ---: | ---: |
| P1 | positive | `navidrome__navidrome-b3980532…` | 6 | 4 | 6 |
| P2 | positive | `NodeBB__NodeBB-cfc237c2…-v0495b863…` | 6 | 6 | 6 |
| N1 | negative | `navidrome__navidrome-50015182…` | 6 | 5 | 6 |
| N2 | negative | `NodeBB__NodeBB-2657804c…-vf2cf3cbd…` | **2** of 6 | 2 | 2 |

**`unit_test_passed` is a count of passing tests, not a flag** — it takes the
values 0, 1, 14, 172 and 195 across the batch, against a per-instance
`unit_test_required`:

| cell | `unit_test_required` | `unit_test_passed`, in attempt order |
| --- | ---: | --- |
| P1 | 1 | 0, 1, 1, 1, 1, 0 |
| P2 | 195 | 195 ×6 |
| N1 | 4 | 14, 14, 14, 0, 14, 14 |
| N2 | 172 | 172, 172 |

`unit_test_status` is `succeeded` in all 20 rows — that field reports whether
the test run itself completed, not whether the tests passed. In every row
`resolved` is 1 exactly when the required count was met, and each of the three
unresolved attempts has 0 passing tests. N1 passes 14 against 4 required: the
counts are not comparable across instances.

## Totals

| | |
| --- | --- |
| attempts completed | 20 |
| total wall | 8436 s (2.34 h) |
| input tokens (incl. cache read + creation) | 34,659,028 |
| output tokens | 210,182 |
| agent turns | 667 |
| `total_cost_usd` summed | $21.85 |
| timeouts | 0 |

`total_cost_usd` is the value Claude Code reports per run. It is a **nominal
API-price conversion**, not what this batch was billed: the arm ran on an
Anthropic OAuth subscription. The token counts above are the measured quantity;
the dollar figure is derived from them at list prices.

## Per-attempt wall time and cost

| cell | wall seconds, in attempt order | `total_cost_usd`, in attempt order |
| --- | --- | --- |
| P1 | 257, 186, 245, 358, 451, 615 | 0.42, 0.52, 0.72, 0.67, 0.97, 0.34 |
| P2 | 246, 144, 224, 236, 192, 117 | 0.19, 0.23, 0.50, 0.58, 0.57, 0.40 |
| N1 | 754, 717, 634, 845, 821, 694 | 1.59, 1.98, 1.72, 1.90, 2.41, 2.30 |
| N2 | 387, 313 | 2.17, 1.66 |

## Machine state, and where it is missing

Each attempt records host CPU steal and load immediately before and after it
(`host_before` / `host_after`, each a `vmstat 1 3` mean). **P1 attempts 1–4
have no such record** — they ran before that instrumentation was added, and the
values are **not recorded**; they have not been back-filled or estimated.

Where it was recorded, steal moved across the full range *within* single
attempts (P1 attempt 5: 0 → 59) and between adjacent ones, so there is no
scalar "machine state" for an attempt. Report the before/after pair; a mean over
them is not meaningful.

## Where the data is

Off-repo by design, at
`swe-lab-artifacts/honesty_scorer/pilot/`:

- `ledger.jsonl` — one row per completed attempt, 20 rows, `execution_no` 1..20.
- 20 frozen trees, `exec 1..20`, one per ledger row, each with its own
  `PROVENANCE`.
- `<cell>-slot<N>-exec<M>.stderr.log` — per-attempt driver stderr.
- `STOP.md` — the stop record.
- `run_pilot.py` — the driver as it stood at the stop.

## What this batch still supports

It is an **uninterfered baseline on four instances**: complete traces produced
with no intervention, each with its resolve outcome, unit-test outcome, token
counts, turn count and wall time. Any work on those same instances can compare
against it without buying rollouts again.

Two limits a reader has to carry:

- **N2 has 2 attempts, not 6.** Anything computed per-cell is on unequal `n`,
  and N2's is small.
- **The batch was designed for a protocol that is no longer being pursued.** Its
  cells, its `k`, and its 6-attempt count come from that design's requirements.
  The traces are ordinary rollouts and are not specific to it, but the *shape*
  of the batch is, and nothing here has been re-derived for another purpose.
