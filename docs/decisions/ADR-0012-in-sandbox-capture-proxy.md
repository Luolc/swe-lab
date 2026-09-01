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

**A killed run yields partial capture, never a corrupt file.** If the caller's
timeout fires, the trap never runs and the proxy dies with the container.
`cc-reverse-proxy` appends one JSON record per completed exchange and closes the
file each time, so the log truncates at a line boundary and every line already
written is a complete record. "Killed" therefore means *some* of the data, not
*bad* data.

### 4. The log lands in the workspace — parity, not new exposure

The proxy writes `claude.proxy.jsonl` straight into the workspace, so the agent
can read (and write) it. That is deliberate, and it is **parity with what
`STREAM` capture already does**: `claude.event_stream.jsonl` is redirected into
the same workspace on every stream run today, and it carries the same content —
the agent's own conversation. Nothing in the proxy log is knowledge the agent
does not already have about its own turn: not the golden patch, not the tests,
not future commits. On the axis [ADR-0010](ADR-0010-benchmark-integrity.md)
cares about, the two captures are equivalent.

Two things are *not* claimed by that argument, and are recorded here rather than
smoothed over:

- **Operator identity is a real delta.** The proxy records response headers
  verbatim, including `anthropic-organization-id` / `anthropic-workspace-id`,
  which the agent would not otherwise see. That is not a benchmark-integrity
  leak, and it is already a known defect on the *publishing* side —
  [trace-synthesis task 09](../trace-synthesis/plans/README.md#task-09-redact-the-production-proxy-capture)
  owns redacting it at write time. Moving the log in-container makes that task's
  fix an in-sandbox or post-read concern rather than a host-side one; it does
  not change what has to be redacted.
- **The proxy is not a control.** An agent that wanted to could kill it or
  ignore `ANTHROPIC_BASE_URL`. See §5.

### 5. The egress chokepoint moves to the sandbox's network, not the proxy

[ADR-0010](ADR-0010-benchmark-integrity.md) §3a proposed default-deny egress
and named the host-side proxy as the enforcement point: "our PROXY capture
already routes the agent's API traffic through a host-side recorder we own;
that chokepoint becomes the *enforcement* point". An in-sandbox proxy **cannot
be that**, because it lives on the same side of the boundary as the agent.

This ADR does not weaken any control that exists — §3a is unimplemented and
rollout still runs `network=True`, under which a host-side proxy was never an
enforcement point either. What it does is settle *where* enforcement will go
when §3a is built: in the **sandbox's network configuration** (the container's
own egress policy), which is the backend's business and the only layer the
agent is actually outside of. That is a better place regardless — an enforcement
point the agent can `kill(1)` was never one — and it decouples the control from
whether a run happens to be recording. ADR-0010 carries a dated amendment
saying so.

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
