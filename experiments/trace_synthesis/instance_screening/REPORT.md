# Instance screening — REPORT

| Field | Value |
| --- | --- |
| Author | swelab-screen-impl (Claude Opus 5) |
| Reviewer | swelab-screen-review (Codex) |
| Task | screen all 40 SWE-bench Pro candidates in [issue #261](https://github.com/Luolc/swe-lab/issues/261) for task quality |
| Corpus | SWE-bench Pro, public test split, 731 rows |
| Method | pure data analysis — parquet + repository source at `base_commit`. **No container was started.** |
| Started | 2026-09-01 04:05 PDT |

## Contents

- [Conclusions](#conclusions)
- [The criterion](#the-criterion)
- [The five mechanical screens](#the-five-mechanical-screens)
- [The `interface` field pins the wrong symbol](#the-interface-field-pins-the-wrong-symbol)
- [Calibration: four instrument defects, found and fixed](#calibration-four-instrument-defects-found-and-fixed)
- [A correction: 54% was wrong](#a-correction-54-was-wrong)
- [Screen overlap — the number this was run to get](#screen-overlap--the-number-this-was-run-to-get)
- [The control: a random 40 from the same corpus](#the-control-a-random-40-from-the-same-corpus)
- [Runnability comes before task quality](#runnability-comes-before-task-quality)
- [Per-instance verdicts](#per-instance-verdicts)
- [What to hand the trace-synthesis line](#what-to-hand-the-trace-synthesis-line)
- [Defined, not executed: the blind re-judgement](#defined-not-executed-the-blind-re-judgement)
- [Open questions](#open-questions)

## Conclusions

1. **16 of the 40 are broken, 23 are usable, 1 is unresolved** — and of the 16,
   exactly one (`element-web-aec454dd`) rests on an argument this report calls
   weak. Per-instance
   evidence is in [the table below](#per-instance-verdicts) and machine-readable
   in [`candidates.json`](candidates.json). 40% is higher than the ~30% OpenAI
   reports for the full 731, and
   [the numbers are not comparable](#the-control-a-random-40-from-the-same-corpus)
   — different definitions, and this is not a random sample.
2. **One disease dominates, and it has a name: the compile gate.** Thirteen of
   the sixteen are *overly strict tests* (the other three are *underspecified
   prompts*, and one is a *misleading prompt*), and the sharpest sub-species is a graded
   test that only compiles against a **signature the prompt never states**. A
   solver who implements every sentence of the task and keeps the existing
   signature scores zero, with no partial credit. Three instances turn on
   nothing else.
3. **The `interface` field — the corpus's own anti-false-negative mechanism —
   is applied to the wrong symbol in 3 of 40 instances (7.5%).** It has its own
   section, [below](#the-interface-field-pins-the-wrong-symbol); the survey
   confirms no published detector looks at this field pair at all.
4. **A screen's hit rate is not a broken-task rate, and a miss costs far more
   than a false alarm.** Reading the first pass's 22 alarms found *instrument*
   defects, not broken tasks — and applying the cost asymmetry
   ([calibration](#calibration-four-instrument-defects-found-and-fixed))
   rejected one of the three fixes planned, because it would have silenced a
   true positive to buy tidier output.
   Anything built on top of an uncalibrated alarm rate measures noise — which is
   also why [the control](#the-control-a-random-40-from-the-same-corpus) could
   not answer the question it was run to answer.
5. **The screens are complementary and the overlap is real but partial.**
   [Numbers here](#screen-overlap--the-number-this-was-run-to-get). No screen's
   hit set contains another's, and the two verdicts that were contested between
   the two task pairs were both decided by screen 4 alone.
6. **Screen 5 is a different kind of instrument from the other four,** and the
   report is explicit about it so the five are not mistaken for one tool:
   screens 1–4 output a *suspicion* a human resolves, while a committed DOM
   snapshot is unsatisfiable-by-construction and so condemns on its own.
7. **Image runnability is a harder gate than task quality, and it is
   orthogonal.** All five `protonmail/webclients` instances are un-runnable on
   this box — the agent binary cannot execute in that image — regardless of how
   good the tasks are. Two of them are otherwise clean candidates.

## The criterion

**Determinacy.** Is the behavior the hidden tests judge *uniquely* pinned down by
what the solver holds — `problem_statement` + `requirements` + `interface` + the
repository at `base_commit`? A task that is not pinned down is broken whether or
not some rollout guessed right, which is why issue #261's own selection
criterion (mixed outcome) screens nothing: "one of two rollouts resolved" is
true of all 40 by construction.

Mapped onto OpenAI's four categories from
[*Separating signal from noise in coding evaluations*](../../../docs/research/swebench-pro-task-quality.md)
(2026-07-08): *overly strict tests*, *underspecified prompts*, *low-coverage
tests*, *misleading prompt*.

Sharpened, during this round, into one binary sub-criterion:

> **The compile gate.** If the graded test only compiles — or only imports —
> when a symbol's **name, parameter count, parameter types, or return arity**
> differs from `base_commit`, and no prompt field states that change, the task
> is not determinate.

Two properties make this worth naming separately. It is **binary**: there is no
partial credit for a functionally perfect implementation that keeps the existing
signature. And it is **checkable from two lines of quoted source** — the
definition at `base_commit` and the definition in the gold patch — so it has no
interpretive slack.

It is therefore **not the same measurement** as OpenAI's *overly strict tests*,
which is a human read by a panel of engineers. A human reviewer looking at
`key []byte` → `key string` may well think "that's a natural refactor" and pass
it; a compiler does not. We are not applying a stricter standard so much as a
sharper instrument to one specific disease — and the two counts must not be
mixed, exactly as
[the survey already separates](../../../docs/research/swebench-pro-task-quality.md)
OpenAI's 30%, kimjune01's 15% and DeepSWE's 32.4%.

**Reading `git log` does not count as deriving.** Two independent reasons.
The graded tests are not in the working tree during a rollout — they arrive via
`before_repo_set_cmd` at grading time, demonstrated directly in the
[handmade-instance report](../handmade_instance/REPORT.md#why-the-agent-could-not-see-it).
And this repository runs a
[benchmark-integrity audit](../../../docs/decisions/ADR-0010-benchmark-integrity.md)
that classifies reading future commits as cheating; we cannot treat as
*derivable* the same act we flag as cheating.

## The five mechanical screens

[`screens.py`](screens.py), run over all 40. Screens 1–4 are **alarms that route
an instance to manual review**, never verdicts; screen 5 is different and is
described separately below. They catch five different diseases:

| # | Screen | What it catches | Precondition it reports |
| --- | --- | --- | --- |
| 1 | **unpinned token** | the tests demand a literal — a string, a number, an identifier — that the prompt never gave and the gold patch introduces | — |
| 2 | **requirements/interface diagonal** | the two prose fields **contradict** each other about the same unit (`The method` vs `Type: Function`) | `diagonal_units_compared`: zero means the fields never described the same unit, which is *no signal*, not *no conflict* |
| 3 | **symbol coverage** | the `interface` field **does not declare** a symbol the graded tests exercise and the gold patch defines | `interface_declares_names` |
| 4 | **signature change** | a symbol that exists at `base_commit` is **redefined with a different signature** and the interface field does not declare it | the symbol must be found at `base_commit` |
| 5 | **graded snapshot** | the graded tests assert against a **committed DOM snapshot** the test patch brings with it | see below — three conditions, all required |

**Screen 5 is not a heuristic, and must not be filed with the others.** A
committed DOM snapshot fixes every class name and every nesting level of the
rendered output. No prose prompt can pin that, so the task is *overly strict
tests* **by construction** rather than by suspicion — the argument is deductive,
and the screen condemns on its own instead of routing to a human. Because that
is a strong claim, it fires only when all three of these hold, which keeps a
legitimate regression guard out of it:

1. the test patch's added lines contain a snapshot assertion;
2. the test patch **adds or modifies** the matching `__snapshots__/*.snap`; and
3. the test file paired with that snapshot appears in `fail_to_pass`.

A snapshot file that already exists at `base_commit`, untouched by the test
patch, whose test sits in `pass_to_pass`, is a "don't break the DOM" guard — a
perfectly good test, and this screen leaves it alone.

**Condition 3 earned its keep immediately.** Three of the 40 instances contain
`toMatchSnapshot` and a committed `.snap`; only **two** survive the condition.
`element-web-aec454dd`'s snapshot is `Pill-test.tsx.snap`, and no `fail_to_pass`
entry names `Pill-test.tsx` — the graded tests there are `LruCache-test.ts` and
`UserProfilesStore-test.ts`. That snapshot is collateral from the same upstream
PR, not the grading mechanism, and condemning on it would have been wrong. The
instance is still judged broken, but on entirely separate and much weaker
grounds ([see its row](#per-instance-verdicts)), and the report says so rather
than borrowing the deductive argument it is not entitled to.

Screen 2 is, per the survey, without any published equivalent — the
`requirements`/`interface` pair is specific to SWE-bench Pro and the detectors in
the literature were built for original SWE-bench. Screens 3 and 4 were added
during this round, each in response to a concrete miss.

**Screens 2 and 4 have preconditions, and report them.** `teleport-b4e7cd3a`
first read *clean* on the diagonal, because the diagonal is only meaningful when
`requirements` and `interface` are talking about the same units — and there the
whole defect was that they are not. "No signal" reported as "no evidence" is how
that instance was first mis-judged, by the other task pair and nearly by this
one.

**Naming a symbol is not declaring its signature.** Screen 4 suppresses an alarm
only when the **`interface` field** declares the symbol (a `Name:` row, an
inline `Function:`-style row, or a backticked code span). Extending that to
`requirements` was tried and rejected on evidence: `teleport-b4e7cd3a`'s
requirements mention `buildKeyLabel` twice and still never state its parameter
type, so crediting a bare mention would silence the screen's motivating case.
The consequence is a known benign-alarm class — `webclients-ac23d1ef` and
`teleport-2b15263e` both have their new signature written out in
`requirements` rather than in `interface` — which is what manual review is for.

## The `interface` field pins the wrong symbol

`interface` is the field Scale added to SWE-bench Pro specifically to stop a
correct implementation from failing on a naming mismatch. In **3 of these 40
instances (7.5%)** it is filled in for one symbol while the grading happens on
another, so the anti-false-negative mechanism protects a function nobody
grades:

| instance | the interface field declares | the graded tests grade | in the prompt? |
| --- | --- | --- | --- |
| `teleport-b4e7cd3a` | `MaskKeyName` (path, input `string`, output `[]byte`, and the masking rule) | `buildKeyLabel`, via `TestBuildKeyLabel` | named in `requirements`, but its parameter type — the only thing the test actually forces — never appears |
| `teleport-ba6c4a13` | `SetOnHeartbeat` — which the graded tests never call | `processState.getState()` and a `componentStateEnum`, both newly added and unexported | not by any name, in any field |
| `webclients-6e165e10` | `getRegularRenewalNoticeText` | `getCheckoutRenewNoticeText`, imported by every graded test | not by any name, in any field |

All three share one shape: the field is *internally complete and precise about
the symbol it describes* — which is why a reviewer checking "is the interface
field well-formed?" passes them. The question that catches them is a different
one: **is the symbol it describes the symbol the graded tests evaluate?**

That is the question [screen 3](#the-five-mechanical-screens) asks, and it is
the reason screen 3 exists. Screens 1 and 2 are structurally blind to it:
`string` and `[]byte` both appear in `teleport-b4e7cd3a`'s prompt, so no token
is unpinned; and `requirements` and `interface` do not contradict each other,
because they are not talking about the same unit at all. This last point is
also why the diagonal screen now reports `diagonal_units_compared` — with no
shared unit it has nothing to compare, and reporting that as *clean* is how
this instance was first mis-judged.

Per the survey, **no published detector examines this field pair**; the
`requirements`/`interface` pair is specific to SWE-bench Pro and the screening
methods in the literature were built for original SWE-bench.

## Calibration: four instrument defects, found and fixed

The first pass alarmed on 22/40 with the token screen. Reading the alarms found
defects in the *instrument*, not in the tasks. Fixing them turned out to need a
rule about **which direction** to fix in, because the two errors a screen can
make are not symmetric:

> **A miss costs an order of magnitude more than a false alarm.** A false alarm
> costs a reader a few seconds. A miss costs a broken task entering the training
> data — and `ansible-de5858f4` was one regex away from being passed as clean.
> So the instinct to "reduce noise" is the dangerous direction here, and any
> change that shrinks the alarm set needs an argument that is *deductive*, not
> merely tidier.

Applying that rule cost one of the three fixes originally planned, and narrowed
a second:

| # | Defect | Example | Fix, and why it is safe |
| --- | --- | --- | --- |
| 1 | **prose inside comments counted as a requirement** | `element-web-b007ea81` alarmed on `100`, `curve` and `seeing`, all words in one comment on each side; `teleport-bb562408` produced 78 alarms that were mostly the Apache licence header | comments are dropped from the **identifier and number** streams only. A word that appears solely in a comment cannot be a graded requirement — a deductive argument, not noise reduction. **String literals are never stripped**: doing so ate `navidrome-d0dceae0`'s required literal `http://localhost/p/ABC123` from the `//localhost` onward, which is exactly the miss this rule forbids |
| 2 | **the signature screen ignored the interface field** | `flipt-3b2c25ee`'s `New` grows a `store` parameter and the interface field declares the new signature verbatim | suppress only when that field *declares* the symbol. Deductive: the change is stated, which is what the field is for |
| 3 | **a miss:** one character class for all three quote delimiters cannot capture a literal containing a *different* quote | `ansible-de5858f4`'s graded test asserts `actual_cache == '{"version": 1}'`; the inner `"` truncated the match to `version`, a word `requirements` does contain | one pattern per delimiter — pure recall gain, but **not sufficient here**, see below |
| 4 | **a poisoned cache:** a truncated tarball was cached as if complete | one `codeload` stream ended early mid-run | retry, and never write a partial token set |

### The fix that was rejected

The token screen read only the files the patches touch, so a symbol defined
anywhere else in the repository read as un-derivable — `flipt-3b2c25ee` alarmed
on `storage.ListWithOptions` and `storage.NewNamespace`, ordinary existing API.
The obvious fix is to tokenize the whole repository at `base_commit` and treat
everything in it as *given*.

**That fix is wrong, and it silences a true positive.** Existing somewhere in
the checkout makes a symbol *available*; it does not make it *pinned*, and the
gap between those two is precisely what the screen is for. The counter-example
is in this very set: `navidrome-b3980532`'s graded test asserts
`...apiKey == lastFMAPIKey`, comparing against a constant by symbol. That
constant of course exists in the repository — and the prompt still never names
it, which is why that instance is judged broken. Whole-repo suppression would
have marked it clean.

So the repository token set is kept, and used as **annotation instead of
suppression**: `unpinned_but_present_in_repo` lists which alarms a reader can
dismiss quickly, and the alarm itself still fires. `flipt-3b2c25ee`'s four
alarms are still reported, and are still resolved by hand — in that case
correctly, because the interface field gives `Storer.ListFlags`'s parameter type
as `*storage.ListRequest[storage.NamespaceRequest]`, and that package's
constructors are the only way to build one.

### A miss that survived the fix, and why

Fix 3 makes the tokenizer see `{"version": 1}`, and it still does not make
`ansible-de5858f4` alarm. The reason is a second conjunct in the screen: it
reports a token only when the graded tests **require** it *and* the gold patch
**introduces** it. Gold writes the cache with

```python
cache_version = 1
cache = {'version': cache_version}
```

so the literal string `{"version": 1}` exists only in the *test*; the gold
produces it at run time, through `json.dumps`, and never as text. The
intersection is therefore empty and the screen stays quiet, even though the
graded assertion is byte-exact and the prompt gives neither the value `1` nor
the serialization.

That conjunct is there to suppress false alarms — and by the asymmetry rule
above it is itself sitting on the dangerous side, because it can only ever
*remove* alarms. Dropping it would surface every test literal absent from the
prompt, which is a much noisier screen and a larger change than this round
should make. It is recorded here as a **known, mechanism-level miss** rather
than fixed, so that nobody reads a quiet token screen as a clean bill:
`ansible-de5858f4` is judged broken on the assertion itself, read by hand.

### What this says about the token screen

**It is a good criterion that a poor implementation was discrediting, but it
is not complete.** On its home ground it is exact: `flipt-e50808c0`'s alarm set
contains, verbatim, `buffer capacity below 2 or above 10`,
`flush period below 2 minutes or greater than 5 minutes` and
`file not specified` — precisely the three strings the graded `TestLoad` asserts
by equality, and precisely the three the prompt never gives (it gives only the
*conditions*: "outside 2–10", "outside 2m–5m", "enabled without a file"). That
instance is condemned on the screen's own output. Its poor reputation came from
the artifacts above, all implementation — but the miss in the previous section
is a property of the *criterion's second conjunct*, and that one is not fixed.

| screen | before calibration | after |
| --- | ---: | ---: |
| token | 22/40 | 20/40 |
| diagonal | 6/40 | 6/40 |
| symbol | 11/40 | 14/40 |
| signature | 9/40 | 9/40 |
| snapshot | 0/40 | 2/40 |
| **any screen** | 28/40 | 27/40 |

The point is not the tidier numbers. It is that a hit rate holed by silent
misses, or padded by artifacts, cannot stand in for a broken-task rate — so it
cannot be the dependent variable of any further measurement, including the
control below.

## A correction: 54% was wrong

Partway through this round I reported a broken rate of **54%** to
`swelab-orchestra`, with an argument that the mixed-outcome subset must be
enriched for broken tasks. Both halves need correcting, and the earlier number
is left on the record here rather than quietly replaced.

- **54% covered only the first 13 instances** — the fastest end of issue #261's
  ordering — and that stretch happens to be unusually bad. Over all 40 the rate
  is **40%**. It is the fourth time in one day that a small sample bit this
  workstream.
- **The enrichment argument is unsupported.** The control below was run to test
  it and did not find it. What remains is a difference in *definition*: the
  compile gate is binary and mechanical, OpenAI's *overly strict tests* is a
  human panel read. Two different instruments produce two numbers that should
  not be differenced.

## Screen overlap — the number this was run to get

Over the 40 candidates, after calibration:

| screen | hits | token | diagonal | symbol | signature | snapshot |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **token** | 20 | — | 3 | 9 | 4 | 2 |
| **diagonal** | 6 | 3 | — | 2 | 2 | 0 |
| **symbol** | 14 | 9 | 2 | — | 7 | 0 |
| **signature** | 9 | 4 | 2 | 7 | — | 0 |
| **snapshot** | 2 | 2 | 0 | 0 | 0 | — |

- **all five fire together:** 0
- **at least one fires:** 27 of 40; **none fires:** 13


No screen's hit set is contained in another's, so none can be dropped. The two
verdicts contested between the two task pairs this round —
`teleport-b4e7cd3a` and `vuls-4c04acbd` — trip screens 3 and 4 and nothing
else, and it was **screen 4 that settled both arguments**, because it is the
only one whose output is two lines of quoted source (the definition at
`base_commit` beside the definition in the gold patch) with no room left to
interpret.

### Against the verdicts

| | count | at least one screen fires |
| --- | ---: | ---: |
| judged **broken** | 16 | **16** |
| judged **usable** | 23 | 10 |
| unresolved | 1 | 1 |

**Every instance judged broken trips at least one screen, and every instance no
screen touched turned out to be a good task.** Ten of the 23 usable ones also
alarm, so a hit means "read this by hand", at roughly a 40% dismissal rate —
which is the cost side of the asymmetry rule, and cheap.

**This is not an independent validation, and must not be read as one.** The
screen output was in front of the reader during the hand judgement, so the two
are not independent and the 16/16 is an upper bound rather than a blind
measurement. Testing the screens properly needs verdicts formed without seeing
them — which means a *judge* who has never seen them, not a different set: the
contamination is in this reader, not in the instances. That experiment is
[specified below](#defined-not-executed-the-blind-re-judgement) and was not
run.

**The same contamination applies to the silent 13, and it bites hardest exactly
where it matters.** "Every instance no screen touched turned out to be a good
task" reads like evidence that the screens miss little. It is not: those 13
verdicts were formed by a reader who had already seen that no screen fired, so
the row cannot underwrite the screens' recall any more than the 16/16 row can.

That distinction is load-bearing because of one specific known hole. The token
screen reports a literal only when the graded tests **require** it *and* the
gold patch **introduces** it, and
[that second conjunct](#a-miss-that-survived-the-fix-and-why) can only ever
remove alarms. It demonstrably swallowed one instance in this set —
`ansible-de5858f4`, recovered by reading the assertion by hand, not by any
screen. **How many more it swallowed is unknown**, and the silent 13 cannot
answer that question, because they are the very rows a mechanism-level miss
would land in.

## The control: a random 40 from the same corpus

To test whether issue #261's mixed-outcome subset is *enriched* for broken
tasks, the same four screens were run over a seeded random sample of 40 rows
from the full 731 (`--random 40 --seed 261`). Mechanical layer only: no manual
verdicts, by [decision](#open-questions), because the alarm rate is not a
broken-task rate and re-measuring it on another 40 would re-measure the same
contaminated quantity.

| screen | random 40 (seed 261) | mixed-outcome 40 |
| --- | ---: | ---: |
| token | 18/40 (45%) | 20/40 (50%) |
| diagonal | 4/40 (10%) | 6/40 (15%) |
| symbol | 15/40 (37%) | 14/40 (35%) |
| signature | 8/40 (20%) | 9/40 (22%) |
| snapshot | 2/40 (5%) | 2/40 (5%) |
| **any screen** | 26/40 (65%) | 27/40 (67%) |

**The control does not support the enrichment hypothesis.** Every difference is
within noise at *n* = 40 — a two-instance gap is 5 percentage points with a
standard error near 11. Two readings remain, and nothing here separates them:

- **(a)** the mixed-outcome subset genuinely is not enriched for these four
  diseases, and the 40% figure needs a different explanation — most plausibly
  that the compile gate is a sharper instrument than OpenAI's human read, not
  that it is a stricter standard;
- **(b)** the screens' alarm rate is too false-alarm-dominated to resolve an
  enrichment effect. The [calibration](#calibration-three-false-alarm-sources-found-and-fixed)
  is direct evidence for this.

So the 40% is recorded here as an **unexplained difference**, not as evidence of
enrichment. Answering it properly needs a manual verdict layer on the random 40,
which is not on the critical path.

**Direct cross-checking against OpenAI's own labels is closed off, not skipped.**
The survey records `[未找到]` for the instance-ID lists behind their 200 / 249 /
286 counts — they are not published.

## Runnability comes before task quality

A separate gate, discovered by `swelab-steered-impl` while sampling, and it
outranks everything above: **the agent binary must be able to execute inside the
instance image.** On `webclients-a6e6f617` the rollout returned exit `2` with
`timed_out: 0` — indistinguishable, at that level, from a reasoning failure —
while the run record said:

```
claude_code.exit_code = 127
claude_code.wall_seconds = 0.69
agent_complete = 0
/opt/claude-code/claude: cannot execute: required file not found
```

The agent never started; that image cannot run a linux-x64 glibc binary. The
exit code alone conflates three outcomes — the agent reasoned wrong, the agent
was killed by the timeout, the agent never ran — so a usable sample requires
`agent_complete == 1` **and** `claude_code.exit_code == 0` as well.

Cheaply checkable per image family, and no extra run is needed: every rollout
already records `/opt/claude-code/claude --version` as the first line of
`rollout/a0/claude.info`.

| family | instances in #261 | status |
| --- | --- | --- |
| `qutebrowser`, `openlibrary` (python) | 4 | **proven** — rollouts completed |
| `vuls`, `navidrome`, `teleport` (go) | 15 | **proven** |
| `NodeBB` (js) | 4 | **proven** — `2.1.212`, exit 0 |
| `protonmail/webclients` (js) | 5 | **un-runnable** — exit 127 |
| `element-web` (js), `tutanota` (ts) | 6 | untested |

The failure is per-repository image, not per-language: NodeBB is JavaScript and
runs fine.

## Per-instance verdicts

Ordered as in issue #261 (fastest first). `f2p`/`p2p` are the required and
regression test counts. Full evidence per row — including each screen's raw
output — is in [`candidates.json`](candidates.json).

| # | instance | verdict | category | f2p / p2p | evidence |
| ---: | --- | --- | --- | ---: | --- |
| 0 | `internetarchive__openlibrary-5de7de19211e71b29b2f2ba3b1dff2fe065d660f-v08d8e8889ec945ab821fb156c04c7d2e2810debb` | ❌ bad | misleading_prompt | 16 / 9 | requirements say "The method" three times for the same three units the interface types "Type: Function"; the two halves of the task statement disagree and the graded tests only accept one placement. |
| 1 | `ansible__ansible-c1f2df47538b884a43320f53e787197793b105e8-v906c969b551b346ef54a2c0b41e04f632b7b73c2` | ❌ bad ⚠︎untested image | underspecified_prompt | 4 / 0 | test_api_parameters asserts the destinationAddress->dst_address API field mapping; none of the three prompt fields contains those spellings, no sibling bigip_message_routing_* module exists at base_commit, and the one in-repo precedent (bigiq_application_*.py) maps to destination_address instead. |
| 2 | `navidrome__navidrome-5001518260732e36d9a42fb8d4c054b28afab310` | ✅ good | — | 4 / 0 | sessionKeyProperty is named in the prompt; the one signature change (sessionKeys.delete drops uid) is stated: "must automatically derive the current user from the context.Context ... without passing an explicit user ID". |
| 3 | `future-architect__vuls-4c04acbd9ea5b073efe999e33381fa9f399d6f27` | ❌ bad | overly_strict_tests | 3 / 0 | compile gate. base report/util.go:523 is `func diff(curResults, preResults models.ScanResults) (diffed models.ScanResults, err error)`; gold adds two bool parameters AND drops the error return. The graded test assigns one value. No prompt field mentions the return arity, and keeping (result, err) is the conservative choice. |
| 4 | `tutao__tutanota-fe240cbf7f0fdd6744ef7bef8cb61676bcdbb621-vc4e41fd0029957297843cb9dec4a25c7c756f029` | 🟡 good, caveated ⚠︎untested image | — | 107 / 0 | checkEventValidity and the four CalendarEventValidity members are declared in the interface and every graded assertion follows from requirements. Caveats: fail_to_pass is 107 whole test suites with pass_to_pass empty, so one unrelated flake fails the instance; and TranslationKeysTest requires en/de/de_sie key sets to be identical, which punishes wiring the new UserErrors unless all three translation files are edited - never stated. Also note requirements give the precedence as invalid > pre-1970 > ordering while gold implements invalid > ordering > pre-1970; no graded case distinguishes them. |
| 5 | `protonmail__webclients-a6e6f617026794e7b505d649d2a7a9cdf17658c8` | ✅ good 🚫un-runnable image | — | 1 / 0 | interface gives file path, function name, input, output and the rule verbatim; one graded test; 22-line gold patch; all five screens silent. NOTE: image family unrunnable (see runnability). |
| 6 | `navidrome__navidrome-b3980532237e57ab15b2b93c49d5cd5b2d050013` | ❌ bad | overly_strict_tests | 1 / 0 | test asserts ...apiKey equals the lastFMAPIKey symbol; requirements only say "fall back to a built-in shared API key" and never name it (steered-impl's dossier). |
| 7 | `future-architect__vuls-abd80417728b16c6502067914d27989ee575f0ee` | ❌ bad | overly_strict_tests | 6 / 0 | test calls o.parseRpmQfLine(line) directly; requirements name only pkgPs/postScan/getOwnerPkgs and never ask for the extraction, and the interface field says "No new interfaces are introduced" (steered-impl's dossier). |
| 8 | `gravitational__teleport-b4e7cd3a5e246736d3fe8d6886af55030b232277` | ❌ bad | overly_strict_tests | 1 / 0 | compile gate. base lib/backend/report.go:294 is `func buildKeyLabel(key []byte, sensitivePrefixes []string) string` and already implements the identical masking; base report_test.go:69-83 already holds the identical expectation table. The whole graded delta is the parameter type []byte -> string, which no prompt field mentions. The interface field pins MaskKeyName while TestBuildKeyLabel grades buildKeyLabel. |
| 9 | `qutebrowser__qutebrowser-9ed748effa8f3bcd804612d9291da017b514e12f-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d` | ✅ good | — | 6 / 979 | gold only changes the private _parse_value(self, val) -> (self, kind, val); the graded tests call klass().to_py(val) throughout. The observed failure was a genuine regression the agent could have seen by running the existing suite (steered-impl). |
| 10 | `flipt-io__flipt-3b2c25ee8a3ac247c3fad13ad8d64ace34ec8ee7` | ✅ good ⚠︎untested image | — | 4 / 0 | requirements are unusually exact - the gRPC code Internal and the message "failed to fetch list of flags" verbatim, the BOOLEAN/VARIANT+Enabled rule, the default namespace. The interface declares Storer's method signature and New's full new signature, so the one signature change is stated. The four token alarms (ListWithOptions, NewNamespace, Results, FlagType_VARIANT_FLAG_TYPE) are all pre-existing repo API. |
| 11 | `element-hq__element-web-b007ea81b2ccd001b00f332bee65070aa7fc00f9-vnan` | ✅ good ⚠︎untested image | — | 7 / 0 | arraySmoothingResample and arrayRescale are pure additions; requirements bullets 8-9 spell out the smoothing algorithm (neighbour-pair averaging at alternating interior positions excluding endpoints; repeat until length <= 2*points) and all four graded downsample cases reproduce under that reading. The three arrayFastResample entries in fail_to_pass fail at base only because the test module cannot import the two new names. Token alarms 100/curve/seeing are words in comments. |
| 12 | `future-architect__vuls-e3c27e1817d68248043bd09d63cc31f3344a6f2c` | ❌ bad | overly_strict_tests | 8 / 0 | interface field reads "No new interfaces are introduced" while fail_to_pass is Test_ensure, which calls a brand-new unexported `ensure(servers, path, scanResults, generateFunc func() (string, error)) (needsOverwrite bool, err error)`. Its name, its parameter order, and the fact that it is separate from EnsureUUIDs are all unstated. |
| 13 | `future-architect__vuls-f0b3a8b1db98eb1bd32685f1c36c41a99c3452ed` | ✅ good | — | 2 / 0 | the whole task is a rename requirements state verbatim ("Rename the confidence label CpeNameMatch to CpeVersionMatch across all definitions, usages, and string representations") plus a new CpeVendorProductMatch with the score 10 given. The two graded tests are pre-existing tests re-pointed at the new name. |
| 14 | `gravitational__teleport-ba6c4a135412c4296dd5551bd94042f0dc024504-v626ec2a48416b10a88641359a169d99e935ff037` | ❌ bad | overly_strict_tests | 16 / 0 | TestProcessStateGetState grades a newly added unexported processState.getState() and a componentStateEnum; lib/service/state.go exists at base with processState and stateOK but neither getState nor any per-component map. No prompt field gives those names. The interface field declares only SetOnHeartbeat, which the graded tests never touch - the same shape as teleport-b4e7cd3a. |
| 15 | `flipt-io__flipt-c6a7b1fd933e763b1675281b30077e161fa115a1` | ❌ bad ⚠︎untested image | underspecified_prompt | 6 / 0 | tests require `version: 1.0` as the supported document version and assert EqualError(err, "unsupported version: 5.0"); the prompt says only "validate that the document version is supported" and never gives a version value or an error format. |
| 16 | `protonmail__webclients-cb8cc309c6968b0a2a5fe4288d0ae0a969ff31e1` | ✅ good 🚫un-runnable image | — | 10 / 0 | requirements give a worked example for every one of the ten graded cases (drive.env.proton.black -> drive.proton.local:8888 etc.) and the interface declares the file and function. NOTE: image family unrunnable. |
| 17 | `NodeBB__NodeBB-2657804c1fb6b84dc76ad3b18ecf061aaab5f29f-vf2cf3cbd463b7ad942381f1c6d077626485a1e9e` | ✅ good | — | 3 / 169 | the payload reshape from an array to a single {tid, order} is stated verbatim ("accepts a single payload containing the topic identifier (tid) and a zero-based target position (order)"); the three graded behaviours (no-privileges error, no-op on an unpinned topic, relative order preserved) each map to a requirements bullet. All four screens silent. Soft spot, and a useful one: order 0 means the top of the pinned list as displayed (getSortedSetRevRange), the opposite direction from the old score-as-order behaviour - derivable but easy to get backwards. |
| 18 | `gravitational__teleport-007235446f85b1cbaef92664c3b3867517250f21` | ✅ good | — | 1 / 0 | the exact error text is produced by the test's own fake FileSystem (newErrMissingFile), so the solver only has to propagate it unwrapped, which requirements demand ("path-qualified error in the exact form"). Requirements also state explicitly that they do not prescribe internal function names. One graded test, and it is an end-to-end SCP flow. |
| 19 | `protonmail__webclients-ac23d1efa1a6ab7e62724779317ba44c28d78cfd` | ✅ good 🚫un-runnable image | — | 1 / 18 | requirements state the optional cancellation parameter and, bullet by bullet, the exact result object the single graded test asserts. The signature alarm is benign: the added parameter is optional and stated. NOTE: image family unrunnable. |
| 20 | `gravitational__teleport-2b15263e49da5625922581569834eec4838a9257-vee9b09fb20c43af7e520f57e9239bbcf46b7113d` | ✅ good | — | 14 / 0 | the interface field is exhaustive (every exported type, constructor and method of the new tokencount.go) and requirements state the two changed signatures verbatim, including `(any, *model.TokenCount, error)`. The expected token totals 721/729/932 rest on perMessage/perRole/perRequest, which already exist at base_commit - the gold diff removes lines that use them. |
| 21 | `element-hq__element-web-aec454dd6feeb93000380523cbb0b3681c0275fd-vnan` | ❌ bad ⚠︎untested image | overly_strict_tests (low conf.) | 36 / 164 | LruCache.values() ordering is asserted as an exact array in about eight graded tests - set a then b gives ["a value", "b value"], fixing least-recent-first - while requirements say only "iterates current contents in the cache's internal order", which fixes no direction at all. Low confidence, and deliberately so: least-recent-first is the more common convention, so a careful solver may well land on it. Note the snapshot screen does NOT condemn this one - its test patch does carry a committed snapshot, but for Pill-test.tsx, which no fail_to_pass entry names; that snapshot is collateral in the same upstream PR, not the grading mechanism. |
| 22 | `internetarchive__openlibrary-d40ec88713dc95ea791b252f92d2f7b75e107440-v13642507b4fc1f8d234172bf8129942da2c2ca26` | 🟡 good, caveated | — | 28 / 0 | requirements state both renames verbatim ("The function previously named import_author should be replaced by author_import_record_to_author", same for build_query -> import_record_to_edition) and the interface declares load_author_import_records and check_cover_url_host. Caveat: 28 required tests over a broad import pipeline, pass_to_pass empty. |
| 23 | `gravitational__teleport-c335534e02de143508ebebc7341021d7f8656e8f` | 🟡 good, caveated | — | 1 / 0 | the interface declares LocalKeyAgent.ClientCertPool with its full signature and requirements name the assertion the single graded test makes ("must surface the proxy's 'subsystem request failed' error"). Caveat: that one test starts real auth and proxy processes and performs an SSO login, so a failure is as likely to be environmental as cognitive; pass_to_pass empty. |
| 24 | `NodeBB__NodeBB-f2082d7de85eb62a70819f4f3396dd85626a0c0a-vd59a5728dfc977f44533186ace531248c2917516` | ✅ good | — | 5 / 111 | the interface declares postsAPI.getRaw and postsAPI.getSummary with `caller, { pid }` inputs and the "... or null" contract, and requirements state the deleted-post rule (admin, moderator or author) that three of the five graded tests turn on. |
| 25 | `navidrome__navidrome-d0dceae0943b8df16e579c2d9437e11760a0626a` | ❓ unresolved | overly_strict_tests (low conf.) | 2 / 0 | the graded response test builds an expected Subsonic Share with Url "http://localhost/p/ABC123" and an Expires timestamp, and navidrome compares responses against committed .snapshots/ golden files. Requirements say only "Response formats must comply with standard Subsonic specifications" and "apply reasonable defaults" without naming a default expiry. Not resolved: whether the golden file is checked out at grading time, which decides whether the byte-exact comparison is actually graded. |
| 26 | `qutebrowser__qutebrowser-77c3557995704a683cdb67e2a3055f7547fa22c3-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d` | ✅ good | — | 2 / 26 | requirements pin both graded tests almost verbatim, including the private attribute name ("an accessible values._vmap attribute whose iteration order reflects the insertion order; iter(values) must exactly match list(values._vmap.values())") and the repr shape `odict_values([ScopedValue(...)])`, which also fixes the container as collections.OrderedDict. test_add_url_benchmark is in the test patch but not in fail_to_pass. Caveat: so tightly specified that a hint has little left to teach. |
| 27 | `flipt-io__flipt-e50808c03e4b9d25a6a78af9c61a3b1616ea356b` | ❌ bad ⚠︎untested image | overly_strict_tests | 23 / 0 | TestLoad asserts exact validation error values - errors.New("buffer capacity below 2 or above 10"), errors.New("flush period below 2 minutes or greater than 5 minutes"), errors.New("file not specified"). Requirements state the conditions ("outside 2-10", "outside 2m-5m", "enabled without a file") and never the wording. The token screen surfaced all three literals. |
| 28 | `element-hq__element-web-f63160f38459fb552d00fcc60d4064977a9095a6-vnan` | ✅ good ⚠︎untested image | — | 4 / 195 | every graded assertion is toHaveTextContent on a string requirements give verbatim ("Can't load this message", "<displayName> wants to verify"). Both test helpers wrap the component in TileErrorBoundary, so throwing and rendering the string are equally acceptable; the thrown messages are only console-filtered, never asserted. |
| 29 | `ansible__ansible-de5858f48dc9e1ce9117034e0d7e76806f420ca8-v1055803c3a812189a1133297f7f5468579283f86` | ❌ bad ⚠︎untested image | overly_strict_tests | 17 / 41 | test_missing_cache_dir asserts the cache file's byte-exact content, `actual_cache == '{"version": 1}'`. The prompt says only "storage of a version marker in the cache" - it never gives the value 1 nor the serialization. The interface field is empty. get_cache_id's exact "host:port" form (with a trailing colon when there is no port) is likewise only implied. |
| 30 | `element-hq__element-web-4fec436883b601a3cac2d4a58067e597f737b817-vnan` | ❌ bad ⚠︎untested image | overly_strict_tests | 16 / 35 | two independent kills. Deductive: the test patch adds committed DOM snapshots for DeviceDetailHeading-test.tsx, DeviceDetails-test.tsx and SessionManagerTab-test.tsx, all three of which fail_to_pass names, so grading fixes every class name and nesting level of the rendered output. Separately underspecified: the graded tests query by exact data-testid values - device-detail-heading, device-heading-rename-cta, device-rename-input, device-rename-submit-cta, device-rename-cancel-cta, device-rename-error - while requirements say only "should expose stable testing hooks (e.g., data-testid attributes)" and name none of them. |
| 31 | `gravitational__teleport-d6ffe82aaf2af1057b69c61bf9df777f5ab5635a-vee9b09fb20c43af7e520f57e9239bbcf46b7113d` | 🟡 good, caveated | — | 71 / 43 | all four signature changes the screen flags are stated: requirements bullet 35 gives the new callback verbatim as `varValidation(namespace, name string) error`, and MatchExpression plus MatchExpression.Match() are declared in the interface. Caveat: 71 required tests over an expression-language rewrite - determinate on paper, very large in practice. |
| 32 | `protonmail__webclients-d494a66038112b239a381f49b3914caf8d2ef3b4` | ✅ good 🚫un-runnable image | — | 1 / 0 | requirements give all three warning strings verbatim with their priority order, the disabled-flag formula, and the instruction to move the ResizeObserver mock into the shared jest setup that the test file relies on. Caveat: so tightly specified there is little for a hint to teach. NOTE: image family unrunnable. |
| 33 | `navidrome__navidrome-6bd4c0f6bfa653e9b8b27cfdc2955762d371d6e9` | ✅ good | — | 3 / 2 | the one graded redaction case is stated verbatim ("to map or other value types after stringification using Go's default formatting before regex replacement"), and the reverse-proxy bullets give the Remote-User default, the CIDR whitelist semantics and the null auth object. RemoteAddr is a stdlib field. |
| 34 | `protonmail__webclients-6e165e106d258a442ae849cdf08260329cb92d39` | ❌ bad 🚫un-runnable image | overly_strict_tests | 7 / 6 | the interface field declares getRegularRenewalNoticeText while every graded test imports and calls getCheckoutRenewNoticeText, which no prompt field names - the third instance in this set of the interface pinning one symbol while grading happens on another. NOTE: image family unrunnable. |
| 35 | `element-hq__element-web-880428ab94c6ea98d3d18dcaeb17e8767adcb461-vnan` | ❌ bad ⚠︎untested image | overly_strict_tests | 4 / 64 | the graded test "should render as expected" is expect(renderResult.baseElement).toMatchSnapshot() against a committed __snapshots__/UnverifiedSessionToast-test.tsx.snap that the test patch adds, and fail_to_pass names that test file. A DOM snapshot fixes every class name and nesting level; no prose prompt can pin that, so this is overly strict tests by construction rather than by suspicion. |
| 36 | `NodeBB__NodeBB-445b70deda20201b7d9a68f7224da751b3db728c-v4fbcfae8b15e4ce5d132c408bca69ebb9cf146ed` | ✅ good | — | 4 / 69 | each guard is stated verbatim with its error string - a call lacking both mid and roomId must fail with [[error:invalid-data]], likewise chats.list without start/stop/page and users.getPrivateRoomId without uid. The tests pass undefined, so the `= {}` default destructuring follows from "a call without ... must fail with". |
| 37 | `NodeBB__NodeBB-cfc237c2b79d8c731bbfc6cadf977ed530bfd57a-v0495b863a912fbff5749c67e860612b91825407c` | ❌ bad | underspecified_prompt | 2 / 193 | the second graded test sets icon:bgColor to the invalid value 'teal' and requires the returned payload's icon:bgColor to be a member of getIconBackgrounds() - i.e. an invalid stored value must be replaced by a valid one. Nothing in the prompt says so. The requirements field is degenerate: six bullets about Node module-export mechanics (one part-Spanish) instead of the feature. |
| 38 | `gravitational__teleport-bb562408da4adeae16e025be65e170959d1ec492-vee9b09fb20c43af7e520f57e9239bbcf46b7113d` | 🟡 good, caveated | — | 3 / 0 | the interface field is a complete specification of the new package - Config with its three fields and SetDefaults, Buffer[T] with NewBuffer/Append/NewCursor/Close, Cursor[T] with Read/TryRead/Close, and the three error variables - and requirements add the defaults (64, 5m) and the finalizer requirement TestCursorFinalizer grades. Caveat: a from-scratch concurrent data structure, the second slowest in the set. The 78 token alarms were license-header words. |
| 39 | `ansible__ansible-e40889e7112ae00a21a2c74312b330e67a766cc0-v1055803c3a812189a1133297f7f5468579283f86` | 🟡 good, caveated ⚠︎untested image | — | 18 / 191 | requirements state the four-element requirement tuple (name, version, type, path), the git-URL '#' syntax, the accepted type values and the galaxy.yml check that the graded tests exercise. Caveat: the interface field is empty, there are 18 required tests across 27 touched files, and it is the slowest instance in issue #261. |

## What to hand the trace-synthesis line

Ranked by: image family proven runnable, then `pass_to_pass` non-empty (without
regression protection a failure like `qutebrowser-9ed748ef`'s — all 6 required
tests passing, 2 regression tests failing — is invisible and reads as
*resolved*), then a modest `fail_to_pass`, then cheap.

One criterion is not mechanical and moves the order: **a task specified so
tightly that the answer is in the prompt is a poor sample even when it is
determinate.** A hint can only teach what the prompt left derivable-but-unsaid,
and an Oracle writing a guidebook against `qutebrowser-77c35579` — whose
requirements name the private attribute `_vmap` and the `odict_values` repr —
has every stage's justification collapse to "the prompt already told you". So
the ordering below is stated explicitly rather than sorted.

| rank | instance | f2p / p2p | why |
| ---: | --- | ---: | --- |
| 1 | `NodeBB__NodeBB-2657804c1fb6b84dc76ad3b18ecf061aaab5f29f-vf2cf3cbd463b7ad942381f1c6d077626485a1e9e` | 3 / 169 | the best of the set — regression protection, three graded tests, all five screens silent, and its one soft spot (which end of the pinned list `order: 0` means) is exactly the kind of derivable-but-easy-to-invert detail a directional hint can teach |
| 2 | `qutebrowser__qutebrowser-77c3557995704a683cdb67e2a3055f7547fa22c3-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d` | 2 / 26 | the fallback if the one above cannot be made to fail. Cheapest of the proven-runnable determinate instances; ranked second rather than first only because its requirements pin both graded tests almost verbatim, down to the private attribute `_vmap` and the `odict_values` repr, leaving a guidebook little to say |
| 3 | `NodeBB__NodeBB-f2082d7de85eb62a70819f4f3396dd85626a0c0a-vd59a5728dfc977f44533186ace531248c2917516` | 5 / 111 | strong regression protection, and an interface field that declares both graded symbols with their full contract including the null-on-denied return |
| 4 | `NodeBB__NodeBB-445b70deda20201b7d9a68f7224da751b3db728c-v4fbcfae8b15e4ce5d132c408bca69ebb9cf146ed` | 4 / 69 | regression protection; the graded behaviour is three input-validation guards stated verbatim with their exact error string |
| 5 | `navidrome__navidrome-6bd4c0f6bfa653e9b8b27cfdc2955762d371d6e9` | 3 / 2 | requirements state the one subtle graded behaviour verbatim, down to "after stringification using Go's default formatting"; only 2 regression tests, so the protection is thin |
| 6 | `qutebrowser__qutebrowser-9ed748effa8f3bcd804612d9291da017b514e12f-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d` | 6 / 979 | already in use by the sampling line, and the instance that proves why regression protection matters — its harvested failure passed all 6 required tests and failed 2 `pass_to_pass` ones, so with an empty p2p it would have read as *resolved* |
| 7 | `gravitational__teleport-007235446f85b1cbaef92664c3b3867517250f21` | 1 / 0 | one graded test and no regression protection, but the assertion is an exact error string the test's own fake filesystem produces, so the solver only has to not wrap it |
| 8 | `gravitational__teleport-d6ffe82aaf2af1057b69c61bf9df777f5ab5635a-vee9b09fb20c43af7e520f57e9239bbcf46b7113d` | 71 / 43 | determinate — every signature change it makes is stated in the prompt — but 71 required tests over an expression-language rewrite make it the most expensive usable instance here |

Deliberately **not** recommended: `webclients-a6e6f617`, `webclients-ac23d1ef` and `webclients-d494a660` are all determinate and all three sit in the one image family that cannot run the agent; `tutanota-fe240cbf` grades on 107 whole test suites with no regression protection, so any unrelated flake fails it.

## Defined, not executed: the blind re-judgement

The one experiment that would turn this report's self-assessment from an upper
bound into a measurement, specified here so someone can pick it up rather than
wish for it.

| | |
| --- | --- |
| **Question** | What is the screens' actual recall — how many broken tasks does no screen touch? |
| **Why it cannot be answered here** | Every verdict above was formed with the screen output visible, so verdicts and screens are not independent. [Both](#against-the-verdicts) the 16/16 and the silent-13 rows are contaminated by it. |
| **Method** | A judging agent that has never seen `screens.py` or any of its output re-judges the **23 rows the screens disagree with the reader about or are silent on** — the 13 no screen fired on, plus the 10 usable ones that alarmed — against the determinacy criterion alone. Compare its verdicts against this report's. |
| **What it measures** | A broken task in the silent 13 is a genuine screen miss, and the first real estimate of the [second-conjunct hole](#a-miss-that-survived-the-fix-and-why). A usable task among the 10 alarms confirms the dismissal rate. |
| **Cost** | One independent agent, no containers, no rollouts — the same pure-text budget as this round. |
| **Status** | **Not executed.** |

## Open questions

- **Why 40% and not 30%?** [Two candidate explanations](#the-control-a-random-40-from-the-same-corpus),
  no evidence separating them. Settling it needs a manual verdict layer on the
  random 40.
- **`navidrome-d0dceae0` is unresolved.** Its response tests compare against
  committed `.snapshots/` golden files, but those live in the **gold** patch
  rather than the test patch, so whether the byte-exact comparison is actually
  applied at grading time depends on `before_repo_set_cmd` — unchecked.
- **`element-web-aec454dd` is a low-confidence *bad*, and the only such row.**
  It turns on `LruCache.values()` iterating least-recent-first, which the prompt
  does not fix in either direction. That is the more common convention, so a
  careful solver may well land on it; the verdict could flip on one more
  reading. Its snapshot file does **not** condemn it — see
  [screen 5's condition 3](#the-five-mechanical-screens).
- **Nothing here was executed.** Every verdict is a reading of the parquet and
  the repository at `base_commit`. A verdict of *good* predicts that a
  faithful implementation passes; only a gold-patch run proves it, and this
  round deliberately started no container.
