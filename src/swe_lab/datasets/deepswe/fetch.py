"""Fetch and verify the published DeepSWE parquet (task-30 §2b).

The pin here is the loader's trust anchor: the HF repo is mutable, like a
docker tag, so the sha256 lives in this repo and every load re-verifies the
local file against it — a regeneration upstream (even by us) then fails
loudly instead of changing the dataset under a sweep's feet.
"""

from __future__ import annotations

import hashlib

from etils import epath

from .constants import (
    HF_REPO_ID,
    PARQUET_FILENAME,
    PINNED_DEEPSWE_PARQUET_SHA256,
)


def ensure_deepswe_parquet(data_dir: epath.PathLike) -> epath.Path:
  """Ensure the pinned parquet is present and verified in ``data_dir``.

  Downloads from the public HF repo when absent (anonymous — the repo is
  public), and verifies the sha256 **every time**, present or fresh: the
  verification is the anchor doing its job, not a download-time nicety.

  Args:
    data_dir: The dataset's ``data/`` directory (created if missing).

  Returns:
    The verified parquet path.

  Raises:
    ValueError: If the file does not match the pin — a stale local copy after
      a deliberate bump, or a drifted upload; the message says which action
      fixes which.
  """
  data_dir = epath.Path(data_dir)
  data_dir.mkdir(parents=True, exist_ok=True)
  target = data_dir / PARQUET_FILENAME
  if not target.exists():
    # Imported lazily: loading a dataset that is already on disk must not
    # require hub access, or the offline path would break.
    from huggingface_hub import hf_hub_download

    fetched = hf_hub_download(
        repo_id=HF_REPO_ID, filename=PARQUET_FILENAME, repo_type="dataset"
    )
    _ = target.write_bytes(epath.Path(fetched).read_bytes())
  actual = hashlib.sha256(target.read_bytes()).hexdigest()
  if actual != PINNED_DEEPSWE_PARQUET_SHA256:
    raise ValueError(
        f"{target} does not match the pinned sha256:\n"
        f"  expected {PINNED_DEEPSWE_PARQUET_SHA256}\n"
        f"  actual   {actual}\n"
        "If the pin was just bumped, delete the file to re-download; if it"
        " was not, the published artifact drifted — do not use it, and check"
        " the HF repo's manifest.json against the pin."
    )
  return target
