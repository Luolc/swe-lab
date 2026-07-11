# Batch annotation — manual QA log

Random-sampled instances annotated with the finalized pipeline (3 samples +
aggregate). Each is QA'd by hand as it lands: a brief note if fine, a detailed
one if something is wrong. Outputs live under `outputs/related_files/swebench_pro/<id>/`
(committed as the deliverable). Sampling seed: `20260706`
(`round{1,2,3}_ids.txt`).

Legend: ✅ good · ⚠️ minor · ❌ problem.

## Round 1

_In progress. Rolling window of 4 concurrent pipelines (8-core / 16 GB box)._

| # | instance | lang | agg snippets | valid | note |
| --- | --- | --- | --- | --- | --- |
| 4 | protonmail/webclients `da91f08` | js | 5 | ✅ | on-target: all 4 gold-patch files + `dom.ts` helper; candidates 5/5/5 |
| 1 | internetarchive/openlibrary `25858f9` | python | 11 | ✅ | update_work/edition + retry helper + test; the "missed" `solr/utils.py` is a NEW file (correctly excluded — can't read what doesn't exist yet); cand 11/11/10 |
| 3 | qutebrowser `ff1c025` | python | 11 | ✅ | all 3 code gold files + tests; correctly drops changelog doc; cand 10/10/10 |
| 2 | future-architect/vuls `83bcca6` | go | 12 | ✅ | all 7 gold files + a test; cand 11/12/11 |
| 7 | internetarchive/openlibrary `c05ccf2` | python | 8 | ✅ | gold file + callers/helpers + tests; cand 8/8/8 |
| 6 | gravitational/teleport `1a77b79` | go | 4 | ✅ | gold file (3 focused ranges) + test; cand 4/4/4 |
| 5 | gravitational/teleport `e6d8629` | go | 8 | ✅ | all 4 gold files + access_checker context + test; cand 7/7/6 |
| 8 | future-architect/vuls `b8db2e0` | go | 11 | ✅ | all 9 gold files + test; similar-pattern impls tagged well; cand 10/10/11 |
| 9 | ansible/ansible `be59caa` | python | 5 | ✅ | iptables.py (3 ranges) + tests; correctly drops changelog fragment; cand 5/5/5 |
| 11 | internetarchive/openlibrary `6072570` | python | 4 | ⚠️ | core model method + account.py + test; missed 2 template files of 4 gold (UI layer); cand 3/4/4 |
| 10 | element-hq/element-web `33299af` | js | 13 | ✅ | all 3 gold files (incl. CSS) + RightPanelStore/hooks/test; cand 10/13/11 |
| 14 | ansible/ansible `de01db0` | python | 4 | ✅ | pip.py (3 ranges) + test; correctly drops changelog + porting-guide docs; cand 4/4/4 |
| 13 | ansible/ansible `502270c` | python | 5 | ✅ | hostname.py core + test + _utils; one broad 230-667 similar-pattern range (strategy classes; relevant but large); cand 5/8/4 |
| 15 | navidrome/navidrome `27875ba` | go | 8 | ✅ | consts/serve_index/config/embed + test; both "missed" gold files are NEW (loader + yaml), correctly excluded; cand 8/7/7 |
| 12 | ansible/ansible `b748ede` | python | 9 | ✅ | all 4 existing gold code files (galaxy/api, urls, uri, action/uri) + test; missed only changelog (NEW); cand 6/8/9 |
| 16 | flipt-io/flipt `7161f7b` | go | 5 | ✅ | both code gold files + tests; missed only go.work.sum (checksum/lock, correctly excluded); cand 5/5/5 |
| 17 | qutebrowser `bf045f7` | python | 5 | ✅ | gold file (webenginetab) + base class browsertab + debug + tests; no misses; cand 5/5/6 |
| 18 | element-hq/element-web `a692fe2` | js | 8 | ✅ | all 4 existing gold code files + test; missed only shouldForceDisableEncryption.ts (NEW); cand 10/7/9 |
| 19 | navidrome/navidrome `97434c1` | go | 10 | ✅ | all 8 gold code files + request + test; missed only new migration + go.mod/go.sum (deps, correctly excluded); cand 9/10/10 |
| 20 | navidrome/navidrome `6b3b4d8` | go | 3 | ✅ | both existing gold code files (walk_dir_tree revert + tag_scanner) + test; missed only utils/paths.go (NEW); cand 4/4/3 |

### Round 1 summary

20/20 valid & complete; **19 ✅ / 1 ⚠️ / 0 ❌**. Cost $34.62 (80 agent calls).

- The annotator reliably covers the **existing** gold-patch code files + relevant
  tests, and correctly excludes: files the patch *creates* (new files), docs
  (changelog / porting-guide / `.asciidoc`), and dependency manifests
  (`go.mod` / `go.sum` / `go.work.sum`). Candidates were very consistent.
- The one genuine gap (**#11**): two **existing** UI template files (`*.html`)
  the fix modifies were omitted, while core logic + tests were covered.
- **No severe problems.** Refinement for round 2: a prompt line to ensure every
  *existing* file the gold patch modifies is covered (not files it creates).

## Round 2

_Tuned prompt (coverage line). Lighter QA: valid + gold-code coverage per example._

| # | instance | lang | agg snippets | valid | existing-gold covered | note |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | flipt-io/flipt `1737085` | go | 8 | ✅ | 2/2 | cand 6/7/9 |
| 4 | future-architect/vuls `dc49646` | go | 8 | ✅ | 4/4 | cand 6/6/7 |
| 2 | future-architect/vuls `73f0ada` | go | 12 | ✅ | 4/4 | cand 11/13/12 |
| 5 | element-hq/element-web `72a8f8f` | js | 5 | ✅ | 2/2 | cand 5/5/5 |
| 3 | protonmail/webclients `7e54526` | js | 19 | ✅ | 11/11 | cand 18/17/14 |
| 8 | qutebrowser `1af602b` | python | 6 | ✅ | 1/1 code (only miss = changelog.asciidoc doc, correctly excluded) | cand 6/6/4 |
| 6 | internetarchive/openlibrary `b67138b` | python | 17 | ⚠️ | 5/6 existing code; missed parse_xml.py (MARC parser, relevant) | cand 15/15/16 |
| 7 | qutebrowser `e5340c4` | python | 11 | ✅ | 7/7 | cand 9/9/11 |
| 11 | flipt-io/flipt `381b90f` | go | 9 | ✅ | 7/7 | cand 9/9/9 |
| 9 | future-architect/vuls `f0b3a8b` | go | 6 | ✅ | 4/4 code (only miss = go.mod/go.sum deps) | cand 7/5/8 |
| 10 | qutebrowser `5e0d6dc` | python | 7 | ✅ | 3/3 | cand 7/7/7 |
| 14 | NodeBB/NodeBB `da0211b` | js | 5 | ✅ | 2/2 | cand 5/4/4 |
| 15 | qutebrowser `8d05f02` | python | 11 | ✅ (recovered) | one sample ended without writing output (MissingOutputError) → whole pipeline died. Root cause: error raised after the retry loop. Fixed (retry no-output samples + pipeline tolerates a failed sample); re-run succeeded, cand 11/10/11. |
| 13 | ansible/ansible `42355d1` | python | 16 | ✅ (recovered) | pre-fix no-output failure; re-run with the fix succeeded, 3 candidates 13/13/15, existing-gold 5/5 |
| 12 | ansible/ansible `d58e69c` | python | 15 | ✅ | 3/3 | cand 14/16/16 |
| 16 | future-architect/vuls `fd18df1` | go | 8 | ✅ | 3/3 code (only miss = go.mod/go.sum) | cand 8/10/10 |
| 18 | internetarchive/openlibrary `77c16d5` | python | 8 | ✅ | 7/7 | cand 8/8/8 |
| 17 | gravitational/teleport `4d0117b` | go | 17 | ✅ | 3/3 | cand 17/17/18 |
| 19 | tutao/tutanota `1ff82aa` | ts | 9 | ✅ | 3/3 | cand 10/10/9 |
| 20 | internetarchive/openlibrary `62d2243` | python | 11 | ✅ | 2/2 | cand 11/7/7 |

### Round 2 summary

20/20 valid & complete; **19 ✅ / 1 ⚠️ / 0 ❌** (both no-output failures recovered
after the robustness fix). The tuned prompt (existing-file coverage line) held up.

- **Coverage.** Every *existing* gold-patch code file is covered except one
  (below). A whole-repo self-check of all aggregates on disk found the only
  "missed" files to be legitimately-excluded non-code: docs (`.rst`
  porting-guide, `.asciidoc` changelogs), dependency lockfiles (`go.mod` /
  `go.sum` / `go.work.sum`), and **generated** protobuf (`*.pb.go`). None are
  readable source a solver would open.
- **The one genuine gap (#6):** `openlibrary/catalog/marc/parse_xml.py` (a MARC
  parser the fix touches) was omitted by all three candidates despite the
  coverage line — a single-file miss on one instance, core logic + tests still
  covered. Minor, not severe.
- **Robustness validated in the field.** Two samples (#13, #15) hit the flaky
  MissingOutputError; the fix (`df22a2f` — retry no-output samples + aggregate
  the survivors) recovered both on re-run, with no pipeline deaths afterward.
- **No severe problems.** Proceeding to round 3 (41–60) with the same setup.

## Round 3

_Same setup (tuned prompt, 3+aggregate, rolling window of 4). Examples 41–60,
`round3_ids.txt`, disjoint from rounds 1–2._

| # | instance | lang | agg snippets | valid | existing-gold covered | note |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | NodeBB/NodeBB | js | 8 | ✅ | 2/2 | cand 8/8/8 |
| 2 | internetarchive/openlibrary | python | 6 | ✅ | 5/5 | cand 6/5/5 |
| 3 | ansible/ansible | python | 7 | ✅ | 2/2 | cand 6/7/8 |
| 4 | ansible/ansible | python | 6 | ✅ | 0/0 (new-module task; all gold files are NEW — snippets point at sibling modules) | cand 8/5/5 |
| 5 | NodeBB/NodeBB | js | 5 | ✅ | 2/106* | *noisy bundled gold patch: 99 files are i18n `public/language/*.json` (excluded), and 5 more belong to an unrelated "reputation" feature squashed into the same commit. Annotation correctly targets the real fix `src/plugins/install.js` + error msg + test. cand 3/5/3 |
| 7 | flipt-io/flipt | go | 8 | ✅ | 2/13* | *all 11 "missed" are infra/non-source (CI workflows, .golangci/.goreleaser, Makefile, Dockerfile, build script, CHANGELOG/README docs, go.mod/go.sum) — bundled release churn, correctly excluded. Both config source files covered. cand 7/8/7 |
| 6 | ansible/ansible | python | 13 | ✅ | 4/5 (only miss = installing_collections.txt docs snippet, correctly excluded) | cand 12/10/12 |
| 10 | future-architect/vuls | go | 5 | ✅ | 1/1 | cand 5/5/4 |
| 8 | element-hq/element-web | js | 9 | ✅ | 2/2 | cand 9/8/8 |
| 9 | ansible/ansible | python | 12 | ✅ | 5/5 | cand 13/11/13 |
| 13 | navidrome/navidrome | go | 6 | ✅ | 2/2 | cand 5/6/5 |
| 12 | NodeBB/NodeBB | js | 11 | ✅ | 7/7 | cand 10/12/10 |
| 14 | element-hq/element-web | js | 5 | ✅ | 2/2 | cand 5/5/5 |
| 15 | ansible/ansible | python | 3 | ✅ | 2/2 | cand 3/5/3 |
| 11 | protonmail/webclients | js | 15 | ✅ | 11/11 | cand 14/14/15 |
| 18 | ansible/ansible | python | 6 | ✅ | 2/2 | cand 6/7/6 |
| 16 | flipt-io/flipt | go | 8 | ✅ | 5/5 | cand 7/8/7 |
| 19 | ansible/ansible | python | 7 | ✅ | 1/1 | cand 7/7/8 |
| 17 | flipt-io/flipt | go | 13 | ✅ | 3/3 | cand 12/12/13 |
| 20 | protonmail/webclients | js | 14 | ✅ | 11/11 | cand 12/15/17 |

### Round 3 summary

20/20 valid & complete; **20 ✅ / 0 ⚠️ / 0 ❌**. No pipeline deaths (robustness
fix held). The tuned prompt continues to cover existing gold code + tests and
exclude new files, docs, lockfiles, and generated code.

- **Two noisy bundled gold patches surfaced (a dataset property, not an agent
  bug).** #5 (NodeBB plugin-identifier validation) ships a gold patch of **106
  files** — 99 are i18n `public/language/*.json`, and 5 belong to an unrelated
  "reputation settings" feature squashed into the same commit; the annotation
  correctly targeted the real fix (`src/plugins/install.js` + error message +
  test). #7 (flipt config metadata) bundles CI/release infra (workflows,
  `.goreleaser`, `Makefile`, `Dockerfile`, docs, `go.mod/go.sum`); the
  annotation covered the two config source files and excluded the infra. In both
  cases the annotation is **better-scoped than the raw gold patch**.
- **Whole-corpus self-check (all 61 aggregates on disk): 0 invalid.** Only two
  instances "miss" a source-looking gold file: #5 above (the unrelated bundled
  feature) and round-2 #6 (`parse_xml.py`, the single genuine minor miss). Every
  other apparent miss is a doc, lockfile, generated file, or new file.
- **No severe problems across 60 sampled instances (rounds 1–3).**

### Cross-round tally (60 sampled)

| round | valid | ✅ | ⚠️ | ❌ | notes |
| --- | --- | --- | --- | --- | --- |
| 1 | 20/20 | 19 | 1 | 0 | ⚠️ = #11 two existing UI templates missed |
| 2 | 20/20 | 19 | 1 | 0 | ⚠️ = #6 parse_xml.py; 2 flaky no-output samples recovered |
| 3 | 20/20 | 20 | 0 | 0 | 2 noisy bundled gold patches correctly scoped |
| **all** | **60/60** | **58** | **2** | **0** | 0 severe; 0 invalid across 61 aggregates on disk |

## Round 4

_Examples 61–80. `round4_ids.txt` = `Random(20260706).sample(remaining, 40)[:20]`
where `remaining` excludes all of rounds 1–3 (pairwise-disjoint, verified). Same
pipeline + rolling window of 4._

| # | instance | lang | agg snippets | valid | existing-gold covered | note |
| --- | --- | --- | --- | --- | --- | --- |
| 3 | navidrome/navidrome | go | 6 | ✅ | 5/5 | cand 6/6/6 |
| 1 | NodeBB/NodeBB | js | 8 | ✅ | 3/3 | cand 8/7/8 |
| 2 | qutebrowser/qutebrowser | python | 8 | ✅ | 4/4 | cand 7/8/8 |
| 6 | internetarchive/openlibrary | python | 3 | ✅ | 1/1 | cand 3/3/3 |
| 8 | flipt-io/flipt | go | 5 | ✅ | 1/1 | cand 5/4/4 |
| 7 | gravitational/teleport | go | 15 | ✅ | 1/1 | cand 16/14/17 |
| 5 | navidrome/navidrome | go | 18 | ✅ (recovered) | 10/10 | aggregate CLI exited nonzero with empty stderr → classified "unknown" → not retried → killed the run (3 samples were fine). Fixed: empty-signal failures now retryable; re-aggregated from saved candidates. cand 17/20/17 |
| 4 | flipt-io/flipt | go | 15 | ✅ | 8/9 (only miss = go.work.sum lockfile, correctly excluded) | cand 14/13/13 |
| 9 | flipt-io/flipt | go | 9 | ✅ | 3/3 | cand 8/8/10 |
| 12 | qutebrowser/qutebrowser | python | 4 | ✅ | 2/2 | cand 4/4/3 |
| 10 | qutebrowser/qutebrowser | python | 5 | ✅ | 2/4 (both misses = .asciidoc docs, correctly excluded) | cand 7/6/5 |
| 11 | flipt-io/flipt | go | 5 | ✅ | 2/3 (only miss = go.work.sum lockfile, correctly excluded) | cand 5/5/5 |
| 13 | internetarchive/openlibrary | python | 7 | ✅ | 1/1 | cand 7/6/6 |
| 14 | qutebrowser/qutebrowser | python | 9 | ✅ | 4/4 | cand 9/8/9 |
| 15 | flipt-io/flipt | go | 18 | ✅ | 5/7 (misses = go.mod/go.sum deps, correctly excluded) | cand 17/17/17 |
| 18 | gravitational/teleport | go | 6 | ✅ | 4/4 | cand 5/6/5 |
| 16 | flipt-io/flipt | go | 12 | ✅ | 6/6 | cand 10/11/12 |
| 17 | protonmail/webclients | js | 9 | ✅ | 8/8 | cand 9/9/9 |
| 20 | flipt-io/flipt | go | 6 | ✅ | 2/2 | cand 6/6/5 |
| 19 | qutebrowser/qutebrowser | python | 10 | ✅ | 3/4 (only miss = commands.asciidoc docs, correctly excluded) | cand 10/11/10 |

### Round 4 summary

20/20 valid & complete; **20 ✅ / 0 ⚠️ / 0 ❌**. Whole-corpus self-check (all 81
aggregates on disk): 0 invalid; the only source-file misses remain the two known
cases (round-3 #5 bundled reputation feature, round-2 #6 `parse_xml.py`). Round 4
introduced no new gaps.

- **One transient failure, caught and fixed (#5, navidrome).** The aggregate
  step exited nonzero and the run died even though all 3 samples had succeeded.
  Investigation (`.cache/annotate-failures/…__agg.log`) showed the real cause was
  an **API 401 "Invalid authentication credentials"** written to *stdout* — a
  mid-session OAuth-token blip after 18 turns / ~13.5 min of successful work; an
  immediate re-aggregate from the saved candidates succeeded. Two code fixes
  landed (`f78594c`, `3044d87`): parse stdout on failure so API errors are
  classified from `result`/`api_error_status` instead of empty stderr, and treat
  a mid-session 401 as retryable. Recovered via re-aggregation → ✅ 10/10.
- **No severe problems.**

### Cross-round tally (80 sampled)

| round | valid | ✅ | ⚠️ | ❌ | notes |
| --- | --- | --- | --- | --- | --- |
| 1 | 20/20 | 19 | 1 | 0 | ⚠️ = #11 two existing UI templates missed |
| 2 | 20/20 | 19 | 1 | 0 | ⚠️ = #6 parse_xml.py; 2 flaky no-output samples recovered |
| 3 | 20/20 | 20 | 0 | 0 | 2 noisy bundled gold patches correctly scoped |
| 4 | 20/20 | 20 | 0 | 0 | 1 transient 401 caught → classifier fix; recovered |
| **all** | **80/80** | **78** | **2** | **0** | 0 severe; 0 invalid across 81 aggregates on disk |

## Round 5

_Examples 81–100 (final round to reach 100). `round5_ids.txt` = the second half
of `Random(20260706).sample(remaining, 40)` (disjoint from rounds 1–4). Runs with
the CLI-failure classification fix live._

| # | instance | lang | agg snippets | valid | existing-gold covered | note |
| --- | --- | --- | --- | --- | --- | --- |
| 4 | protonmail/webclients | js | 6 | ✅ | 5/5 | cand 6/7/6 |
| 3 | future-architect/vuls | go | 9 | ✅ | 5/7 (misses = go.mod/go.sum deps, correctly excluded) | cand 11/9/11 |
| 2 | navidrome/navidrome | go | 13 | ✅ | 1/1 | cand 11/11/10 |
| 1 | navidrome/navidrome | go | 16 | ✅ | 6/6 | cand 18/12/17 |
| 6 | ansible/ansible | python | 6 | ✅ | 1/1 | cand 6/4/5 |
| 5 | protonmail/webclients | js | 6 | ✅ | 2/2 | cand 5/6/4 |
| 8 | internetarchive/openlibrary | python | 9 | ✅ | 4/4 | cand 9/9/9 |
| 7 | element-hq/element-web | js | 7 | ✅ | 3/3 | cand 8/8/8 |
| 10 | navidrome/navidrome | go | 7 | ✅ | 5/6 (only miss = reflex.conf dev hot-reload config, not app source) | cand 8/7/7 |
| 9 | gravitational/teleport | go | 10 | ✅ | 2/2 | cand 10/7/9 |
| 11 | future-architect/vuls | go | 15 | ✅ | 10/10 | cand 15/15/19 |
| 12 | internetarchive/openlibrary | python | 18 | ✅ | 9/9 | cand 16/16/19 |
| 14 | ansible/ansible | python | 16 | ✅ | 3/3 | cand 15/16/14 |
| 13 | flipt-io/flipt | go | 16 | ✅ | 5/5 | cand 18/12/13 |
| 15 | ansible/ansible | python | 4 | ✅ | 1/1 | cand 4/4/4 |
| 18 | protonmail/webclients | js | 7 | ✅ | 1/1 | cand 7/7/6 |
| 17 | qutebrowser/qutebrowser | python | 13 | ✅ | 5/5 | cand 13/13/13 |
| 16 | flipt-io/flipt | go | 12 | ✅ | 8/8 | cand 10/13/12 |
| 20 | flipt-io/flipt | go | 8 | ✅ | 3/3 | cand 8/6/7 |
| 19 | internetarchive/openlibrary | python | 22 | ✅ | 10/10 | cand 21/22/21 |

### Round 5 summary

20/20 valid & complete; **20 ✅ / 0 ⚠️ / 0 ❌**. Ran with the CLI-failure
classification fix live — no failures this round. Whole-corpus self-check (all
101 aggregates on disk): 0 invalid; the only source-file misses remain the two
known cases (round-3 #5 bundled feature, round-2 #6 `parse_xml.py`).

- One borderline (#10, navidrome): the only uncovered existing file was
  `reflex.conf`, a dev hot-reload tooling config — not application source a
  solver would read. Counted ✅.
- **No severe problems.**

### Final tally (100 sampled — phase 1 complete)

| round | valid | ✅ | ⚠️ | ❌ | notes |
| --- | --- | --- | --- | --- | --- |
| 1 | 20/20 | 19 | 1 | 0 | ⚠️ = #11 two existing UI templates missed |
| 2 | 20/20 | 19 | 1 | 0 | ⚠️ = #6 parse_xml.py; 2 flaky no-output samples recovered |
| 3 | 20/20 | 20 | 0 | 0 | 2 noisy bundled gold patches correctly scoped |
| 4 | 20/20 | 20 | 0 | 0 | 1 transient 401 caught → classifier fix; recovered |
| 5 | 20/20 | 20 | 0 | 0 | clean; ran with the fix live |
| **all** | **100/100** | **98** | **2** | **0** | 0 severe; 0 invalid across 101 aggregates on disk |

**Takeaways for the full-dataset run.** The pipeline is stable: 100/100 valid, no
severe errors, and the two failure modes we hit (flaky no-output samples; a
mid-session API 401) are now retried instead of killing a run. The two ⚠️ misses
are single existing-file omissions on otherwise-correct annotations. The metric
to watch is *existing gold-code coverage*, not raw file overlap — a large share
of gold-patch files are new files, docs, lockfiles, generated code, or (in a few
cases) unrelated changes bundled into the same commit, all correctly excluded.

## Round 6 (stream capture) — 2026-07-09

Purpose: validate the new `--capture stream` implementation (no reverse proxy;
trace derived from `claude --output-format stream-json`). 20 new instances,
pairwise-disjoint from rounds 1–5 (`round6_ids.txt`, seed `20260709`), each run
through the full 3-samples + aggregate pipeline in stream mode.

- **20/20 valid, 20/20 complete, all `capture=stream`.** The stream
  completeness signal (terminal `result.subtype == "success"`) and the unified
  exchange record work correctly end to end.
- **Existing-gold coverage: 17/20 full; 3 minor misses**, all in the
  correctly-excluded category (not code a solver must read):
  - ansible `…/developing_modules_documenting.rst` (doc)
  - element-web `src/i18n/strings/en_EN.json` (i18n strings)
  - vuls `README.md`, `go.mod`, `go.sum` (doc + dep manifests)
- **No severe problems; 0 invalid.** Quality matches the proxy-mode phase-1
  rounds — same doc/manifest omission pattern, no regression from stream.
- **One operational hiccup, not a stream bug:** `protonmail/webclients-2c3559`
  failed provisioning on a *stale git worktree* still pointing at the pre-rename
  repo path. Root-caused and fixed in `core/repo/provider.py` (self-heal: rebuild
  a broken worktree, `prune` + `--force` on add); re-ran cleanly (4/4 gold).

| round | valid | ✅ full | ⚠ minor | ❌ | notes |
| --- | --- | --- | --- | --- | --- |
| 6 (stream) | 20/20 | 17 | 3 | 0 | doc/manifest/i18n omissions only; stream == proxy quality |

**Verdict:** stream capture is validated — safe to use, and a reasonable
candidate to make the default. Proxy remains the code default for now.

## Round 7 (stream) — 2026-07-10

20/20 valid, all 3-candidate, **0 STALL, 0 severe**. Coverage 15 full / 5 minor
(en_EN.json i18n, go.mod/manifest, docs — all correctly-excluded).

Operational: the initial run hit a **memory-thrashing hang** (9 concurrent agents
+ old `capture_output` buffering big-repo streams in RAM → swap → processes
frozen 40-150 min/instance, 13h for 14). Fixed (`c4d12d5`): stream stdout to
file + `killpg` the process group on timeout. 4 instances came out 2-candidate (a
sample died on `"API Error: socket connection closed unexpectedly"`); re-ran them
+ the unfinished ones at MAXJOBS=2 → all 3-candidate, perf clean (wall≈active).
Added `perf_check.py` (per-run stall detection) — run it alongside `qa_check.py`
every round now.

| round | valid | 3-cand | ✅ full | ⚠ minor | STALL | notes |
| --- | --- | --- | --- | --- | --- | --- |
| 7 (stream) | 20/20 | 20/20 | 15 | 5 | 0 | doc/i18n/manifest misses only; after hang-fix + 2-candidate re-run |

## Round 8 (stream) — 2026-07-10

20/20 valid, all 3-candidate, **1 STALL, 0 severe**. Coverage 19 full / 1 minor
(one ansible task missed 4 `.rst`/`.txt` docs — correctly-excluded doc files).

Ran at **MAXJOBS=2** end-to-end. First tried **MAXJOBS=4** (12 agents): even with
the round-7 file-stream fix it swap-thrashed (6/20 in 36 min, pipelines stuck
27-32 min, swapout +~1.6 GB), so fell back to 2. At 2 the box stayed healthy
all round (≈45-57% free, swapout flat) — the memory hang is a
concurrency-vs-16 GB problem, mode-independent. The **1 STALL** (`ansible-b2a289`,
candidate_1 wall 589s / active 195s → idle 394s) was **not** memory: it's an
occasional Claude Code API-retry backoff (transient socket drop), well under the
1800s timeout — the agent recovered and the final stayed 3-candidate. Combined
parquet now 161 instances / 1488 snippets; 644 traces (95.5 MB) pushed.

| round | valid | 3-cand | ✅ full | ⚠ minor | STALL | notes |
| --- | --- | --- | --- | --- | --- | --- |
| 8 (stream) | 20/20 | 20/20 | 19 | 1 | 1 | MAXJOBS=2 (4 thrashes); 1 API-retry stall, recovered |

## Round 9 (stream) — 2026-07-10

20/20 valid, all 3-candidate, **0 STALL**. Coverage 16 full / 4 minor. Ran at
MAXJOBS=2, healthy throughout (≈47-48% free, swapout flat, 0 failures).

3 of the 4 minor misses are the usual correctly-excluded doc/manifest files
(`.asciidoc`, `CHANGELOG.md`, `go.mod/go.sum`, an `examples/*/main.go`). The
**one substantive gap**: `vuls-4a72295d` (Trivy library-only scan) covered the
core fix files (parser/detector/base.go) + the adjacent `models/library.go` but
missed two other modified model files, `models/cvecontents.go` /
`models/vulninfos.go` — a real annotation-recall miss, not a pipeline defect
(valid, 3-candidate, no stall). Left as-is pending review; re-run wouldn't
reliably improve recall. Combined parquet now 181 instances / 1678 snippets.

| round | valid | 3-cand | ✅ full | ⚠ minor | STALL | notes |
| --- | --- | --- | --- | --- | --- | --- |
| 9 (stream) | 20/20 | 20/20 | 16 | 4 | 0 | MAXJOBS=2; 1 real source-recall miss (vuls-4a72295d) flagged |

## Round 10 (stream) — 2026-07-10

20/20 valid, all 3-candidate, **0 STALL**. Coverage 16 full / 4 minor — every
miss is a correctly-excluded manifest/doc/generated file (`go.mod/sum/work.sum`,
`README.md`, `rpc/flipt_grpc.pb.go`). Ran at MAXJOBS=2, healthy throughout
(swapout +~63 MB over the round, no thrash). No source-recall misses in this
round.

| round | valid | 3-cand | ✅ full | ⚠ minor | STALL | notes |
| --- | --- | --- | --- | --- | --- | --- |
| 10 (stream) | 20/20 | 20/20 | 16 | 4 | 0 | MAXJOBS=2; all misses manifest/doc/generated |

## Recall audit across all 201 — source-file misses

Added `recall_audit.py`: for every annotated instance it classifies each missed
existing-gold file as *acceptable* (doc / i18n / dependency-manifest / generated
code / build-CI / test-data) vs *source* (real app code). Manually reviewed all
32 instances that had any miss — the classifier had **no false negatives** (no
real source hidden in the "acceptable" bucket); its only errors are
over-inclusion, caught by hand:

- **`vuls-4a72295`** — genuine miss (`cvecontents.go`, `vulninfos.go`). Re-ran:
  best of 3 attempts reached 4/7, recovering `cvecontents.go` + `scanner/base.go`;
  `vulninfos.go` missed in all 3 runs = a real recall ceiling (likely a
  peripheral model tweak). **Kept the 4/7 aggregate.**
- **`openlibrary-b67138`** — genuine miss (`catalog/marc/parse_xml.py`, the MARC
  parser central to the 880-field task). Re-ran → **6/6, fixed.**
- **`NodeBB-76c6e30`** — *not* a defect. Problem is plugin-id validation; the
  annotation correctly found `src/plugins/install.js` + `cli/manage.js` +
  `constants.js` + `test/plugins.js`. The "missed source" (`flags.js`,
  `topic.js`, `reputation.tpl`) belongs to an unrelated reputation feature
  bundled in the same gold commit — correctly excluded. Left as-is.
- **`flipt-3d5a345`** (`examples/openfeature/main.go`) and **`navidrome-ee21f3`**
  (`reflex.conf`) — peripheral example / dev-tooling files, not needed to solve
  the stated problem. Left as-is.

Lesson: a low coverage ratio can be gold-patch contamination (bundled unrelated
files), not an annotation failure — so a "source miss" must be confirmed against
the problem statement, not trusted from the metric alone. Combined parquet: 201
instances / 1874 snippets.
