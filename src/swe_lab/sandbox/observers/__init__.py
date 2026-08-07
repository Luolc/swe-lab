"""Shared, harness-agnostic observers reused across compositions."""

from .diff_extract import DiffExtractObserver, PATCH_NAME
from .git_history_purge import (
    GitHistoryLeakError,
    GitHistoryPurgeObserver,
    INTEGRITY_ARTIFACT,
)
from .result_verify import ResultVerifyObserver, VERIFIER_ARTIFACT

__all__ = [
    "DiffExtractObserver",
    "GitHistoryLeakError",
    "GitHistoryPurgeObserver",
    "INTEGRITY_ARTIFACT",
    "PATCH_NAME",
    "ResultVerifyObserver",
    "VERIFIER_ARTIFACT",
]
