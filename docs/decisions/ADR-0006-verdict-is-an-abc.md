# ADR-0006: `Verdict` is an ABC, not a Protocol

## Status

Accepted

## Date

2026-08-02

## Context

[ADR-0002](ADR-0002-interface-style-abc-vs-protocol.md) chooses the interface
mechanism by what the interface *is*, and put `Verdict` in the first bucket —
"structural shape on data/records → `typing.Protocol`", read-only properties a
frozen dataclass satisfies by shape, with no forced inheritance. It named the
opposite choice and rejected it:

> **ABC everywhere, including data shapes.** Rejected: forcing `Verdict` /
> record types to inherit a base is the exact anti-pattern Protocol exists to
> avoid, and blocks external record types from conforming.

That was right about what `Verdict` was in July: a scalar `score` and a derived
`resolved`.

It is no longer what `Verdict` is. [ADR-0005](ADR-0005-flaky-eval-retry.md)
added `attempts`, `flaky` and `with_attempts`, and two of the seven members are
now **derivations carrying a rule**, not fields:

- `resolved` is `score >= 1.0`;
- `flaky` is `attempts > 1 **and** resolved` — deliberately not `attempts > 1`,
  because a run that failed every attempt is failed, not flaky.

A Protocol can state those in a docstring and enforce neither. Every implementer
re-derives them, and one that writes `flaky = attempts > 1` type-checks,
satisfies the Protocol, and is wrong: it reports every exhausted retry chain as a
flake, which is exactly the signal the known-flaky registry consumes and exactly
the mistake ADR-0005 wrote the rule down to prevent. That is a behavior interface
with shared invariants wearing a data shape's clothes, and ADR-0002's own
taxonomy sends it to `ABC`.

The repo rule that an invariant needs a test or a downgraded claim applies here
too: as a Protocol, "flaky is derived, never stored" is a claim no test can
attach to, because there is no shared implementation to test.

## Decision

**`Verdict` becomes an `abc.ABC`.** `score`, `with_attempts`, `summary` and
`metrics` are abstract; `resolved` and `flaky` become **concrete and
inherited**, so the two derivations exist exactly once and a subclass gets them
right by default.

`attempts` is **declared, not abstract** — a bare `attempts: int` annotation.
It is the one member that really is data, and a subclass satisfies it with an
ordinary dataclass field. Declaring it as an abstract property instead does not
type-check (a field cannot override a read-only property) and would force every
verdict to wrap a private field in a getter to say the same thing. This is the
residue of ADR-0002's point that data does not want a base class: the answer is
to model the data member as data, not to abandon the ABC that owns the
derivations.

The ABC declares **`__slots__ = ()`**, and that is load-bearing rather than
tidiness. `abc.ABC` declares it, but a subclass that does not re-declare it
reintroduces `__dict__` on every instance and silently defeats `slots=True` on
the concrete verdict. Measured on this repo's own shape: 40 → 56 bytes per
instance, and typo'd attribute assignment stops raising. A verdict is
constructed per instance per rollout, so it is a footprint regression and a
correctness one; a test pins it rather than a comment asking politely.

ADR-0002 is superseded **only for `Verdict`**. Its taxonomy stands unchanged, as
do its other placements — `Grader` / `Sandbox` / `RepoProvider` remain ABCs,
`SandboxObserver` remains a concrete base, and `RepoInstance` / `DatasetRecord`
remain Protocols, because those really are structural shapes with no shared
derivation to own.

## Alternatives considered

| Option | Why not |
|---|---|
| **An optional concrete `BaseVerdict`, Protocol stays the bound.** Non-breaking; a dataset may inherit the derivations or not. | The derivations stay optional, so the wrong `flaky` is still expressible and still conforms. It buys DRY without buying the invariant, which is the part that matters. |
| **Keep the Protocol, pin the derivations with tests.** Cheapest, no break. | Tests can only cover the implementations in this repo. The failure mode is a verdict written *outside* it, which is precisely what the Protocol was chosen to allow. |
| **`@runtime_checkable` Protocol.** | Adds `isinstance` and nothing else — not shared implementation, not completeness. ADR-0002 rejected it for the same reason. |
| **Leave `flaky` out of the interface**, letting each dataset expose it ad hoc. | It is consumed generically (the run record, the flaky discovery channel). Removing it from the contract moves the duplication rather than removing it. |

## Consequences

**Good**

- The two derivations exist once, and `flaky`'s "not merely retried, but
  retried *and* resolved" rule is enforced instead of documented.
- Instantiation-time enforcement: an incomplete verdict cannot be constructed,
  where a Protocol failed only at type-check time and only if checked.
- Explicit `class SweBenchProVerdict(Verdict)` — navigable, and `@override`
  becomes meaningful on the members that stay abstract.

**Bad, and accepted knowingly**

- **Breaking for downstream.** A verdict type that conformed structurally no
  longer satisfies the `V: Verdict` bound on `Grader`, `UnitTestSpec`,
  `TaskInstance` and `EvalParseObserver`. The fix is one word — add `(Verdict)`
  to the class statement — but it is a required edit, not a deprecation, and it
  is exactly the cost ADR-0002 declined to pay. We pay it now because the thing
  bought has changed: in July there was nothing to inherit.
- A dataset whose verdict is not a class at all (a `TypedDict`, a namedtuple
  from another library) can no longer conform without a wrapper.

**Neutral**

- `SweBenchProVerdict` keeps `frozen=True, slots=True`; the ABC's `__slots__ =
  ()` is what makes that continue to mean something.
- No behaviour changes for any existing run: the concrete derivations are
  byte-identical to the ones deleted from `SweBenchProVerdict`.

## Amendment (2026-08-03): the derivations this ADR was written for are down to one

The example throughout is `flaky` — a derivation over `attempts`, which
[ADR-0005](ADR-0005-flaky-eval-retry.md) had put on the verdict.
[ADR-0008](ADR-0008-retry-moves-to-the-task.md) moves retrying to the task
level and deletes `attempts`, `flaky` and `with_attempts`: one verdict grades
one tree, and how many attempts it took is the runner's fact.

The decision itself is unchanged and still right. `resolved` is a derivation
with a rule ("`score >= 1.0`") that every dataset must get identically, which
a Protocol can state but not enforce; that is the whole argument, and one
member is enough to carry it.
