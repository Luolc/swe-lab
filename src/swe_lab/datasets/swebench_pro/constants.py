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

# Last-resort HOME for the eval script — used only when the image sets none
# *and* the passwd database has no entry for the running UID. Plenty of
# toolchains refuse to run without one (Go's build cache lives in
# `$HOME/.cache/go-build`), so on such an image every Go test fails.
# Deliberately the *last* tier, not the harness's unconditional override: an
# instance image often pre-warms its dependency caches under the real HOME (Go
# modules, npm, pip), and replacing it would force a re-download — under
# `--no-network` a failure, not a slowdown.
#
# Under /tmp on purpose: this tier is reached when the UID has no passwd entry,
# which in practice means a non-root user (`docker run -u`, OpenShift's random
# UIDs) — and such a user cannot create a directory at the filesystem root, so
# a `/eval-home` would fail the very case it exists for. /tmp is world-writable.
EVAL_HOME = "/tmp/eval-home"
# Interpreters invoked in the container (both on PATH in the instance images).
BASH = "bash"
PYTHON = "python"
