# Roadmap

This file tracks the next workflow improvements after the v2 20-step Python/mock orchestration baseline.

## Completed in the Current Baseline

- `configs/flow.yaml` is now a v2 20-step flow using canonical nodes: `pillar`, `sc`, `decap1`, `decap2`, `decap3`, `fecap`, `die`, `wafer`, and `onon`.
- Legacy active step configs were moved under `archive/legacy_config_scheme/steps/`; active `configs/steps/` now contains the v2 20-step process skeleton.
- Python v2 validation rejects legacy names, prevents `onon` from running as a model node, and requires wafer to run every step.
- Python v2 step input expansion writes generated COMSOL parameter TXT files for upstream RVE inputs.
- V2 `step_input.json` now uses a compact txt-driven contract with template specs, compact `runs`, input parameter TXT paths, and output TXT paths.
- ONON is now a global RVE input for decap, fecap, die, and wafer.
- Active `configs/steps/*.yaml` now use shorthand `run:` declarations, `decap` groups, and `preset: full_chain`; the loader expands fixed-chain dependencies and implicit inheritance.
- `configs/templates.yaml` supports `template_families` for repeated variants such as `decap_model_decap1/2/3`; the loader expands families into ordinary template specs.
- `configs/templates.yaml` now carries model paths, study tags, and output TXT/result filenames; COMSOL material/property wiring lives inside the models.
- The COMSOL backend now fails fast before invoking Java if declared input parameter TXT files are missing.
- The mock backend supports v2 `run`, `inherit`, `default_from`, `alias`, same-step working-state references, stress-only D inheritance, per-step `logs/step.log`, and machine-readable `logs/interface.json` interface audits.
- V2 summary output uses canonical status fields and `bow_x_um` / `bow_y_um`; loss calculation accepts these fields directly.
- The COMSOL backend wrapper writes dispatch/result information to `logs/step.log`.
- `java/ComsolStepWorker.java` has a first-pass txt-driven skeleton with parameter TXT loading and output TXT parsing.
- Stage/global calibration use shared cache directories instead of per-trial flow caches.
- COMSOL-style parameter TXT files under `configs/params/` are merged in global, step, calibration override order.
- Calibration trials now stage trial-specific `configs/params/calibration_override.txt` files so YAML trial parameters also reach COMSOL through the active TXT stack.
- Step cache keys include staged parameter TXT content hashes.
- Default calibration filters `configs/calibration_space.yaml` to groups whose `target_step` is enabled in the active flow.
- `calibration_optuna.py` accepts `--comsol-command` and passes it through staged/global calibration into `make_backend`.
- `configs/` now contains the active v2 scheme; old nominal YAML, tag, three-file TXT configs, and seed step configs are archived under `archive/legacy_config_scheme/`.
- Runtime support for old tag/extractor-driven RVE injection has been removed; active flow loading is v2 params TXT only.
- Old local step parameter TXT files and superseded implementation notes were moved out of the active config/docs paths into `archive/legacy_config_scheme/params/` and `archive/project_notes/`.
- `README.md` and `docs/comsol_parameter_txt_simplification_plan.md` are the active project overview/design docs. Older planning/spec documents are archived under `archive/project_notes/`.
- `dump_data` is the preferred JSON/YAML writer; `dump_json` is a compatibility alias.
- `config_io` now depends on PyYAML from `requirements.txt`; the temporary built-in YAML fallback was removed.
- Runtime support for old `device/mat/wafer` flow shapes, `wafer_inputs`, `device_rves`, `parameter_map`, and tag/extractor-driven RVE injection has been removed from active Python code.
- `mock_comsol_worker.py`, `flow_runner.py`, `step_input.py`, `state.py`, `summary.py`, `schemas.py`, and `cache_manager.py` are now v2 params-TXT-only.
- `python/comsol_opt/process.py` was deleted because process updates are now model/template/parameter-TXT driven rather than Python wafer-slot updates.

## Next High-Priority Work

1. Validate v2 template paths and study/output TXT contracts against real COMSOL exports.
   Export Java from representative `pillar`, `sc`, `decap`, `fecap`, `die`, and `wafer` models. Confirm `.mph` paths, stress/CP study tags, parameter TXT loading, and output TXT file names in `configs/templates.yaml`.

2. Add explicit multi-component / multi-study run selection to the Java worker contract.
   Some `.mph` files contain multiple components and studies. Extend `configs/templates.yaml` so each run can declare the exact study sequence to execute, for example `run_sequence: [{component: comp6, study: std3}, {component: comp6, study: solid6cp1std}]`, while preserving the current simple `studies.stress/cp` shorthand for one-component models. Java should log the selected component/study pair for each execution and fail fast if the requested study tag is missing.

3. Finish Java worker parsing and execution hardening.
   Replace the regex JSON parser with a real JSON helper or generated parser, compile against COMSOL 6.3, and verify `S0`, `D`, and `DV0` ordering against exported model Java.

4. Replace provisional v2 process model paths.
   The current v2 step configs express the 20-step DAG and logging contract, but several template paths are placeholders until real `.mph` files and study/output TXT contracts are confirmed.

5. Add start/stop checkpointed staged reruns.
   Staged calibration should eventually start each stage from the previous stage's best checkpoint and stop at the stage target step. That will avoid rerunning S00-to-target for every trial.

6. Add Java worker execution coverage.
   Current tests inspect the worker contract text because the local machine does not have a Java runtime or COMSOL API. Add a compile/runtime harness with COMSOL stubs or a small parser test once CI has a Java runtime.

7. Persist calibration metadata.
   Add an optimizer metadata file per stage with sampler name, seed, bounds, selected parameters, cache path, and code/config hash.
