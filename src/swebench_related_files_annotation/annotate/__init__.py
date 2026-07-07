"""Single-instance annotation agent runner."""

from __future__ import annotations

from .runner import annotate_by_id, annotate_instance, RunResult
from .schema import Annotation, Snippet, SnippetCategory

__all__ = [
    "Annotation",
    "RunResult",
    "Snippet",
    "SnippetCategory",
    "annotate_by_id",
    "annotate_instance",
]
