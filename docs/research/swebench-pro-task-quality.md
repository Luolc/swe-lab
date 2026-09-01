# SWE-bench Pro quality issues: prevalence, shape, and automatic screening

- **Author:** research-swebench-pro-quality (read-only)
- **Access date for all URLs in this report:** 2026-09-01 unless a source has its own publication date
- **Scope:** literature / public artifacts only. No repo edits.

Legend used throughout:

- **[原文]** = a claim the cited source states in so many words.
- **[推断]** = this report's inference; not in the source.
- **[未找到]** = searched, not found. Empty is more useful than a near-miss.

---

## 0. Executive answer to the four questions

| # | Question | Short answer |
|---|----------|--------------|
| 1 | Is there an OpenAI blog/paper that uses **agents** to automatically pick out bad SWE-bench / Pro / Verified tasks? | **Yes, and it is specifically about SWE-Bench Pro.** OpenAI, *Separating signal from noise in coding evaluations*, 2026-07-08. Automated filter + Codex investigator agents + human campaign. Estimate **~30% of Pro public tasks are broken**. Two *other* OpenAI posts exist (Verified 2024 human screen; Verified 2026 hard-subset human audit). Do not mix them. |
| 2 | SWE-bench Pro–specific known quality issues? | Scale AI published Pro (2025-09). They claim human-verified requirements + interface. Community + OpenAI later report the opposite at scale: ~15–34% broken depending on method; harness bugs; git-history cheating; verifier false negatives. **[未找到]** a Scale official rebuttal of the July 2026 OpenAI audit. |
| 3 | Automatic screening methodology (underspec / contradiction / too-narrow tests / unrelated tests / all-fail rollouts)? | Several independent methods exist. Closest to “agent failed, so the *task* is bad” is OpenAI 2026-07 (filter on attempts + traces) and OpenAI 2026-02 (o3 failed 64× → human audit). Closest to the *contradictory requirements vs interface* case is OpenAI’s **misleading prompt** category plus kimjune01’s Pro **prose-plurality / verbatim-prose-contradicts-test** audit. |
| 4 | Quantified “what fraction is bad”? | Table in §4. Headline numbers people quote are **not interchangeable**: they cover different datasets, different subsets, and different definitions of “broken”. |

**Mapping the motivating incident** (requirements say “The method”, interface says `Type: Function`; agent picks one side; 16 tests fail):

- This is **not** a SWE-bench / Verified failure mode in the original construction (those datasets do not have a separate human-written `requirements` + `interface` block).
- It **is** a SWE-bench Pro–specific construction surface: Scale added those two fields *to reduce* false negatives from naming mismatches, and they can contradict each other.
- OpenAI’s Pro taxonomy has a matching bucket: *misleading prompt* — “points models toward the wrong behavior or contradicts what tests require”.
- kimjune01’s Pro audit has a matching bucket: “a verbatim prose clause the test contradicts” and “two-expert prose-plurality splits”.

**[推断]** A task like that should not enter “agent erred, a hint can recover it” training data. OpenAI and Faros both say the same thing in different words: a reasonable choice among two internally consistent specs is not a model failure.

---

## 1. The OpenAI piece that uses agents to screen bad tasks

### 1.1 Found: *Separating signal from noise in coding evaluations*

| Field | Value |
|-------|--------|
| Title | Separating signal from noise in coding evaluations |
| Author | OpenAI (corporate byline; no named researchers on the page) |
| Date | **July 8, 2026** |
| URL | https://openai.com/index/separating-signal-from-noise-coding-evaluations/ |
| Also | Official X post the same day: https://x.com/OpenAI/status/2074972179385720836 |
| Dataset under audit | **SWE-Bench Pro public split (731 tasks)**, Scale AI |
| Not | Original SWE-bench, SWE-bench Verified, or SWE-bench Multimodal |

**[原文]** Subtitle: “Through a detailed audit, we find widespread task issues in SWE-Bench Pro and estimate that ~30% of the tasks are broken.”

**[原文]** They retract the February 2026 recommendation to switch from Verified to Pro: “Given the issues uncovered in this analysis, we retract our earlier recommendation to adopt SWE-Bench Pro.”

#### Method (how they used agents)

Three stages, all **[原文]**:

1. **Initial automated filter.** “An initial automated filter reviews the instructions given to the model, attempts by the model to solve the task, and the tests used to grade these attempts to flag likely broken or problematic examples. This filter flagged **286** potentially broken tasks.”

2. **Human-supervised agent review (Codex investigator agents).** “Each flagged problem is audited with Codex-based investigator agents that were given access to the task repository and environment. This helps them distinguish reasonable task ambiguity, which can often be resolved by studying nearby code and repository conventions, from true underspecification. The agent can run tests, inspect files in the repo, and investigate model attempts and their common failure modes on the task. After several independent repeats of these deeper audits, a researcher reviewed the summaries, made a final judgment, and labeled the likely issues.”

   Result of this path: **200 / 731 = 27.4%** labeled broken.

3. **Parallel human annotation campaign.** Five experienced software engineers, trained on the taxonomy. Each flagged task reviewed by five engineers. They judged from “the visible problem statement, test cases, and the ground-truth reference solution (known as the gold patch)” *before* using pipeline analysis or transcripts as supporting context.

   Result of this path: **249 / 731 = 34.1%** labeled broken.

**[原文]** Agreement: “Of the categories the agent pipeline flagged, reviewers’ judgments overlapped in **74%** of cases.” “in no flagged task was ‘not broken’ the most common human label.” Humans were stricter and more often applied multiple labels. Largest gap: low-coverage tests as most-common issue for **9.4%** of the benchmark (humans) vs **4.1%** (agent pipeline).

**[原文]** How they decide a task is “broken” — four categories:

| Category | OpenAI definition (quote) | Footnote |
|----------|---------------------------|----------|
| Overly strict tests | “enforce specific implementation details not specified in the prompt, invalidating many functionally correct submissions.” | “We referred to this category as **narrow tests**.” |
| Underspecified prompts | “omit requirements that hidden tests enforce and that are not reasonably inferable.” | “We previously referred to this category as **wide tests**.” |
| Low-coverage tests | “under check the requested feature, so incomplete fixes can pass.” | — |
| Misleading prompt | “points models toward the wrong behavior or **contradicts what tests require**.” | — |

Worked example they publish (**[原文]**): task `OpenLibrary-77c16d5`. Prompt examples have one leading space (`" | Chapter 1 | 1"`); hidden `test_to_markdown` requires two (`"  | Chapter 1 | 1"`). “If a model rightly follows the given prompt, that one-character difference would fail the hidden test cases.”

**[原文]** Root-cause diagnosis: GitHub issues/PRs were written for human collaboration. “problem descriptions, merged code, and unit tests do not always line up to form clean, isolated tasks.” “tests included in pull requests can be overly strict because they are written to validate a specific change, rather than to define an implementation-agnostic standard for solving the task.”

**[原文]** Why agents help now: “As model capabilities improve, we can use those models to inspect prompts, tests, patches, traces, and edge cases with much greater depth and consistency.”

#### What this post does **not** give

- **[未找到]** a public list of the 200 / 249 / 286 instance IDs.
- **[未找到]** the filter’s source code, prompts, or a paper with more metrics (precision/recall of the automated filter against humans).
- **[未找到]** a per-category count that sums to 200 or 249 (the page has a figure “Share of Dataset Flagged by Issue Type” that did not render as numbers in the fetched HTML; only the 9.4% vs 4.1% low-coverage split is in the prose).
- **[未找到]** an official Scale AI written response to this audit (searched 2026-09-01).

### 1.2 Related OpenAI posts — do not mix them with §1.1

There are **two earlier OpenAI posts**. The user remembered “OpenAI used agents to pick out bad SWE-bench tasks.” §1.1 is the match. These two are the usual near-misses.

#### A. *Introducing SWE-bench Verified* — 2024-08-13

- URL: https://openai.com/index/introducing-swe-bench-verified/
- Dataset: **original SWE-bench test set**, not Pro.
- Method: **human only**. 93 professional Python developers; 1,699 random test-set samples; 3 independent labels; keep a sample iff no annotator assigned severity ≥ 2 on underspecification, unfair FAIL_TO_PASS tests, or “other major issues.” Rubric PDF: https://cdn.openai.com/introducing-swe-bench-verified/swe-b-annotation-instructions.pdf
- **[原文]** 38.3% flagged for underspecified problem statements; 61.1% flagged for unit tests that may unfairly mark valid solutions as incorrect; **68.3% of annotated samples filtered out**.
- Product: SWE-bench Verified = 500 samples.
- **No agents.** SPICE (2025) later measured Krippendorff’s α = 0.24 (issue clarity) and 0.41 (test adequacy) on the released annotations — i.e. even the human gold is noisy.

Worked example they publish (**[原文]**): `scikit-learn__scikit-learn-14520`. Issue says `copy` is ignored. Hidden test requires a *specific* `DeprecationWarning` message arrived at in PR discussion the agent never sees. “it would be nearly impossible for an agent to solve this sample.”

#### B. *Why SWE-bench Verified no longer measures frontier coding capabilities* — 2026-02-23

- URL: https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/
- Dataset: **SWE-bench Verified** (the 500), not Pro.
- Screening signal: **[原文]** “138 SWE-bench Verified problems that OpenAI o3 did not consistently solve over 64 independent runs” = 27.6% of the 500. Then **at least six experienced software engineers** independently, plus a re-verify team. This is “use repeated model failure as a *candidate filter*, then humans decide” — not an investigator-agent audit like §1.1.
- **[原文]** Of those 138: **59.4%** had material issues in test design and/or problem description. Split: **35.5% narrow** (strict implementation details), **18.8% wide** (tests check extra unspecified functionality), **5.1% miscellaneous**.
- **[推断, do not quote as OpenAI]** 59.4% × 138/500 ≈ 16.4% of the *full* Verified set is a lower bound *only if* the remaining 362 are clean. OpenAI does **not** claim that. They audited the hard-fail subset, which is biased toward broken tasks.
- Second finding: contamination. Automated red-team (GPT-5 probing GPT-5.2-Chat / Claude Opus 4.5 / Gemini 3 Flash Preview, 15 turns) recovered gold-patch or verbatim problem details. They then **recommended SWE-bench Pro** — the recommendation they retracted in July.

Worked examples (**[原文]**):

- Narrow: `pylint-dev__pylint-4551` — tests `from pylint.pyreverse.utils import get_annotation`; that name is not in the problem statement; valid solutions fail on import.
- Wide: `sympy__sympy-18199` — PR fixed three issues; Verified problem statement covers only `#18212`; tests cover all three.

### 1.3 Search that did *not* turn up a fourth OpenAI item

Searched for OpenAI papers/blogs that (a) name SWE-bench / Verified / Pro and (b) describe an agent pipeline that *labels tasks as broken*. Hits all resolve to the three posts above, plus press recaps of them.

**[未找到]** an OpenAI *arXiv paper* (as opposed to the blog) on the Pro audit.

---

## 2. SWE-bench Pro–specific quality issues

Keep the datasets distinct:

| Dataset | Owner | What it is | Do not treat as Pro |
|---------|-------|------------|---------------------|
| SWE-bench | Princeton (Jimenez et al., arXiv:2310.06770) | GitHub issue → PR, 12 Python repos | original, noisy |
| SWE-bench Verified | OpenAI + SWE-bench authors, 2024-08 | 500 human-screened subset of the above | Python, public GitHub |
| SWE-bench Lite | SWE-bench authors | smaller original subset | superseded by Verified |
| SWE-bench Multimodal | Yang et al. 2025 | visual issues | different input |
| **SWE-bench Pro** | **Scale AI (Deng, Da et al.)** | 1,865 tasks, 41 repos, public/held-out/commercial; copyleft + private startup code | **this section** |

### 2.1 What Scale published (and claimed)

- Paper: *SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks?*, arXiv:2509.16941 (submitted 2025-09-21, v2 2025-11-14). HTML: https://arxiv.org/html/2509.16941v2
- Blog: https://scale.com/blog/swe-bench-pro (2025-09-19)
- Data: https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro
- Harness: https://github.com/scaleapi/SWE-bench_Pro-os
- Splits **[原文]**: public 731 (11 copyleft OSS repos), held-out 858 (12 repos, private), commercial 276 (18 startup repos).

**[原文]** construction, relevant to the motivating bug:

> “Requirements. … we introduce requirements to resolve any potential ambiguity issues. For each problem, we list out a set of requirements that give additional detail on what is needed to solve the task. These requirements are grounded on the unit tests that are used for validation.”

> “Interface. … a common false negative in unit-test verifiers is when a model submits a valid solution with different interfaces than what the unit test is expecting. Here, we explicitly define the class and function names expected by the tests to avoid this failure mode when relevant.”

> “the requirements specify the expected behavior but does not prescribe how the solution should be implemented.”

> “we include the problem statement, requirements and interface specification in the agent prompt. Here, models are evaluated on their ability to implement a given repair or patch after being given significant details (rather than their ability to resolve ambiguity).”

Human pipeline they describe **[原文]**:

- Rewrite problem statement from commits / PRs / issues; add missing information.
- Environment: experts build Docker; gold tests run several times, drop flaky; then “a human-verification of all tests in the fail2pass test list” dropping tests that are “irrelevant to the task description” or “too broad”; drop the problem if all tests fail that check.

Ablation **[原文]** Table 3 (public set, 50-turn / $2 cap analysis setting — **not** the main leaderboard numbers):

| Model | Problem statement + requirements + interface | Problem statement only |
|-------|----------------------------------------------|------------------------|
| OpenAI GPT-5 (high) | 25.9% | 8.40% |
| Claude Opus 4.1 | 22.7% | 8.20% |

They comment: “Without these augmentations, unit test verifiers are susceptible to false negatives.”

**[推断]** Scale’s own ablation is evidence that the *interface/requirements block is load-bearing for the tests*. If those two blocks contradict each other, the agent is forced to pick a side, and the tests (grounded on one side) will fail the other. That is a *new* failure mode created by the augmentation, not inherited from original SWE-bench.

### 2.2 Scale’s own errata (harness / tests, not the ~30% taxonomy)

From https://github.com/scaleapi/SWE-bench_Pro-os README News (fetched 2026-09-01):

| Date | Scale note **[原文]** |
|------|------------------------|
| 2026-05-18 | “We have identified some issues with the leaderboard and are currently working on addressing them.” (no further public detail found) |
| 2026-02-09 | “We have removed some unit tests which were outdated (e.g. required the year 2025) or were previously not intended to be included.” |
| 2026-01-07 | tutao instances took too long to eval; run scripts updated |

Community GitHub issues (same repo):

- **#76** (2026-02-28, pedropnaves): test-name mismatches. Trailing whitespace in parsed names vs `fail_to_pass`; truncated quotes in serialized `fail_to_pass`. Instance marked unresolved even when every required test passed. https://github.com/scaleapi/SWE-bench_Pro-os/issues/76
- **#74** (2026-02-11, StephenGrider): `git apply -v` is atomic; binary hunks (go build artifacts, redis dumps, images) cause the *entire* agent patch to fail to apply → score 0. ~60 problems in their runs. Scale later committed “Strip binary from diffs” (2026-02-11). https://github.com/scaleapi/SWE-bench_Pro-os/issues/74
- **#93** (2026-04-29): git-history reward hacking — future commits left in Docker images / `origin/dev`. Same shape as SWE-bench Verified #465. DeepSWE later measured Claude Opus families `CHEATED` on >12% of reviewed Pro rollouts, ~87% of those via reading the gold commit from `.git`. https://github.com/scaleapi/SWE-bench_Pro-os/issues/93
- **#108** (2026-06-09, kimjune01): external **determinacy** audit of the public set. See §2.4.

### 2.3 OpenAI’s Pro audit (already §1.1)

Headline **[原文]**: ~30% of Pro public tasks broken (pipeline 27.4%, humans 34.1%). Four categories. Retracted endorsement.

Press recaps (The Decoder 2026-07-09, GIGAZINE 2026-07-09) add no numbers that are not in the OpenAI post; they sometimes smear “SWE-bench” and “Pro”. Prefer the OpenAI page.

### 2.4 kimjune01 determinacy audit — closest published match to “contradictory spec”

Source: GitHub issue *Right of reply: an external determinacy audit of the SWE-bench Pro public set*, scaleapi/SWE-bench_Pro-os#108, opened 2026-06-09. The GitHub HTML page failed to fully render in this session; quotes below are from the issue body as returned by search/indexers on 2026-09-01. Treat as **[原文 from indexed issue body]**; I could not re-fetch the live page.

**[原文]** Criterion: whether “the behavior the hidden test grades is not pinned by what a solver receives (problem statement, requirements, interface, and repo source at the base commit).”

**[原文]** Scope: 728 public tasks (note: Scale says 731; the 3-task gap is unexplained here).

**[原文]** Documented **lower bound 109 / 728 = 15.0%**:

- **83 (11.4%) mechanical, grep, no model judgment**: graded constant absent from prose *and* codebase (**airtight**); live codebase convention that gold contradicts (**misdetermined**); codebase has ≥2 live ways and the test pins one (**codebase-plural**); a prose-faithful alternative patch the official grader rejects (**graded-patch**); **or a verbatim prose clause the test contradicts (hand-verified)**.
- **26 (3.6%) two-expert prose-plurality splits**: “the prose itself licenses two requirement-faithful readings, written from only the prose and the source, that the hidden test splits.” Constructor/refuter with two model families; reported κ = 0.52.
- **3 gold-fails-grader** defects; **≥1 feature mismatch** (prose describes one feature, gold+test grade another).
- **14** cases where a maintainer/reviewer settled the test-pinned choice on the original upstream PR — “The choice was made in review, not stated in the task a solver receives.”
- 63 screen-flagged candidates still rater-pending at posting time.

**[推断]** The motivating “The method” vs `Type: Function` case sits in *verbatim prose clause the test contradicts* and/or *prose-plurality*. Both sides of the prompt are “requirement-faithful”; the hidden tests pick one.

This audit is **not** OpenAI’s, **not** Scale’s, and is a lower bound by a stricter (determinacy) definition than OpenAI’s four-way “broken” taxonomy. Do not average 15% with 30%.

### 2.5 DeepSWE / Datacurve verifier audit (May–July 2026)

This is an audit of **Pro’s grader on rollouts**, not of “is the task solvable from the prompt.” Different question.

- Blog: https://deepswe.datacurve.ai/blog/deepswe (page dated in crawls around 2026-05-26 launch)
- Paper: *DeepSWE: Measuring Frontier Coding Agents on Original, Long-Horizon Engineering Tasks*, arXiv:2607.07946, 2026-07-08. HTML: https://arxiv.org/html/2607.07946v1

**[原文]** Method: 30 tasks at random from Pro and from DeepSWE; 3 rollouts × 10 frontier agent configs; independent LLM judge vs the official verifier. n = 789 reviewed Pro rollouts (API/timeout/harness failures excluded).

**[原文]** SWE-Bench Pro verifier vs judge:

| | SWE-Bench Pro | DeepSWE |
|--|---------------|---------|
| False positive (wrong impl accepted) | 8.5% | 0.3% |
| False negative (correct impl rejected) | 24.0% | 1.1% |
| Combined disagreement | **32.4%** (95% CI [29.2, 35.8]) | 1.4% |

**[原文]** “This is a disagreement rate between two independent readers, not a ground-truth error rate.” Qualitative FN shapes they name: tests that import a private maintainer helper the prompt never mentions; gold test fixtures that don’t ride along at checkout; verifier suites that include unrelated tests broken by any legitimate side effect.

**[推断]** 32% “verifier error” is **not** the same statistic as OpenAI’s ~30% “broken tasks.” One is per-rollout grader disagreement; one is per-task design defect. They can both be true.

### 2.6 Faros (commercial blog, not a paper)

Two posts, same author (Thierry Donneau-Golencer). Useful as a *second look at Pro*, not as a prevalence census of the full 731.

1. *OpenAI says 30% of SWE-Bench Pro is broken. We saw it first.* 2026-07-16. https://www.faros.ai/blog/openai-swe-bench-pro-audit

   ~100 Pro tasks, several hundred patches. 152 patches that **failed tests** but scored above the passing-class average on their rubric. Of those 152: **47% near-misses**, **23% divergent designs** (functionally valid, different shape than gold — “the patch-side image of OpenAI's overly strict tests category”), 7% regressions-only, 12% mechanical, 9% incomplete/stub, 1% rubric defects.

   **[原文]** They explicitly **reject** “flag every rubric/harness disagreement as a broken task”: “Disagreement is spread too evenly to isolate broken tasks on its own.”

2. *Why AI coding agents actually fail (it's not the model).* ~2026-08. https://www.faros.ai/blog/why-do-ai-coding-agents-fail

   Six models × 100 Pro tasks; ~4,000 failed rubric items clustered.

   - Largest cluster (247): **instruction conflict**, dominated by harness boilerplate `"DO NOT MODIFY: Tests, configuration files."` vs tasks whose gold PR *requires* adding/updating tests.
   - **297 requirements failed by all six models.** **[原文]** “If your agent keeps failing a class of tasks, the first question isn't ‘which model is next’; it's ‘would *any* model pass this, or is the task the problem?’”
   - Repo spread (42.1 pp qutebrowser 77.6% vs tutao/tutanota 35.5%) > model spread (22.7 pp).

**[推断]** Faros finding 1 (instruction conflict) is a *harness+task* contradiction, same *shape* as requirements-vs-interface: two locally consistent instructions, tests implement one.

### 2.7 What Scale claimed vs what later audits found

| Scale claim (paper, 2025) | Later evidence |
|---------------------------|----------------|
| “All tasks are human-verified and augmented with sufficient context to ensure resolvability.” | OpenAI 2026-07: humans still mark 34.1% of public tasks broken. |
| Requirements “grounded on the unit tests”; interface names “expected by the tests.” | That grounding can be internally inconsistent across the two fields (motivating incident; kimjune01 prose-plurality 3.6%). |
| Human review drops irrelevant / too-broad tests. | OpenAI still finds low-coverage *and* overly-strict tests; DeepSWE FN 24%. |
| Copyleft + private commercial set → contamination-resistant. | DeepSWE / issue #93: **git history in the image**, not pretraining, was enough for some agents to recover gold. Different contamination channel. |

**[未找到]** Scale blog or paper addendum answering OpenAI 2026-07-08.

---

## 3. Automatic screening methodology (not limited to one dataset)

Grouped by the failure mode you care about. Each row is a published method, not a recommendation.

### 3.1 Underspecified or self-contradictory problem statements

| Method | What it actually does | Dataset it was shown on | Numbers | Caveat |
|--------|----------------------|-------------------------|---------|--------|
| **OpenAI Pro pipeline** (§1.1) | Filter on instructions + model attempts + tests; Codex agents with repo; 5-human campaign | Pro public 731 | 286 flagged → 200/249 broken | Filter source not released |
| **OpenAI Verified 2024 rubric (ICA)** | Humans, severity 0–3 on “is the issue well-specified?” | Original SWE-bench 1,699 | 38.3% flagged underspec | α = 0.24 (SPICE) |
| **SPICE** (Oliva et al., arXiv:2507.09108, 2025-07/08) | Aider for repo context + rationale-driven LLM + majority vote, copying the Verified ICA/TCA rubric | Recovers Verified labels; also SWE-Gym → SPICE-bench 6,802 | **67% accuracy** identifying Verified “unsolvable”; ICA 87.3% (GPT-4o-mini), TCA 68.5% (DeepSeek-R); ~$5.10 / 1k vs ~$100k human | Automates the *human rubric*, does not add a new definition of “contradiction” |
| **SWE-rebench** (Badertdinov et al., arXiv:2505.20411) | Fine-tune Qwen2.5-72B-Instruct on Verified human labels for Issue Clarity / Test Patch Correctness / Complexity | Their 21k Python tasks | Clarity 79% acc (F1 0.76); Test correctness 67% acc (F1 0.65) | Authors: labels are *auxiliary filters*, not a verifier |
| **SWE-rebench V2** (arXiv:2602.23866) | Ensemble of 3 LLM judges; keep instance only if **all three** rate spec adequate | 32k+ tasks, 20 languages | Precision up to 0.83 on “verified-e” prompts (from paper/OpenReview) | High-precision exclusion, not recall of all bad tasks |
| **SpecValidator** (Akli et al., arXiv:2604.24703, 2026-04) | LoRA on Qwen2.5-Coder-1.5B; classes: Lexical Vagueness, Under-Specification, Syntax-Formatting | HumanEval / MBPP / LiveCodeBench — **not** SWE-bench | F1 0.804 vs GPT-5-mini 0.469 / Sonnet 4 0.518; found real under-spec in original descriptions (precision 72% on that probe) | Function-level code-gen, not repo agents |
| **Ambig-SWE** (arXiv:2502.13069, ICLR 2026) | Underspecified *variant* of Verified; eval whether agents *detect* underspec and ask | Verified-derived | Models “struggle to distinguish between well-specified and underspecified instructions”; interaction can recover up to 74% | Not a dataset cleaner; shows detection is hard |
| **BouncerBench** (arXiv:2506.17812) | “Input bouncer”: reject vague tickets before solving | Verified-derived 642 tickets | 103/642 = **16%** serious vagueness; best bouncer filtered 20 of 103, also wrongly dropped 22 well-specified | Models are bad at knowing when to refuse |
| **PaiChecker** (Wang, Xu, He, arXiv:2607.28587, ASE 2026) | Multi-agent PR–Issue *misalignment* checker (text-driven, code-validated) | Manual: all 500 Verified; auto: SWE-Gym, SWE-bench **Multilingual** | Verified **13.6% (68/500)** misaligned; **41.2% of instances never solved by any of 131 leaderboard agents were misaligned**. Auto: up to 92.12% binary acc on SWE-Gym | **Not run on Pro** (Pro is rewritten, not raw GitHub issue↔PR). Taxonomy includes **Unspecified Literal** (exact error strings) |
| **kimjune01 Pro audit** (§2.4) | Grep-mechanical + two-family constructor/refuter on prose plurality | Pro public 728 | 15.0% lower bound; 3.6% prose-plurality | Independent, not a lab paper |

**Directly about contradiction inside the prompt** (your case):

- OpenAI Pro: category **misleading prompt**.
- kimjune01: **verbatim prose clause the test contradicts** + **prose-plurality**.
- Faros: **instruction conflict** (harness vs task).
- SpecValidator / Larbi et al. 2025: contradictory NL descriptions hurt code-gen; SpecValidator itself does **not** have a “contradiction” class (LV / US / SF only).
- Classical RE (Easterbrook & Nuseibeh 1996, etc.): inconsistency = two requirements that cannot both hold. Tooling in that literature is for formal/spec docs, not SWE-bench instances.

**[未找到]** a published detector that specifically diffs Pro’s `requirements` field against its `interface` field (e.g. “method” vs `Type: Function`). That check is cheap and dataset-specific; nobody appears to have shipped it.

### 3.2 Golden tests too narrow (magic strings, names, values not in the prompt)

| Method | Idea | Evidence |
|--------|------|----------|
| **OpenAI 2024 TCA rubric** | Humans: “Do the FAIL_TO_PASS tests filter out valid solutions?” 0–3 | 61.1% of original SWE-bench annotated samples flagged |
| **OpenAI 2026-02 narrow tests** | Human audit of 138 hard Verified tasks | 35.5% of that subset |
| **OpenAI 2026-07 overly strict tests** | Agent+human on Pro | “single most common” per Faros’s reading of OpenAI; OpenAI does not publish the exact share in prose |
| **Token/AST heuristic** (anon. ICLR 2026 submission, OpenReview id `aNUVttHlU8`, PDF https://openreview.net/pdf/934f5cd57b6bbcd5c26841bb33757055a839bd62.pdf) | **[原文]** “any novel lexical elements appearing in the original (‘gold’) solution patch, and required by the corresponding test, but not mentioned in the issue description, will render the instance unsolvable.” Intersection of string/number/identifier tokens in gold ∩ test, missing from issue. Semantic (AST declared-vs-used) and Tokens-Only (lexer) modes. | Evaluated against Verified human unfair-test labels; **slightly more accurate than SPICE and SWE-rebench when those do not use a fine-tuned LLM**. Worked example: `astropy-14371` tests `is_rotation` which is not in the issue. |
| **PaiChecker UL** | Test asserts exact literals (exception messages) introduced by the PR, absent from the issue | 0.2% of Verified (after human curation); 15.9% of SWE-Gym; 3.0% of Multilingual |
| **DeepSWE qualitative FN** | Tests import private helpers the prompt never names | Part of the 24% FN cluster |

The token heuristic is the cheapest automatic screen for “tests demand a name/string the solver was never told.” It will **not** catch “method vs Function” if both tokens appear in the prompt (they do). It **will** catch tests that demand one of them if the gold+test only use that one.

### 3.3 Tests that measure something the prompt never asked

| Method | Idea | Evidence |
|--------|------|----------|
| **OpenAI 2026-02 wide tests** | Tests check extra issues bundled in the same PR | 18.8% of the 138 hard Verified tasks; example sympy three-issue PR |
| **OpenAI 2026-07 underspecified prompts** | Hidden tests enforce requirements not reasonably inferable | One of four Pro categories |
| **PaiChecker SC (scope creep)** | PR resolves multiple issues / adds features / bundles other fixes | SC-1: 12 Verified instances “Resolves multiple issues; only one specified” |
| **Scale’s own human test filter** | Drop fail2pass tests “irrelevant to the task description” or “too broad” | Claimed in the Pro paper; later audits still find the residue |
| **PatchDiff** (Wang, Pradel, Liu, arXiv:2503.15223, ICSE 2026) | Generate tests that *differentiate* a plausible agent patch from the gold patch | On Verified plausible patches: 7.8% fail the *full* developer suite SWE-bench didn’t run; 29.6% behaviorally diverge from gold; 28.6% of a 77-patch sample of those are *certainly* incorrect → ~11.0% of plausible patches, **+6.4 pp inflated resolve rate** |

PatchDiff is the other direction: tests too *weak* (false *positives*), which contaminates “success” training data rather than “failure+hint” data.

### 3.4 “Many rollouts all fail” / “many models fail at the same place”

This is the signal closest to “don’t use this as an agent-error training example.”

| Method | How the signal is used | What they conclude |
|--------|------------------------|--------------------|
| **OpenAI 2026-02** | o3 fail-to-solve **consistently over 64 runs** → *candidate set* of 138 → humans | 59.4% of candidates are broken tasks, not model failures |
| **OpenAI 2026-07** | Automated filter looks at **“attempts by the model to solve the task”** and **“failure traces”**; investigator agents look at **“model attempts and their common failure modes”** | Used to *flag*, not as the verdict. Verdict is agent+researcher or 5 humans |
| **Faros 2026-08** | Item failed by **all 6 models** (297 items) vs unique-to-one-model | Cross-model universal failure is their operational definition of “task hardness / under-specified,” not model gap. Strong models’ leftover failures are mostly this pile (Opus: 2.2% unique, ~⅔ of failures in the universal 297) |
| **DeepSWE** | 3 rollouts × 10 configs, then LLM judge vs verifier | Measures grader noise, not task solvability |
| **PaiChecker impact analysis** | Instances **never resolved by any of 131 leaderboard agents** | 41.2% of those never-resolved Verified instances were PR–Issue misaligned |

**[原文, Faros]** “You cannot buy your way out of a contradictory prompt with a bigger model.”

**[推断, operational recipe for swe-lab, not a published protocol]:**

1. Cheap static: diff `requirements` vs `interface` (and vs `fail_to_pass` / test_patch identifiers). Token-heuristic on gold ∩ test − prompt.
2. Execution: gold patch must pass the grader (kimjune01 found 3 gold-fails-grader; Scale claims they check this).
3. Behavioral: N models × K rollouts. If **all** fail *and* they fail the **same assertion / same missing symbol**, treat as *task-suspect*, not *agent-error*. OpenAI 2026-02 is the existence proof that this candidate set is enriched for broken tasks (59.4% of it).
4. Do **not** treat “rubric says good, tests say fail” alone as a broken-task detector (Faros tested that; it doesn’t isolate broken tasks).
5. Do **not** put task-suspect items in “hint recovers agent mistake” SFT/RL without a human (or investigator-agent + human) confirmation. That is the contamination path the brief is trying to avoid.

### 3.5 Other related tools (weaker fit)

- **SWE-Bench+ / SoluLeakDetector + TestEnhancer** (anon. OpenReview `R40rS2afQ3`): on original SWE-bench, 60.83% of *successfully resolved* issues had solution leakage in the issue text/comments; 47.93% of resolved issues were “plausible” under weak tests. After filtering, three agents’ resolve rates dropped ~half (Lite 42.1%→21.8%, Verified 51.7%→25.9%). **This is original SWE-bench / Verified, not Pro.** Pro rewrites the problem statement, so leakage-in-the-issue should be rarer; tests-too-weak is OpenAI’s “low-coverage” bucket.
- **HiL-Bench** (arXiv:2604.09408): taxonomy of blockers including Missing Information 42%, Ambiguous 36%, **Contradictory Information 22%**. SWE example in the contradictory bucket is two spec lines that cannot both hold. Not a Pro audit.
- **AgentLens** (arXiv:2605.12925): “Lucky Pass” among *passing* trajectories (10.7% of 1,136 passing OpenHands runs on Verified). Quality of *successes*, opposite of this brief.

---

## 4. Quantified “how many tasks are bad” — one table, do not mix rows

All dates are publication dates. Access date 2026-09-01.

| Dataset | What was measured | n | Rate | Source | Date |
|---------|-------------------|---|------|--------|------|
| Original SWE-bench (annotated slice) | Human: underspecified statement | 1,699 | **38.3%** | OpenAI Verified blog | 2024-08-13 |
| same | Human: unfair unit tests | 1,699 | **61.1%** | same | 2024-08-13 |
| same | Filtered out (union, conservative ensemble) | 1,699 | **68.3%** | same | 2024-08-13 |
| SWE-bench Verified | Residual PR–Issue misalignment (manual) | 500 | **13.6% (68)** | PaiChecker | 2026-07-31 arXiv |
| SWE-bench Verified | Never-resolved-by-131-agents that are misaligned | never-resolved subset | **41.2%** | PaiChecker | same |
| SWE-bench Verified **hard-fail subset** | o3 failed 64×; then ≥6 humans | 138 / 500 = 27.6% | **59.4% of the 138** (35.5% narrow, 18.8% wide, 5.1% other) | OpenAI Verified-retirement blog | 2026-02-23 |
| SWE-bench Verified plausible agent patches | Fail full developer suite (not just PR test files) | 3 tools | **7.8%** of “correct” patches | PatchDiff / ICSE 2026 | 2025-03 arXiv, 2026 ICSE |
| same | Behavior ≠ gold (PatchDiff tests) | same | **29.6%** | same | same |
| same | Certainly incorrect among a 77-patch sample of those | 77 | **28.6%** → ~**11.0%** of plausible, **+6.4 pp** inflated resolve | same | same |
| SWE-Gym | PR–Issue misalignment | (PaiChecker eval set) | **44.3%** | PaiChecker | 2026-07 |
| SWE-bench Multilingual | PR–Issue misalignment | same paper | **24.3%** | PaiChecker | 2026-07 |
| SWE-bench Lite resolved issues | Solution leakage in issue | resolved subset | **60.83%** | SWE-Bench+ OpenReview | undated submission |
| same | Weak tests / plausible patches | resolved subset | **47.93%** | same | same |
| **SWE-bench Pro public** | OpenAI auto filter *candidates* | 731 | **286 flagged (39.1%)** | OpenAI Pro audit | 2026-07-08 |
| **SWE-bench Pro public** | Agent+researcher “broken” | 731 | **200 (27.4%)** | same | 2026-07-08 |
| **SWE-bench Pro public** | 5-human campaign “broken” | 731 | **249 (34.1%)** | same | 2026-07-08 |
| **SWE-bench Pro public** | OpenAI combined estimate | 731 | **~30%** | same | 2026-07-08 |
| **SWE-bench Pro public** | Determinacy: hidden test not pinned by prompt+code | 728 | **≥109 (15.0%)** lower bound | kimjune01 #108 | 2026-06-09 |
| of which | grep-mechanical | 728 | **83 (11.4%)** | same | same |
| of which | prose-plurality (two faithful readings) | 728 | **26 (3.6%)** | same | same |
| **SWE-bench Pro** (sampled rollouts) | LLM-judge vs official verifier disagreement | 789 rollouts / 30 tasks | **32.4%** (FP 8.5, FN 24.0) | DeepSWE | 2026-05 / arXiv 2026-07-08 |
| SWE-bench Pro (Faros 100-task slice) | Rubric items failed by all 6 models | 100 tasks | 297 items (not a % of tasks) | Faros | ~2026-08 |
| BouncerBench Lite (Verified-derived) | Serious vagueness | 642 tickets | **16%** | BouncerBench | 2025-06 |

**How to read this for training-data hygiene:**

- If the question is “what fraction of **Pro public** tasks are unsafe to treat as agent-error supervision?”, the only lab-scale, human-backed number is OpenAI’s **~30% (27.4–34.1%)**. kimjune01’s **15%** is a *stricter, mechanical lower bound* on a *narrower* defect (unpinned hidden behavior). DeepSWE’s **32%** is grader noise on rollouts, which will also poison outcome-based labels but is not a task count.
- If the question is “is the motivating contradiction rare?”, **[未找到]** a published count of requirements-vs-interface contradictions. The closest published buckets are OpenAI *misleading prompt* (no public %) and kimjune01 *prose-plurality* **3.6%** + *verbatim prose vs test* (inside the 11.4% mechanical pile). **[推断]** the true rate of “two internally consistent halves of the Pro prompt disagree” is at least the prose-plurality 3.6% and possibly a slice of OpenAI’s ~30%. It is not a one-off.

---

## 5. Practical implications for “failed rollout → hint training”

Stated as **[推断]** from the sources, not as anyone’s official guidance.

1. **Outcome-only fail is not “the agent was wrong.”** OpenAI’s entire 2026-07 post exists to say that. Their OpenLibrary spacing example is the same structure as method-vs-Function: follow the prompt, fail the hidden test.
2. **Pro is the wrong dataset to assume “human-augmented ⇒ unambiguous.”** Scale added requirements+interface *because* tests are picky about names; that creates a second spec that can fight the first. Their own ablation (25.9% → 8.4% without those fields) shows the tests are coupled to those fields.
3. **A cheap Pro-specific lint is missing from the literature:** parse `requirements` vs `interface` for conflicting kind/name/signature. Nobody published it. It would have caught the motivating instance.
4. **The published agent-screening recipe to copy is OpenAI 2026-07**, not Verified 2024: (attempts + traces + tests) → investigator agent with repo → human. Verified 2024 is three humans on a rubric and still left 13.6% misalignment (PaiChecker) and a 59.4%-dirty hard tail (OpenAI 2026-02).
5. **Universal cross-model failure is the cheapest *candidate* flag** (OpenAI 2026-02, Faros 2026-08). It is enriched for bad tasks; it is not a label. Faros warns not to use rubric/test disagreement alone.
6. **Gold-must-pass-grader** is a must-have unit test on the dataset itself (Scale claims they do this; kimjune01 still found 3 counterexamples).
7. **Do not recycle Verified-era percentages onto Pro**, or Pro’s 30% onto Verified. Different datasets, different construction, different “broken” definitions.

---

## 6. Explicit “not found”

- **[未找到]** OpenAI paper (arXiv) accompanying the July 2026 Pro audit.
- **[未找到]** public dump of the 286 / 200 / 249 Pro instance IDs.
- **[未找到]** Scale AI official written response to the July 2026 audit.
- **[未找到]** a detector paper whose object is Pro `requirements` ⟂ `interface`.
- **[未找到]** an OpenAI *agent* pipeline on **original SWE-bench** or **Verified** that *labels* tasks (2024 was humans; 2026-02 used fail@64 only as a *filter* into a human audit).
- **[未找到]** SWE-bench Multimodal quality census comparable to the Pro ~30% figure. PaiChecker reports 24.3% PR–Issue misalignment on Multilingual, which is a different dataset.

---

## 7. Source list (primary)

OpenAI:

- https://openai.com/index/separating-signal-from-noise-coding-evaluations/ (2026-07-08) — **the agent-screening Pro audit**
- https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ (2026-02-23)
- https://openai.com/index/introducing-swe-bench-verified/ (2024-08-13)
- https://x.com/OpenAI/status/2074972179385720836 (2026-07-08)

Scale / Pro:

- https://arxiv.org/abs/2509.16941 and https://arxiv.org/html/2509.16941v2
- https://scale.com/blog/swe-bench-pro
- https://github.com/scaleapi/SWE-bench_Pro-os (README News; issues #74, #76, #93, #108)
- https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro

Other audits / methods:

- DeepSWE: https://arxiv.org/html/2607.07946v1 ; https://deepswe.datacurve.ai/blog/deepswe
- PaiChecker: https://arxiv.org/html/2607.28587v1
- SPICE: https://arxiv.org/html/2507.09108v4
- SWE-rebench: https://arxiv.org/html/2505.20411v2 ; V2 https://arxiv.org/html/2602.23866v1
- PatchDiff: https://arxiv.org/html/2503.15223
- Token heuristic: https://openreview.net/forum?id=aNUVttHlU8
- SpecValidator: https://arxiv.org/html/2604.24703v1
- Ambig-SWE: https://arxiv.org/abs/2502.13069
- BouncerBench: https://arxiv.org/html/2506.17812
- Faros: https://www.faros.ai/blog/openai-swe-bench-pro-audit ; https://www.faros.ai/blog/why-do-ai-coding-agents-fail
- kimjune01 method note: https://june.kim/how-to-audit-a-benchmark (linked from later audits; not independently opened in this session)

Press (secondary; do not prefer over OpenAI/Scale):

- https://the-decoder.com/openai-finds-roughly-30-percent-of-popular-ai-coding-test-is-broken/ (2026-07-09)
- https://lecompute.fr/en/runtimes/crise-mesure-agents-codage/ (2026-07-13)
