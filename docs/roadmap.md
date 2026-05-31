# Roadmap

This file tracks the next workflow improvements after the current Python/mock orchestration baseline.

## Completed in the Current Baseline

- Stage/global calibration use shared cache directories instead of per-trial flow caches.
- `configs/flow.yaml` declares `wafer_slots` once; each step only lists updated wafer slots.
- `step_input.py` expands `wafer_inputs.inherit` before writing `step_input.json`.
- Unknown `update_rule` values fail fast.
- `run_wafer: false` skips the wafer solve in the mock backend and marks `wafer_skipped`.
- `dump_data` is the preferred JSON/YAML writer; `dump_json` is a compatibility alias.

## Next High-Priority Work

1. Add COMSOL command plumbing to calibration.
   `run_flow.py` already accepts `--comsol-command`, but `calibration_optuna.py` does not. Add the CLI flag and pass it through staged/global calibration into `make_backend`.

2. Complete Java worker state transfer.
   `java/ComsolStepWorker.java` currently respects `run_devices`, `run_mats`, and `run_wafer`, but it still needs to read `state_in`, inherit unchanged RVE slots, preserve `materials_state`, preserve `geometry_state`, append `history`, and inject RVE outputs into downstream COMSOL models.

3. Add start/stop checkpointed staged reruns.
   Staged calibration should eventually start each stage from the previous stage's best checkpoint and stop at the stage target step. That will avoid rerunning S00-to-target for every trial.

4. Replace Java regex JSON parsing.
   The worker should move from regex extraction to a small JSON parser or generated Java helper once the final COMSOL batch runtime constraints are known.

5. Promote COMSOL tags into runtime configuration.
   `configs/template_tags.yaml` already captures assumed tags. The Java worker should consume equivalent generated inputs instead of hard-coded `std1`, `gev1`, `gev_rho`, `gmevescp2`, and `gev_bow`.

6. Persist calibration metadata.
   Add an optimizer metadata file per stage with sampler name, seed, bounds, selected parameters, cache path, and code/config hash.
