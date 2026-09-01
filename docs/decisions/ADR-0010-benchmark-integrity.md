# ADR-0010: Benchmark integrity — the environment is the control, the record is the proof

## Status

Accepted (amended 2026-08-06 — see the Amendment below)

## Date

2026-08-06

## Context

An agent that retrieves the reference fix instead of deriving it scores as if it
solved the task. Every number we produce is inflated by however often that
happens, and we currently do nothing to prevent it.

The evidence is in
[the integrity study](../reviews/2026-08-06-benchmark-integrity-study.md); this
ADR does not repeat it. Four findings drive the decisions:

1. **The network is the dominant vector.** Cursor measured 63% of successful
   SWE-Bench Pro resolutions as retrieved rather than derived — 57% by upstream
   lookup, only 9% by mining the bundled `.git`. Restricting access moved Opus
   4.8 Max from 87.1% to 73.0%.
2. **Restricting egress costs no legitimate capability.** reward-hack-bench
   measures the same 58% fair-solve rate under an open policy and under the
   policy that eliminates cheating entirely. We are not trading score for
   integrity.
3. **Prompting does not work.** Poolside reports steering yields "a measurable
   decrease but not an eradication"; detectors are "fundamentally limited by
   only catching the forms of reward hacks we know about".
4. **Some of it cannot be closed, only observed.** BenchJack reports SWE-bench
   Pro as poorly patch-resilient: the specific exploit dies, a re-scan finds a
   new path. A design that claims closure will be wrong; one that measures and
   records can stay honest.

Our own exposure (study §3): egress open, git history untouched
([#191](https://github.com/Luolc/swe-lab/issues/191)), and a planted
auto-loaded file rides the extracted patch into the evaluator. Three things are
already right and are worth protecting: the expectation
(`required_tests.json`) never enters the agent's sandbox, the evaluator is a
separate container whose only input channel is `patch.diff`, and an unparseable
result cannot score as a pass.

One constraint is ours specifically: **ADR-0001 diffs against `base_commit` and
the eval script resets to it.** Any sanitization that destroys that object
breaks extraction and grading. This rules out the simplest fix in the field
(Cursor's `rm -rf .git && git init`) outright.

## Decision

### 1. Integrity is a property of the *environment*, never of the agent

We do not ask the agent not to cheat, and we do not add anti-cheating text to
the prompt. Every control is something the environment makes impossible or
records as having happened. Steering is unmeasurable, unfalsifiable, and
changes what the benchmark measures — an agent told not to look at git history
is a different subject than one that cannot.

Corollary: a control that cannot be enforced must be **declared and refused**,
not silently skipped.

### 2. Integrity is *declared per entry*, alongside the sandbox

A workflow entry already declares its sandbox config (ADR-0007 as amended); an
integrity policy is the same kind of fact and is declared the same way. It is
not a global setting and not a harness option: the rollout entry and the
grading entry need **different** policies, and the difference is load-bearing —
the evaluator legitimately needs refs the agent must not have.

A backend that cannot honor a declared policy **raises**, exactly as
`ghjob` already does for `network=False`. A policy that silently degrades is
worse than no policy, because the numbers keep being reported.

### 3. Three controls, in measured priority order

**3a. Egress — default-deny, narrow allowlist.** The rollout entry stops running
with general network access. The agent needs exactly one destination — the model
API — and gets exactly that. Our PROXY capture already routes the agent's API
traffic through a host-side recorder we own; that chokepoint becomes the
*enforcement* point, not merely the recording point.

This is first because it is 57% of the measured problem and because our current
default (`network=True`) is the worst-measured policy in the field (100% cheat
rate under an open policy).

**3b. Git history — purge the future, keep the past.** Delete local branches and
remote-tracking refs; delete tags **whose commit postdates the base commit**;
remove remotes, `FETCH_HEAD`, `ORIG_HEAD`; expire the reflog; `gc --prune=now`
so unreferenced objects stop being readable by bare sha.

**Date-filtered, not blanket** — this is a deliberate divergence from #191's
proposed sequence. Past tags are legitimate research a human engineer would
have, some regression tasks are solvable only through them, and SWE-bench
Verified preserves them for exactly that reason. Matching upstream's rule also
keeps our numbers comparable with theirs.

The purge runs in `after_create` — the sandbox is up, the repo is present, the
agent has not started — contributed by an **observer the coding-agent task
supplies itself**, so no caller can forget it on one code path. Not in the
harness's invocation script: that would tie an environment property to one
harness and every other harness would need its own copy. (#191's placement
argument, adopted as stated.)

**3c. Verifier tampering — detect and record; do not block.** The agent's only
channel into the evaluator is `patch.diff`, and that patch legitimately may add
files. We therefore do **not** add a rule forbidding new files. We detect
patches that introduce auto-loaded execution hooks — `conftest.py`,
`sitecustomize.py`, `.pth` files, plugin entry points in packaging metadata —
and record that fact on the run. A flagged run is reported, not silently
dropped: whether a given hook file is an exploit or a legitimate test helper is
a judgment, and the framework's job is to surface it, not to guess.

### 4. Every control asserts, and a failed assertion fails the *attempt* — loudly

A sanitization step that silently stops working is worse than none, because the
numbers keep being reported. So each control verifies its own postcondition:

- the solution commit is **unreachable** (`git cat-file -e` must fail);
- the base commit is **reachable** (must succeed — ADR-0001 depends on it);
- no commit reachable from any ref postdates HEAD (catches leaks whose sha we do
  not know in advance — a future commit that is not *the* fix still leaks the
  direction of one).

This inverts the usual rule for setup steps, deliberately: **a contaminated
result is worse than a missing one.**

"Loudly" means a distinct, recorded outcome — **not** a crash that loses the
record. ADR-0009 and [#188](https://github.com/Luolc/swe-lab/issues/188)
settled that the record is always written and an incomplete record beats no
record; that holds here. An integrity failure is a *failed attempt with a named
reason*, never a success and never a missing shard.

### 5. Every record carries the policy that produced it

The record stamps the integrity policy in force. Runs produced under different
policies are **not comparable**, and the framework must not pool them silently —
aggregation across differing stamps is an error, not a warning.

This is the decision that keeps the *existing* numbers honest rather than
retroactively wrong. Everything in `outputs/` today was produced with no
controls at all; it needs to be readable as such forever, not quietly averaged
with post-fix runs.

### 6. Post-hoc trace audit, as defence in depth

We keep full traces. A detector over them — allowlist-shaped, after SWE-bench's
`git_peek_suspicious_commits.py`, since the same git commands are legitimate
research — reports suspected retrieval as a **metric on the run**, never as a
gate. It catches what the environment controls missed, which is the class
BenchJack says will always be non-empty.

## Alternatives Considered

**Tell the agent not to cheat (prompt steering).** Rejected. Measured as
reducing but not eradicating; unfalsifiable; and it changes the subject under
test. Retained only as a *reporting* obligation: if a prompt ever does say
something on this, it is part of the declared policy stamp.

**`rm -rf .git && git init`, restore history at scoring (Cursor's approach).**
Rejected — **incompatible with our design**, not merely heavier. ADR-0001 facet
1 diffs against `base_commit` and the eval script resets to it; re-initializing
destroys that object. It also removes legitimate past history, changing what the
benchmark measures.

**Delete every ref, as #191 proposes.** Rejected in favour of date-filtering
(§3b). Blanket deletion takes past tags with it, breaking regression tasks and
diverging from upstream's rule for no benefit.

**Rebuild the images with the fix baked in.** Rejected as the primary control.
[SWE-bench_Pro-os#75](https://github.com/scaleapi/SWE-bench_Pro-os/issues/75)
reports the Docker Hub images do not match the repo Dockerfiles, so a rebuild is
not the same artifact as the published image — we would be measuring a different
environment than everyone else, while `docker pull` continues to hand any other
consumer the exposed one. Run-time sanitization works on the images as
published. (Nothing stops us adopting a fixed upstream image later; the observer
becomes a no-op whose assertions still pass, which is the desired end state.)

**An LLM judge on outgoing requests.** The best-measured policy — 0% cheat at no
fair-solve cost — and *not* rejected, only deferred. It is a heavier
dependency (a model call per request) than a default-deny allowlist, and the
allowlist captures most of the benefit at a fraction of the cost. This is the
documented escalation path if allowlist evasion is ever observed.

**Block patches that add new files.** Rejected: it breaks legitimate solutions.
Detection (§3c) instead.

**Accept and disclose.** Rejected as a resting place, but adopted as the
*interim* state: until the controls land, our numbers carry a known unquantified
inflation, and §5's stamp is what makes that statement durable rather than a
footnote someone loses.

## Consequences

- **Old and new numbers are not comparable, and the framework will say so.**
  Everything produced before these controls is contaminated by an unmeasured
  amount. Per §5 they cannot be pooled. This is a real cost and it is the
  correct one — the alternative is a silently wrong series.
- **Some runs will now fail that previously "succeeded".** By design (§4). A
  purge assertion failure means we could not guarantee a clean environment; the
  attempt is worth losing.
- **`ghjob` cannot honor a deny-egress policy** and will refuse it, as it
  already refuses `network=False`. Rollouts needing strict egress are
  host-backend only until that changes.
- **`gc --prune=now` costs setup time per rollout** on large repos, paid once
  per sandbox before the agent starts.
- **The controls are not a proof of cleanliness.** BenchJack's patch-resilience
  finding says a re-scan finds new paths; §3c and §6 are shaped as detection
  precisely because closure cannot be claimed. We should expect to add vectors
  to this list, and the policy stamp is what makes that survivable.
- **Nothing here has been run against a real image yet** (study §3.5). The git
  sequences are reasoned from git's reachability rules and matched against three
  reference implementations; packed refs, `alternates` and any second copy of
  the repo in the image remain unverified. First implementation task is
  empirical validation, and the assertions in §4 are what make that validation
  ongoing rather than one-off. *(Done — see the amendment below.)*

## Amendment (2026-08-06): priority, and egress is configuration not construction

The decisions above stand. What changes is the **ordering** in §3 and how much
of §3a is ours to build.

**Egress (§3a) drops from first to already-handled.** The 57% figure is not in
doubt, but the control is a setting rather than a project for this repo:

- **Docker backend:** set `network=False` on the rollout entry. It already
  exists and is already honored (`--network none`). The agent's model API is
  reached through the recorder we already own, so nothing else has to change.
- **Downstream remote sandbox:** a custom egress rule via a host-side proxy is
  **already solved there** and is not the harness's job to re-implement.

So §3a is a configuration change plus the §5 stamp that records it, not a
control to design. It is *not* deprioritized as a risk — the risk is unchanged,
and task 25 §9 states that the history purge is only sound in combination with
it.

**Git history (§3b) is now P0**, and is designed in
[task 25](../horizontal/plans/task-25-git-history-purge.md). That plan is
empirically validated against five real images (four languages, including an
Alpine one), which closed the "not run against a real image" consequence above
and found two defects in the reference implementations we would otherwise have
inherited: a batch ref delete that **aborts on the `origin/HEAD` symref**,
purging nothing, and an assertion whose `date -d` is **GNU-only** and breaks on
the Alpine images this dataset actually ships. Both are fixed there.

**Verifier tampering (§3c) becomes a post-rollout verifier task, at P1.** Two
things reduce its urgency, and one keeps it on the list:

- the eval already `git reset --hard`s and restores the golden test files by
  path, so the common shape — editing the tests — is already dead;
- a planted `conftest.py` is a much rarer shape, and we have not observed one.

So **we build no control against it.** Instead a *verifier* runs as its own
workflow entry after the rollout — rule-based if that suffices, a small model
judge otherwise — reporting what it finds on the record. Detection, never a
gate, exactly as §3c already decided; what changes is that it is scheduled after
task 25 rather than alongside it, and that no blocking rule is contemplated at
all.

Unchanged: §1 (environment, not prompt), §2 (declared per entry, refused
loudly), §4 (every control asserts; a failure is a recorded failed attempt),
§5 (policy stamp; no pooling across policies), §6 (post-hoc trace audit).

## Amendment (2026-09-01): the egress chokepoint is the sandbox's network, not the capture proxy

§3a named the host-side PROXY recorder as the enforcement point for default-deny
egress ("that chokepoint becomes the *enforcement* point"). That is no longer
available, and on reflection was never sound.

[ADR-0012](ADR-0012-in-sandbox-capture-proxy.md) moves the capture proxy
**inside the sandbox**, because the host-side shape made a now-required
component depend on a host firewall rule, an unbounded index-derived port, and a
listener exposed to the whole tailnet — and cannot work at all on a backend
handed an already-running job. An in-sandbox proxy sits on the same side of the
boundary as the agent, which can kill it or ignore `ANTHROPIC_BASE_URL`.

So §3a's *goal* stands unchanged and still at P0 — the rollout entry should stop
running with general network access — while its *mechanism* moves to the
**backend's container network configuration**, which the agent is genuinely
outside of and which does not depend on whether the run happens to be recording
its traffic. A recorder is evidence, not a control; conflating the two was the
error.

**The new mechanism, concretely**, so this does not become a homeless P0. On
`DockerHostSandbox` the shape is: the backend creates a **per-run user-defined
Docker network** and attaches the container to it with **no route off the host**
(`docker network create --internal`), so the container's default route reaches
nothing; the one reachable peer on that network is a **host-side forwarding proxy
that allowlists the model API host**. Enforcement therefore lives in a process
the agent cannot kill and on a route it cannot go around — nothing else is
routable — while the *recording* proxy stays inside the sandbox. The two are
deliberately separate processes with separate jobs, which is the distinction §3a
originally collapsed. `network=False` (`--network none`) is the degenerate case
of the same control and is what the offline entries already use.

**This is intended, not enforced. There is no test, and there is no code.**
What exists today is a boolean: `SandboxConfig.network`, realized as
`--network none` or nothing at all. `definitions.py` passes `network=True` for
both `rollout` and `unit_test`, so the shipped solving path runs with **general**
network access; only `git_integrity_audit` passes `network=False`. Read every
"default-deny egress" sentence in §3a as a statement of intent about a control
that does not exist yet.

Enforcement is also **per-backend, and one backend cannot do it at all**:
`build_sandbox` refuses `network=False` on `ghjob` outright ("backend 'ghjob'
cannot honor network=False") because the job container is already live when we
are handed it. So §3a, once built, will be a control the A-host backend asserts
and the A-ghjob backend cannot — which §2's per-entry declaration must surface
rather than silently skip.

**The first test that would let this claim be stated as enforced** asserts that a
rollout entry's container cannot open a connection to a host outside the
allowlist, from inside the sandbox, and that the attempt is recorded. Until such
a test exists, §3a stays a plan.

**Nothing implemented changes with this amendment.** §3a was unbuilt before it
and is unbuilt after it; rollout ran `network=True` before and runs `network=True`
after. No control is weakened — what changes is *where* the control will be built,
and that the gap between the plan and the code is now written down instead of
implied.

**A correction, not a settlement.** An earlier draft of this amendment said
proxy capture's in-workspace log was "parity with `STREAM` capture" and therefore
not a new integrity surface. That was wrong on the facts and is corrected in
[ADR-0012](ADR-0012-in-sandbox-capture-proxy.md) §4: the old `ProxyRecorder`
kept its log on the *host* until `before_destroy`, so the agent never saw it
while running, and a proxy record carries HTTP headers that no stream event has.
A raw capture contains the request's `Authorization` credential and the
operator's organization / workspace identity.

For **this** ADR's purpose the distinction still holds — the record *bodies* are
the agent's own conversation, so no §3 control is weakened and no run becomes
more cheatable — but that is a statement about benchmark integrity only, and it
must not be read as clearing the change. The credential and operator-identity
exposure is real, it is **new**, and it is closed by redacting sensitive headers
**at write time**, so an unredacted capture never reaches a collected artifact.
That obligation is
[trace-synthesis task 09](../trace-synthesis/plans/README.md#task-09-redact-the-production-proxy-capture);
until it holds, no proxy-captured trace may be published.

Unchanged: §1, §2, §3b, §3c, §4, §5, §6, and the 2026-08-06 amendment.
