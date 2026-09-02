# First e2e supervised rollout — launch runbook

Rewritten 2026-09-02 after an adversarial review of an earlier draft found 7
defects, 4 of them blocking. **Every substantive line below cites where it was
checked — a file:line, a command, or a source document.** A line without a
coordinate is a line nobody has checked; do not add one without checking it
first, and do not trust this file's coordinates without re-reading them if a
cited file has moved since 2026-09-02.

This is the launch procedure. The frozen closure criteria, cost accounting
and evidence rules live in
[`PREREGISTRATION.md`](PREREGISTRATION.md) — linked here, not repeated, for
the same reason `RolloutOutcome` and the seven acceptance points are linked
rather than copied throughout this repo's docs.

## 0. Where this runs

**The main checkout, `/home/ubuntu/dev/swe-lab` — never a worktree.**
`--output-root` (the flag that would let a worktree point its output
elsewhere) is on [#351](https://github.com/luolc/swe-lab/pull/351), not yet
on `main` as of this writing. Without it, a run's output lands under the
checkout it started in, and `git worktree remove` deletes gitignored content
silently (`docs/conventions.md:659` — "`git worktree remove` deletes
gitignored content, silently"). Running from the main checkout is not a
preference here; it is the substitute for a flag that does not exist yet.

## 1. Pre-flight — re-check every item at launch time, never trust a prior pass

A checklist item that was true yesterday is not evidence it is true today;
every box below is re-verified immediately before pressing the button in
§2, not read off this file.

- [ ] **The event-stream gap is fixed.** Build the supervised harness, take
      its invocation-script mount, and assert the event-stream filename
      appears in the script body:

      ```
      cd /home/ubuntu/dev/swe-lab
      uv run python3 -c "
      from swe_lab.harnesses.claude_code.harness import ClaudeCodeHarness
      from swe_lab.harnesses.claude_code.constants import EVENT_STREAM_NAME, AGENT_SCRIPT_NAME
      h = ClaudeCodeHarness(capture='proxy', correction_channel=True)
      script = h.mounts('/app')[AGENT_SCRIPT_NAME].resource.content.decode()
      assert EVENT_STREAM_NAME in script, 'event stream redirection missing from the invocation script'
      print('OK:', EVENT_STREAM_NAME, 'present')
      "
      ```

      **Verified PASSING** against `main` = `5145510` on 2026-09-02, now that
      [#349](https://github.com/luolc/swe-lab/pull/349) has merged (the fix
      landed there). Re-run this at launch time regardless — a passing check
      today is not evidence it still passes when the button is actually
      pressed, and this is exactly the item a later regression would be most
      costly to trust from memory. **If this fails, do not press the button
      in §2**: without the redirection, a supervised proxy-captured run's
      `event_stream.jsonl` is never written, the actor hangs on the
      correction-channel FIFO for the full agent timeout
      (`_AGENT_TIMEOUT_S = 3600.0`,
      `src/swe_lab/workflow/definitions.py:63`), and the run lands as
      `TIMED_OUT` — a wiring failure charged to the actor's budget
      (`PREREGISTRATION.md` §3, §7).
- [ ] `#349` is merged: `git -C /home/ubuntu/dev/swe-lab fetch origin && git
      -C /home/ubuntu/dev/swe-lab log --oneline -1 origin/main` names a
      commit that closes #349, and `git rev-parse main` ==
      `git rev-parse origin/main` in the main checkout. **Merged 2026-09-02**
      (`main` = `5145510`) — still a launch-time check, not a fact to carry
      forward from this sentence: re-verify, since this file does not update
      itself when `main` moves again.
- [ ] `#350` (the pre-registration) is merged **and has been read** —
      merged as of `ca4e5c4` (2026-09-02); its closure criteria are frozen
      before this run starts, and re-reading it now is what keeps them that
      way (`PREREGISTRATION.md` §9).
- [ ] No container is currently running or lingering from a prior attempt:
      `docker ps -q | wc -l` is `0`, **and** `docker ps -aq | wc -l` is
      checked too — a stopped-but-not-removed container still holds its
      writable layer, which on 2026-09-01 was the only reason one attempt's
      record survived at all (`docs/conventions.md:673` — the `docker rm`
      evidence-destruction hazard). Do not prune any container found here;
      investigate what it is first.
- [ ] **No merge is currently in progress against a file this run's
      pre-flight or launch depends on.** Not a path into any one session's
      scratchpad — a passing state here has to be checkable from any machine,
      by anyone, at any time:

      ```
      cd /home/ubuntu/dev/swe-lab
      gh pr list --state open --json number,title,mergeStateStatus
      ```

      Confirm none of the listed PRs both touches
      `src/swe_lab/trace_synthesis/`, `docs/conventions.md`, or
      `src/swe_lab/workflow/definitions.py` (`gh pr diff <n> --name-only` to
      check) and shows a merge actively underway. If uncertain, ask the
      orchestrating session directly before launch — a five-minute delay
      against a run that costs a rollout to redo.
- [ ] A container window for this run has been announced to the other
      agents sharing this box, and no agent runs docker-marked tests until
      it is closed (`AGENTS.md:108` — the one-container-at-a-time rule).
- [ ] The dataset loads and has the expected count:

      ```
      cd /home/ubuntu/dev/swe-lab
      uv run python -c "
      from swe_lab.datasets.loader import load_dataset
      ds = load_dataset('swebench_pro')
      assert len(ds) == 731, f'expected 731, got {len(ds)}'
      print('OK: 731 records')
      "
      ```

      Confirms the pinned parquet ([#345](https://github.com/luolc/swe-lab/pull/345))
      is present and intact before spending any wall-clock on a rollout that
      would fail at dataset load. Verified passing (`dataset_count=731`) on
      2026-09-02.
- [ ] The chosen instance's Docker image is present locally **now** — not
      merely "was present on some earlier date":

      ```
      cd /home/ubuntu/dev/swe-lab
      uv run python -c "
      from swe_lab.datasets.loader import load_dataset
      ds = load_dataset('swebench_pro')
      inst = ds.require('instance_internetarchive__openlibrary-5de7de19211e71b29b2f2ba3b1dff2fe065d660f-v08d8e8889ec945ab821fb156c04c7d2e2810debb')
      print(inst.sandbox_spec().image_ref)
      "
      ```

      then check that exact tag:

      ```
      docker images --format '{{.Repository}}:{{.Tag}}' | grep 'openlibrary-internetarchive__openlibrary-5de7de19'
      ```

      Verified passing on 2026-09-02: the image-derivation command prints
      `jefzda/sweap-images:internetarchive.openlibrary-internetarchive__openlibrary-5de7de19211e71b29b2f2ba3b1dff2fe065d660f-v08d8e8889ec945ab821fb156c04c7`,
      and the `grep` above finds exactly that line.

      For reference, the other three of #261's candidate instances — not
      what this run launches, PREREGISTRATION.md §2 already fixed the
      choice — were derived and checked present the same way, on 2026-09-02:

      | candidate | `grep` substring | image present |
      | --- | --- | --- |
      | `ansible-c1f2df47` | `ansible-ansible__ansible-c1f2df47` | yes |
      | `navidrome-5001518260` | `navidrome-navidrome__navidrome-5001518260` | yes |
      | `vuls-4c04acbd9` | `vuls-future-architect__vuls-4c04acbd9` | yes |

## 2. The command

The instance is
[`PREREGISTRATION.md` §2](PREREGISTRATION.md#2-the-instance)'s choice —
read it from there, not retyped here, so the two documents cannot disagree
about which instance this is. **Run through `uv run`, matching every other
command in this file.** Not because a bare `python -m swe_lab` is broken —
whether it works **depends on the reader's shell state**: in one checkout
with a `direnv`-activated virtualenv it succeeds; in a clean environment
(`env -u VIRTUAL_ENV -u PATH /usr/bin/python3 -m swe_lab --help`) it fails
with `No module named swe_lab`, and this worktree's own plain `python -c
"import swe_lab"` failed the same way on 2026-09-02, `VIRTUAL_ENV` unset.
A runbook a human reads on some machine cannot assume which of those two
states they're in. `uv run` is what removes the dependency on that state,
not a fix for a break — the two readings above are each true only in the
shell that produced them, and neither generalizes past it.

```
cd /home/ubuntu/dev/swe-lab
uv run python -m swe_lab run supervised_rollout_and_unit_test \
  instance_internetarchive__openlibrary-5de7de19211e71b29b2f2ba3b1dff2fe065d660f-v08d8e8889ec945ab821fb156c04c7d2e2810debb
```

Workflow name confirmed registered at `src/swe_lab/workflow/definitions.py:268`
on `main` (`register_workflow("supervised_rollout_and_unit_test", ...)`,
verified against `main` = `5145510` on 2026-09-02, after
[#349](https://github.com/luolc/swe-lab/pull/349) merged).

**No overrides. Three reasons, each independently checked, not remembered:**

1. **The syntax itself.** An override is spelled `--<entry>.<field-path>=value`;
   anything without a literal `=` is rejected outright
   (`src/swe_lab/cli/overrides.py:100` —
   `if not arg.startswith("--") or "=" not in arg: raise OverrideError`). A
   space-separated form does not parse at all.
2. **Overriding the actor's model would silently break a pin this workflow
   builds on purpose.** The actor's `DEFAULT_MODEL` and the judge's
   `SUPERVISOR_MODEL` are both pinned to Claude Sonnet 5 —
   `DEFAULT_MODEL = "claude-sonnet-5"`
   (`src/swe_lab/harnesses/claude_code/constants.py:114`, confirmed on
   current `main`) and `SUPERVISOR_MODEL = "anthropic/claude-sonnet-5"` (the
   same model, OpenRouter-qualified;
   `src/swe_lab/workflow/definitions.py:126`, also confirmed on current
   `main`). The pin is deliberate: it is what keeps a positive result from
   being read two ways at once — "supervision worked" versus "a stronger
   model's reasoning leaked into the judge." `--rollout.harness.model=...`
   overrides only the actor's half and nothing refuses the resulting
   mismatch.
3. **`--unit_test.retries=2` would be a no-op.** `_UNIT_TEST_RETRIES = 2` is
   already the default (`src/swe_lab/workflow/definitions.py:67`, used at
   lines 113 and 227).

## 3. If it fails — read this before doing anything

1. **Do not re-run on the same `--rollout-id`.** A non-`--resume` invocation
   deletes the prior attempt's output directory outright:
   `if not resume: output_dir.rmtree(missing_ok=True)`
   (`src/swe_lab/cli/run.py:139-142`), and the run's workspace lives under
   that same directory (`epath.Path(output_dir) / "ws" / f"a{attempt}"`,
   `src/swe_lab/workflow/run_task.py:367`). **The most natural next action
   after a failure — re-running the same command — is the action that
   destroys the evidence of what just happened**, including the proxy
   capture and `supervisor.jsonl` this run's own closure criteria depend on.
   `--resume` is not an escape hatch here: it refuses to re-run a task that
   already reached a terminal failure. Use a fresh `--rollout-id`, or move
   `output_dir` aside first, if the run needs to be repeated.
2. **The container is already gone — there is no "go look inside it
   afterward."** Teardown is best-effort and unconditional on every exit
   path, failure included: `"""Remove the container, best-effort; never
   raises."""` (`src/swe_lab/sandbox/backends/host.py:188`), and this fires
   on a Ctrl-C too. Anything that existed only in the container's writable
   layer and was not copied out before teardown is not recoverable after
   the fact.

## 4. Reading the result

The closure rules are frozen in `PREREGISTRATION.md` and are not restated
here — re-read them there, at report time, not from memory of this run's
launch. Two consequences worth flagging before the button is pressed, because
they are exactly the ones a post-hoc reading would be tempted to soften:

- A `TIMED_OUT` outcome on this run defaults to **our** fault, not the
  actor's, unless `proxy_log.jsonl` itself shows otherwise
  (`PREREGISTRATION.md` §7).
- **If [#351](https://github.com/luolc/swe-lab/pull/351) has not landed by
  the time this run happens, the actor's own native session transcript is
  not merely "not yet collected" — it is destroyed with the container,
  permanently, for this specific run**, and cannot be recovered by any later
  work (§3 of this runbook). Under `PREREGISTRATION.md` §5's frozen branch,
  that means points 3 and 4 of task 01's acceptance table are **not closed**
  by this run, full stop — not "closed provisionally," not "closed pending
  confirmation." Check whether #351 landed **before**, not after, deciding
  what this run is worth running for.

## 5. Cost

- **Actor side — measured, a range, not a point.** `$2.04`–`$4.17`, mean
  `$3.00` (`docs/trace-synthesis/downstream-scale-note.md:18-32`). That
  source states its own reading rule — "treat the spread, not the mean, as
  the planning input" — and the range carries four coordinates that do not
  all match this run: `n=3`, two instances of one repository, actor
  `claude-sonnet-5`, Claude Code `2.1.212`, workflow `rollout_and_unit_test`
  — **unsupervised**, so this figure excludes the judge's own per-boundary
  call, which this run's workflow adds.
- **Supervision side — call counts only, not money.**
  `supervision.boundaries` / `supervision.corrections` on the rollout
  record are available now that `#349` has landed
  (`PREREGISTRATION.md` §6, point 2). Tokens and dollars are **not
  instrumented** for either count
  (`PREREGISTRATION.md` §6, point 3) — do not convert a call count into a
  cost figure; nothing in this run's tooling supports that conversion.
- **Cap for this stage: 10 SWE-bench Pro instances × 2 rollouts**
  (`docs/trace-synthesis/downstream-scale-note.md:10-11`). **This launch is
  one instance, one rollout** — the first point on that budget, not a
  license to spend the rest of it in the same session.

## Boundaries

- **Do not edit `docs/conventions.md`** — queued for a single pass by the
  wiring line.
- **Do not edit anything under `src/swe_lab/trace_synthesis/`** —
  [#348](https://github.com/luolc/swe-lab/pull/348) and
  [#351](https://github.com/luolc/swe-lab/pull/351) are open and queued
  against those files as of 2026-09-02 ([#349](https://github.com/luolc/swe-lab/pull/349)
  has since merged); a concurrent edit against either open one is exactly
  the kind of collision this repo has already paid for once today.
