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

- *What did the client send upstream?* — answered by the wire, and only by the
  wire. Not the same as *what the model saw*: two reminders reach the actor
  that appear in no client request body at all — a token-usage
  `<system_warning>` and a `PROMPT INJECTION WARNING` — so they are injected
  above the client→API wire and a proxy capture cannot see them
  ([`spec.md` §10](../trace-synthesis/spec.md),
  [`injection_shape/REPORT.md`](../../experiments/trace_synthesis/injection_shape/REPORT.md)).
- *What must the stored trace preserve, for a consumer that will decide its own
  training representation?* — answered by whether the information survives at
  all.

The owner ruled (2026-09-02, restated since) that preservation is the bar for
the second question: **as long as the information is stored, the downstream
consumer experiments with the SFT format.** Under that bar the two captures do
not rank; they differ, and the difference is recorded rather than resolved.

## Decision

1. **The wire remains the truth about what the client sent upstream.** A claim
   about the request bodies a run produced is answered by a proxy capture and by
   nothing else, and nothing here promotes a stream trace to that role. It is
   **not** a complete record of what the model saw — see the Context — and this
   ADR does not widen it into one.
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
- **Every stream run's stdin is stream-json, including an unsupervised one.**
  The flag is not accepted on its own: the pinned 2.1.212 exits 1 with
  *"--replay-user-messages requires both --input-format=stream-json and
  --output-format=stream-json"*. So a stream run passes `--input-format
  stream-json` and opens with its prompt encoded as one user event
  (`prompt.stream.json`), the same encoding the correction channel already
  used; the plain `prompt.txt` is still written beside it as the readable
  record. Nothing on this path feeds the CLI a plain-text stdin any more.
- **One difference from the measured arms survives, and it is not the input
  format.** `streamjson_input/driver.py` fed its arms through a **live pipe**
  that stayed open across turns; an unsupervised run here redirects from a
  **regular file** that reaches EOF after the single opening event — which is
  what ends the run, and is the mechanism the correction channel deliberately
  removes. Whether replay behaves identically against a finite file is
  untested; the first unsupervised run after this change settles it by reading
  its own trace for the task text.
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
