# Task 21: Native supervision, the Python side

The design of record is
[issue #375](https://github.com/Luolc/swe-lab/issues/375). Task 20 is the
binary — the crate under `rust/swe-lab-supervisor/`, its tests and its CI — and
its §8 lists what it deliberately leaves out. This document is that list: the
Python half of the same migration, which is everything outside `rust/`.

Status lives in [`README.md`](README.md), not here.

## 1. What the boundary is

The binary and this repository's Python are two programs. Exactly three things
cross between them, and each is a place the two can drift silently:

| What | Direction | Where it lives |
| --- | --- | --- |
| the config document | Python writes, the binary reads | `native_supervision.py` |
| `SWE_LAB_SUPERVISOR_BASE_URL`, `SWE_LAB_SUPERVISOR_API_KEY` | Python passes by reference, the binary reads in-process | the sandbox's `pass_env` |
| the terminal summary | the binary writes, Python classifies from | `native_supervision.py` |

Nothing else. The actor's argv is handed over as opaque tokens (§4), and the
supervisor's own log is an artifact the run persists rather than a channel.

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

## 8. Out of scope here

The binary itself (task 20), `capture="stream_replay"`, and the removal of the
host runtime.
