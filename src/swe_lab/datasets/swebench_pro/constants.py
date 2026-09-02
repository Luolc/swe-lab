"""SWE-Bench Pro constants: image, pinned harness source, file/path layout.

Single source of truth for every literal the SWE-Bench Pro adapter and grader
share, so a name like ``run_script.sh`` is defined once rather than re-typed in
both ``execution`` (which fetches it) and ``unit_test`` (which stages it).
"""

from __future__ import annotations

from swe_lab.sandbox.observers import BASE_REF_NAME as _BASE_REF_NAME
from swe_lab.sandbox.observers import PATCH_NAME as _PATCH_NAME

# --- Docker images -----------------------------------------------------------

# Prebuilt per-instance images on Docker Hub (public mirror of Scale's ECR); the
# dataset's ``dockerhub_tag`` is the tag verbatim.
IMAGE_REPO = "jefzda/sweap-images"
# Every image clones the repo to this path, so eval/rollout run against it.
WORKDIR = "/app"

# --- Pinned Scale harness source (fetched from GitHub) -----------------------

# Scale's GitHub repo we fetch the per-instance harness from, pinned to an exact
# commit for reproducibility. Why this SHA: it was origin/main's tip when we
# built this — pinned 2026-07-10; the commit itself is dated 2026-05-18 ("Merge
# PR #98 from scaleapi/miguelrc-scale-patch-1"), i.e. the latest harness at the
# time. We pin a SHA instead of tracking main so the fetched run_script.sh /
# parser.py can't drift under us mid-project. Bump deliberately, and only after
# re-checking that the new scripts still match our eval logic.
SCALE_SWEBENCH_PRO_REPO = "scaleapi/SWE-bench_Pro-os"  # owner/repo slug (MIT)
SCALE_SWEBENCH_PRO_COMMIT = "ca10a60a5fcae51e6948ffe1485d4153d421e6c5"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
HARNESS_FETCH_TIMEOUT_S = 30.0

# --- Dataset parquet pin (verified, not fetched — see fetch.py) -------------

# The published HF file this dataset loads. Downloaded manually per
# ``datasets/swebench_pro/README.md`` (dataset data is not committed); this
# name is what the loader looks for once it is there.
PARQUET_FILENAME = "test-00000-of-00001.parquet"

# sha256 of the published parquet — the loader's trust anchor for a file
# fetched from a mutable HF ``main`` ref (same shape as
# ``deepswe.constants.PINNED_DEEPSWE_PARQUET_SHA256``).
#
# What this pin proves: the local copy is byte-identical to the one HF's own
# LFS storage serves at ``ScaleAI/SWE-bench_Pro`` — two independent sources
# agree (below), so this is not merely "the hash of the file I happened to
# download". What it does **not** prove: that the file HF serves today is the
# one the paper's numbers were computed from, or was ever reviewed for
# correctness. The pin fixes "does not silently change under us" going
# forward; it does not establish "was correct to begin with".
#
# Provenance (2026-09-02):
#   - Local download: `curl` per this dataset's README, `sha256sum` on the
#     result.
#   - HF's own record, independent of that download:
#     `curl -s "https://huggingface.co/api/datasets/ScaleAI/SWE-bench_Pro?blobs=true"`
#     -> `siblings[].lfs.sha256` for `data/test-00000-of-00001.parquet`.
#   Both gave the same digest and the same size (7,816,820 bytes).
PINNED_SWEBENCH_PRO_PARQUET_SHA256 = (
    "c8cd7115496ad4e9a8b21d088cef576a65bf821bb542b24336f13f714cef13f8"
)

# --- Harness / workspace file names ------------------------------------------

# The per-instance harness: fetched from Scale, then staged into the workspace.
RUN_SCRIPT_NAME = "run_script.sh"
PARSER_NAME = "parser.py"
# The rest of the workspace the entryscript reads/writes. ``_build_eval_script``
# (which references them by in-container path) and ``compile_unit_test`` (which
# mounts them inline) both use these, so the script and the files never drift.
# The patch is the exception: it is not this dataset's name to choose but the
# cross-dataset store name the extraction side produces, so the default is
# re-exported from there and the rollout → eval edge matches by construction.
PATCH_NAME = _PATCH_NAME
BASE_REF_NAME = _BASE_REF_NAME
ENTRYSCRIPT_NAME = "entryscript.sh"
OUTPUT_JSON_NAME = "output.json"
STDOUT_LOG_NAME = "stdout.log"
STDERR_LOG_NAME = "stderr.log"

# --- Gitignored cache subdirs (under cache_root) -----------------------------

HARNESS_SUBDIR = "eval_harness"  # per-instance fetched run_script/parser
WORKSPACES_SUBDIR = "eval_workspaces"  # per-instance grading workspace

# --- In-container execution --------------------------------------------------

# DECIDED (#242): the pair of last-resort homes stays under /tmp — this one
# and the harnesses' HOME_LAST_RESORT (/tmp/agent-home) — rather than moving
# to a /eval-home for name symmetry with the config root. This tier fires only
# for a UID with no passwd entry, in practice a non-root user, and such a user
# cannot mkdir at the filesystem root (measured: DeepSWE images ship / at 755,
# not world-writable). /tmp is world-writable; symmetry is achieved in the
# /tmp direction instead.
EVAL_HOME = "/tmp/eval-home"
# Interpreters invoked in the container (both on PATH in the instance images).
BASH = "bash"
PYTHON = "python"
