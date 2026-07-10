"""Evaluation: grade a patch by running the instance's tests in its container.

Apply a candidate ``git diff`` on top of ``base_commit`` inside the prebuilt
image, run the instance's ``run_script.sh``, parse the output with its
``parser.py``, and decide resolved iff ``(fail_to_pass ∪ pass_to_pass)`` all
pass. Ports the logic of Scale's ``swe_bench_pro_eval.py`` (hardening the
brittle bits); reuses their per-instance ``run_script`` / ``parser`` as pinned,
gitignored fetched artifacts (see ``harness``).
"""
