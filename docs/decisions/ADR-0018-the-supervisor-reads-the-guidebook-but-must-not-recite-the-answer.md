# ADR-0018: The supervisor reads the guidebook but must not recite the answer

## Status

Accepted. The owner decided the direction on 2026-09-03; the implementation
and the trace-synthesis spec reconciliation landed together.

This decision supersedes the information-barrier argument in the module
docstring of `src/swe_lab/trace_synthesis/supervisor.py` and in the
`Observation` docstring. Their useful warning survives as the speech boundary
below: privileged knowledge is a real leakage risk, but excluding the guidebook
also removes the instance-specific basis for teaching.

## Date

2026-09-03

## Context

### The intended pipeline and the shipped component disagree

The trace-synthesis pipeline has three relevant phases:

- Phase A obtains a real failed rollout on a solvable task
  ([`spec.md` lines 99–133](../trace-synthesis/spec.md#L99-L133)).
- Phase B gives an Oracle the failed conversation, grading material, the
  reference patch when one exists, and unpurged history. The Oracle writes a
  staged guidebook whose fields include `Justification`: how a blind actor could
  derive the step from the task statement and earlier stages
  ([`spec.md` lines 135–181](../trace-synthesis/spec.md#L135-L181)).
- Phase C is described as a supervisor consuming the actor's observations
  together with the guidebook and a host-side belief state
  ([`spec.md` lines 188–217](../trace-synthesis/spec.md#L188-L217)).

The pipeline diagram makes the same connection explicitly: `guidebook.md`
flows to the supervisor, while the blind actor sees only a tagged directional
hint ([`spec.md` lines 67–97](../trace-synthesis/spec.md#L67-L97)). The
guidebook schema explains why `Justification` exists: without a derivable reason
the supervisor has nothing honest to say
([`guidebook.py` lines 1–9](../../src/swe_lab/trace_synthesis/guidebook.py#L1-L9)).

Phase B is implemented. `OracleAnalysisTask` receives the failed artifacts and
privileged grading material, writes `guidebook.md`, and validates the guidebook
shape. The prompt requires each stage's justification to be derivable from the
task statement, the base repository, and preceding stages
([`oracle.py` lines 1–18](../../src/swe_lab/trace_synthesis/oracle.py#L1-L18),
[`oracle.py` lines 246–259](../../src/swe_lab/trace_synthesis/oracle.py#L246-L259)).

At the time of this decision, the shipped supervisor deliberately severed that
connection. `Observation` contained only `task`, `evidence`, `cursor`, and
`said`; its docstring said the
absence of a phase-B guidebook prevents the supervisor from steering by an
answer it never quotes
([`supervisor.py` lines 191–223](../../src/swe_lab/trace_synthesis/supervisor.py#L191-L223)).
The module docstring gave the same rationale and substituted one pinned,
instance-independent general-practice criterion
([`supervisor.py` lines 8–21](../../src/swe_lab/trace_synthesis/supervisor.py#L8-L21)).
`test_supervisor_input_carries_no_privileged_field` pinned the four-field shape
with an exact allowlist
([`test_supervisor_component.py` lines 29–42](../../tests/test_supervisor_component.py#L29-L42),
[`test_supervisor_component.py` lines 150–159](../../tests/test_supervisor_component.py#L150-L159)).

The result is not a partial implementation of Phase B → C. It is a different
information boundary: the artifact whose `Justification` field exists to make
honest supervision possible cannot reach either the judge or the writer.

### The old argument identified a real risk but put the boundary in the wrong place

The old argument is correct about the risk. The Oracle has seen privileged
material. A supervisor can leak the answer without copying the gold patch: it
can paraphrase a decisive edit or test expectation into a seemingly harmless
instruction. Merely omitting literal patch text does not make a correction
honest.

The argument does not apply to the owner's intended teaching relationship. A
teacher may know the answer and still constrain what they say: point to the
student's current action, question it, offer a way to think, or identify a
direction to inspect. Preventing the teacher from knowing the lesson removes
the basis for instance-specific judgement as well as the answer.

The information barrier therefore still excludes the raw gold patch, reference
patch, test patch, hidden tests, and equivalent privileged artifacts as
independent supervisor inputs. The guidebook is the single reviewed derivative
allowed through. The answer-leakage boundary moves to the writer's speech act.

### Existing evidence is suggestive, not decisive

The general-practice criterion is deliberately identical across instances. It
recognizes generic failures such as editing before reading an error, guessing
about unread code, widening the search, repeating a failed step, changing a
valid test, or declaring success without evidence
([`general-practice.md` lines 1–31](../../src/swe_lab/trace_synthesis/criteria/general-practice.md#L1-L31)).
It cannot identify the instance-specific route that the Phase-B guidebook was
written to preserve.

In the first end-to-end supervised run, that criterion was consulted at 170
boundaries and produced three corrections. All three corrections were false of
the actor by the time they arrived; the report separates stale-prefix failures
from the first judgement, which had no admitted evidence
([`pipeline_end_to_end/REPORT.md` lines 50–58](../../experiments/trace_synthesis/pipeline_end_to_end/REPORT.md#L50-L58),
[`pipeline_end_to_end/REPORT.md` lines 117–175](../../experiments/trace_synthesis/pipeline_end_to_end/REPORT.md#L117-L175)).
This shows that the current criterion can be both sparse and wrong on one run.
It does not identify why it was sparse.

A separate offline experiment gave one guidebook, the actor's recent steps, and
no Oracle to a judge. On two traces, 20 of 67 parsed steps were adjudicable and
four were called off track; two of those four caught the exact trap the
guidebook was written to prevent
([`guidebook_as_step_criterion/REPORT.md` lines 37–76](../../experiments/trace_synthesis/process_supervision/guidebook_as_step_criterion/REPORT.md#L37-L76),
[`guidebook_as_step_criterion/REPORT.md` lines 78–88](../../experiments/trace_synthesis/process_supervision/guidebook_as_step_criterion/REPORT.md#L78-L88)).
The report explicitly withholds pass/fail because no threshold was registered
and the trace-level sample is two
([`guidebook_as_step_criterion/REPORT.md` lines 7–35](../../experiments/trace_synthesis/process_supervision/guidebook_as_step_criterion/REPORT.md#L7-L35)).

Together these observations make the hypothesis plausible: an
instance-independent criterion may leave the judge with too little basis to
speak. They do not establish that it is the main cause. The runs differ in
their input, judge prompt, unit of judgement, and execution path; they are not
arms of one comparison.

## Decision

### The complete guidebook is visible to the supervisor

For a guidebook-guided supervised run, the complete validated phase-B
`guidebook.md` enters the supervisor's visible input. The judge and writer may
both read all of it, including `Edits` and `Tests`. Their equal visibility is an
implementation choice, not a security boundary.

The guidebook is not copied into the actor's context or persisted as part of the
training conversation. Only a correction the writer emits may cross that
boundary.

The guidebook complements rather than replaces the general-practice criterion:

- the criterion remains the shared standard for generic engineering conduct;
- the guidebook supplies the instance-specific route and the derivable reasons
  for it;
- the criterion's pinned digest remains useful for artifact identity, but it is
  no longer described as the complete barrier against instance-specific
  solution knowledge.

No field-level projection of the guidebook is introduced. In particular, the
implementation will not parse out `Justification` and hide `Edits` or `Tests`
from either model call. Such a projection would contradict the chosen boundary
and add a second guidebook representation that can drift from the artifact.

### The boundary is on what the writer says

The intended correction is a teacher's nudge:

- it may identify what the actor is doing and where;
- it may express doubt about that direction;
- it may suggest how to reason about the observation;
- it may point toward a file, subsystem, concept, or experiment to inspect.

It must not directly supply the implementation: no line to change, replacement
code, exact test expectation, or equivalent solution instruction.

The guidebook fields give this speech constraint a source discipline. The
writer uses `Justification` as the primary source for why a nudge can honestly
be said. `Goal`, `Actions`, and `Expected observations` can locate the current
stage and a productive direction. `Edits` and `Tests` may inform the writer's
private understanding but are not material to relay to the actor.

This is an intended prose property, not an enforced invariant. A writer can
paraphrase an answer, and no local predicate can decide whether a short natural
language hint has crossed the line from teaching into solving. The same limit
already applies to whether an Oracle's prose genuinely contains a derivable
justification: schema validation proves presence, not truth
([`guidebook.py` lines 41–63](../../src/swe_lab/trace_synthesis/guidebook.py#L41-L63)).

### Mechanical checks provide a floor, not proof of non-leakage

The implementation will enforce only the shallow properties it can name and
test:

1. Keep the existing non-empty and 400-character limit on an intervention
   ([`supervisor.py` lines 45–49](../../src/swe_lab/trace_synthesis/supervisor.py#L45-L49),
   [`supervisor.py` lines 140–172](../../src/swe_lab/trace_synthesis/supervisor.py#L140-L172)).
2. Reject fenced code blocks and diff hunk headers in writer output. This blocks
   two literal forms of handing over implementation text; it does not block
   inline code, file names, prose instructions, or paraphrases.
3. Reject a writer output that shares any consecutive eight-word shingle with
   the complete guidebook. This reuses the established overlap shape in
   [`criterion.py` lines 64–69 and 120–133](../../src/swe_lab/trace_synthesis/criterion.py#L64-L69).
   Comparing with the complete guidebook catches copying from `Edits`, `Tests`,
   or any other section without creating a second parser. It also rejects a
   verbatim copy from `Justification`; the writer may derive its reason from
   that field but must formulate the nudge for the actor's present state.

These checks detect length, two answer-like containers, and verbatim copying.
They do not detect a short leaked constant, a decisive identifier, a renamed
test expectation, or a semantic paraphrase of the solution. Passing them must
never be reported as “the writer did not reveal the answer.” That judgement
comes from human review of sampled intervention records and their inputs.

### The constructor barrier remains exact and gains a negative control

`Observation` gains exactly one field, `guidebook`, carrying the validated
Markdown. Its field allowlist becomes:

```text
task, evidence, cursor, said, guidebook
```

The existing exact-field test remains necessary: any additional dataclass field
turns it red. It is not sufficient by itself, because a constructor that accepts
and discards arbitrary keyword arguments could keep the same dataclass field set.

The revised test therefore has both arms:

- positive arm: an `Observation` carrying a guidebook constructs successfully,
  and the exact text reaches both the judge and writer prompts;
- negative control: attempts to construct the same observation with each of
  `gold_patch`, `reference_patch`, `test_patch`, `hidden_tests`,
  `fail_to_pass`, `pass_to_pass`, and `fix_commit` are rejected at the
  constructor boundary.

The second arm is the discriminant. A constructor that allows only the
guidebook and one that accepts arbitrary privileged material both pass the
positive arm; only the latter passes the negative control and fails the test.

This field barrier proves only that the supervisor interface has no separate
channel for those artifacts. It cannot prove that the guidebook contains no
derived solution knowledge; allowing that reviewed derivative is the decision
this ADR records.

## Alternatives Considered

### Keep the guidebook out and retain the general criterion alone

Rejected. It makes the current information barrier strong by deleting the
Phase-B → Phase-C handoff the pipeline was designed around. It also leaves the
`Justification` field without its stated consumer. The existing run demonstrates
that the general criterion can produce sparse, mistimed false corrections, but
does not by itself prove guidebook access will improve them.

### Give the judge the guidebook and keep it from the writer

Rejected as the architectural boundary. A writer that knows only a generic
criterion still lacks the derivable reason that makes an instance-specific
nudge honest. Separating their views also creates another transformation whose
fidelity would need to be defined and tested. A later implementation may use
separate prompts, but both calls remain allowed to see the full artifact.

### Project the guidebook to `Justification` and hide `Edits` / `Tests`

Rejected. This treats an input projection as the safety property after the owner
placed the boundary on speech. It also loses context the judge may need to know
whether the actor has deviated, while not preventing leakage: a justification
can itself identify the decisive route, and a model can infer edits from it.

### Give the actor the guidebook directly

Rejected. It replaces supervision with instruction following and puts the
Oracle's complete privileged derivative into the training context. This ADR
allows the guidebook into the supervisor, not into the actor or trace.

### Claim a semantic leak detector

Rejected. Code-block, diff-header, length, and shingle checks have useful but
narrow failure domains. Labeling them a non-leakage detector would suppress the
human audit while leaving paraphrased answers untouched.

## Consequences

### Trace semantics

The guidebook remains host-side supervisor input. A trace changes only when the
guidebook changes whether the supervisor speaks or what correction it emits.
The emitted correction remains visible conditioning in the actor's conversation
and is preserved unedited in the collected trace.

The two trace-admission criteria in spec §6 are unchanged:

- **(a)** no SFT loss is taken on tokens the actor did not generate; the
  correction is conditioning, not an assistant target;
- **(b)** the correction still uses the stdin delivery shape already shown to
  occur when an interactive user sends a message during inference.

Those criteria decide whether the trace representation is admissible. They do
not decide whether a correction teaches reasoning rather than hands over the
solution. An overly specific hint can pass both criteria and still make a poor
training example. This ADR adds no third mechanical admissibility criterion;
specificity remains a sampled human judgement.

### Implementation

1. The supervisor input carries the validated guidebook, and the
   exact allowlist test with the positive and negative-control arms above.
2. The phase-B artifact flows into construction of a guidebook-guided phase-C
   supervisor. A missing or structurally invalid guidebook is refused before the
   actor starts and does not silently fall back to the general criterion.
3. The complete guidebook is included in the shared judge/writer prompt beside
   the general-practice criterion. Behavioural tests drive both calls with the
   exact artifact.
4. Writer output is rejected when it is empty, over 400 characters, contains a
   fenced code block or diff hunk header, or shares an eight-word shingle with
   the complete guidebook. Independent rejection arms and acceptance controls
   cover each shallow check, including a short directional hint and an inline
   file reference.
5. Existing `supervisor.jsonl` decision rows carry the guidebook SHA-256, exact
   credential-free judge request, judge reason, and emitted text. This extends
   an existing declared native artifact rather than changing the persisted
   report contract. A source-aware 12-word-shingle test subtracts text also
   present in the task prompt before identifying guidebook-only text in the
   actor conversation.
6. The ordinary non-Docker quality bar covers the implementation. Rollouts and
   paid comparisons remain separate experimental work.

### Spec reconciliation in the accepting PR

The accepting PR updates these exact locations:

- **§3, pipeline diagram and Phase B:** retain that the guidebook is private
  from the actor and trace, and make explicit that it is visible in full to the
  supervisor. Remove wording that can be read as private from Phase C.
- **§3, Phase C:** make the full guidebook handoff normative and state that the
  information boundary excludes raw privileged artifacts while the speech
  boundary governs corrections.
- **§5, mechanism decisions:** preserve the channel decision and distinguish
  “direction only, never specifics” as an intended, human-audited speech
  property with the shallow checks listed here. This ADR alone does not decide
  whether guidebook-guided injection becomes the production default; reconcile
  the existing terminated-arm note only after that separate product choice is
  made.
- **§6 and §8:** state that (a)/(b) are unchanged and do not measure hint
  specificity or teaching value; connect the existing specificity trade-off to
  the writer's guidebook use.
- **§12:** replace “the guidebook is the only thing it learns” as an unexplained
  exception with the exact five-field allowlist and its negative control; retain
  the invariant that the guidebook never enters the actor's context or training
  trace. Do not add “the writer never reveals the answer” as an invariant.
- **§13:** show the phase-B artifact as a declared phase-C supervisor input.
- **§15 and §16:** re-check whether restoring guidebook-guided steering changes
  the current success criteria or out-of-scope boundary. Do not change them
  merely because this ADR makes the interface possible; the product-default
  question below remains open.

The task-05 plan and the module docstrings must also be reconciled when the code
changes, but they are not substitutes for the spec edits above.

## Open Questions

### Is the general criterion the main reason the judge rarely speaks?

Unknown. The current evidence supports plausibility, not attribution. The
guidebook experiment shows that a guidebook can yield reviewable,
instance-specific judgements; the end-to-end run shows that the general
criterion can be sparse and wrong. They are not a controlled comparison.

The minimum discriminating evidence is a replay over the same recorded actor
prefixes with the same judge model, sampling parameters, prompt shape, evidence
window, and decision policy, varying only whether the full guidebook is present.
Report judgement, would-have-spoken, and usable-answer counts for both arms. A
later live paired run is needed to answer the more valuable question: whether
guidebook-informed speech improves actor outcomes or trace quality.

A recently reported segmented run had four decision opportunities and no
speech, but this checkout contains no committed report or witness for that run.
It is therefore not evidence this ADR can independently cite. Downstream
feedback after the end-to-end workflow ships may supply the missing record and
should be read before prioritizing the comparison.

### Does guidebook-guided speech become a production default?

Not decided here. The current spec says injection is a terminated experiment
arm and the production default is an uninterfered rollout with post-hoc
guidebook grading. This ADR defines the information and speech boundary for a
guidebook-guided supervisor when such a run is constructed; it does not by
itself authorize paid runs or reverse the product default. That choice needs the
downstream evidence described above and, if changed, a spec reconciliation that
names the affected success criteria.

### What sampling evidence is enough to trust the speech constraint?

Unknown. The shallow checks do not answer it. Before a guidebook-guided arm is
used to produce training data, an experiment must pre-register the sample unit,
the reviewer question, and what constitutes “directional” versus “solution
instruction.” The result should report examples at the boundary and the rate of
human-rejected corrections; no threshold is selected in this ADR.

### What persisted audit shape is compatible with the report contract?

The supervisor log already records emitted text, but auditing the new boundary
also needs the guidebook identity and the input/reason behind the decision. The
exact record shape is deferred because changing the report contract is an
ask-first boundary. Implementation must not smuggle that schema change into the
guidebook wiring; either reuse existing extensible fields or obtain an explicit
decision before changing the contract.
