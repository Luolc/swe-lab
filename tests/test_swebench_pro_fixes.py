"""Per-instance environment fixes: provenance, placement, and blast radius.

The fixes exist to stop `pass_to_pass` bystanders from failing runs for reasons
unrelated to the task (see ``fixes.py``). What has to hold: the vendored bytes
are the ones upstream published, the bash lands in the one window where it
survives, and an instance without a fix is untouched.
"""

import base64
from collections.abc import Iterator
import dataclasses
from dataclasses import replace
import hashlib
import json
import re

import pytest

from swe_lab.datasets.swebench_pro.constants import (
    PARSER_NAME,
    RUN_SCRIPT_NAME,
)
from swe_lab.datasets.swebench_pro.fixes import (
    _FIXES,
    applied_fix_name,
    apply_instance_fix,
    fixed_instances,
    register_fix,
    SweBenchProUnitTestSpec,
    with_setup,
)
from swe_lab.datasets.swebench_pro.fixes._seam import _RUN_MARKER
from swe_lab.datasets.swebench_pro.fixes.element_web_wysiwyg import (
    _WYSIWYG_INSTANCE,
    _WYSIWYG_TARBALL_NAME,
    wysiwyg_tarball,
)
from swe_lab.datasets.swebench_pro.fixes.tutanota_clock import (
    _SHIM_NAME,
    _TUTANOTA_CLOCK_INSTANCE,
    clock_shim,
)
from swe_lab.datasets.swebench_pro.known_flaky import flaky_instances
from swe_lab.datasets.swebench_pro.record import SweBenchProInstance
from swe_lab.datasets.swebench_pro.unit_test import compile_unit_test
from swe_lab.sandbox import Inline

# npm's published ``dist.integrity`` for @matrix-org/matrix-wysiwyg@1.4.1.
_NPM_INTEGRITY = (
    "sha512-B8sxY3pE2XyRyQ1g7cx0YjGaDZ1A0Uh5XxS/lNdxQ/0ctRJj6IBy7Kti"
    "UjxDRdA15ioZnf6aoJBRkBSr02qhaw=="
)
_GOLDEN_CHECKOUT = "git checkout deadbeef -- test/a-test.ts"


def _spec(instance_id: str):
  spec = compile_unit_test(
      patch="diff --git a/x b/x\n",
      base_commit="abc123",
      selected_test_files_to_run=("test/a-test.ts",),
      golden_test_checkout_cmd=_GOLDEN_CHECKOUT,
      fail_to_pass=("a",),
      pass_to_pass=("b",),
      run_script=b"#!/bin/bash\n",
      parser=b"print()\n",
  )
  return apply_instance_fix(instance_id, spec)


def test_vendored_tarball_is_the_published_artifact():
  # Provenance, not a checksum ritual: if the blob is ever regenerated from
  # somewhere other than npm, or edited, this is what catches it.
  raw = wysiwyg_tarball()
  digest = base64.b64encode(hashlib.sha512(raw).digest()).decode()
  assert f"sha512-{digest}" == _NPM_INTEGRITY
  assert len(raw) == 780683
  assert raw[:2] == b"\x1f\x8b"  # gzip magic: a real .tgz, not text


def test_an_instance_without_a_fix_is_returned_untouched():
  plain = compile_unit_test(
      patch=None,
      base_commit="abc123",
      selected_test_files_to_run=("test/a-test.ts",),
      golden_test_checkout_cmd="",
      fail_to_pass=(),
      pass_to_pass=(),
      run_script=b"",
      parser=b"",
  )
  assert apply_instance_fix("instance_someone__else-1234", plain) is plain


def test_the_fixed_instance_stages_the_tarball():
  spec = _spec(_WYSIWYG_INSTANCE)
  resource = spec.mounts[_WYSIWYG_TARBALL_NAME].resource
  assert isinstance(resource, Inline)
  assert resource.content == wysiwyg_tarball()
  # the spec's own mounts survive alongside it
  assert RUN_SCRIPT_NAME in spec.mounts
  assert PARSER_NAME in spec.mounts


def test_fix_bash_lands_after_the_golden_checkout_and_before_the_run():
  # The whole seam depends on this window: earlier and `git reset --hard` or
  # the golden checkout wipes it; later and the tests have already run.
  lines = _spec(_WYSIWYG_INSTANCE).eval_script.splitlines()
  checkout = lines.index(_GOLDEN_CHECKOUT)
  marker = lines.index(_RUN_MARKER)
  patch_line = next(
      i for i, line in enumerate(lines) if _WYSIWYG_TARBALL_NAME in line
  )
  assert checkout < patch_line < marker
  # ...and still under `set -e`, so a failed fix aborts rather than grading a
  # half-patched tree.
  assert lines.index("set -e") < patch_line


def test_fix_bash_verifies_what_it_staged_and_what_it_produced():
  script = _spec(_WYSIWYG_INSTANCE).eval_script
  # The integrity check must be over the bytes actually mounted, not a
  # hand-copied constant that can drift from them.
  assert hashlib.sha512(wysiwyg_tarball()).hexdigest() in script
  assert "integrity mismatch" in script
  # A patch that silently no-ops is worse than none: the run then reads clean.
  assert "replacement did not take" in script
  assert "@" not in script.replace("@matrix-org", "")  # no stray placeholders


def test_the_fix_touches_no_test_expectations():
  # The defining property of a *harness* fix: it changes the environment, never
  # what counts as passing.
  fixed = _spec(_WYSIWYG_INSTANCE)
  plain = compile_unit_test(
      patch="diff --git a/x b/x\n",
      base_commit="abc123",
      selected_test_files_to_run=("test/a-test.ts",),
      golden_test_checkout_cmd=_GOLDEN_CHECKOUT,
      fail_to_pass=("a",),
      pass_to_pass=("b",),
      run_script=b"#!/bin/bash\n",
      parser=b"print()\n",
  )
  assert fixed.grader == plain.grader
  assert fixed.native_outputs == plain.native_outputs
  # every line of the original script survives, in order
  original = plain.eval_script.splitlines()
  assert [
      line for line in fixed.eval_script.splitlines() if line in original
  ] == original


def test_every_registered_fix_applies_to_a_real_instance_id():
  # Guards against a typo'd key, which would be a silent no-op forever.
  for instance_id in fixed_instances():
    assert instance_id.startswith("instance_")
    assert apply_instance_fix(instance_id, _plain()) is not _plain()


def _plain():
  return compile_unit_test(
      patch=None,
      base_commit="abc123",
      selected_test_files_to_run=("test/a-test.ts",),
      golden_test_checkout_cmd=_GOLDEN_CHECKOUT,
      fail_to_pass=(),
      pass_to_pass=(),
      run_script=b"",
      parser=b"",
  )


def test_splice_refuses_a_script_it_cannot_place_the_fix_in():
  spec = _plain()
  broken = type(spec)(
      eval_script="echo hi\n",  # no run marker
      mounts=spec.mounts,
      grader=spec.grader,
      native_outputs=spec.native_outputs,
  )
  with pytest.raises(ValueError, match="exactly one"):
    _ = with_setup(broken, setup="echo patched", mounts={})


# ─── run provenance ──────────────────────────────────────────────────────────


def _instance(instance_id: str) -> SweBenchProInstance:
  return SweBenchProInstance(
      repo="acme/widget",
      instance_id=instance_id,
      base_commit="abc123",
      patch="",
      test_patch="",
      problem_statement="",
      requirements="",
      interface="",
      repo_language="js",
      fail_to_pass=(),
      pass_to_pass=(),
      issue_specificity=(),
      issue_categories=(),
      before_repo_set_cmd="",
      selected_test_files_to_run=(),
      dockerhub_tag="tag",
  )


def test_an_ordinary_instance_declares_no_provenance():
  # The default has to stay empty, or every run record grows a noise field.
  assert _instance("instance_someone__else-1234").run_provenance() == {}


def test_a_fixed_instance_names_the_fix_it_got():
  # Two runs of one instance are indistinguishable in the manifest otherwise,
  # while having graded different trees.
  provenance = _instance(_WYSIWYG_INSTANCE).run_provenance()
  assert provenance["env_fix"] == "_fix_instance_element_web_aec454dd"
  assert "known_flaky" not in provenance  # the fix removes it; it is not flaky


def test_an_instance_can_be_both_fixed_and_still_flaky():
  # The two claims are independent, and tutanota f373ac38 is the case that
  # proves it: its fix closes the clock window it used to fail in, and it stays
  # in the flaky registry for the suite-wide race that closes nothing.
  provenance = _instance(_TUTANOTA_CLOCK_INSTANCE).run_provenance()
  assert provenance["env_fix"] == "_fix_instance_tutanota_f373ac38"
  assert isinstance(provenance["known_flaky"], dict)


def test_a_known_flaky_instance_carries_its_measured_rate():
  provenance = _instance(flaky_instances()[0]).run_provenance()
  flaky = provenance["known_flaky"]
  assert isinstance(flaky, dict)
  assert flaky["failure_rate"] == 0.156
  assert flaky["sample_size"] == 64
  assert flaky["graded"] is True
  assert "env_fix" not in provenance  # nothing to fix; that is the point
  # It must survive the trip into a persisted record, which is JSON.
  _ = json.dumps(provenance)


# ─── the downstream extension seam ───────────────────────────────────────────


@pytest.fixture
def clean_registry() -> Iterator[None]:
  """Restore the registry, so a registration in one test cannot leak."""
  before = dict(_FIXES)
  yield
  _FIXES.clear()
  _FIXES.update(before)


def test_a_downstream_fix_registers_without_touching_this_module(
    clean_registry: None,
):
  del clean_registry

  def _fix_instance_acme_widget(
      spec: SweBenchProUnitTestSpec,
  ) -> SweBenchProUnitTestSpec:
    return with_setup(spec, setup='echo "downstream"', mounts={})

  register_fix("instance_acme__widget-1234", _fix_instance_acme_widget)
  spec = _spec("instance_acme__widget-1234")
  lines = spec.eval_script.splitlines()
  assert 'echo "downstream"' in lines
  # ...and it lands in the same window the built-in fixes get, for free
  assert (
      lines.index(_GOLDEN_CHECKOUT)
      < lines.index('echo "downstream"')
      < lines.index(_RUN_MARKER)
  )
  assert "instance_acme__widget-1234" in fixed_instances()


def test_a_downstream_fix_can_replace_a_built_in_one(clean_registry: None):
  del clean_registry
  register_fix(_WYSIWYG_INSTANCE, lambda spec: spec)
  # Deliberate: a consuming project may need different treatment of the same
  # instance. The run record still names what ran, whatever it is.
  assert _spec(_WYSIWYG_INSTANCE).mounts.get(_WYSIWYG_TARBALL_NAME) is None
  assert applied_fix_name(_WYSIWYG_INSTANCE) == "<lambda>"


def test_a_fix_that_is_not_a_plain_function_is_still_named(
    clean_registry: None,
):
  del clean_registry

  class _Callable:

    def __call__(
        self, spec: SweBenchProUnitTestSpec
    ) -> SweBenchProUnitTestSpec:
      return spec

  register_fix("instance_acme__widget-9", _Callable())
  # No __name__ on an instance — the record must still say *something*.
  assert applied_fix_name("instance_acme__widget-9") == "_Callable"


def test_with_setup_preserves_every_field_it_does_not_change():
  # Regression: `with_setup` used to rebuild the spec field by field, so a field
  # added later (`retries`) was silently dropped. This asserts the general rule
  # rather than that one field, so the next addition cannot repeat it.
  spec = replace(_plain(), retries=3, native_outputs={"log": "stdout.log"})
  fixed = with_setup(spec, setup="echo patched", mounts={})
  changed = {"eval_script", "mounts"}
  for field in dataclasses.fields(spec):
    if field.name in changed:
      continue
    assert getattr(fixed, field.name) == getattr(spec, field.name), field.name
  assert fixed.retries == 3  # named explicitly: this is the one that broke


def test_the_clock_shim_ships_as_a_resource_the_fix_can_read():
  # The preload is a data file inside the package rather than a Python string,
  # so a wheel that fails to carry it breaks nowhere until a run is already
  # inside a container. Reading it here is the packaging check.
  raw = clock_shim()
  assert b"globalThis.Date = new Proxy(RealDate" in raw
  mount = _spec(_TUTANOTA_CLOCK_INSTANCE).mounts[_SHIM_NAME]
  assert mount.resource == Inline(raw)


def test_the_clock_probe_expects_the_hour_the_shim_actually_pins():
  # The two halves name the pinned hour independently: the shim as a constant,
  # the setup's probe as a literal in a string comparison. If they drift, the
  # fix aborts *every* run of the instance in setup — so they are checked
  # against each other rather than trusted to stay in step.
  (hour,) = re.findall(
      r"^const TARGET_UTC_HOUR = (\d+)$", clock_shim().decode(), re.MULTILINE
  )
  script = _spec(_TUTANOTA_CLOCK_INSTANCE).eval_script
  assert f'if [ "$probe" != "{hour} 0 true" ]; then' in script


def test_a_fixed_instance_keeps_its_retry_override():
  # End to end: the override has to survive the fix, or a consumer setting it on
  # an instance that happens to have a fix would be silently ignored.
  compiled = replace(_plain(), retries=2)
  fixed = apply_instance_fix(_WYSIWYG_INSTANCE, compiled)
  assert fixed.retries == 2
  assert _WYSIWYG_TARBALL_NAME in fixed.mounts  # the fix did run
