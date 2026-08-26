"""DeepSWE 1.1 as a swe-lab dataset (task-30).

Datacurve's 113-task Harbor-format benchmark, materialized into a parquet
this package builds (``build_parquet``) and hosts on the public HF repo named
in ``constants``. The loader half (record + registration) is the next task-30
phase; today this package is the producer side.
"""
