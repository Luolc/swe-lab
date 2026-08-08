"""The sandboxed-task engine: one manager, five hooks, pluggable sandboxes.

The harness-/dataset-/eval-method-agnostic core of the SandboxRun design
(``docs/horizontal/spec.md``): a ``SandboxManager`` owns a sandbox's lifecycle
and drives composed ``SandboxObserver``s around one main action; *solving*
(rollout) and *grading* (eval) are two compositions of this one engine. Test
doubles live in :mod:`swe_lab.sandbox.testing`.
"""

from .backends import (
    backend_of,
    build_sandbox,
    build_sandbox_config,
    DockerHostSandbox,
    DockerHostSandboxConfig,
    GhjobSandboxConfig,
    GitHubJobClaudeCodeBinaryObserver,
    GitHubJobSandbox,
    HostClaudeCodeBinaryObserver,
    HostCodexBinaryObserver,
    HostMetricsObserver,
    register_sandbox,
    registered_backends,
    sandbox_config_type,
    sandbox_factory,
    SandboxConfig,
)
from .errors import SandboxError
from .manager import SandboxManager
from .mounts import merge_mounts, Mount, Mounts
from .observer import (
    ArtifactSchema,
    CompositeObserver,
    merge_output_schemas,
    SandboxObserver,
)
from .persist import AttemptRecord, index, persist, promote, RUNS_NAMESPACE
from .resources import Inline, LocalFile, Resource
from .result import (
    Contribution,
    NAME_SEPARATOR,
    qualified_name,
    RunResult,
    RunStatus,
)
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
    "ArtifactSchema",
    "CompositeObserver",
    "Contribution",
    "NAME_SEPARATOR",
    "DockerHostSandbox",
    "DockerHostSandboxConfig",
    "GhjobSandboxConfig",
    "GitHubJobClaudeCodeBinaryObserver",
    "HostClaudeCodeBinaryObserver",
    "HostCodexBinaryObserver",
    "HostMetricsObserver",
    "ExecResult",
    "FilesystemStore",
    "GitHubJobSandbox",
    "Inline",
    "LocalFile",
    "Mount",
    "Mounts",
    "Resource",
    "RUNS_NAMESPACE",
    "AttemptRecord",
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
    "backend_of",
    "build_sandbox",
    "build_sandbox_config",
    "build_store",
    "index",
    "merge_mounts",
    "merge_output_schemas",
    "persist",
    "promote",
    "register_sandbox",
    "register_store",
    "qualified_name",
    "registered_backends",
    "sandbox_config_type",
    "sandbox_factory",
    "registered_stores",
]
