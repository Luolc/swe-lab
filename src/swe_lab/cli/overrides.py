"""Adjust a statically-registered workflow per invocation, by field path.

A definition is written once and registered; an invocation changes what it
needs to change. Rather than a flag per knob — which cannot reach a downstream
user's own task or backend config at all — one grammar reaches every level,
because every level is dataclass-shaped configuration::

    --rollout.harness.model=opus      # a field on the agent
    --rollout.sandbox.network=false   # a field on the entry's declared config
    --unit_test.retries=2             # a field on the entry itself

The walk is mechanical: resolve the path against ``dataclasses.fields``, coerce
the leaf by its **annotated** type, and rebuild with nested ``replace()``.
Nothing is ever assigned through a reference, so the definition a registry
holds is never edited by a run that overrides it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
import functools
import math
import types
import typing
from typing import Any, get_args, get_origin

from etils import epath

from swe_lab.harnesses import build_harness, Harness
from swe_lab.sandbox import build_sandbox_config, SandboxConfig
from swe_lab.workflow import WorkflowEntry

# Entry fields an override may not *set*, and why — each message is the whole
# explanation a caller gets.
_FIXED_FIELDS: Mapping[str, str] = {
    "key": (
        "an entry's key is its identity — the store segment its records live"
        " under, what resume matches, and what later entries' bindings name."
        " Changing it mid-resolution would re-home a run's records and make"
        " every other override order-dependent"
    ),
    "task": (
        "a task is not a value an argument can spell; override its fields"
        " instead (or swap its harness by name)"
    ),
}

# Field types resolved from a registry when the value is a bare name rather
# than a path into the object: `--rollout.harness=codex`,
# `--rollout.sandbox=ghjob`. Both swap the whole object for that name's
# default; `--rollout.sandbox.network=false` then walks into it.
_REGISTRIES: Mapping[type, typing.Callable[[str], object]] = {
    Harness: build_harness,
    SandboxConfig: build_sandbox_config,
}


class OverrideError(Exception):
  """An override that cannot be applied — refused before anything runs."""


@dataclass(frozen=True, slots=True)
class Override:
  """One ``--<entry>.<field-path>=<value>`` from the command line.

  Attributes:
    entry: The workflow entry key the path starts at.
    path: The field path within that entry, outermost first.
    value: The raw text to coerce, once the target field's type is known.
    spelling: The argument as typed, so every error can quote it back.
  """

  entry: str
  path: tuple[str, ...]
  value: str
  spelling: str


def parse_overrides(args: Sequence[str]) -> list[Override]:
  """Parse the leftover command-line arguments into overrides.

  Anything that is not ``--<entry>.<field-path>=<value>`` is refused rather
  than ignored: these arrive as *unknown* options (the command declares none of
  them), so a mistyped known flag would otherwise pass silently.

  Args:
    args: The arguments the command did not recognize, in order.

  Returns:
    The parsed overrides, in the order given.

  Raises:
    OverrideError: On anything unparseable, or a repeated path.
  """
  parsed: list[Override] = []
  seen: dict[tuple[str, tuple[str, ...]], str] = {}
  for arg in args:
    if not arg.startswith("--") or "=" not in arg:
      raise OverrideError(
          f"unrecognized argument {arg!r}; overrides are spelled"
          " --<entry>.<field-path>=<value>"
      )
    target, _, value = arg[2:].partition("=")
    entry, _, rest = target.partition(".")
    if not entry or not rest:
      raise OverrideError(
          f"{arg!r}: an override names an entry and a field path, as in"
          " --rollout.harness.model=opus"
      )
    path = tuple(rest.split("."))
    if any(not segment for segment in path):
      raise OverrideError(f"{arg!r}: empty path segment")
    if (entry, path) in seen:
      # Two values for one field is a mistake in either direction; keeping the
      # last would hide it, like every other duplicate in this codebase.
      raise OverrideError(
          f"{arg!r} overrides the same field as {seen[(entry, path)]!r}"
      )
    seen[(entry, path)] = arg
    parsed.append(Override(entry=entry, path=path, value=value, spelling=arg))
  return parsed


def apply_overrides(
    entries: Sequence[WorkflowEntry], overrides: Sequence[Override]
) -> tuple[WorkflowEntry, ...]:
  """Return the entries with every override applied.

  Order is not the caller's problem: the whole set is checked first, then
  applied **shortest path first**. That single rule also settles the one case
  where two overrides could interact — replacing an object and setting a field
  on it (``--rollout.harness=codex --rollout.harness.model=o3``) — since the
  replacement is the shorter path and therefore lands first.

  Args:
    entries: The definition's entries, in declared order.
    overrides: The parsed overrides, in any order.

  Returns:
    The rebuilt entries, in declared order.

  Raises:
    OverrideError: If an override names an unknown entry, an unknown field, a
      field that may not be overridden, or a value its field cannot take.
  """
  by_key = {entry.key: entry for entry in entries}
  for override in overrides:
    if override.entry not in by_key:
      raise OverrideError(
          f"{override.spelling}: no entry {override.entry!r} in this workflow"
          f" (entries: {', '.join(by_key)})"
      )
  for override in sorted(overrides, key=lambda o: len(o.path)):
    by_key[override.entry] = _override_entry(by_key[override.entry], override)
  return tuple(by_key[entry.key] for entry in entries)


def _override_entry(entry: WorkflowEntry, override: Override) -> WorkflowEntry:
  """Apply one override to one entry, entry fields first, then its task.

  The fall-through is what makes the common case short: the overwhelmingly
  common target is a *task* field, and spelling ``.task.`` in front of every
  one of them is ceremony. An entry field wins where the names collide, and
  ``--<entry>.task.<field>`` always reaches the task unambiguously.

  Args:
    entry: The entry to rebuild.
    override: The override to apply.

  Returns:
    The rebuilt entry.

  Raises:
    OverrideError: If neither the entry nor its task has the named field.
  """
  head = override.path[0]
  if len(override.path) == 1 and head in _FIXED_FIELDS:
    # Walking *through* `task` is how a shadowed field is reached
    # (`--rollout.task.env=…`); what is refused is *setting* either of these.
    raise OverrideError(f"{override.spelling}: {_FIXED_FIELDS[head]}")
  if any(field.name == head for field in fields(entry)):
    return _rebuilt(entry, override.path, override)
  if any(field.name == head for field in fields(entry.task)):
    return replace(entry, task=_rebuilt(entry.task, override.path, override))
  raise OverrideError(
      f"{override.spelling}: {head!r} is not a field of"
      f" {type(entry.task).__name__} ({_field_names(entry.task)}) or of"
      f" {type(entry).__name__} ({_field_names(entry)})"
  )


def _rebuilt(obj: Any, path: tuple[str, ...], override: Override) -> Any:
  """Return ``obj`` with the field at ``path`` replaced by the coerced value.

  Args:
    obj: The dataclass instance to rebuild.
    path: The remaining field path, outermost first.
    override: The override being applied (for coercion and errors).

  Returns:
    A copy of ``obj`` with the leaf replaced.

  Raises:
    OverrideError: On an unknown field, a walk through a non-dataclass, or a
      value the leaf's type cannot take.
  """
  head, rest = path[0], path[1:]
  by_name = {field.name: field for field in fields(obj)}
  if head not in by_name:
    raise OverrideError(
        f"{override.spelling}: {head!r} is not a field of"
        f" {type(obj).__name__} ({_field_names(obj)})"
    )
  annotation = _hints(type(obj)).get(head)
  if annotation is None:
    raise OverrideError(
        f"{override.spelling}: {type(obj).__name__}.{head} has an annotation"
        " that cannot be resolved, so it is not overridable"
    )
  if not rest:
    return replace(obj, **{head: _coerce(annotation, override)})
  current = getattr(obj, head)
  if not is_dataclass(current):
    raise OverrideError(
        f"{override.spelling}: {type(obj).__name__}.{head} is a"
        f" {type(current).__name__}, which has no fields to walk into"
    )
  return replace(obj, **{head: _rebuilt(current, rest, override)})


@functools.cache
def _hints(cls: type) -> Mapping[str, Any]:
  """Return a class's *resolved* annotations.

  Every module here uses postponed annotations, so ``Field.type`` is a string;
  ``get_type_hints`` is what turns it back into the type coercion needs. A
  class whose annotations cannot be resolved at all yields nothing, and its
  fields report as not overridable rather than being coerced by guesswork.

  Args:
    cls: The dataclass to resolve.

  Returns:
    Field name → resolved type, empty when resolution failed.
  """
  try:
    return typing.get_type_hints(cls)
  except Exception:  # noqa: BLE001 — any resolution failure is "not overridable"
    return {}


def _field_names(obj: Any) -> str:
  """Return an object's overridable field names, for an error message."""
  return ", ".join(
      field.name for field in fields(obj) if field.name not in _FIXED_FIELDS
  )


def _coerce(annotation: Any, override: Override) -> Any:
  """Turn the raw text into a value of the field's annotated type.

  The table is deliberately small — the primitives, paths, enums, and the two
  container shapes this codebase's config actually uses. An unrepresentable
  type is not overridable, and says so; it grows when a real field needs it.

  Args:
    annotation: The field's resolved type.
    override: The override being applied (its value, and its spelling).

  Returns:
    The coerced value.

  Raises:
    OverrideError: If the type is not representable on a command line, or the
      text is not a value of it.
  """
  raw = override.value
  origin, args = get_origin(annotation), get_args(annotation)
  if origin is types.UnionType:
    inner = [arg for arg in args if arg is not type(None)]
    if raw.lower() == "none" and len(inner) < len(args):
      return None
    if len(inner) == 1:
      return _coerce(inner[0], override)
    raise OverrideError(
        f"{override.spelling}: {annotation} has several concrete types, so it"
        " is not overridable"
    )
  if origin in (tuple, list, Sequence):
    return tuple(part for part in raw.split(",") if part)
  if origin in (dict, Mapping):
    return dict(_pair(part, override) for part in raw.split(",") if part)
  if isinstance(annotation, type):
    return _coerce_scalar(annotation, override)
  raise OverrideError(
      f"{override.spelling}: {annotation} is not a type this can build from"
      " text"
  )


def _coerce_scalar(annotation: type, override: Override) -> Any:
  """Coerce to a concrete class: the primitives, paths, enums, a registry.

  Args:
    annotation: The field's resolved class.
    override: The override being applied.

  Returns:
    The coerced value.

  Raises:
    OverrideError: If the class is not representable, or the text is not one
      of its values.
  """
  raw = override.value
  for base, build in _REGISTRIES.items():
    if issubclass(annotation, base):
      # A bare name, not a path: the value names a registered implementation.
      try:
        return build(raw)
      except Exception as error:  # noqa: BLE001 — the registry's own message
        raise OverrideError(f"{override.spelling}: {error}") from error
  if issubclass(annotation, Enum):
    try:
      return annotation(raw)
    except ValueError:
      values = ", ".join(str(member.value) for member in annotation)
      raise OverrideError(
          f"{override.spelling}: expected one of {values}"
      ) from None
  return _coerce_plain(annotation, override)


def _coerce_plain(annotation: type, override: Override) -> Any:
  """Coerce to a primitive or a path.

  Args:
    annotation: The field's resolved class.
    override: The override being applied.

  Returns:
    The coerced value.

  Raises:
    OverrideError: If the class is not representable as text, or the text is
      not one of its values.
  """
  raw = override.value
  if issubclass(annotation, bool):
    if raw.lower() in ("true", "1", "yes"):
      return True
    if raw.lower() in ("false", "0", "no"):
      return False
    raise OverrideError(f"{override.spelling}: expected true or false")
  if issubclass(annotation, str):
    return raw
  if issubclass(annotation, (int, float)):
    try:
      value = annotation(raw)
    except ValueError:
      raise OverrideError(
          f"{override.spelling}: {raw!r} is not {annotation.__name__}"
      ) from None
    if isinstance(value, float) and not math.isfinite(value):
      raise OverrideError(
          f"{override.spelling}: {raw!r} is not a finite number"
      )
    return value
  if issubclass(annotation, epath.Path) or annotation is epath.Path:
    return epath.Path(raw)
  raise OverrideError(
      f"{override.spelling}: {annotation.__name__} is not a type this can"
      " build from text"
  )


def _pair(part: str, override: Override) -> tuple[str, str]:
  """Split one ``k=v`` of a mapping value.

  Args:
    part: One comma-separated piece.
    override: The override being applied, for the error.

  Returns:
    The key and value.

  Raises:
    OverrideError: If the piece is not ``k=v``.
  """
  key, sep, value = part.partition("=")
  if not sep or not key:
    raise OverrideError(
        f"{override.spelling}: {part!r} is not k=v (a mapping is spelled"
        " k=v,k=v)"
    )
  return key, value
