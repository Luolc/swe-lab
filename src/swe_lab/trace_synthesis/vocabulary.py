"""The names a supervised run is recorded under, on either runtime.

A leaf: it imports nothing of this package's own, and nothing that reaches
back into it. That is the whole reason it exists as a module. Both supervision
runtimes report under these names — the host one in
:mod:`~swe_lab.trace_synthesis.channel`, the in-sandbox wrapper via
:mod:`~swe_lab.trace_synthesis.native_supervision` — and the ``claude_code``
harness needs them too, so any home that also holds behavior puts the harness
and the runtime on a cycle through each other.

Keeping the vocabulary shared is deliberate and is not a parity claim: the two
runtimes diverge on what they *do* (#380, #381, #383). What a reader of a
finished run must not have to know is which of them produced it, so
``supervision.boundaries`` counts boundaries either way.
"""

from __future__ import annotations

#: How many actor events the supervisor was consulted about — one per row of
#: the account, which is what makes ``boundaries == 0`` read as "never
#: supervised" rather than "supervised and quiet".
#:
#: **Not the judge-call count.** A boundary whose evidence window is empty
#: consults no judge and is recorded as
#: :data:`~swe_lab.trace_synthesis.supervisor.LOG_KIND_UNJUDGED`, so for the
#: shipped policy this count is an *upper bound* on the calls a supervised run
#: pays for beyond the actor. The gap is those rows and is read off the
#: account, which is also the only place a `boundaries > 0, corrections == 0`
#: run says which of "judged and stayed silent" and "never judged" it was.
#: Counted separately from the actor because the repo's per-rollout cost figure
#: is the actor's alone.
BOUNDARIES_METRIC = "supervision.boundaries"

#: How many corrections were delivered. Never absent on a supervised run, so
#: "spoke zero times" is distinguishable from "was not supervised" — the
#: artifact below says which.
CORRECTIONS_METRIC = "supervision.corrections"

#: The supervisor's own account of a run, one JSON object per event consumed.
#: A reader checking that a run was supervised at all reads this artifact.
SUPERVISOR_LOG_NAME = "supervisor.jsonl"
