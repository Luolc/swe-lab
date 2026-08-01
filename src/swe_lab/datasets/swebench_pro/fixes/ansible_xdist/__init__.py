"""ansible a20a5270: a pytest-xdist temp-directory collision."""

from __future__ import annotations

from ...constants import RUN_SCRIPT_NAME
from .._seam import (
    RegisteredFix,
    render,
    SweBenchProUnitTestSpec,
    with_setup,
)

_ANSIBLE_XDIST_INSTANCE = (
    "instance_ansible__ansible-a20a52701402a12f91396549df"
    "04ac55809f68e9-v1055803c3a812189a1133297f7f546857928"
    "3f86"
)

# Runs from the repo root under `set -e`, after the golden checkout. Rewrites
# the *harness's* run script, not the repo: the only thing that changes is how
# many processes run the tests.
_ANSIBLE_XDIST_SETUP = """
script="$SANDBOX_WORKSPACE/@RUN_SCRIPT@"
units="ansible-test units"
if ! grep -q "$units --python" "$script"; then
  echo "no '$units' invocation in @RUN_SCRIPT@; the harness changed" >&2
  exit 1
fi
sed -i "s|$units --python|$units --num-workers 1 --python|" "$script"
if ! grep -q -- "--num-workers 1" "$script"; then
  echo "failed to pin ansible-test to a single worker" >&2
  exit 1
fi
"""


def _fix_instance_ansible_a20a5270(
    spec: SweBenchProUnitTestSpec,
) -> SweBenchProUnitTestSpec:
  """Pin ``ansible-test units`` to one worker, ending an xdist tmpdir race.

  The harness runs unit tests through ``ansible-test units``, which builds its
  own pytest command with **``-n auto``** hardcoded
  (``test/lib/ansible_test/_internal/units/__init__.py``), so the worker count
  is the machine's CPU count. Those workers then share one numbered basetemp —
  the observed failure is an ERROR at *setup* from worker ``[gw10]``, with a
  basetemp of ``/tmp/pytest-of-root/pytest-15`` carrying no per-worker
  ``popen-gwN`` component — and ``tmp_path_factory.mktemp`` appends a
  predictable ``…Input0`` suffix, so two workers land on the same directory and
  race a recursive skeleton copy. The loser gets ``FileNotFoundError`` mid-copy,
  which is why the failing test name moves between runs: it is whichever
  fixture lost.

  Because the rate scales with the worker count, this is *not* reliably fixed
  by retrying: on a wide machine the collision is close to certain, and it was
  still failing at three retries. Pinning the worker count removes the race
  instead of resampling it.

  ``--num-workers`` is a documented ``ansible-test units`` flag at this commit
  (``test/lib/ansible_test/_internal/cli.py``), so this asks the harness for
  something it already supports. The cost is near zero: the instance selects
  two files, only one of which is a unit test.

  **The divergence:** the harness's own invocation is edited in the workspace,
  which no other entry here does. What keeps it an environment fix is that it
  changes neither the tests nor the source under test nor the expectation — only
  how many processes run them. The instance's single ``fail_to_pass``
  (``test_extract_tar_file_outside_dir``) never failed; all 56 observed failures
  were ``pass_to_pass`` bystanders riding the same run.

  Args:
    spec: The compiled spec for this instance.

  Returns:
    The spec with the worker-count pin spliced in.
  """
  return with_setup(
      spec,
      mounts={},
      setup=render(_ANSIBLE_XDIST_SETUP, RUN_SCRIPT=RUN_SCRIPT_NAME),
  )


ANSIBLE_XDIST = RegisteredFix(
    instances=(_ANSIBLE_XDIST_INSTANCE,), fix=_fix_instance_ansible_a20a5270
)
