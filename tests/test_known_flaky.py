"""Known-flaky registry: what it records, and that a run carries it.

The registry only annotates — it must never change a verdict. What has to hold:
every entry is a real measurement, and the annotation actually reaches the
result a human reads.
"""

from swe_lab.datasets.swebench_pro.known_flaky import _NODEBB_ORPHANS as _NODEBB
from swe_lab.datasets.swebench_pro.known_flaky import (
    flaky_instances,
    known_flaky,
    KnownFlaky,
)


def test_an_instance_with_no_measured_flakiness_has_no_entry():
  assert known_flaky("instance_someone__else-1234") is None


def test_every_entry_is_a_measurement_not_a_guess():
  # A rate with no sample size behind it is an anecdote, and one with no
  # environment does not transfer — these races are load-sensitive by nature.
  assert flaky_instances()  # the registry is not silently empty
  for instance_id in flaky_instances():
    entry = known_flaky(instance_id)
    assert isinstance(entry, KnownFlaky)
    assert instance_id.startswith("instance_")
    assert 0.0 < entry.failure_rate < 1.0
    assert entry.sample_size > 0
    assert entry.measured_on
    assert entry.flaky_tests
    assert entry.reason
    assert entry.evidence  # a diagnosis nobody can check is a rumour


def test_the_nodebb_orphans_entry_records_why_no_fix_is_possible():
  entry = known_flaky(_NODEBB)
  assert entry is not None
  # `graded` is the whole reason this instance lives here and not in fixes.py:
  # the racy test is one of its fail_to_pass, so it *is* the task.
  assert entry.graded is True
  assert entry.failure_rate == 0.156
  assert entry.sample_size == 64
  assert "cleanOrphans" in entry.flaky_tests[0]


def test_the_tutanota_entries_record_a_deferred_fix_not_an_impossible_one():
  # These are here for a different reason than NodeBB: the flaky test is not
  # graded (it is in neither f2p nor p2p), a fix does exist, and the entry has
  # to say so — otherwise a future reader re-derives the whole investigation.
  tut = [i for i in flaky_instances() if "tutanota" in i]
  assert len(tut) == 20
  entry = known_flaky(tut[0])
  assert entry is not None
  assert entry.graded is False
  assert "parser" in entry.reason
  assert all(known_flaky(i) is entry for i in tut)  # one shared measurement


def test_the_registry_covers_the_sweep_without_duplicating_measurements():
  # Entries are shared where the mechanism is shared (tutanota, the two wysiwyg
  # emoji instances), so a rate is written down once per mechanism, not once
  # per instance — otherwise 20 copies drift apart the first time one is edited.
  entries = {id(known_flaky(i)) for i in flaky_instances()}
  assert len(flaky_instances()) == 30
  assert len(entries) == 10  # distinct measurements behind those 29 instances
