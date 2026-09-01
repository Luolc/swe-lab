"""The failure sample: what a cached failure is, and how it is staged.

Phase B of the pipeline starts from a rollout that already failed — its typed
conversation, the grader's verdict on its patch, and the patch itself. The
``oracle_failures`` dataset record carries those three and stages them into the
workspace through ``TaskInstance.mounts`` (ADR-0007 §2); the Oracle task reads
them back by name. The names are the contract between the two, and they live
here — in neither — so the task never imports a concrete dataset.
"""

from __future__ import annotations

# The typed ``Conversation`` of the failed rollout, serialized as JSON.
FAILED_CONVERSATION_NAME = "failed_conversation.json"
# The dataset grader's verdict on the failed patch: ``resolved`` / ``score``,
# the grader's ``metrics`` and its ``summary`` (which names the failed tests).
FAILED_VERDICT_NAME = "failed_verdict.json"
# The patch the failed rollout submitted.
FAILED_PATCH_NAME = "failed_patch.diff"

# The three together: what an instance must stage for a task to have a failure
# to analyze. A task checks an instance's mounts against this before it
# stages or starts anything.
FAILURE_NAMES: tuple[str, ...] = (
    FAILED_CONVERSATION_NAME,
    FAILED_VERDICT_NAME,
    FAILED_PATCH_NAME,
)
