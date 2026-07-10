"""Evaluation: grade a patch by running the instance's tests in its container.

Apply a candidate ``git diff`` on top of ``base_commit`` inside the prebuilt
image, run the instance's ``run_script.sh``, parse the output with its
``parser.py``, and decide resolved iff ``(fail_to_pass ∪ pass_to_pass)`` all
pass. The flow is dataset-agnostic: it consumes an ``EvalSpec`` built by the
dataset's adapter (for SWE-bench Pro that resolves the image and fetches the
per-instance ``run_script`` / ``parser`` — see
``core.datasets.swebench_pro.execution``).
"""
