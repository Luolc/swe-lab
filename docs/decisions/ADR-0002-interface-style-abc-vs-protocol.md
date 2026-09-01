# ADR-0002: Interface style — ABC/base class over Protocol

## Status

Accepted (superseded **for `Verdict` only** by
[ADR-0006](ADR-0006-verdict-is-an-abc.md); the rest stands)

## Date

2026-07-22

## Context

Early code and design docs said the repo "prefers `Protocol`s for seams" (spec
Code Style; several task designs). That guidance was wrong — it conflated
"interface" in the general sense with Python's `typing.Protocol` specifically —
and it produced Protocols for behavior interfaces where a nominal base class is
the better fit.

`typing.Protocol` gives **structural** (duck) conformance: a class satisfies it
by shape, with no `class X(TheInterface)` link. That has real costs for a
behavior interface whose implementers all live in this repo:

- **No navigation.** There is no explicit link from the interface method to its
  implementations, so IDE / type-checker "go to implementations" is unreliable.
- **No enforcement.** A missing or mis-named method is not caught at
  instantiation; the class silently stops conforming until some call site that
  expects the Protocol happens to reject it.
- **The one Protocol win — decoupling — is usually absent here.** Every
  implementer already imports the package that defines the interface (e.g.
  `SweBenchProGrader` already imports `evaluation.verdict`), so a nominal
  `class SweBenchProGrader(Grader[...])` adds no coupling that isn't already
  there.

Protocol is still the right tool for a **structural shape on data** — "a record
that has these fields" — where forcing every record to inherit a base is the
actual anti-pattern (a future external dataset's record should not have to
import and subclass our base just to expose `instance_id`).

## Decision

Choose the interface mechanism by what the interface *is*:

1. **Structural shape on data/records → `typing.Protocol`.** Read-only
   properties / fields that frozen-dataclass records satisfy by shape. Records
   must not be forced to inherit. Examples: `Verdict`, `RepoInstance`,
   `DatasetRecord`.
2. **Behavior interface, all methods required → `abc.ABC` + `@abstractmethod`.**
   Implementers write `class Impl(TheInterface)` and mark methods `@override`.
   Gains explicit navigation, and enforcement at instantiation (an incomplete
   subclass cannot be constructed). Examples: `Grader`, `Sandbox`,
   `RepoProvider`.
3. **Behavior interface where partial override is normal → a concrete base
   class with default (usually no-op) methods.** `@abstractmethod` would wrongly
   force implementing every hook. Example: `SandboxObserver` (five lifecycle
   hooks; observers override the few they need).

Generic interfaces use PEP 695 brackets in every case
(`class Grader[V: Verdict](ABC)`).

## Alternatives Considered

- **Keep "prefer Protocol" everywhere.** Rejected: it optimizes for a decoupling
  benefit we don't get (implementers are in-repo and already import the
  interface) at the cost of navigation and instantiation-time enforcement.
- **ABC everywhere, including data shapes.** Rejected: forcing `Verdict` /
  record types to inherit a base is the exact anti-pattern Protocol exists to
  avoid, and blocks external record types from conforming.
- **Runtime-checkable Protocols.** Rejected: `@runtime_checkable` adds `isinstance`
  support but not navigation or completeness enforcement — it doesn't address
  the actual costs.

## Consequences

- Converted to `ABC` + `@abstractmethod` (implementers gain `class Impl(Base)` +
  `@override`): `Grader` (`SweBenchProGrader`), `Sandbox` (`DockerHostSandbox`,
  `GitHubJobSandbox`, `FakeSandbox`), `RepoProvider` (`GitCheckoutProvider`).
- Kept `Protocol` (structural shapes): `Verdict`, `RepoInstance`,
  `DatasetRecord`.
- Kept the concrete-base-class pattern: `SandboxObserver`.
- **Exempt:** `BenchmarkAdapter` (`core/benchmark.py`) — retired at cutover
  (10b) along with `EvalSpec`; not worth churning dying code.
- Supersedes the "`Protocol`s for seams" guidance in the spec Code Style and the
  affected task designs; `docs/conventions.md` carries the rule going forward.
