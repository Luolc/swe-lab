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
correct line you write afterwards unreachable — and the task text will not
settle it for you.**

**Goal.** Place the three new units so that *every* access path the task
describes actually works.

**Actions.**
- Read the requirements section and note the **noun** it uses for each new unit.
- Read the interface section and note the **type** it assigns to each one.
- Put the two side by side before writing any code.

**Expected observations.** They disagree, and each is consistent with itself:

- The requirements say "**The method** `get_isbn_or_asin(...)`", "**The method**
  `is_valid_identifier(...)`", "**The method** `get_identifier_forms(...)`" —
  the same noun, three times.
- The interface section says `Type: Function` for all three, with
  `Path: openlibrary/core/models.py` and **no container**.

A *method* is reached through a class; a *function* at that path is reached
through the module. These are different interfaces, and the task asserts both.

**Justification.** The task specifies the same three units twice, in two
vocabularies, and the two do not agree. No amount of re-reading resolves that —
the conflict is in the text, not in your understanding of it. So committing to
one reading is a coin flip, and it is a coin flip on every assertion that will
be made about these three names. Neither placement excludes the other: a
`@staticmethod` on the class and a module-level name can both exist, and they
can be the same object. When a specification contradicts itself and satisfying
both readings is cheap, satisfy both — do not guess which half the caller used.

Note separately that none of the three needs instance state. That tells you
*how* to attach them to the class (`@staticmethod`), not *whether* to.

**Exit criteria.** All three of these resolve and return the same thing:
`Edition.get_isbn_or_asin("…")`, `some_edition.get_isbn_or_asin("…")`, and
`models.get_isbn_or_asin("…")`. If you can only make one of them work, you have
picked a side — go back and say why.

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
- Whatever that answer is, exercise the three units yourself against the exact
  input/output pairs in the requirements — and do it through **every** access
  path the task names ([stage 2](#stage-2--decide-where-the-three-new-units-live)),
  not only the one your file happens to expose. A smoke test that imports the
  names the way *you* wrote them will pass whichever way you wrote them; that is
  what makes it worthless as a check on placement.

**Expected observations.** The suite passes. And **no test in the working tree
mentions any of the three new names.**

**Justification.** Your change introduces three new public units. If the suite
that just went green contains no test that names them, then it did not exercise
them, and its green says only that you broke nothing — it is silent on whether
the new units are correct, and silent on whether they are even reachable by the
name the task specified. A pass whose scope you have not checked is not
evidence. The requirements are themselves a list of concrete input/output pairs;
you can execute them directly, through each access path the specification
names, and that check *does* discriminate — which a self-consistent smoke test
against your own import does not.

**Exit criteria.** You have called each of the three units through `Edition`,
through an instance, **and** through the module, on the exact inputs listed in
the requirements —
including `""`, a lowercase ASIN, an ISBN-10 and an ISBN-13 — and seen the
specified outputs.
