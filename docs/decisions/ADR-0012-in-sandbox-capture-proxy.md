# ADR-0012: The capture proxy runs inside the sandbox

## Status

Accepted. Amends [ADR-0010](ADR-0010-benchmark-integrity.md) §3a, whose egress
control named the host-side proxy as its enforcement point.

## Date

2026-09-01

## Context

`ClaudeCodeHarness(capture="proxy")` records a run's API traffic with
`cc-reverse-proxy`. Until now that proxy was a **host** process: one per run,
listening on a host port, dialled from inside the container over Docker's
`host.docker.internal` gateway.

Proxy capture stopped being a preference and became a **requirement**. On the
OpenRouter path the proxy performs two injections Claude Code cannot make
itself: it mirrors `Anthropic-Beta` into `X-Anthropic-Beta` (without which
interleaved thinking silently does nothing) and it injects provider preferences
carrying `require_parameters: true`. A run that needs either of those needs the
proxy, and the trace is a by-product rather than the reason.

That left a required component resting on three fragile things:

1. **A host firewall rule.** The container reaches the host over the Docker
   bridge, so `20000:20999` and `25000:25999` had to be opened to
   `172.17.0.0/16` on the host. Until that landed, experiments were blocked
   outright for a full round — a machine-configuration change gating an
   experiment.
2. **Ports derived from a dataset index, with no ceiling.** `port_for_index`
   was `base_port + index`, with a second, independent base for the aggregator;
   nothing checked the two ranges against each other or against the ephemeral
   range. The engine path was worse: `ClaudeCodeHarness.proxy_port` was a single
   default whose own docstring said "two runs on one host must not share one",
   so **concurrent rollouts required hand-assigning a port per run**.
3. **A listener on every interface.** `cc-reverse-proxy` binds `:port`, and the
   host's `base` role already allows all inbound on `tailscale0`. Those ports
   were therefore reachable from any node on the tailnet — not something the
   firewall change introduced, but something it made routine.

There is also a backend the host-side shape simply cannot serve: a
`GitHubJobSandbox` is handed a job that is **already running**, and a host-side
process has nowhere to live in it.

## Decision

**The proxy runs inside the sandbox, as a declared asset the invocation script
starts and reaps.**

### 1. The binary travels the existing asset seam

`cc-reverse-proxy` is a single-file, standard-library-only Go program, so it
cross-compiles to a static `linux/amd64` binary (`CGO_ENABLED=0`) in one
command. That is exactly the shape of the pinned Claude Code binary, so it
takes the same road: the harness *declares* an `AgentAsset` at
`PROXY_BINARY_AT` and each backend places it the way that backend can
(`MountedAssetsObserver` for a container, `InstalledAssetsObserver` for a job).
Neither the backend nor the asset seam learned anything new.

The asset's "release" is the **sha256 of the Go source**, because there is no
version string to pin. That is not decoration: the cache path contains it, so
editing the source cannot silently reuse a stale binary the way the old fixed
cache path could.

### 2. The port is a constant, and that is the point

Each sandbox has its own network namespace, so `PROXY_PORT` is private to one
run. It cannot collide with another run, with the host, or with anything else
on the machine. `port_for_index`, both base ports, `proxy_port` and
`proxy_base_url` are gone from the engine path; the agent dials
`http://127.0.0.1:<PROXY_PORT>`.

The host binds nothing, so **no firewall rule exists to need** and the tailnet
exposure of the rollout path disappears. `--add-host` comes off every container
with it: nothing in a container dials the host anymore.

(The W1 annotation pipeline keeps a host-side proxy, in
`pipelines/related_files/host_proxy.py`. It is not an exception to this ADR:
that pipeline runs its agent as a host subprocess too, so agent and proxy share
one loopback and there is no boundary to cross.)

### 3. Lifecycle belongs to the invocation script, not to an observer

The script starts the proxy in the background, **polls** the loopback port
until it answers (never a fixed sleep, which is either a wasted second on every
run or a race on a loaded box), and installs an `EXIT` trap that kills and
`wait`s for it. So the proxy is gone and its log complete before `run` returns
— which is why the `ProxyRecorder` observer is deleted rather than rewritten:
it existed only to order a host process against the sandbox lifecycle, and
there is no longer an ordering problem to solve.

A readiness failure is loud (`exit 78`), because the alternative — the agent's
first API call refused — reads as an auth or network problem and costs a run to
diagnose.

**A killed run yields partial capture, never a corrupt file.**
`cc-reverse-proxy` appends one JSON record per completed exchange and closes the
file each time, so the log truncates at a line boundary and every line already
written is a complete record. "Killed" therefore means *some* of the data, not
*bad* data.

**That the proxy actually dies is a per-backend fact, and it was not true on
both.** On `DockerHostSandbox` the timeout kills the `docker` client, the trap
never runs, and `down`'s `docker rm -f` takes the container and every process in
it — so the guarantee holds by construction. On `GitHubJobSandbox` it did not:
`down` is a genuine no-op (the job *is* the container), so a backgrounded proxy
survived the run — and worse, `subprocess.run`'s post-timeout drain blocked on
the stderr pipe that proxy inherited, so a timed-out capture **hung** instead of
timing out. That backend now runs each command in its own process group and ends
the group on timeout, pinned by
`test_a_timed_out_script_takes_its_background_children_with_it`, which fails
(a 120-second block) without it. The claim is stated here because a test holds
it up, not the other way round.

### 4. The log lands in the workspace — a new exposure surface

**This is the part an earlier draft of this ADR got wrong, and the correction is
recorded rather than quietly applied**, because the wrong version was reviewed
and approved before the error was caught.

That draft argued the in-workspace log was "parity, not new exposure", on the
grounds that `claude.event_stream.jsonl` already sits in the same workspace
carrying the agent's own conversation. **Both halves of that are false:**

- **The old path was not in the sandbox at all.** `ProxyRecorder` wrote to a
  *host* temp directory and only landed the log in the workspace at
  `before_destroy` — after the agent was finished. The agent could never see it
  while it ran. The proxy now creates the log in `$SANDBOX_WORKSPACE` *before*
  the agent starts. That is not parity; it is a surface that did not exist.
- **The contents are not equivalent either.** A proxy record is an HTTP
  exchange and a stream event is not: the log carries headers, and a stream
  trace has none anywhere.

Measured on a real captured run rather than reasoned about, one record's headers
are:

- **request** — `Authorization`, plus `Anthropic-Beta`, `User-Agent`,
  `X-Claude-Code-Session-Id` and the `X-Stainless-*` client-telemetry set;
- **response** — `Anthropic-Organization-Id`, `Anthropic-Workspace-Id`,
  `Request-Id`, and the `Anthropic-Ratelimit-Unified-*` quota/utilization set.

So a raw proxy log contains **a live credential** and **the operator's account
identity**, and the change as first written put that file in a directory the
agent reads and writes, and registered it as a collected run artifact.

**The rule, therefore: sensitive headers are redacted at write time, and an
unredacted capture never reaches a collected artifact.** Redaction after the
fact is not equivalent and is not accepted here — it leaves a window in which
the raw file exists on disk, which is the whole objection.

Write time is now *inside* `cc-reverse-proxy`, since the proxy writes to the
workspace with no intermediary of ours, so that is where the fix went
([cc-reverse-proxy#1](https://github.com/Luolc/cc-reverse-proxy/pull/1)). It
masks four values as it records each exchange — the request's `Authorization`
and `X-Api-Key`, the response's `Anthropic-Organization-Id` and
`Anthropic-Workspace-Id` — and **redaction is the default**, with an explicit
`--keep-sensitive-headers` to turn it off. A safe behaviour behind an opt-in
flag would be a default that depends on every future caller remembering, which
is not a default. Values are **masked, not dropped**: the header name survives
with `[REDACTED]`, so "no credential was sent" stays distinguishable from "one
was sent and hidden".

What is deliberately *not* redacted is load-bearing too. `X-Claude-Code-Session-Id`
is an identifier, not a credential, and a session id is one leg of reconciling a
run against its trace — masking it would break that silently. `Request-Id`,
`Anthropic-Beta` and the `Anthropic-Ratelimit-*` family are telemetry and
protocol. Both repos assert this direction explicitly, so a later "mask a few
more while I'm here" cannot land unnoticed.

This pulls forward the core of
[trace-synthesis task 09](../trace-synthesis/plans/README.md#task-09-redact-the-production-proxy-capture),
whose scope narrows to what is left: the publishing gate and the wider
PII sweep, not the header redaction itself.

**The acceptance criterion is stated at the artifact, not at the agent.** An
earlier version of it read "the agent cannot read the credential", and that is
unverifiable in this topology — the agent runs as root in the container, so no
file in the sandbox is beyond its reach, and an invariant nobody can write a
test for is a wish. The testable statement, and the one that matches what is
actually being protected:

> **No record of a proxy capture contains a credential value or an operator
> identifier.**

That is checked from this side by
`swe_lab.harnesses.claude_code.redaction.unredacted_headers`, because the proxy
is an external, separately versioned binary and "the build we ran redacts" is
exactly the assumption that quietly stops holding. `tests/test_proxy_redaction.py`
covers both directions — a credential on the request and account identity on the
response — and both carriers: headers, and the account identifier Claude Code
sends in the request body.

Run against the **real** pre-fix rollout log, it reports **65 findings** over 13
records: four request headers, two response headers, the representative claim,
and `metadata.user_id`. (An earlier header-only version of this check reported
39 on the same file. The two numbers measure different scopes and are not
comparable; the gap is the body field and the representative claim, both added
after review.)

**Verified against both real upstreams.** One real request each was sent
through the merged binary — to `api.anthropic.com` (the `ROLLOUT` path) and to
OpenRouter — and each resulting capture scans clean. On the Anthropic path the
three identity headers were present **and masked**
(`Anthropic-Organization-Id`, `Anthropic-Workspace-Id`,
`Anthropic-Ratelimit-Unified-Representative-Claim`), as was `Authorization`, and
the body's `metadata.user_id`; the session id and the rate-limit telemetry
survived verbatim. Both probe captures were destroyed after scanning and neither
is committed: what is kept is the *record* that the criterion held, not the file.

The residual risk this closed was specific — *a sensitive header the real
upstream sends that never appeared in our sampled inventory* — and its
independent variable is **which upstream**, not how many records. That is why
one request per upstream settles it and a second rollout would not have.

**It found one.** The OpenRouter response carried a `Set-Cookie` (a Cloudflare
`__cf_bm`) recorded verbatim, on a path whose request-side `Cookie` was already
masked. Masking one half of that pair protects nothing, since `Set-Cookie` is
the value that becomes the cookie; it is redacted as of
[cc-reverse-proxy#2](https://github.com/Luolc/cc-reverse-proxy/pull/2) and the
scanner matches. It is a **consistency** fix, not a disclosure one:
`__cf_bm` is bot-management state, neither a credential nor an operator
identifier, so the criterion held on that capture and still holds. Rewriting a
truthful pass as a failure because a nearby improvement was found would make the
criterion mean less, not more.

Two further notes, so the record is complete rather than reassuring:

- **The credential is a duplicate, not a first disclosure — and that is a
  mitigating fact, not a defence.** Both supported auth modes already export the
  same credential into the agent's own environment (the shipped `rollout`
  definition passes `CLAUDE_CODE_OAUTH_TOKEN` through the sandbox's `pass_env`,
  a container-wide `-e`; `--bare` mode's own guard *requires*
  `ANTHROPIC_API_KEY`). So the agent gains no capability it lacked. What it does
  gain is a credential sitting in a **file that gets collected**, which has a far
  larger blast radius than an environment variable, and that is the reason this
  is fixed rather than argued away.
- **File permissions cannot substitute for redaction here.** The agent runs as
  root inside the container — the capture log from a real run is root-owned
  because the same invocation script starts both the proxy and the agent — so no
  path change, mode change or ownership trick makes anything in the sandbox
  unreadable to it. Only keeping the data out of the file works.

On the axis this ADR's predecessor cares about, **benchmark integrity is not
affected**: the record *bodies* are the agent's own conversation, and none of
the header material helps an agent solve its instance. That is why this is a
credential-and-PII defect rather than an [ADR-0010](ADR-0010-benchmark-integrity.md)
§3 control failure — but it is a defect either way, and it is not parity.

- **The proxy is not a control.** An agent that wanted to could kill it or
  ignore `ANTHROPIC_BASE_URL`. See §5.
- **The proxy *binary* is a new agent-reachable surface**, mounted `r-xr-xr-x`,
  so the agent can run its own forwarding proxy. Known and accepted: it hands
  the agent no answer, and under today's `network=True` it adds a tool, not a
  capability. Under the default-deny policy of §5 it stays constrained by the
  network layer, to which its own traffic is equally subject.

### 5. The egress chokepoint moves to the sandbox's network, not the proxy

[ADR-0010](ADR-0010-benchmark-integrity.md) §3a proposed default-deny egress
and named the host-side proxy as the enforcement point: "our PROXY capture
already routes the agent's API traffic through a host-side recorder we own;
that chokepoint becomes the *enforcement* point". An in-sandbox proxy **cannot
be that**, because it lives on the same side of the boundary as the agent.

This ADR does not weaken any control that exists — §3a is unimplemented and
rollout still runs `network=True`, under which a host-side proxy was never an
enforcement point either (with unrestricted egress the agent can bypass any
proxy by dialling the API directly). What it does is settle *where* enforcement
will go when §3a is built: in the **backend's container network configuration**,
which is the only layer the agent is actually outside of. That is a better place
regardless — an enforcement point the agent can `kill(1)` was never one — and it
decouples the control from whether a run happens to be recording.

Concretely, on `DockerHostSandbox`: a per-run `--internal` Docker network with no
route off the host, whose single reachable peer is a host-side **forwarding**
proxy allowlisting the model API host. Enforcement then sits in a process the
agent cannot kill, on a route it cannot go around, while the *recording* proxy
stays inside the sandbox — two processes, two jobs, which is the distinction §3a
collapsed. `--network none` is the degenerate case of the same control.

**None of that is built, and this ADR builds none of it.** Today
`SandboxConfig.network` is a boolean, `definitions.py` passes `network=True` for
both `rollout` and `unit_test`, and `build_sandbox` refuses `network=False` on
`ghjob` outright — so the control is *intended*, not enforced, and no test
asserts it. ADR-0010's dated amendment carries the full statement, including what
the first test would have to assert before the claim may be stated as enforced.

## Alternatives Considered

**Keep the proxy host-side and allocate ports properly.** Rejected. It fixes the
weakest of the three problems (collisions) and neither of the others: the
firewall rule and the tailnet exposure are properties of *any* host listener,
and a `GitHubJobSandbox` still has nowhere to put the process.

**Publish the container's port to the host instead of dialling out.** Rejected
for the same reason, inverted: it replaces one host-side listener with another
and re-introduces host port allocation.

**Ship the proxy binary in the instance images.** Rejected. ~731 images, and it
is the same argument that keeps the agent binary out of them: bake nothing that
can be handed over at run time.

**Vendor `cc-reverse-proxy` into this repo.** Rejected — it is the user's
standalone project and read-only to us. The sibling-checkout lookup plus
`CC_REVERSE_PROXY_SRC` stays as it was.

**Keep an observer for lifecycle, starting the proxy with a background
`docker exec`.** Rejected. It puts process management back on the host for a
process that no longer lives there, needs a backend-specific way to background
an exec, and buys nothing the script's `EXIT` trap does not already give.

## Consequences

- Proxy capture works with **no host firewall rule and no port-allocation
  scheme**. The two `ufw` rules opened for `172.17.0.0/16` can be removed, along
  with the bring-up check that asserted them.
- Concurrent rollouts with `capture="proxy"` no longer need per-run
  configuration. Nothing is left to collide.
- The rollout path binds nothing on the host, so its proxy is not reachable from
  the tailnet. **The W1 annotation pipeline still binds a host port** while a
  run is in flight; that is unchanged and out of this ADR's scope (the binding
  is `cc-reverse-proxy`'s, and that project is read-only to us).
- Proxy capture now needs a **Go toolchain on the host** at first use, as it
  did before — but cross-compiling, so a non-linux/amd64 host now produces a
  binary that actually runs in the container instead of an "exec format error".
- One new artifact: `claude.proxy.log`, the proxy's own output. An in-sandbox
  process that fails silently leaves no other evidence, and the failure modes
  are real (a missing CA bundle in an instance image would be invisible from the
  agent's side).
- `ProxyRecorder` and `CodingAgentTask.proxy_factory` are the two host-side
  recorder seams. The first is deleted here. The second was already unused
  before this change and is left alone rather than removed in the same PR.
