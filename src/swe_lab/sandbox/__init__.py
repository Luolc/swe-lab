"""The sandboxed-task engine: one manager, five hooks, pluggable sandboxes.

The harness-/dataset-/eval-method-agnostic core of the SandboxRun design
(``docs/horizontal/spec.md``): a ``SandboxManager`` owns a sandbox's lifecycle
and drives composed ``SandboxObserver``s around one main action; *solving*
(rollout) and *grading* (eval) are two compositions of this one engine. Test
doubles live in :mod:`swe_lab.sandbox.testing`.
"""

from .backends import (
    build_sandbox,
    DockerHostSandbox,
    GitHubJobSandbox,
    register_sandbox,
    registered_backends,
    SandboxConfig,
)
from .errors import SandboxError
from .manager import SandboxManager
from .mounts import merge_mounts, Mount, Mounts
from .observer import CompositeObserver, SandboxObserver
from .persist import index, persist, promote, RunRecord, RUNS_NAMESPACE
from .resources import Inline, LocalFile, Resource
from .result import Contribution, RunResult, RunStatus
from .sandbox import ExecResult, Sandbox, SandboxFs, WORKSPACE_ENV
from .spec import SandboxSpec
from .store import (
    build_store,
    FilesystemStore,
    register_store,
    registered_stores,
    Store,
)

__all__ = [
    "CompositeObserver",
    "Contribution",
    "DockerHostSandbox",
    "ExecResult",
    "FilesystemStore",
    "GitHubJobSandbox",
    "Inline",
    "LocalFile",
    "Mount",
    "Mounts",
    "Resource",
    "RUNS_NAMESPACE",
    "RunRecord",
    "RunResult",
    "RunStatus",
    "Sandbox",
    "SandboxConfig",
    "SandboxError",
    "SandboxFs",
    "SandboxManager",
    "SandboxObserver",
    "SandboxSpec",
    "Store",
    "WORKSPACE_ENV",
    "build_sandbox",
    "build_store",
    "index",
    "merge_mounts",
    "persist",
    "promote",
    "register_sandbox",
    "register_store",
    "registered_backends",
    "registered_stores",
]
