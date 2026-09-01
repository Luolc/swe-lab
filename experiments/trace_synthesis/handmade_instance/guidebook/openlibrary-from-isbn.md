# Guidebook — `Edition.from_isbn()` must accept an ASIN

Instance: `instance_internetarchive__openlibrary-5de7de19211e71b29b2f2ba3b1dff2fe065d660f-…`
Repo: `internetarchive/openlibrary` @ `5f7d8d190e2f0d837545e582fd5db99aae51a979`

Five stages. Each names what it establishes, how you could have known to do it,
and what tells you it is done.

---

## Stage 1 — Find the code under change and its neighbours

**Goal.** Know exactly where `from_isbn` lives, what shape it has today, and
which identifier utilities already exist.

**Actions.**
- `grep -n "from_isbn\|class Edition" openlibrary/core/models.py`
- Read `Edition.from_isbn` in full, and the imports at the top of `models.py`.
- Read `openlibrary/utils/isbn.py` — specifically what `canonical`,
  `to_isbn_13` and `isbn_13_to_isbn_10` return, including on junk input.

**Expected observations.** `from_isbn` is a `@classmethod` on `Edition`. The
three isbn utilities are already imported into `models.py`. `canonical` strips
an identifier down to digits/X, so it returns `""` for a string starting with
`B`.

**Justification.** The problem statement names the file and the method
outright: "In openlibrary/core/models.py, the `Edition.from_isbn()` method".
Everything else in this task hangs off that one method, so its current shape and
its available helpers are the first facts to establish.

**Exit criteria.** You can state `from_isbn`'s current signature, and predict
what `canonical("B06XYHVXVJ")` returns without running it.

---

## Stage 2 — Decide *where* the three new units live

**This stage is a decision, not a formality. Getting it wrong makes every
correct line you write afterwards unreachable.**

**Goal.** Place the three new units so that the callers the task describes can
actually reach them.

**Actions.**
- Re-read the requirements section and note, precisely, the **noun** it uses for
  each new unit.
- Re-read the interface section and note what it does *and does not* specify: it
  gives a path, it does not give a container.
- Look at how the existing code declares units that operate on an identifier
  without needing instance state.

**Expected observations.** The requirements say "**The method**
`get_isbn_or_asin(...)`", "**The method** `is_valid_identifier(...)`", "**The
method** `get_identifier_forms(...)`" — the same noun, three times. The
interface block says only `Path: openlibrary/core/models.py`. And the entire
task is scoped to `Edition.from_isbn`.

**Justification.** A specification that calls something a *method* three times
is telling you it is reached through the class, not through the module. The
interface block's `Path` narrows the file; it is silent about the container,
so it neither contradicts nor settles this — the requirements' wording is the
only signal that speaks to it, and it speaks clearly. A module-level function
with an identical name and an identical body is **a different interface**: it
answers to `models.get_isbn_or_asin(...)` and not to
`Edition.get_isbn_or_asin(...)` or `edition.get_isbn_or_asin(...)`. Note also
that none of the three needs instance state, which tells you *how* to attach
them, not *whether* to.

**Exit criteria.** The three units are declared inside `class Edition`, and both
`Edition.get_isbn_or_asin("…")` and `some_edition.get_isbn_or_asin("…")`
resolve and run.

---

## Stage 3 — Implement the three units to the letter

**Goal.** Behavior that matches every bullet in the requirements, including the
degenerate inputs.

**Actions.** Implement each unit, then walk the requirements list bullet by
bullet and point at the line that realizes it. Pay particular attention to:
- the **order** of the returned list: `[isbn10, isbn13, asin]`;
- dropping `None` **and** empty entries;
- ASIN normalized to uppercase regardless of input case;
- the empty-string cases, which are called out explicitly.

**Expected observations.** `get_isbn_or_asin("")` → `("", "")`.
`get_isbn_or_asin("b06xyhvxvj")` → `("", "B06XYHVXVJ")`.
`get_identifier_forms("", "")` → `[]`. `is_valid_identifier` decides on length
alone — it is not asked to checksum anything.

**Justification.** The requirements enumerate these cases as assertions about
inputs and outputs; each one is a thing that can be checked directly, so leaving
any of them to inference is a choice to be wrong for free.

**Exit criteria.** For every requirement bullet you can name the line that
implements it.

---

## Stage 4 — Rewire `from_isbn`, then follow the rename outwards

**Goal.** `from_isbn` reads its identifier through the new units, and no caller
is left calling it by an old keyword.

**Actions.**
- Rewrite `from_isbn` to: derive `(isbn, asin)`, reject on invalid, build the
  identifier list, return `None` if it is empty, then run the existing lookup
  over that list.
- Rename its first parameter to `isbn_or_asin`.
- `grep -rn "from_isbn(" openlibrary --include=*.py` and update every keyword
  call site.

**Expected observations.** At least two call sites pass the identifier **by
keyword** (`isbn=`), in `openlibrary/plugins/books/dynlinks.py` and
`openlibrary/plugins/openlibrary/code.py`.

**Justification.** The interface section names the input `isbn_or_asin`, so the
parameter is part of the contract you were asked for. A positional caller
survives a rename silently; a keyword caller breaks at run time, and only at run
time — which is exactly why you grep for them rather than trusting the tests.

**Exit criteria.** No caller passes `isbn=` to `from_isbn`.

---

## Stage 5 — Verify, and know what your verification cannot tell you

**Goal.** Do not mistake a green suite for a correct change.

**Actions.**
- Run the test file for the module you changed.
- Then ask a second question, and answer it explicitly: **how many of the tests
  that just ran actually name your three new units?** Grep the test file for
  their names.
- Whatever that answer is, exercise the three units yourself the way the
  requirements describe them — reached from `Edition` and from an instance of
  it — and compare against the exact input/output pairs in the requirements.

**Expected observations.** The suite passes. And **no test in the working tree
mentions any of the three new names.**

**Justification.** Your change introduces three new public units. If the suite
that just went green contains no test that names them, then it did not exercise
them, and its green says only that you broke nothing — it is silent on whether
the new units are correct, and silent on whether they are even reachable by the
name the task specified. A pass whose scope you have not checked is not
evidence. The requirements are themselves a list of concrete input/output pairs;
you can execute them directly, through the same access path the specification
used, and that check *does* discriminate.

**Exit criteria.** You have called each of the three units through `Edition`
(and through an instance), on the exact inputs listed in the requirements —
including `""`, a lowercase ASIN, an ISBN-10 and an ISBN-13 — and seen the
specified outputs.
