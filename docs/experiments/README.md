# Experiments

How we run experiments and investigations in this ML/eval repo: the
**[playbook.md](playbook.md)** — hypothesis → logged run → empirical results →
attributable conclusion → report, plus the honest-reporting rules.

The experiments themselves live under the top-level
[`experiments/`](../../experiments/) directory (exempt from code-quality hooks),
grouped by workstream:

| Experiment | Kind | Serves |
| --- | --- | --- |
| [related_files/prompt_variance](../../experiments/related_files/prompt_variance/) | Full experiment (README + REPORT) | [W1](../workstreams/w1-related-files.md) |
| [related_files/batch_annotation](../../experiments/related_files/batch_annotation/) | Batch run + QA log | [W1](../workstreams/w1-related-files.md) |
| [eval_issues/truncated_golden_test_names](../../experiments/eval_issues/truncated_golden_test_names/) | Investigation | [W2](../workstreams/w2-solve-eval.md) / [W3](../workstreams/w3-quality-audit.md) |
| [eval_issues/shell_expansion_in_entryscript](../../experiments/eval_issues/shell_expansion_in_entryscript/) | Investigation | [W2](../workstreams/w2-solve-eval.md) |
