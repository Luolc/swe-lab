"""element-web aec454dd: wasm/GC double-free in the wysiwyg composer."""

from __future__ import annotations

import base64
import hashlib

from swe_lab.sandbox import Inline, Mount

from .._seam import (
    RegisteredFix,
    render,
    SweBenchProUnitTestSpec,
    with_setup,
)
from .tarball import TARBALL_B64

_WYSIWYG_INSTANCE = (
    "instance_element-hq__element-web"
    "-aec454dd6feeb93000380523cbb0b3681c0275fd-vnan"
)
_WYSIWYG_TARBALL_NAME = "matrix-wysiwyg-1.4.1.tgz"
_WYSIWYG_PACKAGE = "node_modules/@matrix-org/matrix-wysiwyg"
_WYSIWYG_VERSION = "1.4.1"

# Runs from the repo root (the script has already `cd`-ed to WORKDIR), under
# `set -e`, after the golden checkout. `node_modules` is untracked, so nothing
# earlier in the script can undo this and nothing later re-resolves it.
_WYSIWYG_SETUP = """
tarball="$SANDBOX_WORKSPACE/@TARBALL@"
actual="$(sha512sum "$tarball" | cut -d' ' -f1)"
if [ "$actual" != "@SHA512@" ]; then
  echo "matrix-wysiwyg tarball integrity mismatch: $actual" >&2
  exit 1
fi
if [ ! -d "@PACKAGE@" ]; then
  echo "@PACKAGE@ not found; the eval image's layout changed" >&2
  exit 1
fi
find node_modules -type d -path '*/@matrix-org/matrix-wysiwyg' |
while IFS= read -r pkg; do
  rm -rf "$pkg" && mkdir -p "$pkg"
  tar -xzf "$tarball" -C "$pkg" --strip-components=1
done
installed="$(node -p "require('./@PACKAGE@/package.json').version")"
if [ "$installed" != "@VERSION@" ]; then
  echo "matrix-wysiwyg replacement did not take: got $installed" >&2
  exit 1
fi
"""


def _fix_instance_element_web_aec454dd(
    spec: SweBenchProUnitTestSpec,
) -> SweBenchProUnitTestSpec:
  """Replace ``@matrix-org/matrix-wysiwyg`` 1.4.0 with the fixed 1.4.1.

  In 1.4.0 the ``set_link_suggestion`` binding takes ``SuggestionPattern`` **by
  value**, so Rust takes ownership and drops it while the JS wrapper stays
  registered with a ``FinalizationRegistry``. When GC later runs that finalizer
  it frees already-freed memory and wasm-bindgen's borrow guard throws
  ``recursive use of an object detected which would lead to unsafe aliasing in
  rust`` — asynchronously, into whichever test happens to be running. Hence the
  flake is GC-timed: random, load-sensitive, and it moves between tests.

  Diagnosed upstream in element-hq/element-web#24951; fixed by
  matrix-org/matrix-rich-text-editor#635 (``&SuggestionPattern`` + ``clone``),
  published as 1.4.1. This instance's ``base_commit`` (2023-03-27 08:07 UTC)
  lands 2h50m *before* matrix-org/matrix-react-sdk#10458 reverted the feature
  that reaches the broken binding, so the tree is frozen mid-bug — and the 7
  ``Mentions`` tests that reach it are in ``pass_to_pass``, along with 22 more
  wysiwyg tests that fail as collateral. None of them are in ``fail_to_pass``.

  **The divergence:** the image installs with ``--frozen-lockfile`` and the
  lockfile pins 1.4.0 exactly, so this is not a neutral re-resolution — it
  knowingly overrides the lockfile. What makes it a safe trade: 1.4.1 is inside
  the ``^1.4.0`` range ``package.json`` already declares, ``dist/index.d.ts``
  is byte-identical between the two, the package has no runtime dependencies,
  and the published diff is confined to the one binding above.

  Args:
    spec: The compiled spec for this instance.

  Returns:
    The spec with the tarball staged and the replacement spliced in.
  """
  tarball = wysiwyg_tarball()
  return with_setup(
      spec,
      mounts={_WYSIWYG_TARBALL_NAME: Mount(Inline(tarball))},
      setup=render(
          _WYSIWYG_SETUP,
          TARBALL=_WYSIWYG_TARBALL_NAME,
          # Derived from the vendored bytes rather than written down twice, so
          # the check in the container cannot drift from what is mounted.
          SHA512=hashlib.sha512(tarball).hexdigest(),
          PACKAGE=_WYSIWYG_PACKAGE,
          VERSION=_WYSIWYG_VERSION,
      ),
  )


def wysiwyg_tarball() -> bytes:
  """Return the vendored ``@matrix-org/matrix-wysiwyg`` 1.4.1 tarball bytes."""
  return base64.b64decode(TARBALL_B64)


ELEMENT_WEB_WYSIWYG = RegisteredFix(
    instances=(_WYSIWYG_INSTANCE,), fix=_fix_instance_element_web_aec454dd
)
