"""Declared agent assets: what an agent needs, and where — never how.

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

**Resolution is open-ended, and this module does not enumerate it.** An asset
says *what it is* and *where it must end up*; how a given sandbox satisfies
that is the sandbox's business, and the strategies differ in **kind**, not
merely in mechanism:

- **Transfer at run time.** A container is handed a host copy
  (:class:`MountedAssetsObserver`); a CI job whose filesystem *is* the sandbox
  fetches straight to the final path (:class:`InstalledAssetsObserver`). Both
  need bytes, so both use :attr:`AgentAsset.fetch`.
- **Resolve at configuration time.** A sandbox backed by its own maintained
  artifact store does not fetch anything: it looks the asset up **by
  identity** and names the resulting store path in the sandbox's own
  parameters — and it must do so **before the sandbox exists**, because that
  declaration *is* part of how the sandbox gets built. Such a backend never
  calls ``fetch``; the release and the destination path are all it consumes,
  and ``Sandbox.asset_observer`` returning ``None`` is the correct run-time
  answer, because the work already happened.
- Anything else a sandbox can do. The list above is what exists *today*, not a
  closed set. An earlier version of this module asserted there were "exactly
  two ways" and made ``fetch`` mandatory, which was wrong and broke precisely
  the configuration-time case.

That is why ``fetch`` is **optional**, and why an asset is small: it names the
release and the destination and stops there. It does **not** name a platform —
a sandbox knows what it runs on and a harness does not, so choosing the build
(and whether to bundle it, and how it travels) belongs to the sandbox.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import override, TYPE_CHECKING

from etils import epath

from .errors import SandboxError
from .mounts import Mount, Mounts
from .observer import SandboxObserver
from .resources import LocalFile

if TYPE_CHECKING:
  from .sandbox import SandboxFs

type Materializer = Callable[[epath.Path | None], epath.Path]
"""Obtains one asset's bytes and says where they landed.

Used only by the strategies that *transfer* (see the module docstring); a
store-resolving backend never calls one. The contract is the one every
``ensure_*`` function in the harness packages already satisfied:

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
    path: The absolute in-sandbox path the file must exist at — the one thing
      the harness genuinely fixes, because its invocation script execs exactly
      this path. Absolute because an asset is machinery, not the run's
      material: it does not live in the workspace.
    version: The pinned release, so a sandbox that keeps its own copies knows
      *which* one is wanted.
    fetch: How to obtain the bytes, for a backend that must obtain them.
      **Optional**: a sandbox that resolves the asset out of its own
      maintained store never needs one, and requiring it would force every
      harness to hand such a backend a downloader it must not call.
    executable: Whether it is placed with the execute bit. True by default —
      an agent asset is nearly always a binary.
    read_only: Whether the run must not modify it. True by default: an asset
      the agent could rewrite is one the *next* attempt cannot trust.
  """

  path: str
  version: str
  fetch: Materializer | None = field(default=None, repr=False)
  executable: bool = True
  read_only: bool = True

  def require_fetch(self) -> Materializer:
    """Return :attr:`fetch`, or explain why this backend cannot use the asset.

    Called by the strategies that transfer. The failure deserves a real
    message: it means an asset meant for a store-resolving sandbox is being
    run on a fetching one.

    Returns:
      The materializer.

    Raises:
      SandboxError: If the asset carries no way to obtain bytes.
    """
    if self.fetch is None:
      raise SandboxError(
          f"asset {self.path!r} (version {self.version}) declares no fetch,"
          " so this backend cannot transfer it; either the harness should"
          " supply one, or this sandbox should resolve it from its own store"
      )
    return self.fetch


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
            LocalFile(asset.require_fetch()(None)),
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
      _ = asset.require_fetch()(epath.Path(asset.path))
