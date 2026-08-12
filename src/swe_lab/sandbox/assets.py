"""Declared agent assets, and the two ways a backend can materialize one.

The seam task-28 §7 asked for. Before it, every backend imported every
harness's ``ensure_*_binary`` **by name**, so a backend knew which agents
exist: three observers in the Docker backend, one in the GH-job backend, and
a growing hole where the missing combinations should be. Adding an agent meant
editing every backend, and a downstream backend could provision nothing swe-lab
had not heard of.

The shape here inverts that: **a harness declares the assets it needs, and a
backend knows only how to materialize an arbitrary one.** Neither side
enumerates the other, which is the open-registry argument of ADR-0003 §6.5
applied to provisioning.

There are exactly two ways to materialize an asset, and they are the two the
backends were already using by hand:

- **Mount it** (:class:`MountedAssetsObserver`) — the asset is fetched into a
  host cache and handed to the sandbox as a read-only file. What a container
  needs, because a container has no other way to get bytes.
- **Install it** (:class:`InstalledAssetsObserver`) — the asset is fetched
  *directly to its final path*, because the sandbox's filesystem already **is**
  the one doing the fetching (a CI job). No bytes travel; a mount could not
  express that.

A backend answers which one it is by overriding ``Sandbox.asset_observer``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import override, TYPE_CHECKING

from etils import epath

from .mounts import Mount, Mounts
from .observer import SandboxObserver
from .resources import LocalFile

if TYPE_CHECKING:
  from .sandbox import SandboxFs

type Materializer = Callable[[epath.Path | None], epath.Path]
"""Puts one asset's bytes somewhere and says where they landed.

The one contract every ``ensure_*`` function in the harness packages already
satisfies, which is why this seam needed no new fetching code:

- called with ``None`` — put it in the **host cache** and return that path
  (what a backend handing a container a copy wants);
- called with a path — put it **exactly there** and return it (what a backend
  whose filesystem is the sandbox wants).

Idempotent by contract: a run that already has the pinned bytes re-verifies
rather than re-downloads, so composing an asset twice costs nothing.
"""


@dataclass(frozen=True)
class AgentAsset:
  """One file an agent needs at a fixed absolute path inside the sandbox.

  A **declaration**, not a transfer: it names what must exist and how to get
  the bytes, and leaves *how they travel* to whichever backend is running
  (ADR-0003 §3 — the receiver decides the transfer).

  Attributes:
    path: The absolute in-sandbox path the file must exist at. Absolute
      because an asset is machinery, not the run's material — it does not
      live in the workspace.
    materialize: Puts the bytes somewhere and returns where; see
      :data:`Materializer`.
    executable: Whether it is placed with the execute bit. True by default —
      an agent asset is nearly always a binary.
    read_only: Whether the run must not modify it. True by default: an asset
      the agent could rewrite is one the *next* attempt cannot trust.
  """

  path: str
  materialize: Materializer = field(repr=False)
  executable: bool = True
  read_only: bool = True


@dataclass(frozen=True)
class MountedAssetsObserver(SandboxObserver):
  """Fetch assets into the host cache and hand them over as mounts.

  The container answer. A fresh container starts with nothing installed and
  has no way to fetch anything itself, so the bytes are materialized on the
  host — once, cached, checksum-verified by the harness's own fetcher — and
  the mount machinery places them.

  Attributes:
    assets: What to place; empty is legal and contributes nothing, which is
      what a task that runs no agent (a grading run, an audit) declares.
  """

  assets: Sequence[AgentAsset] = ()

  @override
  def mounts(self) -> Mounts:
    """Stage each asset from its host-cached copy.

    Returns:
      Target path → mount, one per asset.
    """
    return {
        asset.path: Mount(
            LocalFile(asset.materialize(None)),
            executable=asset.executable,
            read_only=asset.read_only,
        )
        for asset in self.assets
    }


@dataclass(frozen=True)
class InstalledAssetsObserver(SandboxObserver):
  """Fetch assets straight to their final path, in the sandbox's own filesystem.

  The CI-job answer, and the case a mount cannot express: a mount hands bytes
  over, and the whole point here is that **no bytes should travel** — the job
  has the network and its filesystem already is the one the sandbox reads and
  execs in.

  Runs in ``after_create``, before anything can exec against the asset. The
  fetch is in-process rather than a shelled-out ``curl``: a job runs inside an
  arbitrary instance image and plenty of them ship no ``curl``, while the
  interpreter running this is guaranteed to be there.

  Attributes:
    assets: What to place; empty contributes nothing.
  """

  assets: Sequence[AgentAsset] = ()

  @override
  def after_create(self, sb: SandboxFs) -> None:
    """Install every declared asset at its final path.

    Args:
      sb: Unused — this writes to the job's own filesystem, which *is* the
        filesystem the sandbox reads and execs in.
    """
    del sb
    for asset in self.assets:
      _ = asset.materialize(epath.Path(asset.path))
