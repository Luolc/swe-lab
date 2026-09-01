# Task 35: Container and process lifecycle ownership

**Status lives in [`plans/README.md`](README.md).** This is the design.

## The defect is not missing cleanup

Three leaks appeared on one machine on 2026-09-01, and every one of them had
cleanup code that was correct and did not run:

| # | What leaked | How it died |
|---|---|---|
| ① | a host `cc-reverse-proxy` still holding its port | its supervisor was killed; the child was reparented to `init` |
| ② | `jovial_ellis`, `Created` and never started | the creator raised between `docker create` and the assignment that made the handle reclaimable |
| ③ | a `sleep infinity` producer container, alive 45 minutes | the process holding its lifetime died; nothing else knew the container existed |

Writing three more `finally` blocks addresses none of them, because in all
three the `finally` either had nothing to reclaim yet, or never ran at all. The
common structure is that **no party held the lifetime**:

- **Ownership began at success rather than at creation.** A resource exists from
  the moment the create call returns, and in two of these three that window —
  between "it exists" and "someone is registered to remove it" — is where it
  leaked.
- **A resource that outlives its owner cannot say whose it was.** Leak ③ was
  seen by three agents in turn; each correctly refused to remove a container it
  could not prove was its own, because the labels name a repo and an instance
  and nothing about the run or the process that created it. It was finally
  removed by the workspace's coordinator, on authority rather than on evidence.
  **Undecidable ownership is why "everybody leaves it" is the right response and
  still the wrong outcome.**

## The principle

**Bind a reclaimer at the point of creation, and stamp identity there too.** The
reclaimer makes the normal death safe; the stamp makes the abnormal death
recoverable. Neither substitutes for the other: a process that is SIGKILLed runs
no reclaimer, and a stamp reclaims nothing on its own.

The shape already exists in this repo. `GitHubJobSandbox` starts its child with
`start_new_session=True` and kills the **process group** on timeout
([`ghjob.py`](../../../src/swe_lab/sandbox/backends/ghjob.py), ADR-0012 §3), so
an orphan cannot outlive it. That is the model, generalized rather than repeated.

## Design

Four parts, in dependency order. **A and B are independent of each other and
share files; C is independent of both; D depends on B.**

### A. Ownership starts at creation, not at start

Two guards today are both keyed on *up*, and a container created but not started
passes neither:

- [`host.py`](../../../src/swe_lab/sandbox/backends/host.py) — `up()` assigns
  `self._container = handle` **after** `docker start` returns, so a `start` that
  raises (rather than returning non-zero) leaves `down()` with nothing to
  remove: it early-returns on an empty handle.
- [`manager.py`](../../../src/swe_lab/sandbox/manager.py) — `live = True` is set
  after `sb.up()` returns, and `_teardown` early-returns `if not live`. So the
  same failure skips teardown entirely.

**Change:** record the handle the instant `docker create` returns, before
anything that can fail; and have the manager track *created* rather than *up*,
so teardown runs whenever a resource may exist. `down()` stays best-effort and
still never raises.

**Tests:** `docker create` succeeds and `start` raises → the container is
removed; the manager tears down a sandbox whose `up()` raised after creation.

### B. Every resource names its owner

Add three labels at create time beside the existing `swe-lab` / `swe-lab-instance`:

| label | value |
|---|---|
| `swe-lab-owner-pid` | the creating process's pid |
| `swe-lab-owner-session` | a uuid4 generated once per process |
| `swe-lab-created-at` | RFC 3339, UTC |

Nothing behavioural depends on them; they exist so that a survivor can be
attributed. The pid alone is not enough — pids are reused, and a pytest session
and its subprocesses do not share one — which is what the session id is for.
**That reasoning goes in the code beside the label**, or the next reader deletes
the session id as redundant with the pid.

**Test:** the create argv carries all three, and the session id is stable within
a process and different across processes.

### C. A process reclaimer that survives its parent

`ReverseProxy.__enter__`
([`host_proxy.py`](../../../src/swe_lab/pipelines/related_files/host_proxy.py))
starts the proxy with `subprocess.Popen` and **no** `start_new_session`, and
`__exit__` terminates only the direct child. Kill the supervisor and the proxy
is reparented and keeps its port — leak ①, and the same defect ADR-0012 §3 fixed
for `ghjob`.

**Change:** lift ghjob's session-plus-group-kill into one shared helper and use
it from both. One implementation, two call sites, no third copy.

**Test:** the started child leads its own process group; on exit the group is
signalled, not just the child.

### D. Reaping what a dead owner left

B makes the question decidable; D answers it. One code path, two entry points:

- **`swe-lab containers reap`** — lists every container labelled `swe-lab=1`
  whose `swe-lab-owner-pid` is not alive, with its age and session id. It
  **removes nothing by default**; `--force` removes exactly the listed set.
- **A session-scoped pytest fixture** that, at session end, removes containers
  carrying **this session's** id and **fails the session** if it removed any.

Two rules that make this safe and honest:

1. **Never remove a container whose owner is alive, or whose owner is unknown.**
   Today's standoff was the correct behaviour and must stay possible.
2. **A leak that is silently cleaned is a leak that repeats.** The test fixture
   reports rather than tidies: today's three leaks were visible only because
   nobody swept them.

**Rule 2 is not new here — it is this project's third independent arrival at the
same principle**, in three unrelated domains, and it is worth saying so rather
than presenting it as a fresh idea:

- **Task screening**: *annotate, never suppress*. "A reader given the annotation
  can reproduce suppression exactly, by ignoring the annotated alarms; a reader
  given suppression cannot recover what was never printed"
  ([screening report](../../../experiments/trace_synthesis/instance_screening/REPORT.md)).
- **[ADR-0010](../../decisions/ADR-0010-benchmark-integrity.md) §3c / §6**:
  *detection, never a gate* — "shaped as detection precisely because closure
  cannot be claimed".
- **Here**: report the leak, do not quietly reclaim it.

The shared shape is that **the irreversible direction is the optimistic one**:
suppressing an alarm, gating on an incomplete check, or sweeping a leak each
destroys the evidence that the thing happened, and each is invisible afterwards
— nobody audits an alarm that never fired or a container that was already gone.

Three independent arrivals is past coincidence, so the principle probably wants
a canonical home. **That proposal belongs in D's PR, not here**, and it is a
proposal for exactly one home — a fourth copy of the same rule is the failure
[`doc-map.md`](../../doc-map.md) exists to prevent. What this design commits to
is applying it, not owning it.

## Out of scope

- A global GC or a cron sweeper. Everything here is triggered by a process that
  either owns the resource or is explicitly asked to look.
- Reclaiming resources this repo did not create. The `swe-lab` label is the
  boundary.
- The in-sandbox capture proxy ([ADR-0012](../../decisions/ADR-0012-in-sandbox-capture-proxy.md)),
  which has no host process to orphan and needs nothing here.

## Verification

Each invariant gets a named test; none of them needs Docker except where marked:

| invariant | test |
|---|---|
| a created-but-unstarted container is removed | `create` ok + `start` raises → `rm` issued (fake docker) |
| the manager tears down after a failed `up` | manager with a backend whose `up` raises post-create |
| a resource names its owner | create argv carries pid, session, timestamp |
| the proxy child leads its own group | `start_new_session` asserted; exit signals the group |
| the reaper never touches a live owner | listing with a live pid yields nothing to remove |
| a leaked container fails the test session | fixture finds a stamped container and fails |

## Delivery

Three PRs, split by dependency and not by category:

1. **A + B** — same two files, no dependency between them, one review.
2. **C** — the shared process-group helper and its two call sites.
3. **D** — the reap command and the pytest fixture; needs B's labels.
