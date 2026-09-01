"""Oracle-guided trace synthesis — the code behind ``docs/trace-synthesis/``.

Deliberately re-exports nothing. ``sample`` names the files a cached failure is
staged under and is imported by the ``oracle_failures`` dataset record, which
the dataset loader imports at package init — so pulling ``oracle`` (the task,
which imports the workflow and harness layers) in here would run those imports
in the middle of ``swe_lab.datasets`` initializing. Import the module you need.
"""
