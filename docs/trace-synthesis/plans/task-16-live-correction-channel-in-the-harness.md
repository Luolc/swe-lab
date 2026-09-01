# Task 16 — A live correction channel in the Claude Code harness (design only)

**Status lives in [`plans/README.md`](README.md), not here.**

**Nothing here is authorized to be built.** The verdict that favours this
delivery mechanism is conditional on a compliance test that has not been run
([`DEBATE-VERDICT.md`](../../../experiments/trace_synthesis/process_supervision/DEBATE-VERDICT.md)),
and [`spec.md` §5](../spec.md#5-the-mechanism-decisions) still says *steer from
a hook*. This document exists so that, if the gate passes, the plumbing question
is already answered — and so that if it fails, what is discarded is a document.

Every claim below carries its status: **[M]** measured (with N and design) ·
**[C]** read out of this repo's code at `origin/main`, with the file and line ·
**[I]** inferred from one of those · **[U]** unmeasured.

## 1. What the harness does today

**[C]** `ClaudeCodeHarness.run` (`src/swe_lab/harnesses/claude_code/harness.py`)
is one-shot and blocking: it writes the prompt into the workspace, writes the
caller's env file, and calls `sb.run_script(...)`, which returns only when the
staged script exits. The conversation is read afterwards, out of the sandbox
filesystem.

**[C]** The invocation line the script ends on is

```sh
{binary} {flags} < {prompt} {capture_redirect} 2> {stderr}
```

so the agent's stdin is **a regular file, opened once**. `-p` with no argument
reads the prompt from it, sees EOF immediately, and there is no channel left for
anything to reach the process after `exec`.

**[C]** `run()` rejects a prompt over `MAX_PROMPT_BYTES` (10 MiB) before staging
it. The constant's comment attributes the cap to Claude Code v2.1.128+ and to
*piped stdin*; **[U]** whether it is a per-process total, a per-line limit, or
applies at all to `--input-format stream-json` is **not verified here**, and the
survey that raised it marked it unverified too. Treat 10 MiB as a documented
property of the *prompt* path and as an open question for a *live* one.

**[C]** The harness already runs a second long-lived process inside the sandbox:
`_proxy_start_lines` starts the capture proxy in the background, polls until it
is listening, exits `78` if it dies or never listens, and reaps it with an
`EXIT` trap. **This is the pattern to reuse**, not a new problem to solve.

## 2. The two problems, and only the second is hard

### 2.1 Stdin has to become a channel that stays open

Replace the file redirect with a FIFO the harness creates in the workspace. Only
`_invocation_script` changes shape; nothing outside the harness sees it.

Three consequences follow from one measured fact, and they are the whole
difficulty:

> **[M]** "The CLI does **not** consume stdin once and close it; the process
> exits when stdin is closed (EOF) or when killed" — N=5 multi-message sessions,
> three messages over one process
> ([report §4](../../../experiments/trace_synthesis/streamjson_input/REPORT.md)).

1. **Opening order is load-bearing.** **[I]** A shell redirect from a FIFO blocks
   until a writer opens the other end, so the agent cannot be started before its
   writer exists. The proxy's readiness wait is the precedent: start the writer
   first, prove it is alive, then exec the agent.
2. **The run no longer ends by itself.** Today EOF arrives with the prompt and
   the agent finishes the task and exits. With a writer holding the FIFO open,
   **nothing terminates the run** except the writer closing or `run_script`'s
   timeout killing it. **Who closes the write end, and on what signal, is the
   central design decision of this task** — not a detail of it. A design that
   forgets this converts every rollout into a timeout, and a timeout is promoted
   to `RunStatus.TIMEOUT` (`src/swe_lab/workflow/task.py`) **[C]**, which
   discards the distinction between an agent that finished and one we killed.
3. **A dead writer is not obviously a dead run.** If the supervisor dies but the
   writer's file descriptor stays open, the agent waits, produces nothing, and
   burns the wall clock to the timeout. If instead the descriptor closes, the
   agent sees EOF and **ends the rollout early** — the same failure wearing the
   opposite mask. Neither is acceptable as an accident, so the close must be an
   explicit decision by a component that knows the task is over.

### 2.2 Who acts while `run()` blocks

Two shapes were on the table. **The blast radius of the first is the argument
against it, and it is bigger than it looks.**

**(i) Make `run()` asynchronous, or have it return a handle.** **[C]** measured
by grep at `origin/main`: `Harness.run` is one abstract declaration in
`harnesses/base.py`, **3 production implementations** (`claude_code`, `codex`,
`grok_build`) and **7 definitions across 6 test files**. But it does not stop
there: the caller is `CodingAgentTask.action` (`src/swe_lab/rollout.py`) and
`OracleAnalysisTask.action` (`src/swe_lab/trace_synthesis/oracle.py`), both of
which return an `ExecResult` into `Task.execute`, which owns the observer
ordering, the timeout promotion and the run record. Making the action
non-blocking is therefore a change to `Task.action` as well — **18 definitions
of `action` in `src` and `tests` together** — and to a contract ADR-0007 settled
for reasons unrelated to this channel. Two harnesses that will never have a live
channel would pay for it.

**(ii) Keep `run()` exactly as it is, and put the supervisor beside it.** The
supervisor runs **host-side** (it calls the oracle, so it needs network the
sandbox does not have), reaches the container through a **bind-mounted
directory**, and an in-sandbox **relay** appends what lands there into the FIFO.

**Recommendation: (ii)** — and the reason is stronger than "smaller diff":
**the seam it needs already exists and is already used for exactly this shape.**

- **[C]** `CodingAgentTask.proxy_factory` (`src/swe_lab/rollout.py`) opens a
  context manager **around** the blocking action: "a recorder held open around
  the main action (e.g. a host-side reverse proxy capturing the agent's API
  traffic)". A host-side component whose lifetime brackets a blocked `run()` is
  not a new capability — it is a documented parameter of the task that runs the
  agent.
- **[C]** The transport has been exercised: `experiments/trace_synthesis/steered_rerun/supervisor.py`
  runs a host-side judge with a polling `Watcher` thread against a mounted
  `steer_io` directory, request files answered in place. **[M]** It worked over a
  27-boundary rollout, and its one failure is recorded rather than imagined: the
  polling thread died on a malformed reply at boundary 13 and every later
  boundary went unjudged
  ([`spec.md` §11](../spec.md#11-open-questions)).
- Files rather than sockets is not a preference either: the host firewall
  refuses inbound connections from the Docker bridge, which is why the steered
  re-run used a file drop in the first place. **Reuse it; do not write a second
  transport.**

**What would make (i) right instead:** a second consumer needing the same thing —
an interactive harness, or a second agent with a live channel. One consumer is
not a contract change; two is. ADR-0007 §8 already states this rule for the
neighbouring case ("if a prompt ever needs to arrive as a file the caller
already has, that is a second argument added *then* — not a generality paid for
now"), and this task is the same trade.

## 3. The question this design does not settle by itself

**A new harness class, or a parameter on the existing one?**

The honest statement of the trade:

| | New class (`ClaudeCodeSupervisedHarness`) | Flag on `ClaudeCodeHarness` |
| --- | --- | --- |
| Contract | Clean: the two really do differ — one takes a prompt, the other a prompt **plus a live correction channel** | Muddied: one class whose stdin is a file or a FIFO depending on a field |
| Cost | **[C]** 528 lines to fork, or a base class extracted from them — and every later fix to flags, denied tools, exit-status reporting or capture wiring has two homes | One `if` in `_invocation_script`, one extra mount, one extra field |
| Risk | Divergence: the supervised variant silently stops matching the unsupervised one, and the traces stop being comparable | A field that is meaningless in the common path, and a reader who has to hold both shapes at once |

**Recommendation: a field on the existing class**, for one reason that outweighs
the contract argument: **the two runs must stay byte-comparable.** The whole
value of this channel rests on a supervised rollout differing from an
unsupervised one *only* by the corrections; a fork is a standing invitation for
the two to drift in flags, denied tools or capture wiring, and that drift would
be invisible in the traces it produces. **The criterion for revisiting:** if the
supervised path ever needs a *different* invocation rather than an *extended*
one — different flags, a different output format, a different termination rule —
the classes have genuinely separated and should be separated in code.

## 4. Failure modes to design for, not to meet

Each is a decision the design owes an answer to. Statuses are what is known
today, not what the design will claim.

| Failure | Status | What the design must state |
| --- | --- | --- |
| The supervisor dies mid-run | **[M]** it happened once (steered re-run, boundary 13) | Whether the relay keeps the FIFO open (agent waits, run burns to timeout) or closes it (agent ends early). Neither is a default; pick one and say why |
| The FIFO's write end closes | **[M]** the process exits on stdin EOF | This is the intended **termination** mechanism, so it must be produced deliberately by whoever decides the task is over — and never as a side effect of a crash |
| The relay dies while the supervisor lives | **[U]** | Same question, one layer down; the relay is the only thing holding the write end |
| Reaping order of relay and proxy | **[C]** the proxy is reaped by an `EXIT` trap in the same script | Both are trapped, and the relay must not be reaped before the agent it feeds; state the order rather than inheriting it |
| The 10 MiB stdin cap | **[U]** per-line, per-process, or not applicable to `stream-json` | Write it down as an **assumption**, and name the one-line test that would settle it, rather than designing around a number nobody checked |
| A correction lands mid-tool-call vs between turns | **[M]** N=3 mid-call (queued, folded into a `role: system` `<system-reminder>`, absorbed with no turn of its own) and **[M]** N=25 at a boundary (an independent `user` record) | **Which one the design depends on**, explicitly. They are different context shapes, and only mid-turn was checked against the production TUI |
| `run_script` times out while the host supervisor is still alive | **[C]** the timeout is inside `run_script`; the supervisor is outside it | Who tears down the supervisor, and how it learns the run is gone |

## 5. Constraints this design inherits (already measured, not negotiable)

- **This channel requires proxy capture.** **[M]** Stream capture drops the
  injected message unless `--replay-user-messages` is passed, and *with* it
  renders as a `user` message what the wire carries as `system`; **the wire is
  the truth** ([report §14.5](../../../experiments/trace_synthesis/streamjson_input/REPORT.md),
  pinned into [`spec.md` §10](../spec.md#10-what-is-measured-about-hooks) by
  [#312](https://github.com/Luolc/swe-lab/pull/312)).
  A stream-derived trace would assert a user turn the model never saw.
- **The collector must exclude non-agent-loop requests.** **[M]** A TUI capture
  carries a prompt-suggestion exchange whose body is the whole conversation plus
  a user message nobody sent; taking the last proxy record picks exactly that
  one. Now an invariant in
  [`spec.md` §12](../spec.md#12-invariants-intended-enforced-where-marked).
- **[C] The `event_stream_outcome` defect ([task 12](README.md)) is not retired
  by this design, and its status changes.** That collector reduces a run to its
  *last* `result` event. This channel produces **several `result` events in one
  process** — one per correction-driven turn — where the file-fed path produces
  one. So the defect stops being specific to `--max-turns` segmentation and
  becomes a property of any supervised run: the run's outcome would be whichever
  ending the *last* turn had. **[I]** Task 12 is therefore a prerequisite of
  this task, not a neighbour of it.

## 6. What is still unmeasured, with the cheap test for each

Written so the next person does not mistake an assumption for a finding.

| Unknown | The test |
| --- | --- |
| Does `claude` exit on FIFO EOF the same way it does on a closed pipe? | One host run: `mkfifo`, start the agent against it, write a task, hold, then close — observe whether the process ends |
| Does the 10 MiB cap apply per line, per process, or not at all under `--input-format stream-json`? | One host run with a >10 MiB line, then the same bytes split across lines |
| Does the channel behave the same **inside the sandbox** — the pinned binary, the pinned `CLAUDE_CONFIG_DIR`, a container without the host user's `CLAUDE.md`? | Already registered as [task 13](README.md); every measurement so far is host-side, and the pinned binary is 2.1.212 against 2.1.257 on this machine |
| Does a mid-tool-call correction still fold identically when the tool is MCP rather than local Bash? | Registered as [task 15](README.md); unmeasured because the MCP servers here need auth |

## 7. What this task is not

- **Not an authorization.** The compliance gate is upstream of it.
- **Not a change to the attribution decision.** [`spec.md` §5](../spec.md#5-the-mechanism-decisions)
  stands; moving it takes a new ADR.
- **Not the supervisor's own design** — what it judges, how often, and on what
  evidence is the guidebook question ([task 05](README.md)), not the plumbing
  one.
