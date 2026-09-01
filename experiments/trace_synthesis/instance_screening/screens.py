#!/usr/bin/env python3
"""Five mechanical screens over the issue #261 SWE-bench Pro candidates.

They all answer one question — **determinacy**: is the behavior the hidden tests
judge pinned down by what the solver holds (``problem_statement`` +
``requirements`` + ``interface`` + the repository at ``base_commit``)? A task
that is not pinned down is broken regardless of whether some rollout guessed
right, which is why "one of two rollouts resolved" (issue #261's selection
criterion) screens nothing.

Screen 1 — **the unpinned-token screen**. A lexical element the added test
lines require, that the gold patch introduces, and that appears nowhere in the
solver's four inputs, could only be guessed. Blind spot: it cannot see
*method* versus *Function*, because both words are in the prompt.

Screen 2 — **the requirements/interface diagonal**, per unit. For every unit
the ``interface`` block declares (``Name`` + ``Type``), find how
``requirements`` refers to that same name and compare the two vocabularies. A
unit the interface types ``Function`` while the requirements call it "the
method" is a self-contradictory task statement; the solver has to flip a coin
on placement, and the hidden tests only accept one side. The survey
(``docs/research/swebench-pro-task-quality.md``) found no published detector
for this.

Screen 3 — **the symbol-coverage screen**. The ``interface`` field is the one
mechanism this dataset has for pinning an interface down; Scale added it to
stop a correct implementation from failing on a naming mismatch. So a symbol
that the gold patch *defines*, that the graded tests *exercise*, and that the
``interface`` field never mentions, is a symbol the solver had to guess the
shape of. The motivating case is
``instance_gravitational__teleport-b4e7cd3a…``: the interface block pins
``MaskKeyName`` while ``TestBuildKeyLabel`` grades ``buildKeyLabel`` — the
anti-false-negative mechanism is applied to one function and the grading
happens on another. Neither of the other two screens can see it, because
``string`` and ``[]byte`` both appear in the prompt and the two prose fields
do not contradict each other.

Screen 4 — **the signature-change screen**, the sharpest of the first four. For a
symbol that already exists at ``base_commit`` and that the gold patch
redefines, put the two definition lines side by side. When they differ in
*name, parameter count, parameter types or return arity*, the graded test only
compiles against the new one — so a functionally perfect solution that keeps
the existing signature scores zero, and nothing partial-credits it. That is a
coin flip unless one of the three prompt fields states the change. It decided
two verdicts here that the other screens split on:
``teleport-b4e7cd3a`` (``key []byte`` → ``key string``) and
``vuls-4c04acbd`` (an ``error`` return silently dropped).

Screen 5 — **the snapshot screen**, and it is not the same kind of tool as the
other four. A committed DOM snapshot fixes every class name and nesting level
of the rendered output, so no prose prompt can pin it: the task is *overly
strict tests* by construction. Screens 1–4 output a suspicion for a human to
resolve; this one outputs something closer to a proof. It fires only when the
test patch adds a snapshot assertion, adds or modifies the matching
``__snapshots__`` file, and the paired test file is in ``fail_to_pass`` — so a
snapshot already at ``base_commit``, guarding a ``pass_to_pass`` test against
DOM regressions, is left alone. Both of its hits here are also token hits
(snapshot ⊆ token), so it earns its keep by *upgrading* those two rows from a
suspicion to a near-proof, not by reaching instances nothing else reaches.

The first four screens are complementary, not redundant — four different
diseases:
the tests demand a **literal** the prompt never gave (1); the two prose fields
**contradict** each other (2); the interface field **does not cover** the
symbol grading depends on (3); an existing symbol's **signature moved under
the solver** (4). A hit is an alarm that sends the instance to manual review,
never a verdict.

Screens 2 and 4 also have a precondition, and report it rather than reporting
a clean bill: the diagonal is only readable when the two prose fields talk
about the same units (``diagonal_units_compared``), and the signature screen
only when the symbol was found at ``base_commit``. "No signal" is not
"no evidence" — conflating them is how ``teleport-b4e7cd3a`` first read clean.

Repository source is read over HTTP from GitHub at ``base_commit`` rather than
out of the instance image, because this analysis must not start a container.

**Calibration.** A first pass alarmed on 22/40, and reading the alarms found
defects in the instrument rather than broken tasks. They are fixed here, and
the fix is the point: an alarm rate dominated by false alarms cannot
stand in for a broken-task rate, so anything measured on top of it measures
noise.

1. *The token screen read only the files the patches touch.* A symbol defined
   anywhere else in the repository read as un-derivable — ``storage.ListWithOptions``
   and ``storage.NewNamespace`` in ``flipt-3b2c25ee`` are ordinary existing
   API. The whole repository at ``base_commit`` is now tokenized, streamed from
   the source tarball — but only to **annotate** the alarm
   (``unpinned_but_present_in_repo``), never to suppress it. ``unpinned()``
   still reads the touched files alone. Existing somewhere in the checkout
   makes a symbol *available*, not *pinned*, and annotating dominates
   suppressing: a reader can reproduce suppression by ignoring the annotated
   alarms, but cannot recover an alarm that was never printed.
2. *The token screen counted prose in comments.* ``element-web-b007ea81``
   alarmed on ``100``, ``curve`` and ``seeing`` — all of them words in a
   comment. Comments are now stripped from the **identifier and number
   streams only** — never from string literals, because doing that ate
   ``navidrome-d0dceae0``'s required literal ``http://localhost/p/ABC123``
   from the ``//localhost`` onward, which is exactly the miss this screen
   exists to prevent.
3. *The signature screen ignored the interface field.* ``flipt-3b2c25ee``
   grows a ``store`` parameter on ``New`` — and the interface field declares
   the new signature verbatim, so nothing was guessed. A signature change is
   only an alarm when the interface field does not **declare** the symbol,
   where declaring means a ``Name:`` row, an inline ``Function:``-style row or
   a backticked code span — not merely using the word in prose. That last
   distinction is load-bearing: ``vuls-4c04acbd``'s interface says "for diff
   display" in prose while never declaring ``diff``, whose return arity the
   graded test silently requires.

Usage::

  # the issue #261 mixed-outcome candidates
  direnv exec . uv run python .../screens.py

  # the control: a seeded random sample of the same size from all 731, which
  # answers whether the mixed-outcome selection is enriched for broken tasks
  direnv exec . uv run python .../screens.py --random 40 --seed 261 \
      --out control-screens.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.client
import json
import os
import pathlib
import random
import re
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request

from swe_lab.datasets.loader import load_dataset

_HERE = pathlib.Path(__file__).resolve().parent
_CACHE = pathlib.Path(
    os.environ.get("SCREEN_CACHE", "/tmp/swe-lab-screen-cache")
)

# --- screen 1: the unpinned-token screen ------------------------------------

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# One pattern per delimiter, not one character class for all three. A single
# class cannot capture a literal that contains a *different* quote — and that
# is not hypothetical: ``ansible-de5858f4``'s graded test asserts
# ``actual_cache == '{"version": 1}'``, whose inner ``"`` truncated the match
# to ``version``, a word the prompt does contain. The screen therefore passed
# a task whose test demands a byte-exact literal, which is the exact thing it
# exists to catch.
_STRINGS = (
    re.compile(r'"([^"\n]{3,80})"'),
    re.compile(r"'([^'\n]{3,80})'"),
    re.compile(r"`([^`\n]{3,80})`"),
)
_NUMBER = re.compile(r"\b\d{2,}\b")

# Tokens every test file carries; screening them is pure noise. Kept wide
# because a false alarm costs a reader seconds and a miss costs an experiment.
_BORING = frozenset("""
assert assertEqual assertTrue assertFalse assertRaises assertIn assertIsNone
assertNotNil assertNil assertError assertNoError require Equal NoError Nil
import from def class self return None True False and not for in with test
tests pytest unittest mark parametrize raises fixture expected actual result
value values args kwargs setUp tearDown mock patch MagicMock lambda print len
str int float bool list dict tuple set type object Exception ValueError
TypeError KeyError AttributeError RuntimeError NotImplementedError staticmethod
classmethod property super init main module package the and or if else elif
func var const struct interface range nil err error string bool byte int64
describe it expect beforeEach afterEach jest vi vitest toBe toEqual render
screen fireEvent waitFor userEvent context ctx testing errors fmt strings
package_json src dist node_modules undefined null async await function
""".split())


# Comment syntax across the four languages of this corpus. Applied only to
# text that states a requirement, never to text that states what the solver was
# given — stripping the latter would invent alarms.
_COMMENTS = (
    re.compile(r"/\*.*?\*/", re.S),
    re.compile(r"(?m)//.*$"),
    re.compile(r"(?m)^\s*#.*$"),
    re.compile(r'"""[\s\S]*?"""'),
)


def without_comments(text: str) -> str:
  """Return ``text`` with its comments removed."""
  for pattern in _COMMENTS:
    text = pattern.sub(" ", text)
  return text


def added_lines(patch: str) -> str:
  """Return only the lines a diff adds, without their ``+`` markers."""
  return "\n".join(
      line[1:]
      for line in patch.splitlines()
      if line.startswith("+") and not line.startswith("+++")
  )


def tokens(text: str, drop_comments: bool = False) -> set[str]:
  """Return the screenable lexical elements of a text.

  ``drop_comments`` removes prose words that only ever appear in a comment,
  which cannot be a graded requirement — but it is applied to the *identifier
  and number* streams only, never to string literals. Stripping comments before
  extracting strings destroys real evidence: ``navidrome-d0dceae0``'s test
  requires the literal ``http://localhost/p/ABC123``, and a ``//`` comment rule
  eats it from the ``//localhost`` onward.
  """
  code = without_comments(text) if drop_comments else text
  found = set(_IDENTIFIER.findall(code))
  found |= set(_NUMBER.findall(code))
  for pattern in _STRINGS:
    found |= {match.strip() for match in pattern.findall(text)}
  return {token for token in found if token not in _BORING}


def touched_paths(patch: str) -> list[str]:
  """Return the repository-relative paths a unified diff touches."""
  seen: list[str] = []
  for match in re.finditer(r"^\+\+\+ b/(.+)$", patch, re.MULTILINE):
    path = match.group(1).strip()
    if path != "dev/null" and path not in seen:
      seen.append(path)
  return seen


def fetch(repo: str, commit: str, path: str) -> str:
  """Return one file's text at ``commit``, or ``""`` if it does not exist.

  Cached on disk: the same commit is read by both screens and by hand.
  """
  key = hashlib.sha256(f"{repo}@{commit}/{path}".encode()).hexdigest()
  cached = _CACHE / key
  if cached.exists():
    return cached.read_text(errors="replace")
  quoted = urllib.parse.quote(path)
  url = f"https://raw.githubusercontent.com/{repo}/{commit}/{quoted}"
  try:
    with urllib.request.urlopen(url, timeout=60) as response:
      text = response.read().decode("utf-8", errors="replace")
  except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
    text = ""
  _CACHE.mkdir(parents=True, exist_ok=True)
  _ = cached.write_text(text)
  return text


# Files worth tokenizing out of a repository tarball: source, not assets. The
# cap keeps a generated lockfile or a fixture blob from dominating the set.
_SOURCE_SUFFIXES = frozenset("""
.go .py .js .jsx .ts .tsx .mjs .cjs .java .rb .sh .yml .yaml .json .toml .md
.txt .cfg .ini .html .css .scss .proto .sql
""".split())
_MAX_MEMBER_BYTES = 1_000_000


def _digest(repo: str, commit: str) -> str:
  """Return the cache key for a repository at a commit."""
  return hashlib.sha256(f"{repo}@{commit}".encode()).hexdigest()


def repo_tokens(repo: str, commit: str) -> set[str]:
  """Return every token in the repository at ``commit``.

  The determinacy question asks what the solver could read, and the solver has
  the whole checkout — not only the files the patches happen to touch. Reading
  a narrower set is what made the first pass alarm on ordinary existing API.

  Streamed from the source tarball and cached as a sorted token list, because
  the tarball is large and the token set is not.
  """
  cached = _CACHE / f"tokens-{_digest(repo, commit)}"
  if cached.exists():
    return set(cached.read_text().split("\n"))
  url = f"https://codeload.github.com/{repo}/tar.gz/{commit}"
  # A truncated stream must not be cached: a partial token set silently turns
  # into extra alarms on the next run, with nothing to say it was partial.
  for attempt in range(4):
    found: set[str] = set()
    try:
      with urllib.request.urlopen(url, timeout=600) as response:
        with tarfile.open(fileobj=response, mode="r|gz") as archive:
          for member in archive:
            if not member.isfile() or member.size > _MAX_MEMBER_BYTES:
              continue
            if pathlib.PurePosixPath(member.name).suffix not in _SOURCE_SUFFIXES:
              continue
            handle = archive.extractfile(member)
            if handle is None:
              continue
            found |= tokens(handle.read().decode("utf-8", errors="replace"))
    except (OSError, tarfile.TarError, http.client.HTTPException):
      if attempt == 3:
        raise
      continue
    _CACHE.mkdir(parents=True, exist_ok=True)
    _ = cached.write_text("\n".join(sorted(found)))
    return found
  raise AssertionError("unreachable")


def repo_tokens_cached(repo: str, commit: str) -> set[str] | None:
  """Return the cached token set for a repository, or ``None`` if absent.

  The annotation this feeds is best-effort by design: it makes an alarm cheaper
  to dismiss and never decides anything, so it must not be able to hold the
  screens hostage to a 40-tarball download.
  """
  cached = _CACHE / f"tokens-{_digest(repo, commit)}"
  return set(cached.read_text().split("\n")) if cached.exists() else None


def elsewhere_in_repo(
    instance: object, alarms: list[str], download: bool
) -> list[str]:
  """Return the alarms that do exist somewhere else in the repository.

  Triage, **not** suppression. Existing somewhere in the checkout makes a
  symbol *available*; it does not make it *pinned*, and the screen exists to
  find things that are available but not pinned. Annotating instead of
  suppressing also strictly dominates: a reader holding this list can reproduce
  suppression exactly by ignoring the alarms in it, whereas a reader holding a
  suppressed screen cannot recover an alarm that was never printed. So the
  alarm stands, and this list only says which alarms are cheaper to dismiss.
  """
  if not alarms:
    return []
  present = (
      repo_tokens(instance.repo, instance.base_commit)
      if download
      else repo_tokens_cached(instance.repo, instance.base_commit)
  )
  return [] if present is None else sorted(set(alarms) & present)


def repo_source(instance: object) -> tuple[str, int, int]:
  """Return the touched files' text at ``base_commit``, found count, total."""
  paths = touched_paths(instance.patch) + touched_paths(instance.test_patch)
  paths = list(dict.fromkeys(paths))
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    texts = list(
        pool.map(
            lambda path: fetch(instance.repo, instance.base_commit, path),
            paths,
        )
    )
  return "\n".join(texts), sum(1 for text in texts if text), len(paths)


def unpinned(instance: object, source: str) -> list[str]:
  """Return tokens the tests require that the solver's inputs never mention."""
  required = tokens(added_lines(instance.test_patch), drop_comments=True)
  introduced = tokens(added_lines(instance.patch), drop_comments=True)
  given = tokens(
      "\n".join([
          instance.problem_statement,
          instance.requirements,
          instance.interface,
          source,
      ])
  )
  return sorted((required & introduced) - given)


# --- screen 2: the requirements/interface diagonal --------------------------

# How the corpus writes an interface unit. Three layouts appear across the 731
# rows and all three are keyed on the same two facts, a name and a type:
#   `Type: Function` … `Name: foo`      (openlibrary, navidrome)
#   `Name: \`foo\`` … `Type: function`  (ansible)
#   `Function: foo` … `File: …`         (tutanota)
_KEYED_TYPE = re.compile(r"^\s*(?:\d+\.\s*)?[-*]?\s*Type:\s*`?(\w+)`?", re.M)
_KEYED_NAME = re.compile(r"^\s*(?:\d+\.\s*)?[-*]?\s*Name:\s*`?([\w.]+)", re.M)
_INLINE_UNIT = re.compile(
    r"^\s*(?:\d+\.\s*)?[-*]?\s*(Function|Method|Class|Type|Constant|Interface|"
    r"Enum|Struct|Variable|File):\s*`?([\w.]+)`?\s*$",
    re.M | re.I,
)

# The vocabularies the two fields draw from, collapsed to what the distinction
# actually decides: where the unit lives.
_KIND_OF = {
    "function": "free",
    "func": "free",
    "method": "bound",
    "class": "typeish",
    "type": "typeish",
    "struct": "typeish",
    "interface": "typeish",
    "enum": "typeish",
    "constant": "value",
    "variable": "value",
    "field": "value",
    "property": "value",
    "file": "file",
    "module": "file",
}
_NOUN = re.compile(
    r"\b(method|function|func|class|type|struct|interface|enum|constant|"
    r"variable|field|property|file|module)s?\b",
    re.I,
)


def interface_units(interface: str) -> list[tuple[str, str]]:
  """Return the ``(name, declared type)`` pairs the interface block declares.

  Pairs the two keyed layouts positionally — the fields alternate in a fixed
  order within a block — and adds the inline ``Function: name`` layout.
  """
  units: list[tuple[str, str]] = []
  types = [(m.start(), m.group(1)) for m in _KEYED_TYPE.finditer(interface)]
  names = [(m.start(), m.group(1)) for m in _KEYED_NAME.finditer(interface)]
  for position, name in names:
    nearest = min(
        types, key=lambda t: abs(t[0] - position), default=None
    )
    if nearest is not None and abs(nearest[0] - position) < 400:
      units.append((name, nearest[1].lower()))
  for match in _INLINE_UNIT.finditer(interface):
    units.append((match.group(2), match.group(1).lower()))
  return list(dict.fromkeys(units))


def requirement_noun(requirements: str, name: str) -> str | None:
  """Return the noun ``requirements`` uses for ``name``, if it uses one.

  Looks at the words immediately before each mention of the name and takes the
  nearest kind-noun. ``The method `get_isbn_or_asin`…`` yields ``method``.
  """
  bare = name.split(".")[-1]
  for match in re.finditer(rf"`?\b{re.escape(bare)}\b`?", requirements):
    window = requirements[max(0, match.start() - 60):match.start()]
    nouns = _NOUN.findall(window)
    if nouns:
      return nouns[-1].lower()
  return None


def diagonal(instance: object) -> tuple[list[dict[str, str]], int]:
  """Return the units whose two vocabularies disagree, and how many compared.

  The second number is the screen's precondition. Zero means the two fields
  never described the same unit in kind-noun terms, so the screen had nothing
  to compare — "no signal", which must not be read as "no conflict".
  """
  conflicts: list[dict[str, str]] = []
  compared = 0
  for name, declared in interface_units(instance.interface):
    noun = requirement_noun(instance.requirements, name)
    if noun is None:
      continue
    left, right = _KIND_OF.get(declared), _KIND_OF.get(noun)
    if not (left and right):
      continue
    compared += 1
    if left != right:
      conflicts.append(
          {"name": name, "interface_type": declared, "requirements_noun": noun}
      )
  return conflicts, compared


# --- screen 3: the symbol-coverage screen ----------------------------------

# Definition sites in the four languages this corpus uses. Deliberately shallow
# — a regex over added diff lines, not a parse — because the screen only has to
# name candidates for a reader.
_DEFINITION = (
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*[(\[]", re.M),
    re.compile(r"^\s*type\s+([A-Za-z_]\w*)\b", re.M),
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", re.M),
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)\b", re.M),
    re.compile(
        r"^\s*(?:export\s+)?(?:declare\s+)?(?:const\s+enum|enum|interface|class"
        r"|function|type)\s+([A-Za-z_]\w*)\b",
        re.M,
    ),
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*[:=]", re.M
    ),
)

# What a test *exercises*: something it calls, or names in an import.
_CALLED = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_IMPORTED = re.compile(r"^\s*(?:import|from)\b.*$", re.M)


def defined_by(patch: str) -> set[str]:
  """Return the symbols a diff's added lines define."""
  added = added_lines(patch)
  found: set[str] = set()
  for pattern in _DEFINITION:
    found |= set(pattern.findall(added))
  return {name for name in found if name not in _BORING}


def exercised_by(test_patch: str) -> set[str]:
  """Return the symbols the added test lines call or import."""
  added = added_lines(test_patch)
  found = set(_CALLED.findall(added))
  for line in _IMPORTED.findall(added):
    found |= set(_IDENTIFIER.findall(line))
  return {name for name in found if name not in _BORING}


# A backticked code span. The interface field mixes prose with structure, and
# this is how it marks a name it means as a name.
_CODE_SPAN = re.compile(r"`([A-Za-z_][\w.]*)")


def declared_names(interface: str) -> set[str]:
  """Return the symbols the interface field actually *declares*.

  A ``Name:`` row, an inline ``Function:``-style row, or a backticked code
  span. Deliberately not "any word in the block": ``vuls-4c04acbd``'s
  interface contains the word ``diff`` in the phrase "for diff display" while
  declaring only three other units, and the graded test turns on ``diff``'s
  return arity.
  """
  return (
      {name for name, _ in interface_units(interface)}
      | set(_CODE_SPAN.findall(interface))
  )


def uncovered_symbols(instance: object) -> list[str]:
  """Return graded symbols the gold patch defines that the interface omits."""
  covered = declared_names(instance.interface)
  return sorted(
      (defined_by(instance.patch) & exercised_by(instance.test_patch)) - covered
  )


# --- screen 4: the signature-change screen ---------------------------------

# Where a language writes the signature. Matched against a single line, with
# the symbol's own name interpolated, so the match is the definition and not a
# call site.
_SIGNATURE_TEMPLATES = (
    r"^\s*func\s+(?:\([^)]*\)\s*)?{name}\s*[(\[].*$",
    r"^\s*(?:async\s+)?def\s+{name}\s*\(.*$",
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+{name}\s*[(<].*$",
    r"^\s*(?:export\s+)?(?:const|let|var)\s+{name}\s*[:=].*$",
    r"^\s*(?:export\s+)?(?:type|interface|class|enum)\s+{name}\b.*$",
)


def definition_line(source: str, name: str) -> str:
  """Return the line where ``source`` defines ``name``, or ``""``."""
  for template in _SIGNATURE_TEMPLATES:
    match = re.search(
        template.format(name=re.escape(name)), source, re.M
    )
    if match:
      return " ".join(match.group(0).split())
  return ""


def signature_changes(instance: object, source: str) -> list[dict[str, str]]:
  """Return graded symbols whose signature moved between base and gold.

  Args:
    instance: The dataset row.
    source: The touched files' text at ``base_commit``.

  Returns:
    One entry per symbol the graded tests exercise, that exists at
    ``base_commit``, and whose definition line the gold patch rewrites. A
    symbol absent from ``source`` yields nothing — it is new, which screen 3
    covers, and reporting it here would drown the signal.
  """
  gold = added_lines(instance.patch)
  changed: list[dict[str, str]] = []
  for name in sorted(defined_by(instance.patch) & exercised_by(instance.test_patch)):
    before = definition_line(source, name)
    after = definition_line(gold, name)
    if before and after and before != after:
      changed.append({"symbol": name, "base": before, "gold": after})
  return changed


# --- screen 5: the snapshot screen ------------------------------------------

_SNAPSHOT_ASSERT = re.compile(r"toMatchS(?:napshot|nap)|toMatchInlineSnapshot")
_SNAPSHOT_FILE = re.compile(r"^\+\+\+ b/(.*__snapshots__/(.+)\.snap)$", re.M)
_TEST_FILE = re.compile(r"^\+\+\+ b/(.+)$", re.M)


def snapshot_grading(instance: object) -> list[dict[str, str]]:
  """Return the graded snapshot assertions, if any.

  Unlike the other four this is not a heuristic. A committed DOM snapshot fixes
  every class name and every nesting level of the rendered output; no prose
  prompt can pin that, so the task is *overly strict tests* by construction and
  the screen's output is closer to a proof than to a suspicion.

  Three conditions, all required, so that a legitimate regression guard is not
  condemned: the test patch must add a snapshot assertion, it must add or
  modify the matching ``__snapshots__`` file, and the test file that pairs with
  that snapshot must appear in ``fail_to_pass``. A snapshot file already
  present at ``base_commit`` and left untouched, whose test sits in
  ``pass_to_pass``, is a "don't break the DOM" guard and is fine.
  """
  if not _SNAPSHOT_ASSERT.search(added_lines(instance.test_patch)):
    return []
  graded = "\n".join(instance.fail_to_pass)
  found: list[dict[str, str]] = []
  for snapshot_path, test_name in _SNAPSHOT_FILE.findall(instance.test_patch):
    if test_name in graded:
      found.append({"snapshot": snapshot_path, "graded_test_file": test_name})
  return found


# --- driver -----------------------------------------------------------------


def main() -> None:
  """Run the five screens over a set of instances and write their results.

  The summary ends with the **containments** between hit sets, computed rather
  than remembered. A sentence in the report about which screens overlap is a
  second copy of a fact the data already holds, and nothing keeps two copies in
  sync: the claim "no hit set is contained in another's" survived review once
  while the table one line above it said ``snapshot ⊆ token``. Printing it here
  means the assertion changes when the data does.
  """
  parser = argparse.ArgumentParser(description="Screen SWE-bench Pro tasks.")
  _ = parser.add_argument(
      "--random",
      type=int,
      default=0,
      help="Screen this many randomly sampled rows instead of instances.txt.",
  )
  _ = parser.add_argument("--seed", type=int, default=261)
  _ = parser.add_argument("--out", default="screens.json")
  _ = parser.add_argument(
      "--repo-tokens",
      action="store_true",
      help=(
          "Download each repository's source tarball to annotate which token"
          " alarms exist elsewhere in the checkout. Off by default: the"
          " annotation never decides a verdict, and the download is by far the"
          " most expensive thing here."
      ),
  )
  args = parser.parse_args()

  dataset = load_dataset("swebench_pro")
  if args.random:
    every = [record.instance_id for record in dataset]
    ids = random.Random(args.seed).sample(every, args.random)
  else:
    ids = (_HERE / "instances.txt").read_text().split()
  rows = []
  for iid in ids:
    instance = dataset.require(iid)
    source, found, total = repo_source(instance)
    conflicts, compared = diagonal(instance)
    alarms = unpinned(instance, source)
    row = {
        "instance_id": iid,
        "repo": instance.repo,
        "language": instance.repo_language,
        "base_commit": instance.base_commit,
        "files_found": found,
        "files_touched": total,
        "source_chars": len(source),
        "unpinned_tokens": alarms,
        "unpinned_but_present_in_repo": elsewhere_in_repo(
            instance, alarms, args.repo_tokens
        ),
        "diagonal_conflicts": conflicts,
        "diagonal_units_compared": compared,
        "uncovered_symbols": uncovered_symbols(instance),
        "signature_changes": signature_changes(instance, source),
        "graded_snapshots": snapshot_grading(instance),
        "interface_declares_names": len(interface_units(instance.interface)),
        "required_tests": len(instance.fail_to_pass),
    }
    rows.append(row)
    print(
        f"{iid[:60]:60s} tokens={len(row['unpinned_tokens']):3d}"
        f" diagonal={len(row['diagonal_conflicts']):2d}"
        f" uncovered={len(row['uncovered_symbols']):2d}"
        f" sigchange={len(row['signature_changes']):2d}"
        f" snapshot={len(row['graded_snapshots']):2d}"
        f" files={found}/{total}",
        file=sys.stderr,
    )
  _ = (_HERE / args.out).write_text(json.dumps(rows, indent=2) + "\n")

  hits = {
      "token": {r["instance_id"] for r in rows if r["unpinned_tokens"]},
      "diagonal": {r["instance_id"] for r in rows if r["diagonal_conflicts"]},
      "symbol": {r["instance_id"] for r in rows if r["uncovered_symbols"]},
      "signature": {r["instance_id"] for r in rows if r["signature_changes"]},
      "snapshot": {r["instance_id"] for r in rows if r["graded_snapshots"]},
  }
  lines = [f"{name}: {len(hit)}/{len(rows)}" for name, hit in hits.items()]
  names = list(hits)
  for i, left in enumerate(names):
    for right in names[i + 1:]:
      lines.append(f"{left} & {right}: {len(hits[left] & hits[right])}")
  every = set.intersection(*hits.values())
  union = set.union(*hits.values())
  lines.append(f"all five: {len(every)}")
  lines.append(f"any: {len(union)}   none: {len(rows) - len(union)}")
  contained: list[str] = []
  for index, left in enumerate(names):
    for offset, right in enumerate(names):
      if left == right or not hits[left]:
        continue
      if hits[left] == hits[right]:
        if index < offset:
          contained.append(f"{left} == {right}")
      elif hits[left] < hits[right]:
        contained.append(f"{left} < {right}")
  lines.append("containments: " + (", ".join(contained) or "none"))
  print("\n" + "\n".join(lines), file=sys.stderr)


if __name__ == "__main__":
  main()
