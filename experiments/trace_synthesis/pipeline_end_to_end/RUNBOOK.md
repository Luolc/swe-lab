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
elsewhere) **landed** with [#351](https://github.com/luolc/swe-lab/pull/351)
on 2026-09-02 — `main` = `5875077`, `src/swe_lab/cli/run.py:100`. This
paragraph used to say it was not on `main` yet, and that premise is what
changed; the instruction did not. A run leaves more behind than its output
root — its container, its caches, its `.envrc.local` — and
`git worktree remove` deletes gitignored content silently
(`docs/conventions.md:659`). Run from the main checkout and leave
`--output-root` at its default.

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
- [ ] **The correction channel owns its drop directory.** Build the shipped
      supervised arm's observer, run its `after_create` against a stand-in
      workspace, and assert the drop directory exists and belongs to us:

      ```
      cd /home/ubuntu/dev/swe-lab
      uv run python3 -c "
      import dataclasses, os, tempfile
      from etils import epath
      from swe_lab.rollout import CodingAgentTask
      from swe_lab.workflow.definitions import SUPERVISED_ROLLOUT
      task = SUPERVISED_ROLLOUT[0].task
      assert isinstance(task, CodingAgentTask) and task.supervision_factory is not None
      observer = task.supervision_factory('a task')
      @dataclasses.dataclass
      class Workspace:
        workspace: epath.Path
      with tempfile.TemporaryDirectory() as root:
        observer.after_create(Workspace(epath.Path(root)))
        drop = os.path.join(root, 'corrections')
        assert os.path.isdir(drop), 'the drop directory was not created before the actor could'
        assert os.stat(drop).st_uid == os.getuid(), 'the drop directory is not ours to write into'
      print('OK: drop directory exists and is ours')
      "
      ```

      No container, one second. **Verified both ways on 2026-09-02** against
      `main` = `52addd3`: it prints `OK: drop directory exists and is ours`,
      and reverting the fix in `CorrectionChannel.__post_init__`
      (`src/swe_lab/trace_synthesis/channel.py`) turns it into
      `AssertionError: the drop directory was not created before the actor
      could`. So it is a gate, not decoration. **Why it is repeated here and
      not left to the suite:** the property is otherwise held by a single
      docker-marked test, and the docker-marked tests are CI's job
      (`docs/conventions.md:443` — the local-suite/CI jurisdictions entry),
      so a regression that lands on `main` is invisible in the local gate
      right up to the moment the button is pressed. **If this fails, do not
      press the button in §2**: the in-sandbox relay creates that directory
      as root the instant the actor's script starts, `mkdir -p` on an
      existing directory does not reset ownership, and the host side can then
      never write into it — so the treatment arm delivers nothing, while the
      control arm (`budget=0`) is silent by design. The two arms become
      indistinguishable, and an hour of wall clock and a few dollars buy a
      result nobody can attribute. Fixed in
      [#353](https://github.com/luolc/swe-lab/pull/353).
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
- [ ] **The run happens inside a window orchestra has announced, and not
      before.** Two conditions this run needs — no other agent running a
      docker-marked test or holding a container
      (`AGENTS.md:108` — the one-container-at-a-time rule; this run occupies
      one for a long time), and no local merge in progress against
      `src/swe_lab/trace_synthesis/`, `docs/conventions.md`, or
      `src/swe_lab/workflow/definitions.py` (a concurrent merge moves `main`,
      which the pre-flight's `main` == `origin/main` check above depends on
      staying still) — are **not locally self-checkable**: a local,
      unpushed merge is invisible to any remote query (`gh pr list`'s
      `mergeStateStatus` reports a PR's own mergeability against its remote
      base, not "a merge is happening right now," so it cannot stand in for
      this). Only the orchestrating session, which tracks every agent
      working this repo, actually knows. **So this is not a command to run
      and check — it is waiting for that session's own announcement that the
      window is open, and stopping the instant it announces the window
      closed.** A check that cannot deliver the guarantee it claims is worse
      than no check: it is what let this exact gap through twice already.
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
      merely "was present on some earlier date" — checked as **an exact
      match**, not a substring, which a different or stale tag sharing the
      same prefix would also match. One command derives the reference and
      checks it:

      ```
      cd /home/ubuntu/dev/swe-lab
      uv run python -c "
      import subprocess, sys
      from swe_lab.datasets import load_dataset
      iid = 'instance_internetarchive__openlibrary-5de7de19211e71b29b2f2ba3b1dff2fe065d660f-v08d8e8889ec945ab821fb156c04c7d2e2810debb'
      inst = next(i for i in load_dataset('swebench_pro') if i.instance_id == iid)
      ref = inst.sandbox_spec().image_ref
      proc = subprocess.run(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}'],
                             capture_output=True, text=True)
      if proc.returncode != 0:
        print(f'DOCKER-FAILED rc={proc.returncode}: {proc.stderr.strip()}', file=sys.stderr)
        sys.exit(2)
      present = ref in set(proc.stdout.split())
      print(('PRESENT' if present else 'MISSING'), ref)
      sys.exit(0 if present else 1)
      "
      ```

      Derivation from `datasets/swebench_pro/record.py:267`
      (`image_ref = f"{IMAGE_REPO}:{self.dockerhub_tag}"`) and
      `IMAGE_REPO = "jefzda/sweap-images"`
      (`datasets/swebench_pro/constants.py:17`). **Three outcomes, three
      exit codes, three different next actions — not decoration, the
      criteria this step exists to produce:**

      | exit | meaning | do |
      | --- | --- | --- |
      | `0` | `PRESENT` | continue |
      | `1` | `MISSING` | pull the image **before** the container window is announced, not inside it |
      | `2` | `DOCKER-FAILED` | stop — this is not a missing image, Docker itself is unreachable (`stderr` names why, passed through verbatim, not paraphrased); fix Docker, then start this checklist over |

      Verified all three paths independently on 2026-09-02: `PRESENT`
      (exit `0`), `MISSING` against a fabricated tag (exit `1`), and
      `DOCKER-FAILED` against a deliberately malformed `docker` invocation
      (exit `2`, `stderr` surfaced).

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
- **[#351](https://github.com/luolc/swe-lab/pull/351) landed** on 2026-09-02
  (`main` = `5875077`,
  `src/swe_lab/harnesses/claude_code/native_transcript.py`), so the actor's
  own native session record is taken out of the container before it dies.
  This bullet used to say the opposite, conditionally: that without it the
  transcript is destroyed with the container, permanently, for this specific
  run — which is why `PREREGISTRATION.md` §5's frozen branch turns points 3
  and 4 on exactly this dependency. Re-check that #351 is still on `main` at
  launch time rather than trusting this sentence, and read §5 there for what
  the two points now require.
- **`supervision.corrections == 0` is not a neutral reading.** It has two
  sources — the policy genuinely saw no off-track boundary, or delivery was
  broken — and **the number alone does not separate them.** Read it against
  `supervision.boundaries`, which `PREREGISTRATION.md` §6 readout 2 reports
  beside it: `boundaries > 0` with `corrections == 0` is "judged every
  boundary and stayed silent," which can be legitimate; `boundaries == 0` is
  "nothing was ever judged," which is not a supervised run at all. **A
  supervised run that spoke zero times has to be adjudicated in the report;
  it is never written down as an ordinary result.** Out of
  [#353](https://github.com/luolc/swe-lab/pull/353), whose own failure was
  failure-closed and would have surfaced as `SUPERVISION_FAILED` — but whose
  shape is general: **the treatment arm collapsing silently into the control
  arm.** Nothing false is recorded; the run just answers a question nobody
  asked.

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
- **Do not edit anything under `src/swe_lab/trace_synthesis/`,
  `src/swe_lab/workflow/definitions.py` or `src/swe_lab/harnesses/` while a
  run holds the window — and do not merge a PR that touches them.** A merge
  moves `main`, which §1's `main` == `origin/main` check depends on holding
  still for the length of the run; a concurrent edit against an in-flight PR
  on those files is the kind of collision this repo has already paid for
  once. **Which PRs are in flight is deliberately not recorded here**: that
  is `gh pr list --state open`, and the merge discipline is whatever the
  current window announcement says. This bullet used to enumerate them, and
  the enumeration went stale *during the review of the change that wrote
  it* — a snapshot of which PRs are open stays true for about an hour, in a
  file people execute.
