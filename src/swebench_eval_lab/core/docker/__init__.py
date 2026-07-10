"""Shared Docker execution layer for the solve/eval pipeline.

Both ``rollout`` (run an agent inside a task's container) and ``evaluation``
(apply a patch + run tests in the container) build on the prebuilt per-instance
images published on Docker Hub. This package holds the pieces they share:
image references now, a ``DockerProvider`` (pull / run / exec) next.
"""
