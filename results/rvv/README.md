# RVV Experiment Files

This directory preserves the CSV outputs from the RVV automated benchmark runs.

- `rvv_experiment_138B*`: 1B, 3B, and 8B model-scale run at parallel 4.
- `rvv_experiment_3B_parallel*`: first 3B parallel-sweep run.
- `rvv_experiment_3B_parallel_2*`: second independent 3B parallel-sweep run.

Files without the `_no_response` suffix retain the model response text. Matching
`_no_response` files preserve the measurement fields while omitting that text.
