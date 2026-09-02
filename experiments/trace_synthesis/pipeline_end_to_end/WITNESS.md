# First end-to-end run — witness and provenance

**This is not the report.** The report's body belongs to `REPORT.md`. This file
is what [`docs/conventions.md`](../../../docs/conventions.md#what-may-be-committed-as-evidence)
requires beside it: the derived numbers a claim rests on, provenance
identifying the corpus they came from, and the command that regenerates them
where that corpus exists.

## The split, and how it was decided

The rule asks two questions in order, and the first one wins.

**Question 1 — is this a product of dataset scale, one artifact per instance,
growing with the dataset?** For *every* file in a run directory the answer is
yes: the run tree is allocated per instance and per attempt, so the 10 × 2
batch produces twenty of each of these. **So the whole run directory is corpus,
off-repo, without exception** — including the small files. `patch.diff` (3,107
bytes) and `claude_code.native_transcript.json` (96 bytes) are not witnesses
because they are small; being small does not change what class an artifact
belongs to, and question 1 is asked about the class. `supervisor.jsonl` is the
same answer for the same reason, and it does not become in-repo by being the
only complete supervision account this run has — the rule says so in as many
words: an artifact that is dataset-scale stays off-repo *even when a claim
needs all of it*.

**Question 2 then buys an obligation, not an exemption**, and this file is that
obligation discharged. Nothing under the run directory is copied into the
repository. Corpus and witness stay disjoint by construction.

One consequence worth stating, because it is the reason a distribution is
carried here as a list rather than a count: the claim "no lapse occurred before
`cursor` 87" cannot be rederived from `16`. Its witness is the sixteen cursors
themselves, which is why they are written out below.

The same reasoning splits those sixteen in two. They share one exception class,
`PolicyLapseError <- JudgeAnswerError`, and that is what the class line counts —
but eleven of them are a provider response whose `content` was null, which
reaches the parser as `None`, and five are output that arrived and would not
parse. **A call that returned nothing and a call that returned something
unusable are two failures**, and a witness that prints only `16` cannot support
a claim about either. Each bucket is printed with its own cursors for the same
reason the union is: the claims here are about distribution.

## Provenance

| | |
| --- | --- |
| Corpus | `<runs-root>/supervised_rollout_and_unit_test/instance_internetarchive__openlibrary-5de7de19211e71b29b2f2ba3b1dff2fe065d660f-v08d8e8889ec945ab821fb156c04c7d2e2810debb/r0/` |
| Host | the machine that ran it; **nowhere else** — in two directories on that one machine, see below |
| Run date | 2026-09-02 (UTC) |
| Repository at run time | `main` at `3e97442` |
| Actor | Claude Code, pinned 2.1.212 (`claude.info`'s verbatim `--version`; `PINNED_CLAUDE_CODE_VERSION`, `src/swe_lab/harnesses/claude_code/binary.py:45`) |

Collected artifacts of the rollout attempt (`rollout/a0/`), sha256:

| File | Bytes | sha256 |
| --- | --- | --- |
| `claude.info` | 16146 | `1c5ca911339dacc05d589a0c92ef35bcce2f64d2c607bcea365e8a23d40e0097` |
| `claude_code.event_stream.jsonl` | 333401 | `28abd03ea8d67aa6b313396db9da71f5cb6010ebb9a45a558267cd40d1f55403` |
| `claude_code.exit_code.txt` | 2 | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` |
| `claude_code.native_transcript.json` | 96 | `9183e03cf69a384b18ffc5eaf71503c2900a9a5b5cba81f3b2b71956cf751f48` |
| `claude_code.native_transcript.tar.gz` | 60345 | `35504a1ae6bae54fe47357a90b55146d6d072613618c634a75278256a2a7f2dc` |
| `claude_code.proxy_log.jsonl` | 3064215 | `701808d7ee9941eaa9de11a87277ca9d5be49305fd59ab8981d0e57f347c9136` |
| `claude_code.proxy_stderr.log` | 5974 | `ae1a9d2633eb872a41a9c8a26ae0990489d30f40cb7fd35ffc48c9a7a8e47457` |
| `claude_code.stderr.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `conversation.json` | 143081 | `8a417505891a0bdd44010ff0b289c6e7522ab42db32ff04b148c0b8a7ebf9f7a` |
| `git_integrity.json` | 649 | `1d58b0118707ccf542987777c67aaeff77facab15f9e71357595a49873024550` |
| `patch.base_ref.txt` | 41 | `969099c00763545002cb9baed0887c35d9de4e5b56044bc82a6d029445d8b7d7` |
| `patch.diff` | 3107 | `8066ebf6196bc5c00591eb7f3a50c336c3df57e5e54adbb5557504f1c4a59775` |
| `patch.raw.diff` | 3107 | `8066ebf6196bc5c00591eb7f3a50c336c3df57e5e54adbb5557504f1c4a59775` |
| `supervisor.jsonl` | 27340 | `5950a671e1ce8f8d4c6a476cf3db3a85bf17274034320ecc0085ff2f8b793944` |
| `verifier.json` | 485 | `e379a25752f50b920a531c8c953db9eaa6eb73826359a6e0f4ea6992be151de1` |

And the records the outcome numbers come from:

| File | Bytes | sha256 |
| --- | --- | --- |
| `unit_test/a2/unit_test.output.json` | 3649 | `7bb08df2f5a3808bb8f153ad29201826350b6418edced84c0504f1fff25dbbaa` |
| `store/adhoc/…/rollout/a0/run.json` | 4178 | `6220ea9c692a846186a271164ab963395592928a943396f252bfa8a8e02454ac` |
| `store/adhoc/…/unit_test/a2/run.json` | 1853 | `ce5bb0b59caddae9b4b0a265f4355e417bfa62ebc6fd8c20a1f56ac92add3d5d` |
| `store/adhoc/…/workflow.json` | 5940 | `9482079d5e8d6198aea843b5e5db31e7054e35ab2416c771d7f0ef56b1ad2855` |

`patch.diff` and `patch.raw.diff` share a digest, as do the workspace and store
copies of every collected artifact — the same bytes under three names. The full
inventory, including those copies:

```sh
find "$ATTEMPT" -type f -printf '%10s  %p\n' | sort -k2 | \
  while read -r size path; do echo "$size $(sha256sum "$path")"; done
```

## The witness

Regenerated by one command, where `$ATTEMPT` is the `r0/` directory above:

```sh
python3 experiments/trace_synthesis/pipeline_end_to_end/witness.py "$ATTEMPT"
```

Its output on the corpus named above, verbatim:

```
boundaries               170
  silent                 151
  lapse                  16
  spoke                  3
spoke cursors            4, 8, 12
spoke at                 07:23:47.541, 07:24:04.404, 07:24:23.080
spoke policies           speak-when-off-track
lapse cursors            87, 96, 98, 100, 101, 106, 108, 109, 110, 128, 131, 134, 136, 140, 158, 165
lapse cursor range       87-165
lapse classes            16 x PolicyLapseError <- JudgeAnswerError
lapse no output          11  cursors 96, 98, 100, 106, 108, 109, 128, 134, 140, 158, 165
lapse unparsable output  5  cursors 87, 101, 110, 131, 136
events                   170
result events            1
events carrying a time   90
actor last stamped event 2026-09-02T07:26:19.485Z
supervisor first row     2026-09-02T07:23:35.650499+00:00
supervisor last row      2026-09-02T07:42:14.572388+00:00
supervisor span s        1118.9  (measured; both ends are supervisor rows)
rollout wall s           1124.47  (measured; claude_code.wall_seconds)
overlap with actor s     163.8  (at least)
tail after actor s       955.1  (at most)
tail / rollout wall      0.849  (at most; denominator 1124.47 s)
tail / supervisor span   0.854  (at most; denominator 1118.9 s)
proxy records            33
requests carrying it     24
occurrences              63
in the final message     3
in any response          0
native transcript report {"archived": true, "config_dir": "/agent-home/.claude", "exit_code": 0, "members": 3}
archive members          3
transcript files         1
transcript lines         122
lines carrying it        3
their types              attachment
metric agent_complete             1.0
metric claude_code.exit_code      0.0
metric claude_code.timed_out      0.0
metric claude_code.wall_seconds   1124.4685471039993
metric supervision.boundaries     170.0
metric supervision.corrections    3.0
metric supervision.lapses         16.0
metric patch_is_empty             0.0
metric supervision.unhealthy      False
metric unit_test.required         25.0
metric unit_test.passed           9.0
metric unit_test.missing          16.0
metric unit_test.resolved         0.0
patch.diff bytes         3107
```

Three of these lines are worth reading twice, because each is a coordinate that
a summary would drop:

- **Two ratios, and each one names its denominator.** They share a numerator
  and answer different questions, so a bare percentage is not a reading here.
  The one the report uses is `tail / rollout wall`: **the actor spent at most
  955.1 s of the rollout's 1124.47 s wall clock (84.9%) finished and waiting
  for the supervisor to catch up.** `tail / supervisor span` (85.4%) is a
  statement about the supervisor's own working time, not about what the run
  cost.
- **`events carrying a time 90`.** The `result` event carries no timestamp.
  `actor last stamped event` is the assistant event immediately before it, so
  the actor's stopping point is that instant **or later**. Only the span is
  measured — both of its ends are the supervisor's own rows. The overlap can
  only be longer than printed and the tail only shorter, which is why the
  script labels each line rather than leaving a reader to notice.
- **`in any response 0`** beside `occurrences 63`. The block appears only in
  what the actor *sent*, never in what came back. That is what makes it a
  reading about the actor's context rather than about our own relay.
- **`metric supervision.unhealthy False`** is the *absence* of the key, not a
  zero: the channel leaves no key rather than a zero, so this line says the
  metric was never emitted.

## Where the corpus lives

**Kept on the machine that ran it, and not uploaded.** That is the standing
decision, made by the repository's owner on 2026-09-02 — not an open question
and not a risk awaiting a ruling.

| | |
| --- | --- |
| The run tree, as the pipeline wrote it | `<runs-root>/supervised_rollout_and_unit_test/instance_…/r0/` |
| The kept copy | `~/corpora/swe-lab/first-e2e-2026-09-02/r0/` |

The run tree is not the copy's equal: it is gitignored, so `git worktree remove`
deletes it without asking, and re-running the same rollout id without
`--resume` deletes the whole output directory. The kept copy is what survives
those, and `~/corpora/<repo>/<run>/` is where a run's corpus goes.

The name is the rule's own word — *the corpus is off-repo, the witness is
in-repo* — rather than a container word like `data` that would hold anything.

It is checked rather than assumed: all **122** files match by path and sha256,
and the script above produces byte-identical output from either directory
(`$ATTEMPT` is the `r0/` under whichever one is at hand).

```sh
diff <(cd "$RUN_TREE" && find . -type f -exec sha256sum {} \; | sort -k2) \
     <(cd "$COPY"     && find . -type f -exec sha256sum {} \; | sort -k2)
```

**Nothing is published.** The repository's HF publishing is for *dataset
products* (`HF_TOKEN` is documented for `pipelines/related_files/traces.py` and
`datasets/deepswe/build_parquet.py --upload`); a run's corpus is not one of
those and is deliberately not uploaded.

**A planned destination exists**, and where it stands is stated once, in the
horizontal component's [task index](../../../docs/horizontal/plans/README.md)
(tasks 12 and 13) — read it there, not here. What that changes for this file:
machine-local is not a stopgap standing in for a decision nobody made.

What follows is the rule's own fallback, and it is the whole reason this file
exists: **the numbers above are rederivable only with the corpus in hand.** The
command that rederives them is named, the corpus it needs is identified by
digest, and a reader without that machine has the numbers and their provenance
rather than an assertion.
