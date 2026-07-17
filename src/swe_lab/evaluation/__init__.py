"""Evaluation CLI: grade a patch by running an instance's tests in a container.

This package is only the user-facing command surface (``__main__``): pick a
dataset, build its ``EvalSpec``, and hand it to that dataset's grader. The
grading itself is dataset-specific and lives with the dataset — for SWE-Bench
Pro that is ``core.datasets.swebench_pro.grading`` (it stages ``run_script.sh``
/ ``parser.py`` / the entryscript and decides resolved iff
``(fail_to_pass ∪ pass_to_pass)`` all pass). Only SWE-Bench Pro is wired today.
"""
