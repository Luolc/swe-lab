"""Shared Docker execution layer for the solve/eval pipeline.

Both ``rollout`` (run an agent inside a task's container) and ``evaluation``
(apply a patch + run tests in the container) run the prebuilt per-instance
images. This package holds the dataset-agnostic ``DockerProvider`` (pull / run /
exec); *which* image an instance uses comes from that dataset's adapter, not
from here.
"""

from __future__ import annotations

from .provider import ContainerRun, DockerError, DockerProvider, Mount

__all__ = [
    "ContainerRun",
    "DockerError",
    "DockerProvider",
    "Mount",
]
