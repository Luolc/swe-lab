"""The engine's single error type."""

from __future__ import annotations


class SandboxError(RuntimeError):
  """The engine failed to drive the sandbox lifecycle."""
