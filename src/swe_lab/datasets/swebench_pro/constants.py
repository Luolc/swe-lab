"""SWE-Bench Pro constants: image, pinned harness source, file/path layout.

Single source of truth for every literal the SWE-Bench Pro adapter and grader
share, so a name like ``run_script.sh`` is defined once rather than re-typed in
both ``execution`` (which fetches it) and ``grading`` (which stages it).
"""

from __future__ import annotations

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
# The rest of the workspace the entryscript reads/writes. build_eval_script
# (which references them by in-container path) and evaluate (which writes them)
# both use these, so the script and the files on disk can never drift apart.
PATCH_NAME = "patch.diff"
ENTRYSCRIPT_NAME = "entryscript.sh"
OUTPUT_JSON_NAME = "output.json"
STDOUT_LOG_NAME = "stdout.log"
STDERR_LOG_NAME = "stderr.log"

# --- Gitignored cache subdirs (under cache_root) -----------------------------

HARNESS_SUBDIR = "eval_harness"  # per-instance fetched run_script/parser
WORKSPACES_SUBDIR = "eval_workspaces"  # per-instance grading workspace

# --- In-container execution --------------------------------------------------

# Where DockerProvider bind-mounts the per-instance workspace. The entryscript
# refers to staged files by their in-container path, so this must match the
# ``mount_at`` grading passes to ``run_script`` — hence a single constant.
MOUNT_AT = "/workspace"
# Interpreters invoked in the container (both on PATH in the instance images).
BASH = "bash"
PYTHON = "python"
