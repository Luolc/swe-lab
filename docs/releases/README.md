# Releases — what each version means for a consumer

One file per published version, newest first. Each answers the **one question
no other doc in this repo answers**: *I depend on swe-lab and I am moving from
the previous version to this one — what changed, and what do I have to change?*

That reader is not looking for design. They are looking for the diff at the
seam they touch: a class that is gone, a hook whose default now does something,
a number that used to always be zero. Design lives in an ADR or a task plan;
these files **link** to it and never restate it.

## Index

| Version | Date | Breaking | What it is |
|---|---|---|---|
| [v0.2.13](v0.2.13.md) | 2026-08-25 | no | Two diff-extraction styles end to end: the classic `base_commit` round trip, plus an opt-in pre-agent baseline whose tree mismatch fails ungraded. |
| [v0.2.12](v0.2.12.md) | 2026-08-23 | no | A bigger retry budget on a declared `CodexProvider`; nothing to react to. |
| [v0.2.11](v0.2.11.md) | 2026-08-22 | **yes** — provisioning seam | Agents are provisioned through a declared seam instead of per-backend observers; Grok Build harness; the agent's real exit status is recorded. |

Versions before v0.2.11 have no file here: this folder starts with the release
that first needed one. Their change lists are the GitHub Releases' own
generated notes.

## What belongs here, and what does not

**Here:** anything a consumer's own code has to react to.

- a removed, renamed or moved public name — with its replacement
- a hook or default whose **behavior** changed, especially one that changes
  silently rather than raising (the expensive kind)
- a recorded value that used to be constant and no longer is
- a change that makes results non-comparable with the previous version, even
  though no API moved (a different agent build, a closed injection vector)
- the migration itself: what to write, shown as code

**Not here:**

- *why* the decision was made → an ADR, linked
- how the subsystem is designed → the component's `plans/task-NN-*.md`, linked
- the exhaustive commit list → the GitHub Release's generated notes, which
  `gh release create --generate-notes` already produces from the merged PR
  titles. Duplicating that list in-repo would be a second copy that drifts;
  this file is the **curated** one and links out for the raw one.
- anything about a version that is not yet the one being released — a note is
  written *for* a version, not accumulated speculatively.

## Writing one

A release note is a **point-in-time record**, like an ADR or a task plan: it is
written once, for one version, and afterwards it is history. Do not go back and
rewrite an old one because the code moved on — write the next one.

Order sections by what costs the reader most:

1. **Breaking** — each with **What changed** / **Why** / **What you must do**.
   Say plainly whether ignoring it *raises* or *silently does the wrong thing*;
   the second is worth more words.
2. **Behavior changes** that are not API breaks (results shift, cost shifts).
3. **Added** — new capability, one line each.
4. **Verified** — what was actually run before publishing, with numbers. Not a
   claim that it works; the evidence that it was tried.

## The forcing function

A doc with no re-read trigger rots, so this one is wired into the release flow:
**step 1 of [releasing](../conventions.md#releasing) is finishing this
version's note**, before the version bump lands. That is deliberate — writing
it *after* tagging means writing it from memory, and the migration steps are
exactly the part memory gets wrong.

If a release genuinely has nothing a consumer must react to, the note still
gets written and says so in a sentence. "Nothing to do here" is information.
