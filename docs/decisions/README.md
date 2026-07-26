# Architecture Decision Records

Sequentially-numbered records of significant, hard-to-reverse decisions and the
alternatives rejected. Format follows the `documentation-and-adrs` skill:
**Status · Date · Context · Decision · Alternatives Considered · Consequences**.

- **Don't re-litigate an accepted ADR.** If a decision must change, write a new
  ADR that references and supersedes the old one; don't edit the old in place or
  delete it (it is the historical record).
- **The code is the source of truth.** An ADR records the *why*; where an ADR and
  the code disagree, the code wins and the ADR should be superseded to match.

## Index

| ADR | Decision | Status | Date |
| --- | --- | --- | --- |
| [0001](ADR-0001-patch-extraction-and-grading.md) | Patch extraction and grading — text-only diff vs `base_commit`, strict `git apply` matching Scale | Accepted | 2026-07-17 |
| [0002](ADR-0002-interface-style-abc-vs-protocol.md) | Interface style — ABC/base class over Protocol (Protocol only for structural data shapes) | Accepted | 2026-07-22 |
| [0003](ADR-0003-remote-sandbox-lifecycle.md) | Remote-sandbox support — up-first lifecycle + workspace as a `SandboxFs` capability (amends the spec's host-FS core model) | Accepted | 2026-07-26 |
