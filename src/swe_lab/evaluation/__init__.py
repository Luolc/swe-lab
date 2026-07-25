"""The evaluation axis: judge a run's workspace into a typed verdict.

The general contract lives in ``verdict`` (``Verdict`` / ``Grader`` /
``UnitTestSpec``); each **method** under ``methods/`` is one way to judge —
``unit_test`` (run the dataset's tests and grade the parser output) today,
model-/rubric-based judgment later. The dataset owns the concrete grader (for
SWE-Bench Pro, ``datasets.swebench_pro.unit_test``); this package stays
method-general and never hard-codes a dataset. The ``eval`` / ``verify``
commands compose a method over the sandbox engine.
"""
