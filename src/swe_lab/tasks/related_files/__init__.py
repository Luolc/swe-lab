"""Annotation and aggregation agents.

`annotator` produces one instance's code-snippet annotation; `aggregator`
reconciles several such annotations into one. Both are thin wrappers over
`agent_run.run_agent`.
"""

from __future__ import annotations

from swe_lab.core.agent.errors import (
    AnnotationError,
    MissingOutputError,
    RetryableError,
    UsageLimitError,
)

from .agent_run import run_agent, RunResult
from .aggregator import aggregate_by_id, aggregate_instance
from .annotator import annotate_by_id, annotate_instance
from .pipeline import (
    annotate_by_id_with_aggregation,
    annotate_with_aggregation,
    PipelineResult,
)
from .schema import Annotation, Snippet, SnippetCategory

__all__ = [
    "Annotation",
    "AnnotationError",
    "MissingOutputError",
    "PipelineResult",
    "RetryableError",
    "RunResult",
    "Snippet",
    "SnippetCategory",
    "UsageLimitError",
    "aggregate_by_id",
    "aggregate_instance",
    "annotate_by_id",
    "annotate_by_id_with_aggregation",
    "annotate_instance",
    "annotate_with_aggregation",
    "run_agent",
]
