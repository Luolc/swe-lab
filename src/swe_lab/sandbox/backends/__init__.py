"""Concrete sandbox backends.

Each backend realizes the ``SandboxBackend`` protocol one way. ``host`` drives
Docker from the host; a GitHub-Actions container-job backend is planned.
"""

from .host import DockerHostBackend

__all__ = ["DockerHostBackend"]
