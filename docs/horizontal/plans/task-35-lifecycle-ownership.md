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

Half the shape already exists in this repo. `GitHubJobSandbox` starts its child
with `start_new_session=True` and kills the **process group** on timeout
([`ghjob.py`](../../../src/swe_lab/sandbox/backends/ghjob.py), ADR-0012 §3) —
which reclaims the whole child tree **as long as the parent is alive to send the
signal**. That is the reclaimer half, and it is the half that does *not* cover
the death that produced leak ①. The stamp is what covers that one, and it only
pays off through something that runs later and looks: part D.

Stating which deaths each half covers is not pedantry — an earlier draft of this
design called C "a reclaimer that survives its parent", which is false for a
kill executed *by* the parent, and proposed a verification that only exercised
normal exit. A mechanism claim and a test that cannot fail against it travel
together.

## Design

Four parts, in dependency order. **A and B are independent of each other and
share files; C is independent of both; D depends on B.**

### A. Ownership starts at creation, not at start

[`host.py`](../../../src/swe_lab/sandbox/backends/host.py)'s `up()` assigns
`self._container = handle` **after** `docker start` returns, and `down()`
early-returns on an empty handle — so a `start` that *raises* (rather than
returning non-zero, which is already handled) leaves a created container with
nothing registered to remove it.

**Change, and it is local to the backend:** record the handle the instant
`docker create` returns, then wrap everything after it so any failure removes
the container before the error propagates. `up()` already owns cleanup of its
own partial state; this makes that ownership start where the resource does.

**Not changed: the manager.** An earlier draft had
[`manager.py`](../../../src/swe_lab/sandbox/manager.py) track *created* rather
than *up*, and that is unimplementable as written — the manager sees only
whether `sb.up()` returned, so "created" is not observable through the current
`Sandbox` contract. Making it observable is an **interface change** with
cross-backend semantics, and setting `live` earlier would be worse than the bug:
observer collection would run against a sandbox that never came up. The backend
keeps the responsibility it already has. If a manager-level defense is wanted
later, it starts with designing that observable state, not with moving a flag.

**Tests:** `docker create` succeeds and `start` raises → `docker rm` is issued
for the created handle; the existing non-zero-`start` path keeps its behaviour.

### B. Every resource names its owner

Add two labels at create time beside the existing `swe-lab` / `swe-lab-instance`:

| label | value |
|---|---|
| `swe-lab-owner-pid` | the creating process's pid |
| `swe-lab-owner-session` | a uuid4 generated once per process |

Nothing behavioural depends on them; they exist so that a survivor can be
attributed. The pid alone is not enough — pids are reused, and a pytest session
and its subprocesses do not share one — which is what the session id is for.
**That reasoning goes in the code beside the label**, or the next reader deletes
the session id as redundant with the pid.

No `created-at` label: Docker already records `Created`, and D can read it. A
repository-defined timestamp would be a second copy of a fact the daemon owns,
justified only by a requirement that does not exist yet.

**Test:** the create argv carries both labels, and the session id is stable
within a process and differs across processes.

### C. Kill the whole child tree — and say which deaths that covers

`ReverseProxy.__enter__`
([`host_proxy.py`](../../../src/swe_lab/pipelines/related_files/host_proxy.py))
starts the proxy with `subprocess.Popen` and **no** `start_new_session`, and
`__exit__` terminates only the direct child, so a proxy that spawned anything of
its own outlives the context. Lifting `ghjob`'s session-plus-group-kill into one
shared helper fixes that, in one implementation with two call sites.

**What it does not fix, and an earlier draft of this design claimed it did:
leak ①.** `start_new_session` + `killpg` is executed **by the parent**. When the
parent is killed — which is exactly how leak ① happened — nothing runs the kill,
and putting the child in its own session arguably detaches it further. Calling
this "a reclaimer that survives its parent" was wrong, and the verification I
first proposed only exercised normal exit, which is how the claim went
unchallenged.

So C is scoped to what it actually delivers: **the parent-alive deaths** —
normal exit, exception, timeout — for the whole child tree rather than one pid.
**The parent-death case is D's**, via the process half of the reaper below.
(A child-side `PR_SET_PDEATHSIG` would close it in-process, but the proxy is an
external binary this repo does not build, and `preexec_fn` is documented as
unsafe in the presence of threads; if that changes it becomes the better fix.)

**Tests:** the child leads its own process group; exiting the context signals
the **group**, not just the child. No test here asserts anything about owner
death — that belongs to D, where the mechanism is.

### D. Reaping what a dead owner left

B makes ownership decidable; D acts on it. **This is where leak ① and leak ③ are
actually closed**, because it is the only part that runs when the owner is gone.

Two kinds of resource, one principle — *the identity is stamped where the
resource is created*:

- **containers** carry it in their labels (B);
- **processes** cannot, so a spawn writes a small **spawn record** — owner
  session, owner pid, child pid, port, start time — under a known directory, and
  removes it on clean exit. A record whose owner is dead and whose child still
  lives is an orphan by construction; a record with no live child is stale and
  is deleted.

  **Writing that record has the same creation race A fixes for containers**, and
  the review was right that leak ① is not closed until it is spelled out here: a
  proxy exists the moment `Popen` returns, so a record written *after* the spawn
  leaves a window in which an orphan exists with nothing naming it — and rule 1
  below then correctly forbids the reaper from touching it. The ordering is
  therefore part of the design, not an implementation detail:

  1. **write an intent record before spawning** — owner session, owner pid,
     port, start time, and a unique run id — so the resource is named before it
     can exist;
  2. `Popen`;
  3. **atomically add the child pid** — write a sibling file and `rename`, never
     a partial in-place edit, so a reader sees one state or the other.

  That leaves exactly two incomplete states, and both are recoverable **without
  guessing**: an intent record whose port has no listener is stale and is
  deleted; an intent record whose port *does* have a listener, and whose owner
  is dead, names an orphan the reaper can end **by port** — the record
  identifies the resource even though the child pid was never written.

  **The limit, stated rather than papered over:** this closes leak ① for every
  proxy this repo spawns, because every one of them is recorded before it can
  exist. It does not let the reaper act on a listener that *no* record claims —
  a pre-existing process, or one spawned by code that does not write a record.
  That is an unknown owner, and rule 1 applies: report it, never kill it.

One code path, two entry points:

- **`swe-lab containers reap`** — lists every container labelled `swe-lab=1`,
  and every spawn record, whose owner is **provably not alive**. It removes
  nothing by default; `--force` removes exactly what it re-verifies.
- **A session-scoped pytest fixture** that, at session end, removes containers
  carrying **this session's** id and **fails the session** if it removed any.

Three rules, and the last two are corrections the review forced:

1. **Never reap a resource whose owner is alive, or whose owner is unknown.**
   An unreadable or malformed owner label is *unknown*, not *dead*. Today's
   standoff — three agents each refusing to remove a container they could not
   attribute — was the correct behaviour and must stay possible.

   **This is a rule about reaping *someone else's* resource, and the session
   fixture is not doing that** — the review caught the two reading as one. A
   process that removes what carries **its own session id** is exercising
   ownership, not reaping: it is the owner, it is necessarily alive, and that is
   precisely what entitles it to clean up. Two authorities, two predicates, and
   neither may borrow the other's:

   | path | may remove | owner liveness |
   |---|---|---|
   | `containers reap` | any session **but** this one | only when provably dead |
   | session fixture | **only** this session's id | its own, alive by construction |

   The fixture still reports what it removed and fails the session — rule 2.
2. **Report rather than tidy.** A leak that is silently cleaned is a leak that
   repeats; today's three were visible only because nobody swept them.
3. **`--force` re-checks immediately before each removal, per resource.** Owner
   liveness can change between listing and removing — pid reuse alone
   guarantees it — so a set computed once and deleted later cannot honour rule
   1. Each removal re-reads the labels and re-tests liveness, and skips and
   reports anything that is now live or unknown.

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
| a resource names its owner | create argv carries the pid and session labels; the session id is stable in-process and differs across processes |
| the proxy's whole child tree dies with the context | `start_new_session` asserted; exiting signals the **group** |
| an orphaned proxy is reclaimed after its owner dies | spawn the proxy, `SIGKILL` the owner, run the reaper, assert the port is released — the test that would have caught leak ① |
| an orphan created **before** its child pid was recorded is still reclaimed | kill the owner between the intent record and the update; the reaper ends the listener from the record's port |
| an incomplete record with no listener is deleted, not acted on | intent record whose port is free → removed, nothing signalled |
| a listener no record claims is never touched | a foreign process holding a recorded port is reported as unknown |
| the fixture removes only its **own** session | a container stamped with a different session id survives the fixture untouched |
| the reaper never touches a live owner | listing with a live pid yields nothing to remove |
| the reaper never touches an unknown owner | a container whose owner label is missing or malformed is reported, not removed |
| `--force` re-checks per resource | a resource that becomes live between listing and removal is skipped and reported |
| a leaked container fails the test session | fixture finds a stamped container and fails |

## Delivery

Three PRs, split by dependency and not by category:

1. **A + B** — same two files, no dependency between them, one review.
2. **C** — the shared process-group helper and its two call sites.
3. **D** — the reap command, the spawn records, and the pytest fixture; needs
   B's labels. **Leaks ① and ③ are closed here, not in C**, so this is the PR
   that carries the owner-death test.
