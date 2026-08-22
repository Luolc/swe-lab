# Document map & routing

Where each kind of content lives, and where a new learning goes. **The rule
behind both tables: every fact has exactly one home; other docs *link* to it,
they never copy it.** Most drift starts as a well-meant paragraph in the wrong
file — before writing one, ask *what question does a reader have when they open
this file?* If your paragraph doesn't answer that question, it belongs
elsewhere.

## Document map

`<component>` = `docs/horizontal/` (the shared foundation) or a
`docs/workstreams/<w>/` folder (a vertical). An active component owns its own
`spec` / `plan` / `plans`.

| File / dir | Answers | Never put here |
|---|---|---|
| `AGENTS.md` | How do we work? (modes, git flow, quality bar, boundaries, comms) | status; design; codebase map |
| `docs/README.md` | Where is everything, and where are we? (map + **workstream-level status**) | task-level status; design detail |
| `docs/conventions.md` | How is the code laid out and run? (dir map, commands, hazards, source-of-truth, naming, interface style) | status; an ADR's rejected alternatives |
| `docs/doc-map.md` | Which doc answers which question; where a learning lands (this file) | anything with its own home below |
| `docs/decisions/ADR-NNNN-*.md` | Why did we decide X, and what did we reject? | status; how-to; a second copy of the code |
| `docs/decisions/README.md` | The ADR index + ADR conventions | the ADR bodies |
| `<component>/spec.md` | What are we building and why? (target design) | status; task breakdown; strategy |
| `<component>/plan.md` | In what order, with what risks / DoD / checkpoints? (strategy) | per-task design; status |
| `<component>/plans/README.md` | The ordered task index — **the one live status home** for that component | design detail |
| `<component>/plans/task-NN-*.md` | The deep design of one task (point-in-time record) + an optional dated `## Result` | live status |
| `docs/workstreams/<w>/` | A vertical's design / history (`spec`/`plan`/`todo` when active, `README` when dormant) | horizontal / shared design |
| `docs/releases/vX.Y.Z.md` | I depend on swe-lab and am upgrading to this version — what broke, and what do I change? | design (link to the ADR/plan); the exhaustive commit list (the Release's generated notes own that) |
| `docs/releases/README.md` | The release-note index + what belongs in one | the note bodies |
| `docs/reviews/` | A dated engineering audit (a snapshot, not a spec) | design; status |
| `docs/experiments/` | An empirical question → logged run → `REPORT.md` | production design |

## Routing a learning

When something is decided or discovered, route it to its one home — don't leave
it in a commit message or PR body (nobody re-reads those), and don't write it
into three files (they drift):

| The learning is… | Goes to | Form |
|---|---|---|
| a product / architecture decision (+ what you rejected) | an **ADR** | ADR-first, in the **same PR** as the code |
| a repo-wide rule for agents | `AGENTS.md` | a rule + how a violation gets noticed |
| a component- / workstream-local rule | that component's `spec` / `plan` (or workstream `README`) | inline where it applies |
| a codebase-map fact (where code lives, a hazard, a command) | `docs/conventions.md` | a map row / hazard note |
| the deep design of a task | that `plans/task-NN.md`; a shipped delta → its dated `## Result` | design record |
| **task status** | the component's `plans/README.md` (the ONE place) | the index row |
| **workstream-level status** | `docs/README.md` snapshot (the ONE place) | the snapshot row |
| a naming / interface-style rule | `docs/conventions.md` (or an ADR — e.g. ADR-0002) | a convention |
| an empirical / ML finding | an experiment `REPORT.md` | hypothesis → result |
| a change a **consumer** must react to (removed name, changed default, shifted results) | `docs/releases/vX.Y.Z.md` for the version shipping it | what changed / why / what you must do |

## The single-source-of-truth guards

Each fact has one home; these guards keep it that way (authoritative text lives
where noted — this list only points):

- **One status home.** Task status → the component's `plans/README.md`;
  workstream status → `docs/README.md`. Plan / task docs are point-in-time
  records and **never carry live status** (see the note atop
  [`horizontal/plans/README.md`](horizontal/plans/README.md)).
- **ADR-first, same PR** for a decision change; minor delta → a dated
  `## Amendment` in the ADR, large delta → a new superseding ADR (see
  [`decisions/README.md`](decisions/README.md)).
- **A release note is written before the version bump lands**, not after
  tagging — step 1 of [releasing](conventions.md#releasing). Written from
  memory afterwards, the migration steps are exactly the part that goes wrong.
- **Every doc needs a re-read trigger, or it rots** — including reconciling a
  component's `spec.md` at each checkpoint / workstream-status change (see
  `AGENTS.md` → Boundaries).
- **An invariant needs a test, or it's downgraded** to "intended / today" (see
  `AGENTS.md` → Quality bar).
- **Delete guidance that contradicts the repo** — stale guidance is worse than
  missing, because it gets followed; a pre-commit stale-reference guard backs
  this up.
