"""The evaluation axis: judge a run's workspace into a typed verdict.

The general contract lives in ``verdict`` (``Verdict`` / ``Grader`` /
``UnitTestSpec``); ``unit_test`` is the one way to judge shipped today (run the
dataset's tests and grade the parser output). A rubric- or model-judged method
would be a sibling module here — flat until there is enough of it to layer.

The dataset owns the concrete grader (for SWE-Bench Pro,
``datasets.swebench_pro.unit_test``); this package stays method-general and
never hard-codes a dataset.
"""
