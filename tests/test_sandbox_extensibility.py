"""Import-only extension of the sandbox: a new ``Resource`` kind + a sandbox.

Proves the ADR-0003 seam a consuming company relies on — adding a ``Resource``
subclass and teaching a ``Sandbox`` subclass to transfer it by overriding
``_mount_one`` and calling ``super()`` for the built-ins — needs **no** edit to
swe-lab (import only). A tiny foreshadowing of Task 15's full seam proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import override

from swe_lab.sandbox import Inline, Mount, Resource, SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox

_SPEC = SandboxSpec(
    instance_id="acme__widget-1",
    image_ref="img:tag",
    workdir="/repo",
    base_commit="abc123",
)


@dataclass(frozen=True, slots=True)
class Reversed(Resource):
  """A custom content source: the given bytes, reversed on transfer."""

  content: bytes


class ExtendedSandbox(FakeSandbox):
  """A ``FakeSandbox`` that also knows how to mount a :class:`Reversed`."""

  @override
  def _mount_one(self, target: str, mount: Mount) -> None:
    resource = mount.resource
    if isinstance(resource, Reversed):
      self._put_bytes(target, resource.content[::-1], mount)
      return
    super()._mount_one(target, mount)  # built-ins unchanged


def test_custom_resource_kind_mounts_via_override(tmp_path: Path) -> None:
  """A subclass handles its own ``Resource`` kind; built-ins still work."""
  sandbox = ExtendedSandbox(spec=_SPEC, workspace=tmp_path)
  sandbox.up()

  sandbox.mount(
      {
          "custom.txt": Mount(Reversed(b"abc")),
          "builtin.txt": Mount(Inline(b"kept")),
      }
  )

  assert sandbox.read("custom.txt") == b"cba"  # the override ran
  assert sandbox.read("builtin.txt") == b"kept"  # super() handled the built-in
