# ADR-0017: The wire is the truth about evidence; a stored trace may carry more

## Status

Accepted. Supersedes the *"the two captures disagree about mid-turn, and the
wire is the truth"* row of
[`trace-synthesis/spec.md`](../trace-synthesis/spec.md) §10 — specifically its
**Consequence** cell, which read *"This channel requires **proxy capture**"*.
That row is rewritten in the same change.

## Date

2026-09-03

## Context

v0.3.0 refused `ClaudeCodeHarness(correction_channel=True)` unless
`capture="proxy"`. The refusal rested on one measured disagreement between the
two captures, and on a reading of it.

**The measurement**, from
[`streamjson_input/REPORT.md` §7](../../experiments/trace_synthesis/streamjson_input/REPORT.md)
(N=1 per arm, one arm per cell of *(boundary, mid-turn) × (stream default,
stream + replay, proxy)*, one task and one model):

| capture | a mid-turn correction, after conversion |
| --- | --- |
| stream, default flags | **absent** — the trace does not contain it |
| stream + `--replay-user-messages` | present, as a **`user`** message |
| proxy | present, as a **`system`** message wrapping `<system-reminder>` |

The same report records a second measurement that the refusal did not weigh:
**by default a stream capture also loses the run's own opening prompt.** The
`control` arm's conversion yields six messages, none of them the task text,
because the CLI echoes no stdin; `--replay-user-messages` recovers it
([§7](../../experiments/trace_synthesis/streamjson_input/REPORT.md), consequence
1).

**The reading.** The refusal treated the `user`-versus-`system` divergence as
disqualifying, on the ground that a stream-derived trace would assert a turn the
model never saw. That conflates two questions a capture can be asked:

- *What did the model receive?* — answered by the wire, and only by the wire.
- *What must the stored trace preserve, for a consumer that will decide its own
  training representation?* — answered by whether the information survives at
  all.

The owner ruled (2026-09-02, restated since) that preservation is the bar for
the second question: **as long as the information is stored, the downstream
consumer experiments with the SFT format.** Under that bar the two captures do
not rank; they differ, and the difference is recorded rather than resolved.

## Decision

1. **The wire remains the truth about evidence.** A claim about what the model
   received is answered by a proxy capture and by nothing else. Nothing here
   promotes a stream trace to that role.
2. **A stored trace may carry a representation the wire does not.** The two
   answer different questions, so a trace is not false for holding a form the
   wire did not carry — it is false only if it loses information or misreports
   what it holds.
3. **`capture="stream"` runs with `--replay-user-messages`, unconditionally.**
   Not a mode and not an option: the flag repairs a defect every stream run has
   — a trace containing what the agent answered and not what it was asked —
   and a switch to turn it off would only preserve that defect.
4. **The construction-time refusal is removed.** `correction_channel` is valid
   with either capture.

**The shape divergence is a fact this ADR states rather than resolves.** A
mid-turn correction is a `user` message in a stream-derived trace and a
`system` message wrapping `<system-reminder>` in a proxy-derived one. Both are
faithful to their own capture. A consumer that pools traces from both must key
on which capture produced each; this ADR makes no recommendation between them.

## Alternatives Considered

- **A third capture mode (`stream_replay`), leaving `stream` unchanged.**
  Rejected: the flag fixes a defect that belongs to every stream run, not to
  supervised ones. Behind a mode, the default stays the variant whose traces
  omit their own prompt.
- **Keep the refusal and require proxy capture for supervision.** Rejected by
  the owner ruling above. It answers the evidence question with a rule about
  storage, and it forces a capture proxy into deployments that need one for no
  other reason.

## Consequences

- **Every `stream` run's output changes.** This is a behavior change, not an
  addition: unsupervised traces that previously began without the task text now
  carry it. Anything counting messages or indexing into a converted stream sees
  different values. It needs a release note when the next version is cut.
- **The measurement's domain is narrower than the change.** Every arm that
  exercised `--replay-user-messages` also ran `--input-format stream-json`
  (`streamjson_input/driver.py`, which hardcodes it for all arms). An
  unsupervised run in this harness feeds a plain prompt file on stdin instead,
  so the recovery of the opening prompt is measured in the stream-json-input
  configuration and not in that one. Stated rather than assumed away; the first
  unsupervised run after this change settles it by reading its own trace.
- **`spec.md` §10's row is rewritten in this change**, as the Status says.
- **[Task 16](../trace-synthesis/plans/task-16-live-correction-channel-in-the-harness.md)
  §5 still lists "this channel requires proxy capture" among the constraints it
  calls *already measured, not negotiable*.** That sentence is outdated by this
  ADR and is not corrected here; it is named so the next reader of that plan
  does not take it as current.
- **Supervision no longer implies a second asset.** A supervised stream run
  transfers only the agent binary, starts no proxy, and sets no
  `ANTHROPIC_BASE_URL`; its single event stream is both the trace and the
  supervisor's live view.
