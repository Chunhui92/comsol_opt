# Roadmap

This file tracks the next workflow improvements after the current Python/mock orchestration baseline.

## Completed in the Current Baseline

- Stage/global calibration use shared cache directories instead of per-trial flow caches.
- `configs/flow.yaml` is now a layered DAG entrypoint that references per-step files under `configs/steps/`.
- `step_input.py` can write DAG-style `nodes` with explicit `run`/`inherit` actions.
- The mock backend executes DAG nodes, writes `manifest.json`, and validates inherited RVE state.
- Layered DAG validation fails fast on invalid update rules, duplicate nodes, bad node actions/types, unresolved templates, incorrect outputs, missing inputs, and unavailable upstream RVE references.
- `java/ComsolStepWorker.java` has a first-pass DAG skeleton that parses `nodes`, honors `run` / `inherit`, loads parameter TXT files in `parameter_txt_order`, writes node result files, writes `manifest.json`, carries `rve.*` state forward, preserves material/geometry state, preserves skipped wafer results, and appends wafer history when wafer runs.
- COMSOL-style parameter TXT files under `configs/params/` are merged in global, step, calibration override order.
- Layered runs use each step's own TXT-derived parameters unless calibration supplies an explicit trial params YAML.
- Step cache keys include staged parameter TXT content hashes.
- Default calibration filters `configs/calibration_space.yaml` to groups whose `target_step` is enabled in the active flow.
- `configs/` now contains only the active layered DAG scheme; old nominal YAML, tag, and three-file TXT configs are archived under `archive/legacy_config_scheme/`.
- `README.md` is the single active project overview. Older planning/spec documents are archived with the legacy scheme.
- Unknown `update_rule` values fail fast.
- `run_wafer: false` skips the wafer solve in the mock backend and marks `wafer_skipped`.
- `dump_data` is the preferred JSON/YAML writer; `dump_json` is a compatibility alias.

## Next High-Priority Work

1. Add COMSOL command plumbing to calibration.
   `run_flow.py` already accepts `--comsol-command`, but `calibration_optuna.py` does not. Add the CLI flag and pass it through staged/global calibration into `make_backend`.

2. Validate Java worker assumptions against real COMSOL templates.
   The current worker injects upstream RVE values as provisional parameters named `input_<slot>_sxx`, `input_<slot>_syy`, `input_<slot>_rho`, `input_<slot>_d11`, and `input_<slot>_d22`. Replace these names with the real template inputs or table-loading mechanism, then verify extractor tags from `configs/extractors.yaml` against COMSOL output.

3. Add the remaining real process step configs.
   The current layered default includes the available `S00_init` and `S02_trench_etch` step configs. Add S01, S03, S04, S05*, S06, S07, S08, S09, and final step files with real template keys, node actions, parameter TXT files, and experiment targets before treating the 20-step DAG as complete.

4. Add start/stop checkpointed staged reruns.
   Staged calibration should eventually start each stage from the previous stage's best checkpoint and stop at the stage target step. That will avoid rerunning S00-to-target for every trial.

5. Add Java worker execution coverage.
   Current tests inspect the worker contract text because the local machine does not have a Java runtime or COMSOL API. Add a compile/runtime harness with COMSOL stubs or a small parser test once CI has a Java runtime.

6. Replace Java regex JSON parsing.
   The worker should move from regex extraction to a small JSON parser or generated Java helper once the final COMSOL batch runtime constraints are known.

7. Finish COMSOL tag promotion into runtime configuration.
   `configs/extractors.yaml` now captures extractor tags for the layered scheme. The Java worker should consume these generated inputs instead of hard-coded `std1`, `gev1`, `gev_rho`, `gmevescp2`, and `gev_bow`.

8. Persist calibration metadata.
   Add an optimizer metadata file per stage with sampler name, seed, bounds, selected parameters, cache path, and code/config hash.
