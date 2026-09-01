"""Tests for the dotted-path override grammar.

The engine is pure — arguments in, rebuilt entries out — so these exercise it
directly: what a path resolves against, what a value coerces to, what is
refused, and the property the whole mechanism rests on, which is that
overriding a run never edits the definition the registry holds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, final, override

from etils import epath
import pytest

from swe_lab.cli.overrides import (
    apply_overrides,
    Override,
    OverrideError,
    parse_overrides,
)
from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.unit_test import UnitTestTask
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.rollout import CodingAgentTask
from swe_lab.sandbox import (
    backend_of,
    DockerHostSandboxConfig,
    ExecResult,
    GhjobSandboxConfig,
    SandboxFs,
)
from swe_lab.workflow import Task, WorkflowEntry
import swe_lab.workflow.definitions as definitions


@final
@dataclass
class _Probe(Task):
  """A task with one field of every shape the coercion table claims."""

  text: str = "t"
  count: int = 1
  ratio: float = 0.5
  flag: bool = False
  where: epath.Path | None = None
  names: tuple[str, ...] = ()
  env: Mapping[str, str] = field(default_factory=dict)

  @override
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    del instance
    return sb.run_script("main.sh", timeout=timeout)


def _entry(**kwargs: Any) -> WorkflowEntry:
  return WorkflowEntry("probe", _Probe(**kwargs), timeout=10.0)


def _applied(*args: str, entry: WorkflowEntry | None = None) -> WorkflowEntry:
  """Parse and apply, returning the single rebuilt entry."""
  entries = apply_overrides(
      [entry if entry is not None else _entry()], parse_overrides(args)
  )
  return entries[0]


# ─── parsing ─────────────────────────────────────────────────────────────────


def test_parsing_takes_the_path_apart():
  (parsed,) = parse_overrides(["--rollout.harness.model=opus"])
  assert parsed == Override(
      entry="rollout",
      path=("harness", "model"),
      value="opus",
      spelling="--rollout.harness.model=opus",
  )


def test_a_value_may_contain_the_separator():
  # Only the first `=` splits: a path is never on the right of one.
  (parsed,) = parse_overrides(["--probe.env=A=1,B=2"])
  assert parsed.value == "A=1,B=2"


def test_anything_that_is_not_an_override_is_refused():
  # These arrive as *unknown* options, so a mistyped known flag lands here —
  # ignoring it would silently drop what the caller asked for.
  for arg, match in [
      ("--persits", "unrecognized argument"),
      ("nonsense", "unrecognized argument"),
      ("--rollout=x", "names an entry and a field path"),
      ("--.model=x", "names an entry and a field path"),
      ("--rollout..model=x", "empty path segment"),
  ]:
    with pytest.raises(OverrideError, match=match):
      _ = parse_overrides([arg])


def test_the_same_path_twice_is_refused():
  # Two values for one field is a mistake in either direction; last-one-wins
  # would hide it.
  with pytest.raises(OverrideError, match="overrides the same field"):
    _ = parse_overrides(["--probe.count=1", "--probe.count=2"])


# ─── resolution ──────────────────────────────────────────────────────────────


def test_an_entrys_own_fields_resolve():
  entry = _applied("--probe.timeout=60", "--probe.retries=2")
  assert (entry.timeout, entry.retries) == (60.0, 2)


def _probe(entry: WorkflowEntry) -> _Probe:
  """Return the entry's task, narrowed."""
  task = entry.task
  assert isinstance(task, _Probe)
  return task


def _agent(entry: WorkflowEntry) -> ClaudeCodeHarness:
  """Return the coding task's harness, narrowed."""
  task = entry.task
  assert isinstance(task, CodingAgentTask)
  harness = task.harness
  assert isinstance(harness, ClaudeCodeHarness)
  return harness


def test_a_task_field_resolves_without_saying_task():
  # The overwhelmingly common target is a task field; `.task.` in front of
  # every one of them would be ceremony.
  assert _probe(_applied("--probe.text=hello")).text == "hello"


def test_the_task_can_always_be_named_explicitly():
  assert _probe(_applied("--probe.task.text=hello")).text == "hello"


def test_an_entry_field_wins_where_the_names_collide():
  # `env` exists on both the entry's sandbox config and this task; the shadowed
  # one is reachable by its full path, which is the rule the help text states.
  entry = _entry()
  assert _applied("--probe.sandbox.env=A=1", entry=entry).sandbox.env == {
      "A": "1"
  }
  assert _probe(_applied("--probe.env=B=2", entry=entry)).env == {"B": "2"}


def test_a_nested_config_resolves():
  entry = _applied("--probe.sandbox.network=false")
  assert entry.sandbox.network is False


def test_an_unknown_field_names_both_namespaces():
  with pytest.raises(OverrideError, match="not a field of _Probe"):
    _ = _applied("--probe.nope=1")


def test_an_unknown_entry_names_the_ones_there_are():
  with pytest.raises(OverrideError, match="no entry 'lint'"):
    _ = _applied("--lint.timeout=1")


def test_walking_into_a_non_dataclass_is_refused():
  with pytest.raises(OverrideError, match="no fields to walk into"):
    _ = _applied("--probe.text.upper=1")


# ─── coercion ────────────────────────────────────────────────────────────────


def test_every_shape_in_the_table_coerces():
  entry = _applied(
      "--probe.text=hi",
      "--probe.count=7",
      "--probe.ratio=1.5",
      "--probe.flag=true",
      "--probe.where=/tmp/x",
      "--probe.names=a,b",
      "--probe.env=A=1,B=2",
  )
  task = _probe(entry)
  assert (task.text, task.count, task.ratio, task.flag) == ("hi", 7, 1.5, True)
  assert task.where == epath.Path("/tmp/x")
  assert task.names == ("a", "b")
  assert task.env == {"A": "1", "B": "2"}


def test_an_optional_field_takes_none():
  assert _probe(_applied("--probe.where=none")).where is None


def test_booleans_take_the_words_and_the_digits():
  for raw, expected in [("true", True), ("1", True), ("no", False)]:
    assert _probe(_applied(f"--probe.flag={raw}")).flag is expected
  with pytest.raises(OverrideError, match="expected true or false"):
    _ = _applied("--probe.flag=maybe")


def test_an_enum_takes_its_value_and_lists_them_when_it_does_not():
  entry = WorkflowEntry(
      "rollout", CodingAgentTask(harness=ClaudeCodeHarness()), timeout=10.0
  )
  rebuilt = _applied("--rollout.harness.capture=proxy", entry=entry)
  assert _agent(rebuilt).capture == "proxy"
  with pytest.raises(OverrideError, match="expected one of"):
    _ = _applied("--rollout.harness.capture=telepathy", entry=entry)


def test_numbers_are_checked_for_being_numbers_and_finite():
  for arg, match in [
      ("--probe.count=many", "is not int"),
      ("--probe.ratio=nan", "not a finite number"),
      ("--probe.ratio=inf", "not a finite number"),
  ]:
    with pytest.raises(OverrideError, match=match):
      _ = _applied(arg)


def test_a_nonsense_budget_is_refused_by_the_entry_itself():
  # Not the override layer's rule: an entry built by hand is refused the same
  # way, and the override inherits it.
  for arg in ["--probe.timeout=0", "--probe.retries=-1"]:
    with pytest.raises(Exception, match="entry 'probe'"):
      _ = _applied(arg)


def test_a_mapping_wants_pairs():
  with pytest.raises(OverrideError, match="is not k=v"):
    _ = _applied("--probe.env=A")


# ─── identity, and the registry form ─────────────────────────────────────────


def test_an_entrys_identity_cannot_be_overridden():
  # `key` is the store segment its records live under, what resume matches,
  # and what later entries' bindings name.
  with pytest.raises(OverrideError, match="identity"):
    _ = _applied("--probe.key=other")
  with pytest.raises(OverrideError, match="not a value an argument can spell"):
    _ = _applied("--probe.task=something")


def test_a_bare_name_swaps_the_harness_through_the_registry():
  entry = WorkflowEntry(
      "rollout",
      CodingAgentTask(harness=ClaudeCodeHarness(model="opus")),
      timeout=10.0,
  )
  rebuilt = _applied("--rollout.harness=claude_code", entry=entry)
  # the registry builds a DEFAULT-configured agent; the name is not a field
  assert _agent(rebuilt).model != "opus"


def test_an_unknown_harness_name_lists_the_registered_ones():
  entry = WorkflowEntry(
      "rollout", CodingAgentTask(harness=ClaudeCodeHarness()), timeout=10.0
  )
  with pytest.raises(OverrideError, match="unknown harness"):
    _ = _applied("--rollout.harness=telepath", entry=entry)


def test_a_swap_lands_before_a_field_set_on_what_it_replaced():
  # The shortest-path-first rule, which is why the swap needs no rule of its
  # own: setting a field on the harness being replaced would be lost.
  entry = WorkflowEntry(
      "rollout", CodingAgentTask(harness=ClaudeCodeHarness()), timeout=10.0
  )
  rebuilt = _applied(
      "--rollout.harness.model=haiku",
      "--rollout.harness=claude_code",
      entry=entry,
  )
  assert _agent(rebuilt).model == "haiku"


# ─── the property everything rests on ────────────────────────────────────────


def test_overriding_a_run_never_edits_the_definition():
  # A registry hands out the same objects to every run. If an override edited
  # them in place, the next instance would inherit it — silently.
  before = definitions.ROLLOUT_AND_UNIT_TEST
  rebuilt = apply_overrides(
      before,
      parse_overrides(
          ["--rollout.harness.model=opus", "--unit_test.retries=5"]
      ),
  )
  assert _agent(rebuilt[0]).model == "opus"
  assert rebuilt[1].retries == 5
  # …and the definition is untouched, object for object
  assert _agent(before[0]).model != "opus"
  assert before[1].retries == 2
  assert definitions.ROLLOUT_AND_UNIT_TEST[0] is before[0]


def test_entries_keep_their_declared_order():
  rebuilt = apply_overrides(
      definitions.ROLLOUT_AND_UNIT_TEST,
      parse_overrides(["--unit_test.retries=3", "--rollout.timeout=60"]),
  )
  assert [entry.key for entry in rebuilt] == ["rollout", "unit_test"]
  assert isinstance(rebuilt[1].task, UnitTestTask)


def test_sandbox_swaps_whole_by_backend_name_then_takes_field_edits():
  # `sandbox` behaves exactly like `harness`: a bare name swaps the whole
  # object for that backend's config, and a longer path edits a field of
  # whatever is then there. Replacement lands first (shortest path wins), so
  # the two compose in either order on the command line.
  entries = apply_overrides(
      definitions.ROLLOUT_AND_UNIT_TEST,
      parse_overrides(
          [
              "--rollout.sandbox.pass_env=TOKEN=HOST_TOKEN",
              "--rollout.sandbox=ghjob",
          ]
      ),
  )
  rollout, unit_test = entries
  assert isinstance(rollout.sandbox, GhjobSandboxConfig)
  assert rollout.sandbox.pass_env == {"TOKEN": "HOST_TOKEN"}
  # …and the other entry is untouched: a workflow can straddle two backends.
  assert isinstance(unit_test.sandbox, DockerHostSandboxConfig)
  assert backend_of(rollout.sandbox) == "ghjob"
  assert backend_of(unit_test.sandbox) == "host"


def test_an_unknown_backend_name_is_refused():
  with pytest.raises(OverrideError, match="unknown backend"):
    _ = apply_overrides(
        definitions.ROLLOUT, parse_overrides(["--rollout.sandbox=nope"])
    )


def test_a_literal_alias_is_overridable_and_validates_its_members():
  # The engine grew a Literal branch so a closed value set need not be an enum.
  # Validating HERE is the point: a Literal is a static type, so the CLI
  # boundary is the only place a sweep config's text is ever checked.
  from typing import Literal

  from swe_lab.cli.overrides import _coerce, Override

  plain = Literal["stream", "proxy"]
  assert (
      _coerce(plain, Override("r", ("x",), "proxy", "--r.x=proxy")) == "proxy"
  )
  with pytest.raises(OverrideError, match="expected one of stream, proxy"):
    _ = _coerce(plain, Override("r", ("x",), "ftp", "--r.x=ftp"))


def test_a_pep695_type_alias_is_unwrapped_before_dispatch():
  # `type X = Literal[...]` arrives as a TypeAliasType with no origin of its
  # own; without unwrapping it would read as "not a type this can build from
  # text" and the field would be silently unoverridable — which is exactly how
  # Capture and Effort are declared.
  from swe_lab.cli.overrides import _coerce, Override
  from swe_lab.harnesses.claude_code import Effort

  assert (
      _coerce(Effort, Override("r", ("x",), "xhigh", "--r.x=xhigh")) == "xhigh"
  )
  with pytest.raises(OverrideError, match="low, medium, high, xhigh, max"):
    _ = _coerce(Effort, Override("r", ("x",), "ultra", "--r.x=ultra"))
