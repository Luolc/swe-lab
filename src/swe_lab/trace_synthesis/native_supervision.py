"""The Python half of the native supervision runtime's contract.

The supervisor of record is moving into the sandbox as a static binary that
wraps the actor as its child ([#375]; the crate is ``rust/swe-lab-supervisor``
and its design record is task 20 in `docs/trace-synthesis/plans/`). The binary
is one half of a two-language contract, and this module is the other. Three of
the run's **values** cross the boundary, and each is here:

- **the config document** — one schema-versioned JSON file of *non-secret* run
  settings, written into the workspace and named on the wrapper's ``--config``
  flag. The binary refuses an unknown field rather than ignoring it, so the
  document this module renders is the schema, not a superset of it;
- **two environment variables** — :data:`BASE_URL_ENV` and :data:`API_KEY_ENV`,
  which carry *where the model is* and *how to authenticate to it*. They are
  not in the document, and a document naming either is refused. They travel
  into the sandbox by reference (the backend's ``pass_env``), exactly as the
  actor's own credential does, so neither value reaches a command line, a
  staged file or any artifact;
- **the terminal summary** — what the wrapper writes at the end, and the only
  thing a run may be classified from. Not the exit status, in **either**
  direction: a wrapper that ran cleanly exits with the *actor's* status, so
  exit 0 says the actor was happy and says nothing about whether supervision
  happened — and a wrapper that could not account for the run exits ``1`` of
  its own accord, which says nothing about whether the actor succeeded. The
  exit status describes the wrapper's ability to give an account, not the
  actor's result.

The boundary carries one thing that is not a value and is not here: the
**actor's argv**, handed to the wrapper after ``--`` as opaque tokens. That one
belongs to the harness that knows how to invoke the actor
(``ClaudeCodeHarness.actor_argv``), and building it here would be exactly the
second construction of the actor's flags that #375 says the wrapper must not
have.

**The document is a hand-written mirror of ``config.rs``, and nothing checks
that mechanically.** The two agree today because they were read against each
other field by field; the check that would make that a property rather than a
claim — render here, parse with the binary's own parser, assert accepted, and
assert the deliberately broken ones refused — needs the binary as a runnable
artifact and is a follow-up (task 21 §7a). Until it exists, adding a field here
means reading ``config.rs`` again.

**Nothing here is wired.** This module renders a document, names two
variables, and reads a summary; declaring the binary as an asset, starting the
second capture-proxy instance that terminates TLS for it, handing over the
actor's argv and reporting the metrics below onto a record are the wiring's,
and land with it.

**This runtime is deliberately not the Python one's twin.** The owner's ruling
on [#375] is that the native runtime fixes the three defects the replay
experiment measured in the host runtime rather than reproducing them ([#380],
[#381], [#383]), so the two diverge on purpose: there are no parity fixtures,
and "agrees with Python" is not a property anything here asserts or should.

[#375]: https://github.com/Luolc/swe-lab/issues/375
[#380]: https://github.com/Luolc/swe-lab/issues/380
[#381]: https://github.com/Luolc/swe-lab/issues/381
[#383]: https://github.com/Luolc/swe-lab/issues/383
"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import enum
import json
import pathlib
from typing import Any

from swe_lab.rollout import SUPERVISION_LAPSE_METRIC, SUPERVISION_METRIC

from .channel import BOUNDARIES_METRIC, CORRECTIONS_METRIC
from .criterion import CRITERION_PATH, load_criterion

#: The one config schema the pinned binary reads. It refuses any other version
#: rather than reading what it recognizes, so this moves only together with the
#: binary — see ``rust/swe-lab-supervisor/src/config.rs``.
CONFIG_SCHEMA_VERSION = 1

#: The one policy the binary implements. Written into the document so that a
#: config meant for another policy is refused rather than reinterpreted.
POLICY_KIND = "speak-when-off-track"

#: The criterion's name in the config. Derived from the artifact's own filename
#: rather than written out again: the binary compiles in *this file's* bytes
#: (``include_str!``) and names the criterion after it, so the two agree
#: mechanically and a rename cannot leave one side pinning a name nothing has.
CRITERION_NAME = CRITERION_PATH.stem

#: Where the wrapper binary is placed in the sandbox — an executable asset at a
#: fixed absolute path, outside the workspace, exactly like the agent's own
#: binary and the capture proxy's: it is machinery, not the run's material.
SUPERVISOR_BINARY_AT = "/opt/swe-lab-supervisor/swe-lab-supervisor"

#: The workspace file the config document is written to and named on
#: ``--config``.
SUPERVISOR_CONFIG_NAME = "supervisor-config.json"

#: The workspace file the wrapper writes its terminal summary to, atomically,
#: at the end of the run. Absent means the wrapper did not reach its own
#: ending — which is a classification, not an accident to be tolerated.
SUPERVISOR_SUMMARY_NAME = "supervisor-summary.json"

#: The environment variable naming the base URL of an OpenAI-shaped
#: chat-completions API, ``http://host[:port]/v1``. Plain HTTP by design: the
#: binary carries no TLS, so it speaks to a loopback forwarder inside the
#: sandbox which terminates TLS and forwards the bytes unchanged. Not a
#: secret — but it lives here beside the credential because the pair is the
#: whole of what the document may not carry.
BASE_URL_ENV = "SWE_LAB_SUPERVISOR_BASE_URL"

#: The environment variable holding the bearer credential the endpoint needs.
#: **Passed by reference and never rendered**: the binary reads it in-process,
#: splits a comma-separated list itself (so no key is ever taken apart by a
#: shell), and removes it from the actor's environment before launching it.
API_KEY_ENV = "SWE_LAB_SUPERVISOR_API_KEY"

#: A non-secret variable the **wrapper** sets in the actor's environment, not
#: one we pass in: ``<wrapper pid>-<nanos>``, which it scans ``/proc`` for after
#: ``killpg`` to find descendants that escaped the process group via ``setsid``.
#: Named here because the environment boundary is this module's to describe and
#: because it is the one supervisor variable that must **not** be scrubbed from
#: the actor — the two above still are.
MARK_ENV = "SWE_LAB_SUPERVISOR_MARK"

#: The variable names a sandbox running the wrapper must inherit **by
#: reference** — the backend's ``pass_env``, which passes a name and lets the
#: value cross without a rendered form.
#:
#: :data:`BASE_URL_ENV` is deliberately **not** in it. The endpoint is not a
#: host secret to forward: it is the loopback address of a forwarder the
#: harness itself starts inside the sandbox, so the harness is the only party
#: that knows it and it exports the value directly. A host variable of that
#: name would name something else and would silently take precedence.
#:
#: That exclusion is a **security** property, not environment hygiene. The
#: supervisor sends its requests *with the credential attached*, so an endpoint
#: the host environment can rewrite is an endpoint a stray same-named variable
#: can point at any host it likes — a request carrying ``Authorization`` sent
#: somewhere we did not choose. That is the shape of credential exfiltration,
#: not of dialling the wrong address. Pinning the endpoint in the harness makes
#: "the credential only ever reaches the loopback forwarder we started" true by
#: construction.
SUPERVISOR_PASS_ENV: tuple[str, ...] = (API_KEY_ENV,)

#: The one terminal-summary schema this consumer reads.
SUMMARY_SCHEMA_VERSION = 1

#: How many judgments the wrapper discarded because newer admitted evidence
#: had overtaken them. An event: a run that discarded none leaves no key
#: rather than a zero. Not a failure — it is the freshness gate working — but
#: a run whose verdicts were mostly stale supervised less than its boundary
#: count suggests, and that has to be visible where the outcome is read.
SUPERVISION_STALE_METRIC = "supervision.stale_verdicts_discarded"

#: The largest gap, in milliseconds, between a boundary and the delivery of the
#: judgment made for it. A measurement rather than an event: the wrapper exists
#: because the host runtime's lag was unbounded, so this is the number that
#: says whether it still is, and a run that never lagged reports ``0.0``
#: rather than staying silent about it.
SUPERVISION_MAX_DECISION_LAG_METRIC = "supervision.max_decision_lag_ms"

#: The largest value Rust's ``u32`` and ``u64`` hold. A field whose value does
#: not fit is not a large setting, it is a document ``serde`` refuses.
U32_MAX = 2**32 - 1
U64_MAX = 2**64 - 1


#: Every numeric field, with the range the Rust type it deserializes into
#: admits. ``budget`` and ``cooldown`` are ``u32``; ``window``,
#: ``judge_every_n_assistant_messages`` and ``max_event_line_bytes`` are
#: ``NonZeroU32``, which is where their lower bound of 1 comes from;
#: ``model_call_ms`` is a ``u64`` the binary additionally validates as
#: non-zero. **``term_grace_ms`` is a plain ``u64`` and zero is a value the
#: binary accepts**, so this does not refuse it: the two sides agreeing on what
#: is configurable matters more than this side being defensible alone.
class Blocking(enum.StrEnum):
  """How the actor is held while a judgment is in flight.

  A three-valued setting rather than the boolean it started as: blocking and
  the stale gate are two answers to the same lag, and there turned out to be
  two ways of blocking with different costs. Mirrors `config.rs`'s ``Blocking``,
  which deserializes these kebab-case tokens and refuses anything else.

  Attributes:
    OFF: Not held. The actor runs ahead, and a verdict that newer admitted
      evidence overtook is discarded as stale.
    STDOUT: The wrapper stops reading the actor's stdout; the pipe fills and
      the actor's next write waits for the verdict. The absence of a read,
      which self-releases if the wrapper dies.
    SIGSTOP: ``SIGSTOP`` to the actor's process group, ``SIGCONT`` after the
      verdict. Exact, and a real state the wrapper must leave before it exits.
  """

  OFF = "off"
  STDOUT = "stdout"
  SIGSTOP = "sigstop"


#: The fields whose Rust type is not a number, and the JSON type each must
#: have. Checked through :func:`getattr` like the numeric ones rather than off
#: the attributes directly: the annotations already say ``str`` and
#: ``Blocking``, and the point of the check is the caller who is not
#: type-checked — a downstream consumer, or ``dataclasses.replace``, which
#: takes ``Any``.
NON_NUMERIC_FIELDS: Mapping[str, type] = {
    "model": str,
    "block_actor_while_judging": Blocking,
}

NUMERIC_FIELDS: Mapping[str, tuple[int, int]] = {
    "budget": (0, U32_MAX),
    "cooldown": (0, U32_MAX),
    "window": (1, U32_MAX),
    "judge_every_n_assistant_messages": (1, U32_MAX),
    "model_call_ms": (1, U64_MAX),
    "term_grace_ms": (0, U64_MAX),
    "max_event_line_bytes": (1, U32_MAX),
    "max_actor_stdout_bytes": (1, U64_MAX),
    "max_actor_stderr_bytes": (1, U64_MAX),
}

#: The default ceiling on one judge or writer call, in milliseconds. A
#: mechanism bound rather than a run's choice, which is why it is defaulted
#: here while every policy number below is not.
DEFAULT_MODEL_CALL_MS = 180_000

#: The default shutdown grace, in milliseconds: how long the actor's process
#: group gets to honour ``SIGTERM`` before ``SIGKILL``, and how long the actor
#: gets to exit on its own after its stdin is closed deliberately.
DEFAULT_TERM_GRACE_MS = 10_000

#: The default ceiling on one line of actor stdout. A tool result can be a
#: whole file, so the framing buffer grows to this rather than to a fixed size;
#: a longer line is still written to the event log and reaches no judgment.
DEFAULT_MAX_EVENT_LINE_BYTES = 16 * 1024 * 1024

#: The default ceilings on the *whole* of what the actor writes to each stream.
#: Unlike the per-line ceiling above, reaching one of these ends the run: the
#: log is truncated at a line boundary (a partial line is not written at all),
#: the stream is not read again, and the run is reported unhealthy.
#:
#: **Not backpressure.** Ceasing to read here is terminal, not a pause the
#: wrapper later resumes, so it opens none of the freshness window that
#: blocking the actor mid-judgment would.
DEFAULT_MAX_ACTOR_STDOUT_BYTES = 1024 * 1024 * 1024
DEFAULT_MAX_ACTOR_STDERR_BYTES = 256 * 1024 * 1024


def _require_type(name: str, value: object, kind: type) -> None:
  """Refuse a value whose JSON type the binary's slot for it would refuse.

  A free function taking ``object`` rather than a check written inline: the
  annotations already say ``str`` and ``bool``, so an inline ``isinstance``
  reads as dead code to a type checker and would be removed. The caller this
  guards is the one nothing type-checked — a downstream consumer, or
  ``dataclasses.replace``, whose keywords are ``Any``.

  Args:
    name: The field's name, for the message.
    value: What the caller supplied.
    kind: The Python type of the Rust slot it lands in.

  Raises:
    ValueError: The value is not that type.
  """
  if not isinstance(value, kind):
    raise ValueError(
        f"{name} must be a {kind.__name__}, not a {type(value).__name__}"
    )


@dataclasses.dataclass(frozen=True)
class NativeSupervision:
  """One run's supervision, as the native runtime is configured for it.

  **No policy number has a default**, and that is the same decision the binary
  made in its own schema: a policy that may speak states how often, how far
  apart, on how much evidence, and how often it is asked. ``N`` and ``window``
  were measured to be coupled on one corpus and that analysis was then
  withdrawn (#380), so what is settled is not a value but that neither may be
  chosen silently — least of all here, at the only place that writes a config.

  The three bounds below *are* defaulted: they bound the wrapper's mechanism —
  how long it waits, how much it buffers — rather than what supervision does,
  and a run that has an opinion about them says so.

  Attributes:
    model: The model name sent on every judge and writer request. Never
      defaulted, so every record says who was asked.
    budget: How many corrections the whole run may carry. ``0`` is the control
      arm: the judge still runs at every boundary and nothing is ever said.
    cooldown: How many judgment boundaries must pass between two corrections.
      Never delays the first one.
    window: How many of the actor's most recent admitted records the judge
      sees.
    judge_every_n_assistant_messages: How many admitted assistant messages fall
      between two judgment boundaries. A boundary also falls at every actor
      ``result`` carrying new evidence, so a partial batch at the end of a turn
      is judged rather than dropped.
    block_actor_while_judging: How the actor is held while a judgment is in
      flight — see :class:`Blocking`. Not defaulted either: holding it and the
      stale gate are two answers to the same lag, and a run says which it
      uses. A :class:`Blocking` member, never the bare string: a token the
      binary does not know is a run refused at startup, and requiring the
      member is what makes a typo impossible to write.
    model_call_ms: The bound on one judge or writer call, connection included.
    term_grace_ms: The shutdown grace described at
      :data:`DEFAULT_TERM_GRACE_MS`.
    max_event_line_bytes: The ceiling on one line of actor stdout.
    max_actor_stdout_bytes: The ceiling on the whole of the actor's stdout, as
      described at :data:`DEFAULT_MAX_ACTOR_STDOUT_BYTES`. Reaching it ends the
      run and reports it unhealthy.
    max_actor_stderr_bytes: The same ceiling for the actor's stderr.
  """

  model: str
  budget: int
  cooldown: int
  window: int
  judge_every_n_assistant_messages: int
  block_actor_while_judging: Blocking
  model_call_ms: int = DEFAULT_MODEL_CALL_MS
  term_grace_ms: int = DEFAULT_TERM_GRACE_MS
  max_event_line_bytes: int = DEFAULT_MAX_EVENT_LINE_BYTES
  max_actor_stdout_bytes: int = DEFAULT_MAX_ACTOR_STDOUT_BYTES
  max_actor_stderr_bytes: int = DEFAULT_MAX_ACTOR_STDERR_BYTES

  def __post_init__(self) -> None:
    """Refuse a configuration the binary would refuse, here instead.

    The binary validates its own input and exits ``3`` on a bad one — after a
    sandbox has been paid for and the actor is about to start. Every rule below
    is one of its rules, applied where the value is chosen, so a mistake costs
    a construction rather than a container.

    **Types as well as ranges.** ``serde`` refuses a value of the wrong JSON
    type just as firmly as one out of range, and Python will not stop either:
    ``True`` is an ``int`` here and would render as ``true`` into a slot that
    deserializes a number. So each field is checked against the Rust type it
    deserializes into (:data:`NUMERIC_FIELDS`), not merely against zero.

    Raises:
      ValueError: A value is one the runtime cannot honour; the message names
        the field.
    """
    for name, kind in NON_NUMERIC_FIELDS.items():
      _require_type(name, getattr(self, name), kind)
    if not self.model:
      raise ValueError("model must name a model")
    for name, (low, high) in NUMERIC_FIELDS.items():
      value = getattr(self, name)
      # `bool` is a subclass of `int`, so `budget=True` would otherwise render
      # as JSON `true` into a slot that deserializes an integer.
      if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{name} must be an integer, not a {type(value).__name__}"
        )
      if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")

  def config_document(
      self, *, task: str, criterion_path: pathlib.Path = CRITERION_PATH
  ) -> dict[str, Any]:
    """Render the run's config, verifying the criterion on the way.

    The digest written here is the one :func:`~.criterion.load_criterion` just
    computed off the artifact, never the pinned constant copied across: the
    binary re-verifies its own embedded copy against this value before it
    launches the actor, so a drifted artifact has to be refused *here* for that
    check to mean anything downstream. Refusing at render time also keeps the
    host runtime's property on the native path — a forged criterion stops the
    run before a sandbox exists.

    Args:
      task: What the actor was asked to do.
      criterion_path: The artifact to load. A parameter so a test can point at
        a forged criterion; production never passes it.

    Returns:
      The document, ready to be serialized. Every key is one the binary reads,
      and there are no others — it refuses an unknown field rather than
      ignoring it.

    Refuses rather than renders in two cases, both of which would otherwise
    become the binary's problem inside a paid-for sandbox: a ``task`` that is
    not a string raises ``ValueError`` (the slot is a Rust ``String``), and an
    artifact that is not the pinned criterion raises ``CriterionRejectedError``
    out of :func:`~.criterion.load_criterion`. Neither is a ``Raises:`` section
    because neither ``raise`` is written here — the first is in
    :func:`_require_type`, whose ``object`` parameter is what makes the check
    survive a type checker, and the second is the loader's.
    """
    # `task` is an argument rather than a field, so `__post_init__` never sees
    # it — and it reaches the document exactly as every field does. Checked
    # against the Rust type for the same reason they are.
    _require_type("task", task, str)
    # Propagates `CriterionRejectedError` from `load_criterion`: an artifact
    # that is not the pinned one leaves nothing to configure, and refusing
    # here is what keeps the refusal ahead of the sandbox.
    criterion = load_criterion(path=criterion_path)
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "task": task,
        "criterion": {
            # The pinned name, never the loaded path's: `criterion_path` is a
            # test seam, and a renamed copy of the artifact has the same digest
            # and would otherwise name a criterion the binary does not carry.
            # What identifies the criterion is the digest; what selects it out
            # of the binary's own is this name, and only one of them is ours.
            "name": CRITERION_NAME,
            "sha256": criterion.digest,
        },
        "policy": {
            "kind": POLICY_KIND,
            "budget": self.budget,
            "cooldown": self.cooldown,
            "window": self.window,
            "judge_every_n_assistant_messages": (
                self.judge_every_n_assistant_messages
            ),
            "block_actor_while_judging": (self.block_actor_while_judging.value),
        },
        "model": {"name": self.model},
        "timeouts": {
            "model_call_ms": self.model_call_ms,
            "term_grace_ms": self.term_grace_ms,
        },
        "limits": {
            "max_event_line_bytes": self.max_event_line_bytes,
            "max_actor_stdout_bytes": self.max_actor_stdout_bytes,
            "max_actor_stderr_bytes": self.max_actor_stderr_bytes,
        },
    }

  def config_bytes(
      self, *, task: str, criterion_path: pathlib.Path = CRITERION_PATH
  ) -> bytes:
    """Render the config document as the file the wrapper is handed.

    Args:
      task: What the actor was asked to do.
      criterion_path: As :meth:`config_document`.

    Returns:
      The serialized document. Refuses a forged criterion exactly as
      :meth:`config_document` does, which is what it delegates to.
    """
    document = self.config_document(task=task, criterion_path=criterion_path)
    return json.dumps(document, indent=2, sort_keys=True).encode("utf-8")


@dataclasses.dataclass(frozen=True)
class TerminalSummary:
  """What the wrapper says about the run it just finished.

  Attributes:
    accounted_for: Whether every boundary of the run is accounted for. False
      on any gap, on an unclean wrapper ending, or when no usable actor event
      was ever consumed. The same claim
      :attr:`~.channel.SupervisedRun.supervision_accounted_for` makes on the
      host path, made by the component that owns the run instead.
    actor_exit_code: What the actor exited with, ``128 + signal`` when it died
      of one. Recorded, never classified from.
    supervisor_exit: How the wrapper itself ended, in its own word.
    boundaries: How many judgment boundaries the run had.
    corrections: How many corrections were delivered.
    lapses: How many boundaries went unsupervised for a reason the policy could
      bound to them.
    gaps: How many failures of unknown reach were recorded. Any is enough for
      :attr:`accounted_for` to be false; the count is what a reader weighs.
    stale_verdicts_discarded: How many judgments newer evidence overtook.
    max_decision_lag_ms: The largest boundary-to-delivery gap.
    criterion_sha256: The digest the binary verified its embedded criterion
      against.
    actor_event_log_sha256: The digest of the actor's event log as the wrapper
      closed it.
    supervisor_log_sha256: The digest of the supervisor's own account.
  """

  accounted_for: bool
  actor_exit_code: int
  supervisor_exit: str
  boundaries: int
  corrections: int
  lapses: int
  gaps: int
  stale_verdicts_discarded: int
  max_decision_lag_ms: int
  criterion_sha256: str
  actor_event_log_sha256: str
  supervisor_log_sha256: str


@dataclasses.dataclass(frozen=True)
class UnusableSummary:
  """No summary could be read, and why.

  Returned rather than raised, and kept apart from a summary that reads
  ``accounted_for: false``, because the two are different facts: that one is
  the wrapper reporting a hole it can describe, and this is the wrapper not
  reporting at all. Both disqualify the run as evidence about supervision —
  :func:`supervision_metrics` maps them onto the same metric — but only one of
  them has a boundary count to weigh.

  Attributes:
    reason: What was wrong with it, for the record and for a reader.
  """

  reason: str


#: Every field of the terminal summary, and the type its slot takes. Written
#: out rather than derived from the annotations because ``bool`` is a subclass
#: of ``int``: a truth value would otherwise pass every count slot. Kept in
#: step with :class:`TerminalSummary` by a named test.
SUMMARY_FIELDS: Mapping[str, type] = {
    "accounted_for": bool,
    "actor_exit_code": int,
    "supervisor_exit": str,
    "boundaries": int,
    "corrections": int,
    "lapses": int,
    "gaps": int,
    "stale_verdicts_discarded": int,
    "max_decision_lag_ms": int,
    "criterion_sha256": str,
    "actor_event_log_sha256": str,
    "supervisor_log_sha256": str,
}


def _holds(value: object, kind: type) -> bool:
  """Say whether one summary value is usable as the type its slot takes.

  Args:
    value: What the summary carried.
    kind: The type the slot takes.

  Returns:
    Whether the value is that type — with ``True`` refused for an ``int`` slot,
    which plain ``isinstance`` would accept.
  """
  if kind is int:
    return isinstance(value, int) and not isinstance(value, bool)
  return isinstance(value, kind)


def read_terminal_summary(raw: str | None) -> TerminalSummary | UnusableSummary:
  """Read the wrapper's terminal summary, or say why it could not be read.

  Every field is required and is checked for its type. A field the wrapper adds
  later is **ignored**, which is the one place this consumer is deliberately
  laxer than the binary is about its own config: an unknown config key is a
  setting its writer believed was in force, while an unknown summary key is the
  producer saying more than this reader asked for.

  Args:
    raw: The summary file's contents, or ``None`` when there is no file — an
      unfinished wrapper writes nothing, which is a reading rather than an
      error.

  Returns:
    The summary, or :class:`UnusableSummary` naming what was wrong.
  """
  if raw is None:
    return UnusableSummary(reason="the wrapper wrote no terminal summary")
  try:
    document = json.loads(raw)
  except (json.JSONDecodeError, TypeError) as error:
    return UnusableSummary(
        reason=f"the terminal summary is not JSON: {error!r}"
    )
  if not isinstance(document, dict):
    return UnusableSummary(
        reason=(
            f"the terminal summary is a {type(document).__name__}, not an"
            " object"
        )
    )
  version = document.get("schema_version")
  if version != SUMMARY_SCHEMA_VERSION:
    return UnusableSummary(
        reason=(
            f"terminal summary schema_version {version!r} is not the"
            f" {SUMMARY_SCHEMA_VERSION} this reader knows"
        )
    )
  found: dict[str, Any] = {}
  for name, kind in SUMMARY_FIELDS.items():
    if name not in document:
      return UnusableSummary(reason=f"terminal summary has no {name!r}")
    value = document[name]
    if not _holds(value, kind):
      return UnusableSummary(
          reason=(
              f"terminal summary {name!r} is a {type(value).__name__}, not a"
              f" {kind.__name__}"
          )
      )
    found[name] = value
  return TerminalSummary(**found)


def supervision_metrics(
    summary: TerminalSummary | UnusableSummary,
) -> dict[str, float]:
  """Classify a native supervised run from what the wrapper reported.

  **The actor's exit status is not an input here, deliberately.** A wrapper
  that ran cleanly exits with the actor's own status, so a run can exit ``0``
  having supervised nothing at all — which is precisely the shape
  :data:`~swe_lab.rollout.SUPERVISION_METRIC` exists to stop reaching a reader
  as an ordinary result.

  Args:
    summary: What :func:`read_terminal_summary` returned.

  Returns:
    The metrics for this run's record. Measurements are always present — a
    boundary count of zero is a reading — while events leave no key rather than
    a zero, which is the convention
    :data:`~swe_lab.rollout.SUPERVISION_LAPSE_METRIC` already follows.
  """
  if isinstance(summary, UnusableSummary):
    # No counts to report: the run has no account, which is the whole finding.
    return {SUPERVISION_METRIC: 1.0}
  metrics: dict[str, float] = {
      BOUNDARIES_METRIC: float(summary.boundaries),
      CORRECTIONS_METRIC: float(summary.corrections),
      SUPERVISION_MAX_DECISION_LAG_METRIC: float(summary.max_decision_lag_ms),
  }
  if not summary.accounted_for:
    metrics[SUPERVISION_METRIC] = 1.0
  if summary.lapses:
    metrics[SUPERVISION_LAPSE_METRIC] = float(summary.lapses)
  if summary.stale_verdicts_discarded:
    metrics[SUPERVISION_STALE_METRIC] = float(summary.stale_verdicts_discarded)
  return metrics
