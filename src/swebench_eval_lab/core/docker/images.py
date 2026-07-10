"""Docker image references for SWE-bench Pro instances.

Each instance has a prebuilt image (repo checked out at ``base_commit`` with all
deps installed) published on Docker Hub as
``jefzda/sweap-images:<dockerhub_tag>``. The dataset's ``dockerhub_tag`` column
is the tag verbatim, so the reference is just a format string — no
reconstruction needed.
"""

from __future__ import annotations

# Public Docker Hub repo of prebuilt per-instance images (mirrors Scale's ECR).
SWEAP_IMAGE_REPO = "jefzda/sweap-images"


def image_ref(dockerhub_tag: str, *, repo: str = SWEAP_IMAGE_REPO) -> str:
  """The pullable image reference for an instance's ``dockerhub_tag``."""
  return f"{repo}:{dockerhub_tag}"
