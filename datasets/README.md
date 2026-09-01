# Datasets

This directory holds the datasets used for analysis.

Data files are **not** version-controlled: each dataset's `data/` folder is
gitignored (see the root [`.gitignore`](../.gitignore)). Only the per-dataset
READMEs are tracked, so downloads must be reproduced locally by following the
instructions in each dataset's README below.

## Available datasets

| Dataset | Description | README |
| --- | --- | --- |
| SWE-Bench Pro | Enterprise-level SWE benchmark, public test split (731 examples) | [swebench_pro/README.md](swebench_pro/README.md) |
| DeepSWE 1.1 | Materialized build of [datacurve-ai/deep-swe](https://github.com/datacurve-ai/deep-swe) (113 tasks), one row per task | [dataset card](../src/swe_lab/datasets/deepswe/HF_README.md) |
| Oracle failures | Cached failed rollouts of instances from another dataset, one row per failure; built locally from finished runs, nothing to download | [oracle_failures/README.md](oracle_failures/README.md) |

DeepSWE has **no download instructions and no folder in git**: nothing is
fetched by hand. `load_dataset("deepswe")` downloads the pinned parquet from
the public HF repo into `datasets/deepswe/data/` on first use and verifies its
sha256 on **every** load — the pin is the trust anchor
(`src/swe_lab/datasets/deepswe/constants.py`).

## Layout

```
datasets/
├── README.md              # this file — index of all datasets
└── <dataset_name>/
    ├── README.md          # description + download instructions
    └── data/              # downloaded data files (gitignored)
```

## Adding a new dataset

1. Create a subfolder under `datasets/` named after the dataset.
2. Add a `README.md` in that subfolder describing the dataset and giving the
   exact download commands. Point the data at a `data/` folder inside the
   subfolder, and link back to this file. A dataset the loader materializes
   itself (as `deepswe` does) needs no subfolder — say so in the table
   instead.
3. Register the dataset in the **Available datasets** table above.
