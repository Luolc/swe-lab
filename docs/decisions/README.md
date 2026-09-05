# Architecture Decision Records

Sequentially-numbered records of significant, hard-to-reverse decisions and the
alternatives rejected. Format follows the `documentation-and-adrs` skill:
**Status · Date · Context · Decision · Alternatives Considered · Consequences**.

- **Don't re-litigate an accepted ADR.** If a decision must change, write a new
  ADR that references and supersedes the old one; don't edit the old in place or
  delete it (it is the historical record).
- **Amend vs. supersede, ADR-first, in the same PR.** When a finding or the
  user's latest instruction conflicts with an accepted ADR, don't just change
  the code. In the **same change**: a **minor** delta → a dated
  `## Amendment (YYYY-MM-DD)` note inside the ADR; a **large** delta → a **new
  ADR that supersedes** it (mark the old one `Superseded by ADR-NNNN`). Then make
  the code edit. A reader must be able to trust an ADR without checking the code.
- **The code is the source of truth.** An ADR records the *why*; where an ADR and
  the code disagree, the code wins and the ADR should be amended or superseded to
  match.

## Index

| ADR | Decision | Status | Date |
| --- | --- | --- | --- |
| [0001](ADR-0001-patch-extraction-and-grading.md) | Patch extraction and grading — text-only diff vs `base_commit`, strict `git apply` matching Scale | Accepted (amended 2026-08-25: `patch_baseline`; default-off clause superseded by [0014](ADR-0014-the-pre-agent-baseline-is-the-default.md)) | 2026-07-17 |
| [0002](ADR-0002-interface-style-abc-vs-protocol.md) | Interface style — ABC/base class over Protocol (Protocol only for structural data shapes) | Accepted (superseded for `Verdict` by [0006](ADR-0006-verdict-is-an-abc.md)) | 2026-07-22 |
| [0003](ADR-0003-remote-sandbox-lifecycle.md) | Remote-sandbox support — up-first lifecycle + one lifecycle-bearing `Sandbox` (merges Backend/Sandbox; `Resource` → data; unified placements; amends the spec's host-FS core model) | Accepted | 2026-07-26 |
| [0004](ADR-0004-multi-rollout-run-record-layout.md) | Multi-rollout run-record layout — `rollout_id` + `attempt` key runs for pass@K; drop `run_ts`/`runs/` from the key; split `read_manifests` (bulk) vs targeted `read_manifest` | Accepted (key amended by [0007](ADR-0007-task-and-workflow-layer.md)) | 2026-07-29 |
| [0005](ADR-0005-flaky-eval-retry.md) | Retry a failed eval and mark the verdict flaky — the patch is identical across attempts, so it averages harness noise, not model error | Superseded by [0008](ADR-0008-retry-moves-to-the-task.md) | 2026-08-01 |
| [0006](ADR-0006-verdict-is-an-abc.md) | `Verdict` is an ABC, not a Protocol — it owns the `resolved` / `flaky` derivations (supersedes ADR-0002 for `Verdict` only) | Accepted (amended 2026-08-03) | 2026-08-02 |
| [0007](ADR-0007-task-and-workflow-layer.md) | A task layer above the sandbox manager, and workflows over it — one task = one sandbox; observers from sandbox / runner / task; the grader stays a declared output | Accepted, **partly superseded**: the §10 record-absence rule by [0009](ADR-0009-workflow-record-always-written.md) (amended 2026-08-03) | 2026-08-02 |
| [0008](ADR-0008-retry-moves-to-the-task.md) | Retrying a flaky eval moves to the task level — one fresh sandbox per attempt, every attempt persisted; the verdict stops carrying run history (supersedes [0005](ADR-0005-flaky-eval-retry.md)) | Accepted (amended 2026-08-07) | 2026-08-03 |
| [0009](ADR-0009-workflow-record-always-written.md) | The workflow record is always written — `succeeded` + per-entry status/metrics live *in* it, not in its absence; resume stays task-marker driven (supersedes the absence rule of [0007](ADR-0007-task-and-workflow-layer.md) §10) | Accepted | 2026-08-04 |
| [0010](ADR-0010-benchmark-integrity.md) | Benchmark integrity — controls live in the environment, never in the prompt; egress default-deny, future git history purged (past kept), verifier tampering detected not blocked; every control asserts and every record stamps its policy (amended 2026-08-06: history is P0, egress is configuration, verifier is a P1 post-rollout entry) | Accepted (amended 2026-08-06, 2026-09-01) | 2026-08-06 |
| [0011](ADR-0011-fair-retry.md) | Fair retry — retry only what is not the agent's own doing; `AgentOutcome` replaces the completion bit, the timeout veto is `@final` on `Task` and off by default, and the rollout predicate never reads the patch or the grade | Accepted | 2026-08-07 |
| [0012](ADR-0012-in-sandbox-capture-proxy.md) | The capture proxy runs inside the sandbox — declared as an asset, started and reaped by the invocation script on a fixed loopback port; no host listener, no firewall rule, no port allocation (amends [0010](ADR-0010-benchmark-integrity.md) §3a: the egress chokepoint moves to the sandbox's network) | Accepted | 2026-09-01 |
| [0013](ADR-0013-supervision-on-the-stdin-channel.md) | Supervision is delivered on the harness's own stdin channel (`claude -p --input-format stream-json`), not from a Claude Code hook — recorded with the fact that the pre-registered compliance gate returned `BELOW_BAR` and that the decision moved on an owner ruling rather than on a pass (amends [`trace-synthesis/spec.md`](../trace-synthesis/spec.md) §3, §5, §6, §12) | Accepted | 2026-09-01 |
| [0014](ADR-0014-the-pre-agent-baseline-is-the-default.md) | The pre-agent baseline is the **default** on both halves of the pair, and a stub agent that does nothing must produce an empty patch — measured: an image ships an untracked Redis AOF directory that `git add -N` folds into every patch, 166 KB from a run with no agent (supersedes the default-off clause of [0001](ADR-0001-patch-extraction-and-grading.md)'s 2026-08-25 amendment) | Accepted | 2026-09-01 |
| [0015](ADR-0015-four-words-for-how-a-rollout-ends.md) | Four words for how a rollout ends (`oom_killed` / `system_failed` / `timed_out` / `no_patch`, plus `patch_produced`) — classified by **cause, not exit code**; one causal bit gates grading *and* the denominator; the denominator **defaults to "in"** so only positively-identified system failures leave; every rate is reported with its excluded count | Accepted, **partly superseded**: the §1 taxonomy and §5 reporting rule by [0016](ADR-0016-the-endings-nobody-could-attribute.md) | 2026-09-01 |
| [0016](ADR-0016-the-endings-nobody-could-attribute.md) | The endings nobody could attribute get their own word and their own count — `unclassified` for an ending whose harness supplied no outcome (stays in the denominator, reported as a second count beside the excluded one); a missing diff extractor is `system_failed`, because the task always composes one, so its absence is broken wiring and ours (supersedes the §1 taxonomy and §5 reporting rule of [0015](ADR-0015-four-words-for-how-a-rollout-ends.md)) | Accepted | 2026-09-01 |
| [0017](ADR-0017-what-a-capture-is-evidence-of.md) | The wire is the truth about **evidence**; a stored trace may carry a representation the wire did not — so `capture="stream"` runs with `--replay-user-messages` unconditionally (it also recovers the run's own opening prompt, which the CLI echoes nowhere) and the correction channel no longer requires proxy capture (supersedes the *the wire is the truth* row of [`trace-synthesis/spec.md`](../trace-synthesis/spec.md) §10) | Accepted | 2026-09-03 |
| [0019](ADR-0019-complete-bounded-supervisor-evidence.md) | Python supervision selects complete assistant turns and renders bounded call/result pairs with visible truncation; selection, rendering, and prompt assembly are replaceable components | Accepted | 2026-09-04 |
