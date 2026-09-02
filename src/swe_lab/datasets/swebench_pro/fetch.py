"""Verify the manually-downloaded SWE-Bench Pro parquet against its pin.

Unlike ``deepswe.fetch``, this does not download: the README's manual
``curl`` step is deliberate (dataset data is not committed) and is unchanged
here. What was missing is verification — the loader accepted whatever bytes
were on disk under this name, so a truncated download, a drifted upstream
file, or a stale copy from before a deliberate pin bump were all silently
indistinguishable from the real thing. The sha256 lives in this repo and
every load re-verifies the local file against it, same anchor shape as
``deepswe.fetch.ensure_deepswe_parquet``.

The pin is keyed to :data:`~.constants.PARQUET_FILENAME` as well as content —
a deliberate, newly-introduced constraint, not a byproduct: this is a
single-file dataset and the README's download command names the file
explicitly, so "which file did we get" should never be ambiguous. The cost is
that a present file under any other name reads as absent; the error this
module raises distinguishes that case rather than letting it read as
``datasets/README.md``'s own hazard family — a missing/misnamed dataset file
surfacing as if the instances themselves were broken.
"""

from __future__ import annotations

import hashlib

from etils import epath

from .constants import PARQUET_FILENAME, PINNED_SWEBENCH_PRO_PARQUET_SHA256


def ensure_swebench_pro_parquet(data_dir: epath.PathLike) -> epath.Path:
  """Verify the pinned parquet is present, correctly named, and unmodified.

  Runs on every load, present or not: the verification is the anchor doing
  its job, not a download-time nicety. Does not fetch the file — see
  ``datasets/swebench_pro/README.md`` for the manual download step.

  Args:
    data_dir: The dataset's ``data/`` directory.

  Returns:
    The verified parquet path.

  Raises:
    FileNotFoundError: The pinned file is not present under its pinned name.
    ValueError: The present file does not match the pin.
  """
  data_dir = epath.Path(data_dir)
  target = data_dir / PARQUET_FILENAME
  if not target.exists():
    others = (
        sorted(p.name for p in data_dir.glob("*.parquet"))
        if data_dir.is_dir()
        else []
    )
    if others:
      raise FileNotFoundError(
          f"{target} not found, but {data_dir} has: {others}. The pin is"
          f" keyed to the exact filename {PARQUET_FILENAME!r} — this is a"
          " misnamed or stale file, not a broken dataset. Rename or"
          " re-download per datasets/swebench_pro/README.md."
      )
    raise FileNotFoundError(
        f"{target} not found. See datasets/swebench_pro/README.md for the"
        " download step."
    )
  actual = hashlib.sha256(target.read_bytes()).hexdigest()
  if actual != PINNED_SWEBENCH_PRO_PARQUET_SHA256:
    raise ValueError(
        f"{target} does not match the pinned sha256:\n"
        f"  expected {PINNED_SWEBENCH_PRO_PARQUET_SHA256}\n"
        f"  actual   {actual}\n"
        "The downloaded copy is truncated, drifted from the published file,"
        " or predates a deliberate pin bump — re-download per"
        " datasets/swebench_pro/README.md and re-verify; do not use a file"
        " that fails this check."
    )
  return target
