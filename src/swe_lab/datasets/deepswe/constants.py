"""Pins and names for the DeepSWE dataset (task-30 §2b).

Two pins, two jobs. The **commit pin** fixes what the builder reads — a git
commit sha is a content hash, so a checkout at it is self-verifying. The
**parquet pin** fixes what the loader accepts — the HF repo is mutable, like a
docker tag, so without it a regeneration would change the dataset under a
sweep's feet silently. Bump the pair together, deliberately, with a manifest
diff explaining which tasks changed.
"""

# The upstream source, and the commit the published parquet was built from.
DEEPSWE_GIT_URL = "https://github.com/datacurve-ai/deep-swe"
# `main` as of 2026-08-25 (v1.1 corpus, 113 tasks).
PINNED_DEEPSWE_COMMIT = "435ee89ec2f2e2289f33b0da4f992f0b7b7266b9"

# Where the materialized dataset lives, and what the artifact is called.
# The slug matches upstream's own dataset name (`datacurve/deep-swe-1-1`);
# `materialized` names the transformation (task dirs -> one row per task).
HF_REPO_ID = "luolc/deep-swe-1-1-materialized"
PARQUET_FILENAME = "deep-swe-1-1.parquet"
MANIFEST_FILENAME = "manifest.json"

# sha256 of the published parquet — the loader's trust anchor (task-30 §2b:
# a checksum fetched from the repo it checks proves only internal
# consistency, so the anchor lives here). Filled by the builder's output when
# the artifact is (re)published; empty means "not yet published".
PINNED_DEEPSWE_PARQUET_SHA256 = (
    "954184ffb6fd88c798171dfc8577793b29631bff35c8d02f6fa4bbf88a44abd0"
)
