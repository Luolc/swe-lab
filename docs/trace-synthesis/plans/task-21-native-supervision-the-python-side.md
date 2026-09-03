# Task 21: Native supervision, the Python side

The design of record is
[issue #375](https://github.com/Luolc/swe-lab/issues/375).
[Task 20](task-20-native-supervisor-runtime.md) is the binary — the crate under
[`rust/swe-lab-supervisor/`](../../../rust/swe-lab-supervisor/), its tests and
its CI — and its §8 lists what it deliberately leaves out. This document is that
list: the Python half of the same migration, which is everything outside
`rust/`.

Status lives in [`README.md`](README.md), not here.

## 1. What the boundary is

The binary and this repository's Python are two programs. These cross between
them, and each is a place the two can drift silently:

| What | Direction | Where it lives |
| --- | --- | --- |
| the config document | Python writes, the binary reads | `native_supervision.py` |
| `SWE_LAB_SUPERVISOR_BASE_URL`, `SWE_LAB_SUPERVISOR_API_KEY` | Python passes by reference, the binary reads in-process | the sandbox's `pass_env` |
| the terminal summary | the binary writes, Python classifies from | `native_supervision.py` |
| the actor's argv | Python hands over, the binary execs | the harness's `actor_argv` (§4) |

The first three are the run's values and live in one module; the fourth is the
harness's, because it is the harness that knows how to invoke the actor. The
supervisor's own log is an artifact the run persists, not a channel between the
two programs.

**The config is not a superset of the schema.** The binary deserializes with
`deny_unknown_fields` and gives no field a default, so a document carrying one
extra key, or omitting one policy number, is a run refused *after* a sandbox
has been paid for and the actor is about to start. Every one of the binary's
own rules is therefore applied where the value is chosen.

**The summary is the only thing a run may be classified from.** A wrapper that
ran cleanly exits with the *actor's* status, so exit `0` is compatible with
having supervised nothing at all. A missing, unparseable or unaccounted-for
summary is `supervision.unhealthy`, which `rollout_outcome` already turns into
`SUPERVISION_FAILED`.

## 2. The credential never has a rendered form

The endpoint and the credential are the environment's, not the document's, and
the binary refuses a document naming either. On this side that means:

- they reach the sandbox through the backend's `pass_env` — names only, so the
  value never appears on a command line or in a staged file;
- the binary splits a comma-separated list of keys **in-process**. No shell
  takes it apart, which is the failure mode the cross-repo rules name
  explicitly;
- the config document is a workspace artifact, so a credential in it is a
  credential on disk. Its absence is asserted, against a document first shown
  to be a real one.

## 3. Two proxy instances, not one

The binary carries no TLS — every dependency is pure Rust, and both mainstream
TLS stacks carry C — so it speaks plain HTTP to loopback and refuses an
`https://` base URL with the reason. The TLS termination is a **second
`cc-reverse-proxy` instance** inside the sandbox, started by the invocation
script on its own port with `--target https://openrouter.ai/api`.

Nothing about the proxy changes: it already supports that target, and the
harness already parameterises it (`proxy_target`). What the Python side adds is
a second instance — its own port, its own log, its own readiness poll, its own
entry in the script's single `EXIT` trap. The actor's first instance is
untouched.

## 4. The actor argv is handed over, never rebuilt

The wrapper executes what follows `--` as given: it never joins the tokens into
a shell command and adds no flags of its own. So the Python side must hand it
the argv it would otherwise have run, which means the harness's flag
construction becomes a list of tokens with one consumer today and two
tomorrow — **not** a second construction beside it. A rebuilt argv is a
supervised run that differs from an unsupervised one by more than the
supervision, which is the drift the `correction_channel` field exists to avoid.

**The prompt travels beside the argv, not on stdin.** The owner ruled on
2026-09-03 that the wrapper takes an explicit `--actor-prompt <path>` and writes
those bytes, unparsed, as the first thing on the actor's stdin — then holds that
stdin open. Neither alternative survives: folding the prompt into the config's
`task` binds what the judge measures against to what the actor was told, and
redirecting a file into the wrapper's own stdin makes a plain file's EOF decide
when the actor's input channel closes, which `#375`'s failure semantics reserve
for the wrapper's policy (a quiet result closes it; a correction at a result
boundary keeps it open). So the invocation script carries **no** stdin
redirect, and `prompt.stream.json` — still written by the Python side, exactly
as today — is passed by path.

## 5. Added alongside, never in place of

The host runtime stays exactly as it is until the binary has run end to end.
`#375` ends with its removal, and that removal is its own task with its own
evidence; until then a native supervised definition sits *beside*
`SUPERVISED_ROLLOUT` rather than replacing it.

## 6. No parity work, deliberately

The owner's ruling on #375 is that the native runtime diverges from the host
runtime in three measured places rather than reproducing defects
([#380](https://github.com/Luolc/swe-lab/issues/380),
[#381](https://github.com/Luolc/swe-lab/issues/381),
[#383](https://github.com/Luolc/swe-lab/issues/383)): the judge sees tool
results, what the supervisor has said goes to the writer and not the judge, and
the judge's token ceiling clears its own reasoning distribution. **So there are
no parity fixtures, no alignment assertions, and no documentation claiming the
two agree.** The two runtimes are not A/B-comparable and are not meant to be.

## 7. The slices, and what each can be built without

The binary is being written in parallel, and most of this does not wait on it:

1. **The contract** — the config document, the two variable names, the summary
   reader and the classification. Needs nothing: the schema is `config.rs`,
   which is written.
2. **The argv handoff** — the harness's flags as tokens, with the invocation
   script as one consumer of them. Needs nothing.
3. **The wiring** — the `AgentAsset` for the binary, the second proxy
   instance, the `pass_env` pair, the wrapper launch, and the summary consumed
   onto the run's record. Needs the binary to exist as an artifact.
4. **The round-trip schema check** (§7a). Needs the binary to be runnable, and
   should follow the wiring closely: every slice after it adds config fields.

## 7a. The schema is aligned by hand today, and that is the defect generator

The config document is a hand-written mirror of `config.rs`. Review of the
first slice found **four independent defects, all of the same class**: types
not checked where only ranges were, a quantifier claiming more than the module
owned, `task` reaching the document without passing the validation the fields
pass, and `criterion.name` taken from a caller-supplied path instead of the
pin. None was carelessness in a way the next one would avoid — one schema
maintained by hand in two languages produces a fresh opportunity to disagree on
every field added, and the only thing that finds it is a person reading both
sides field by field.

**The fix is to stop asking a person to do the comparing.** Once the binary is
on `main` as a runnable artifact, a round-trip check: render the document from
Python, hand it to the binary's own parser, assert it is accepted — and assert
that the deliberately broken ones are refused, so the check can tell the two
apart. That is a follow-up task, not a claim about today: **as of this writing
the two sides agree only because they were read against each other.**

## 8. Out of scope here

The binary itself (task 20), `capture="stream_replay"`, and the removal of the
host runtime.
