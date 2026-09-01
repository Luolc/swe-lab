# Injection shape — design and how to run

**Serves** [trace-synthesis task 02](../../../docs/trace-synthesis/plans/README.md#task-02-measure-the-injection-shape),
which settles the [spec's head open question](../../../docs/trace-synthesis/spec.md#11-open-questions).
Findings live in [`REPORT.md`](REPORT.md); this file is the design.

## Question

Phase C needs to put a **hint** in front of a blind actor at a tool boundary.
Per the owner's 2026-09-01 ruling the hint does **not** have to arrive as a
`user`-role turn; a channel qualifies when three things hold:

1. the actor sees it,
2. it is marked as an external injection (a tag), so the actor reads it as
   neither its own output nor the tool's, and
3. our typed `Conversation` conversion preserves it.

## Hypothesis

`PostToolUse`'s `updatedToolOutput`, carrying a tagged suffix **appended to the
tool's real output**, passes all three — (3) in particular, because a tool
result is itself a `user` event in `stream-json` and so should survive
`convert.py`'s user/assistant filter. `additionalContext` is expected to fail
(3): it is delivered inside a system reminder that the stream does not carry.

## Method

Two independent evidence lines, because the three criteria split cleanly in two
and no single capture answers both:

| Line | Capture | Answers |
|---|---|---|
| **wire** | `cc-reverse-proxy` in front of the actor (`ANTHROPIC_BASE_URL`) | criteria 1 and 2 — the exact request body the client sent upstream |
| **stream** | `stream.jsonl` through this repo's `event_stream_to_conversation` | criterion 3 — the only thing phase D actually runs |

A candidate can be perfect on the wire and gone after conversion. That split is
the point; it is also the spec's named fatal failure mode.

Each variant is one headless `claude -p` run in a throwaway workspace with an
isolated `CLAUDE_CONFIG_DIR` and hooks injected per-run via `--settings`. The
workspace holds a `notes.txt` claiming the module computes an **area** and a
`widget.py` that actually computes a **perimeter**, so "check whether the code
does what the notes claim" has a verifiable destination.

Three prompt shapes:

- **compliance** — "run `cat notes.txt`, then tell me in one sentence what this
  project does; be quick". One tool call is the whole task, so a second tool
  call happens only because the hint asked for it. The read-outs are
  *investigated* (more than one tool call, counted off the hook log so proxied
  and unproxied runs measure the same), *answer corrected* (the answer says
  "perimeter" — reachable only by following the hint), and *objected* (the
  answer calls it a prompt injection).
- **visibility** — "quote back verbatim everything that came back to you, and
  say who you think is speaking". Direct evidence for criterion 2.
- **failure** — the same, on a command that exits non-zero, for
  `PostToolUseFailure`.

## Variants

`v*` runs are unproxied; `p*` runs are the same probes behind the proxy.
`python run_experiment.py --list` prints the live list.

| Variant | Channel | Prompt |
|---|---|---|
| `v1` / `p1` | `updatedToolOutput`, `<oracle_hint>` suffix | compliance |
| `v2` / `p2` | `PostToolUse` `additionalContext` | compliance |
| `v3` / `p6` | `PostToolBatch` `additionalContext` | compliance |
| `v4` / `p7` | `PostToolUseFailure` `additionalContext` | failure |
| `v5` | none (log only) | forces a parallel batch — hook fan-out |
| `v6` | `updatedToolOutput` on the `Read` tool | visibility |
| `v7` / `p3` | none | compliance — the baseline |
| `p4` | `updatedToolOutput`, **untagged** prose suffix | compliance |
| `p5` | `updatedToolOutput`, `<supervisor_note>` claiming to be the user | compliance |
| `p8` | `updatedToolOutput`, `<oracle_hint>` suffix | visibility |
| `v8` | `updatedToolOutput`, `<oracle_hint>` suffix, `ANTHROPIC_BASE_URL` set to the real API | compliance |

`p4`, `p5` and `v8` are controls added after round 1: they separate *the tag*
from *any injected imperative text*, *a neutral marker* from *one impersonating
the user*, and *the proxy program* from *the `ANTHROPIC_BASE_URL` variable*.

## How to run

```sh
# all variants, four replicates each; existing runs are skipped, not re-run
direnv exec /home/ubuntu/dev/swe-lab \
  python3 experiments/trace_synthesis/injection_shape/run_experiment.py --repeat 4 all

# regenerate every number in the report
uv run python experiments/trace_synthesis/injection_shape/analyze.py
```

The driver needs `SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN` (this repo's `.envrc.local`);
it maps it to `CLAUDE_CODE_OAUTH_TOKEN`, which is what a bare `claude` reads. It
also drops `CLAUDECODE` from the child's environment — these runs are launched
from inside Claude Code, and the nesting guard would otherwise bite.

The proxy is [`cc-reverse-proxy`](https://github.com/) at
`/home/ubuntu/dev/cc-reverse-proxy/python` (used read-only; the Go original
needs a toolchain this box does not have). The driver starts one per proxied
variant on port 9611 and stops it afterwards.

## Layout

```
hook.py             the probe hook — logs its payload, emits one candidate shape
run_experiment.py   one variant = one claude -p run; idempotent and resumable
analyze.py          raw runs → the report's tables (writes analysis.json)
runs/<variant>/     cmd.txt, meta.json, stream.jsonl, hook_log.jsonl,
                    stderr.txt, and proxy.jsonl for the proxied variants
```

**The proxy log carries credentials and operator identity until it is
redacted.** `cc-reverse-proxy` records the headers it forwards on **both**
sides: the request carries the run's OAuth bearer token (and
`metadata.user_id` in the body), and the response carries the operator's
`anthropic-organization-id` / `anthropic-workspace-id`. The driver redacts all
of it in place as soon as a proxied run ends (`redact_record` /
`redact_proxy_log`), and
[`tests/test_injection_shape_redaction.py`](../../../tests/test_injection_shape_redaction.py)
pins both directions plus the committed artifacts. Anything captured by other
means must be redacted before it goes anywhere near a commit.

This is a property of the capture path, not of this experiment: the same
binary writes the production `claude.proxy.jsonl` under
`capture="proxy"`, with no redaction anywhere in
`src/swe_lab/harnesses/claude_code/`. Filed as
[task 09](../../../docs/trace-synthesis/plans/README.md#task-09-redact-the-production-proxy-capture).

## Limits

`n` is 4–5 per family on one actor model (`claude-sonnet-4-5`) and one synthetic
task. That is enough to separate a channel that always survives conversion from
one that never does — a mechanical, deterministic property — and enough to
attribute a large behavioural gap. It is **not** enough for a rate: read
"2 of 4 objected" as "this happens often", never as 50%.
