# Document map & routing

Where each kind of content lives, and where a new learning goes. **The rule
behind both tables: every fact has exactly one home, and everywhere else
*points* at it rather than restating its value.** Where the line falls — what
counts as a second home, and what is harmless explanatory restatement — is the
first of the [guards](#the-single-source-of-truth-guards) below. Most drift
starts as a well-meant paragraph in the wrong file — before writing one, ask
*what question does a reader have when they open this file?* If your paragraph
doesn't answer that question, it belongs elsewhere.

## Document map

`<component>` = any active component's folder — `docs/horizontal/` (the shared
foundation), `docs/trace-synthesis/`, or a `docs/workstreams/<w>/` folder (a
vertical). An active component owns a
`spec.md` and a `plans/` directory whose `README.md` is the ordered task index
**and the one live status home**; `plan.md` is optional and there is **no**
`todo.md` (see `AGENTS.md` → How we work). A dormant component is just a
`README.md`.

| File / dir | Answers | Never put here |
|---|---|---|
| `AGENTS.md` | How do we work? (modes, git flow, quality bar, boundaries, comms) | status; design; codebase map |
| `docs/README.md` | Where is everything, and where are we? (map + **workstream-level status**) | task-level status; design detail |
| `docs/conventions.md` | How is the code laid out and run? (dir map, commands, hazards, source-of-truth, naming, interface style) | status; an ADR's rejected alternatives |
| `docs/doc-map.md` | Which doc answers which question; where a learning lands (this file) | anything with its own home below |
| `docs/evidence.md` | Why do we believe a result? (the undiscriminating-observation family, its media here, evidence & review rules) | the codebase map; a rule already in `~/.agents/AGENTS.md` |
| `docs/decisions/ADR-NNNN-*.md` | Why did we decide X, and what did we reject? | status; how-to; a second copy of the code |
| `docs/decisions/README.md` | The ADR index + ADR conventions | the ADR bodies |
| `<component>/spec.md` | What are we building and why? (target design) | status; task breakdown; strategy |
| `<component>/plan.md` *(optional)* | In what order, with what risks / DoD? (strategy) — worth writing only for a multi-phase migration, and deleted when it ends | per-task design; task list; status |
| `<component>/plans/README.md` | The ordered task index — **the one live status home** for that component | design detail |
| `<component>/plans/task-NN-*.md` | The deep design of one task (point-in-time record) + an optional dated `## Result` | live status |
| `docs/trace-synthesis/` | Oracle-guided trace synthesis — the component layout above (`spec.md` + `plans/`) | horizontal / shared design; training itself |
| `docs/workstreams/<w>/` | A vertical's design / history — the component layout above when active, just a `README` when dormant (most are) | horizontal / shared design |
| `docs/releases/vX.Y.Z.md` | I depend on swe-lab and am upgrading to this version — what broke, and what do I change? | design (link to the ADR/plan); the exhaustive commit list (the Release's generated notes own that) |
| `docs/releases/README.md` | The release-note index + what belongs in one | the note bodies |
| `docs/reviews/` | A dated engineering audit (a snapshot, not a spec) | design; status |
| `docs/research/` | What does the outside world already know? (a dated survey of external sources, with its own claim/inference/not-found marking) | our own empirical results (an experiment `REPORT.md` owns those); a decision (an ADR does) |
| `docs/experiments/` | An empirical question → logged run → `REPORT.md` | production design |

## Routing a learning

When something is decided or discovered, route it to its one home — don't leave
it in a commit message or PR body (nobody re-reads those), and don't write it
into three files (they drift):

| The learning is… | Goes to | Form |
|---|---|---|
| a product / architecture decision (+ what you rejected) | an **ADR** | ADR-first, in the **same PR** as the code |
| a repo-wide rule for agents | `AGENTS.md` | a rule + how a violation gets noticed |
| a component- / workstream-local rule | that component's `spec.md` (or a dormant workstream's `README`) | inline where it applies |
| a codebase-map fact (where code lives, a hazard, a command) | `docs/conventions.md` | a map row / hazard note |
| the deep design of a task | that `plans/task-NN.md`; a shipped delta → its dated `## Result` | design record |
| **task status** | the component's `plans/README.md` (the ONE place) | the index row |
| **workstream-level status** | `docs/README.md` snapshot (the ONE place) | the snapshot row |
| a fact that is recorded but changes no branch (a metric nobody reads, a default-off remedy, a listed-but-unenforced limitation) | `docs/conventions.md` → Hazards | the hazard entry, naming the branch it should change |
| a naming / interface-style / comment-content rule | `docs/conventions.md` (or an ADR — e.g. ADR-0002) | a convention |
| what a **downstream consumer** needs to run something at a scale we deliberately do not | that component's handoff note (e.g. [`trace-synthesis/downstream-scale-note.md`](trace-synthesis/downstream-scale-note.md)) | measured numbers + decisions with their reasons |
| an empirical / ML finding | an experiment `REPORT.md` | hypothesis → result |
| what an **external** source (a paper, a vendor audit, an upstream issue) already established | `docs/research/<topic>.md` | a survey, every claim cited and dated |
| a change a **consumer** must react to (removed name, changed default, shifted results) | `docs/releases/vX.Y.Z.md` for the version shipping it | what changed / why / what you must do |

## The single-source-of-truth guards

Each fact has one home; these guards keep it that way (authoritative text lives
where noted — this list only points):

- **One home holds *within* a document, too.** A **normative** statement — a
  rule, a threshold, a criterion, a definition — has a single home inside one
  doc as well as across docs; every other mention refers to it. Explanatory
  restatement (a summary, a lead-in, an example) is **not** a second home,
  provided it does not carry the authoritative value itself: "there is a minimum
  cohort gate" is fine, writing the number again in the summary is not. The
  discriminant is **"if this sentence were edited wrong, would a reader act on
  it?"** — if yes, it is a second home. Drift only happens where the values are:
  the hook list below went stale not because several docs mentioned hooks, but
  because three of them each carried a copy of the list.
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
- **Every doc needs a re-read trigger, or it rots.** For a `spec.md` the two
  triggers are mechanical and both live in the PR that outdates it: an ADR
  superseding a spec section rewrites that section in the same PR, and a task
  flipping to ✅ re-checks the spec's Success Criteria and out-of-scope list
  (see `AGENTS.md` → Boundaries).
- **An invariant needs a test, or it's downgraded** to "intended / today" (see
  `AGENTS.md` → Quality bar).
- **Delete guidance that contradicts the repo** — stale guidance is worse than
  missing, because it gets followed. **Nothing mechanical enforces this for
  docs.** The `no-stale-module-refs` pre-commit hook only bans renamed module
  tokens under `src/` and `tests/`, and it is deliberately blind to `docs/`
  (point-in-time records — `plans/task-NN-*.md`, `reviews/`, ADRs — are
  *supposed* to name retired code). Docs stay honest only through the re-read
  triggers above and review; when in doubt, delete rather than leave a claim you
  are not re-checking.
