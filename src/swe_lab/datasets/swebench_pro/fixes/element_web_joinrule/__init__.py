"""element-web 9a31cd0f: a transient label that vanishes before it is seen."""

from __future__ import annotations

from .._seam import (
    RegisteredFix,
    render,
    SweBenchProUnitTestSpec,
    with_setup,
)

_JOIN_RULE_INSTANCE = (
    "instance_element-hq__element-web-9a31cd0fa849da810b4"
    "fac6c6c015145e850b282-vnan"
)
_JOIN_RULE_TEST = "test/components/views/settings/JoinRuleSettings-test.tsx"

# Ports element-hq/element-web@9efa458 ("Unflake JoinRuleSettings test", #11715)
# line for line, including its replacement comment, so the tree matches what
# upstream shipped rather than an approximation of it. Runs after the golden
# checkout, which is what puts the un-deflaked test there in the first place.
_JOIN_RULE_SETUP = """
t="@TEST@"
if ! grep -q 'findByText("Updating space...")' "$t"; then
  echo "no 'Updating space...' assertion in @TEST@; already deflaked?" >&2
  exit 1
fi
imp='fireEvent, render, screen'
note='// Upstream dropped the "Updating space..." assertion here: it'
note="$note sometimes disappeared too quickly to be observed"
note="$note (element-web 9efa458)."
gone='expect(screen.queryByRole("dialog")).not.toBeInTheDocument()'
sed -i "s|{ $imp, within }|{ $imp, waitFor, within }|" "$t"
sed -i "s|// update spaces|$note|" "$t"
sed -i '/findByText("Updating space...")/d' "$t"
sed -i "s|$gone;|await waitFor(() => $gone);|" "$t"
if grep -q 'findByText("Updating space...")' "$t"; then
  echo "failed to remove the transient assertion from @TEST@" >&2
  exit 1
fi
if ! grep -q 'screen, waitFor, within' "$t"; then
  echo "failed to import waitFor into @TEST@" >&2
  exit 1
fi
if ! grep -q 'await waitFor(() => expect(screen.queryByRole' "$t"; then
  echo "failed to wrap the modal-closed assertion in @TEST@" >&2
  exit 1
fi
if grep -q 'toBeInTheDocument();)' "$t"; then
  echo "malformed wrap in @TEST@: semicolon inside the call" >&2
  exit 1
fi
"""


def _fix_instance_element_web_9a31cd0f(
    spec: SweBenchProUnitTestSpec,
) -> SweBenchProUnitTestSpec:
  """Port upstream's deflake of the `JoinRuleSettings` upgrade test.

  The test drives a progress modal through a sequence of labels and asserts the
  last one, ``findByText("Updating space...")``, before the modal closes. That
  label is the final state of ``upgradeRoom``'s progress callback
  (``src/utils/RoomUpgrade.ts``): the relink loop increments
  ``updateSpacesProgress`` and then the function returns, so on React 17 — where
  a setState in a promise callback renders synchronously — the label is
  committed and removed a handful of microtasks apart. ``findByText`` observes
  on MutationObserver deliveries and a 50 ms interval, so whether it ever sees
  the label is decided by where those land in that window.

  **A timeout cannot fix this.** The label is not late, it is gone; waiting
  longer for a node already removed changes nothing. Retrying is nearly as weak,
  because a retry re-rolls the scheduling without widening the window — this
  instance needed three retries to recover, which is expensive and still not
  reliable.

  Upstream hit exactly this and fixed it by **deleting the assertion**:

  - tracking issue: element-hq/element-web#25625, "Flaky Jest test:
    <JoinRuleSettings /> … upgrades room when changing join rule to restricted"
    (the restricted twin of this instance's knock test);
  - fix: element-hq/element-web@9efa458, "Unflake JoinRuleSettings test"
    (#11715), whose commit message reads *"Don't look for 'Updating space'
    message in joinrulesettings test, as it may disappear too quickly for us to
    see"*, and whose replacement comment survives on ``develop`` today.

  This instance's commit is
  matrix-org/matrix-react-sdk@9a31cd0f, "Allow setting room join rule to knock"
  (#11248, 2023-07-19) — the PR that *added* these tests. Upstream deflaked them
  2.5 months later, on 2023-10-05, so the instance is frozen at the commit that
  introduced its own flake. That is the fourth such case in this corpus, after
  NodeBB `22368b99`, vuls `83bcca6e` and teleport `78b0d8c7`.

  **Why this is allowed to touch a graded test**, against the rule stated at the
  top of this module: the removed assertion is about a progress label in a
  *shared room-upgrade dialog*, not about knock join rules, which is what the
  instance grades. The test still runs and still asserts the outcome — that
  ``upgradeRoom`` was called with the right version, and that the modal closed.
  Only an intermediate that upstream itself concluded is unobservable is
  dropped.

  **Both halves of upstream's fix are ported, not just the deletion.** The
  removed ``await findByText`` was also acting as a synchronisation point, so
  deleting it alone would leave the immediately following "modal closed" check
  racing what it used to wait for. Upstream wrapped that check in ``waitFor`` in
  the same commit; porting only the deletion would trade one flake for another.

  One deliberate deviation from upstream: they wrapped a single
  ``queryByRole("dialog")`` assertion in ``waitFor``, and this wraps both in the
  file. Targeting only the first needs fragile sed addressing, and the second is
  the same defensive change on a sibling test — it can only make an assertion
  more patient, never weaker.

  Args:
    spec: The compiled spec for this instance.

  Returns:
    The spec with the deflake spliced in after the golden checkout.
  """
  return with_setup(
      spec,
      mounts={},
      setup=render(_JOIN_RULE_SETUP, TEST=_JOIN_RULE_TEST),
  )


# instance_id -> the fix applied to its spec after compilation.


ELEMENT_WEB_JOINRULE = RegisteredFix(
    instances=(_JOIN_RULE_INSTANCE,), fix=_fix_instance_element_web_9a31cd0f
)
