# Roadmap

This file tracks the next workflow improvements after the current Python/mock orchestration baseline.

## Completed in the Current Baseline

- Stage/global calibration use shared cache directories instead of per-trial flow caches.
- `configs/flow.yaml` is now a layered DAG entrypoint that references per-step files under `configs/steps/`.
- `step_input.py` can write DAG-style `nodes` with explicit `run`/`inherit` actions.
- The mock backend executes DAG nodes, writes `manifest.json`, and validates inherited RVE state.
- COMSOL-style parameter TXT files under `configs/params/` are merged in global, step, calibration override order.
- Unknown `update_rule` values fail fast.
- `run_wafer: false` skips the wafer solve in the mock backend and marks `wafer_skipped`.
- `dump_data` is the preferred JSON/YAML writer; `dump_json` is a compatibility alias.

## Next High-Priority Work

1. Add COMSOL command plumbing to calibration.
   `run_flow.py` already accepts `--comsol-command`, but `calibration_optuna.py` does not. Add the CLI flag and pass it through staged/global calibration into `make_backend`.

2. Complete Java worker DAG execution and state transfer.
   `java/ComsolStepWorker.java` should move from the legacy `run_devices` / `run_mats` shape to the new `nodes` contract, load parameter TXT files in `parameter_txt_order`, honor `inherit`, preserve material and geometry state, write node result files, write `manifest.json`, and append wafer history.

3. Add the remaining real process step configs.
   The current layered default includes the available `S00_init` and `S02_trench_etch` step configs. Add S01, S03, S04, S05*, S06, S07, S08, S09, and final step files before treating the 20-step DAG as complete.

4. Add start/stop checkpointed staged reruns.
   Staged calibration should eventually start each stage from the previous stage's best checkpoint and stop at the stage target step. That will avoid rerunning S00-to-target for every trial.

5. Replace Java regex JSON parsing.
   The worker should move from regex extraction to a small JSON parser or generated Java helper once the final COMSOL batch runtime constraints are known.

6. Finish COMSOL tag promotion into runtime configuration.
   `configs/extractors.yaml` now captures extractor tags for the layered scheme. The Java worker should consume these generated inputs instead of hard-coded `std1`, `gev1`, `gev_rho`, `gmevescp2`, and `gev_bow`.

7. Persist calibration metadata.
   Add an optimizer metadata file per stage with sampler name, seed, bounds, selected parameters, cache path, and code/config hash.
