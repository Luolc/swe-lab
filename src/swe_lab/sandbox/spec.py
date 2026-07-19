"""The run context: what it takes to bring one instance's sandbox up.

``SandboxSpec`` is the four-field slice every flow needs (rollout and eval
alike); everything grading-specific lives in the eval method's own spec
instead (``UnitTestSpec``), per the EvalSpec split settled in the SandboxRun
spec.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SandboxSpec:
  """Identify the image and repo state one sandbox runs against.

  Attributes:
    instance_id: The dataset instance this sandbox serves.
    image_ref: The pullable image reference to run.
    workdir: Where the repo lives inside the image (e.g. ``/app``).
    base_commit: The commit the in-image repo is reset to before work.
  """

  instance_id: str
  image_ref: str
  workdir: str
  base_commit: str
