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
from .resources import Inline, LocalFile, Resource
from .result import Contribution, RunResult, RunStatus
from .sandbox import ExecResult, Sandbox, SandboxFs, WORKSPACE_ENV
from .spec import SandboxSpec

__all__ = [
    "CompositeObserver",
    "Contribution",
    "DockerHostSandbox",
    "ExecResult",
    "GitHubJobSandbox",
    "Inline",
    "LocalFile",
    "Mount",
    "Mounts",
    "Resource",
    "RunResult",
    "RunStatus",
    "Sandbox",
    "SandboxConfig",
    "SandboxError",
    "SandboxFs",
    "SandboxManager",
    "SandboxObserver",
    "SandboxSpec",
    "WORKSPACE_ENV",
    "build_sandbox",
    "merge_mounts",
    "register_sandbox",
    "registered_backends",
]
