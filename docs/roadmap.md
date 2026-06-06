# Roadmap

This file tracks the next workflow improvements after the v2 20-step Python/mock orchestration baseline.

## Completed in the Current Baseline

- `configs/flow.yaml` is now a v2 20-step flow using canonical nodes: `pillar`, `sc`, `decap1`, `decap2`, `decap3`, `fecap`, `die`, `wafer`, and `onon`.
- Legacy active step configs were moved under `archive/legacy_config_scheme/steps/`; active `configs/steps/` now contains the v2 20-step process skeleton.
- Python v2 validation rejects legacy names, prevents `onon` from running as a model node, and requires wafer to run every step.
- Python v2 step input expansion writes Java-ready RVE payloads with `D_upper21`, `symmetric_upper21`, and explicit material/stress target tags.
- The mock backend supports v2 `run`, `inherit`, `default_from`, `alias`, same-step working-state references, stress-only D inheritance, and per-step `logs/step.log` audit logs.
- V2 summary output uses canonical status fields and `bow_x_um` / `bow_y_um`; loss calculation accepts these fields directly.
- The COMSOL backend wrapper writes dispatch/result information to `logs/step.log`.
- `java/ComsolStepWorker.java` has a first-pass expanded-input skeleton with `MaterialTarget`, `StressTarget`, `D_upper21`, and material/stress application helpers.
- Stage/global calibration use shared cache directories instead of per-trial flow caches.
- COMSOL-style parameter TXT files under `configs/params/` are merged in global, step, calibration override order.
- Step cache keys include staged parameter TXT content hashes.
- Default calibration filters `configs/calibration_space.yaml` to groups whose `target_step` is enabled in the active flow.
- `configs/` now contains the active v2 scheme; old nominal YAML, tag, three-file TXT configs, and seed step configs are archived under `archive/legacy_config_scheme/`.
- `README.md` is the single active project overview. Older planning/spec documents are archived with the legacy scheme.
- `dump_data` is the preferred JSON/YAML writer; `dump_json` is a compatibility alias.

## Next High-Priority Work

1. Validate v2 template tags against real COMSOL exports.
   Export Java from representative `pillar`, `sc`, `decap`, `fecap`, `die`, and `wafer` models. Replace provisional component/material/physics/feature/study/extractor tags in `configs/templates.yaml`.

2. Finish Java worker parsing and execution hardening.
   Replace the regex JSON parser with a real JSON helper or generated parser, compile against COMSOL 6.3, and verify `S0`, `D`, and `DV0` ordering against exported model Java.

3. Add COMSOL command plumbing to calibration.
   `run_flow.py` already accepts `--comsol-command`, but `calibration_optuna.py` does not. Add the CLI flag and pass it through staged/global calibration into `make_backend`.

4. Replace provisional v2 process model paths.
   The current v2 step configs express the 20-step DAG and logging contract, but several template paths are placeholders until real `.mph` files and tags are confirmed.

5. Add start/stop checkpointed staged reruns.
   Staged calibration should eventually start each stage from the previous stage's best checkpoint and stop at the stage target step. That will avoid rerunning S00-to-target for every trial.

6. Add Java worker execution coverage.
   Current tests inspect the worker contract text because the local machine does not have a Java runtime or COMSOL API. Add a compile/runtime harness with COMSOL stubs or a small parser test once CI has a Java runtime.

7. Persist calibration metadata.
   Add an optimizer metadata file per stage with sampler name, seed, bounds, selected parameters, cache path, and code/config hash.
