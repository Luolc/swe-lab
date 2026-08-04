"""The unit-test evaluation method: run the eval script, grade the workspace."""

from .run import (
    ENTRYSCRIPT_NAME,
    EvalParseObserver,
    gold_patch,
    UnitTestEvalTask,
    verdict_of,
)

__all__ = [
    "ENTRYSCRIPT_NAME",
    "EvalParseObserver",
    "UnitTestEvalTask",
    "gold_patch",
    "verdict_of",
]
