# Guidebook — `QtColor` must parse percentages per channel

Instance: `instance_qutebrowser__qutebrowser-9ed748effa8f3bcd804612d9291da017b514e12f-…`
Repo: `qutebrowser/qutebrowser` @ `30250d8e680041a15e440aa7441a224d670c3862`
Unit under change: `QtColor` in `qutebrowser/config/configtypes.py`

Five stages. Each names what it establishes, how the actor could have known to
do it, and what tells it the stage is done.

**The failure this guidebook exists to prevent is in stage 4.** Stages 1–3 are
what the actor has to have in hand before stage 4's decision is even visible to
it; stage 5 is the check that would have caught the mistake without any of this.

---

## Stage 1 — Read the unit as it stands

**Goal.** Know exactly what `QtColor.to_py` and `QtColor._parse_value` do today,
including on malformed input.

**Actions.**
- `grep -n "class QtColor" -A 60 qutebrowser/config/configtypes.py`
- Read `_parse_value` and `to_py` in full.

**Expected observations.** `to_py`'s function-syntax branch is guarded by
`if '(' in value and value.endswith(')')` — deliberately loose. It takes
`value.index('(')` as the split point and `value[openparen+1:-1]` as the
argument text, so **any** string with an open paren and a trailing close paren
enters component parsing. `_parse_value` takes one argument and multiplies
every percentage by `255.0 / 100`.

**Justification.** The task is a rewrite of one method. Its current behavior is
the specification for everything the task does *not* ask to change.

**Exit criteria.** The actor can say what `to_py('rgb(1, 2, 3))')` does today
and which exception message it produces.

---

## Stage 2 — Extract the four graded behaviors from the requirements

**Goal.** Turn the prose into a checklist before writing code.

**Actions.** Read the requirements and write down, verbatim, each error-message
suffix and each numeric range.

**Expected observations.** Four message suffixes and one normalization rule:
unknown identifier ends `"<kind> not in ['hsv', 'hsva', 'rgb', 'rgba']"`; wrong
count ends `"expected N values for <kind>"`; a bad component ends `"must be a
valid color value"`; a globally malformed notation ends `"must be a valid
color"`. Hue maps to 0–359; every other channel to 0–255.

**Justification.** The hidden tests parametrize on these strings. The
requirements quote them, which is unusually explicit and is the reason this task
is well-posed.

**Exit criteria.** The four suffixes are written down, and the actor can say
which one `rgb()` produces and which one `foobar` produces.

---

## Stage 3 — Make the percentage normalization per channel

**Goal.** `hsv(10%,10%,10%)` must give hue 35, not 25.

**Actions.** Give `_parse_value` the channel it is parsing, and pick the
multiplier from it. The channel letters are exactly the characters of the
identifier: `'hsva'` is `h`, `s`, `v`, `a` in order, so zipping the identifier
against the value list gives each component its channel for free — which also
makes the count check `len(kind) != len(vals)` rather than a lookup table.

**Expected observations.** Only `h` uses 359; everything else keeps 255.

**Justification.** The problem statement names this as the bug outright, and the
requirements state both ranges.

**Exit criteria.** `hsv(10%,10%,10%)` and `hsva(10%,20%,30%,40%)` produce the
expected colors.

---

## Stage 4 — Add the new validation **without narrowing what already enters it**

**This is a decision, not a formality. The three new error messages are easy;
the trap is the branch you have to route them through.**

**Goal.** Distinguish unknown identifier, wrong component count and bad
component — while leaving the *entry condition* to the function-syntax branch
exactly as loose as it was.

**Actions.**
- Keep `'(' in value and value.endswith(')')` with `value.index('(')` as the
  split. Add the three checks *inside* that branch.
- Before changing the entry condition in any way, ask what happens to a string
  that has unbalanced parentheses.

**Expected observations.** `rgb(1, 2, 3))` and `rgb((1, 2, 3)` both satisfy the
loose guard. They split into components that are not numbers, so they raise
**`"must be a valid color value"`** — the *component* message. A stricter entry
test, such as a regex whose argument group excludes parentheses, makes both
strings miss the branch entirely and fall through to the generic
`"must be a valid color"` at the bottom of the method. Both strings then still
raise, and still raise `ValidationError`, so nothing the task asks about looks
different. **What changed is which of the two messages they carry.**

**Justification.** Nothing in the problem statement, the requirements or the
interface asks for the entry condition to be tightened. Requirement five's
"missing/extra outer parentheses" is about `rgb(1, 2, 3` and `rgb)` — strings
with no matching close paren, which the loose guard already rejects. It is not
about strings whose parens are unbalanced *inside* an otherwise well-formed
call.

**Exit criteria.** `rgb(1, 2, 3))` and `rgb((1, 2, 3)` still raise a
`ValidationError` whose message ends `"must be a valid color value"`, not
`"must be a valid color"`.

---

## Stage 5 — Run the whole neighbouring suite, not the new cases

**Goal.** Catch a regression in behavior the task never mentioned.

**Actions.**
- `python -m pytest tests/unit/config/test_configtypes.py -k QtColor`
- Run it **before** editing anything, so the baseline is known, and again after.

**Expected observations.** `TestQtColor` already contains invalid-input cases
the task text never refers to, including the two unbalanced-parenthesis strings.
They pass at `base_commit`.

**Justification.** The task changes a parser's error taxonomy. A change to a
taxonomy is exactly the change that keeps every case *failing* while moving
which failure it is, so "the new tests pass" is not evidence of anything.

**Exit criteria.** Every test in `TestQtColor` that passed before the change
passes after it. If one moved from one error message to another, that is the
regression, and it is in stage 4.
