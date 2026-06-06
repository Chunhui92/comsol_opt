# comsol_opt

Python orchestration for a multi-scale COMSOL wafer-warpage calibration flow.

The active configuration is the v2 20-step COMSOL process flow:

```text
process step S01..S20
  -> pillar / sc RVEs
  -> decap1 / decap2 / decap3 / fecap RVEs
  -> die RVE
  -> wafer model
  -> bow_x_um / bow_y_um comparison
  -> staged calibration
```

The Python layer owns config expansion, parameter TXT staging, state inheritance, backend dispatch, mock validation, loss calculation, calibration, and runtime logging. A real COMSOL worker should consume only `step_input.json` plus the parameter TXT files referenced there.

## Quick Start

```bash
python3 -m unittest tests/test_workflow.py -v
python3 python/run_flow.py --backend dryrun --run-id dryrun_001
python3 python/run_flow.py --backend mock --run-id mock_001
python3 python/compute_loss.py --summary runs/mock_001/summary.csv
python3 python/calibration_optuna.py --backend mock --run-id calib_001 --n-trials 5
```

Run selected staged calibration:

```bash
python3 python/calibration_optuna.py \
  --backend mock \
  --mode staged \
  --stages G2_trench_release,G10_final \
  --run-id staged_001 \
  --n-trials 5
```

## Active Layout

```text
configs/
├── flow.yaml
├── templates.yaml
├── extractors.yaml
├── calibration_space.yaml
├── steps/
├── params/
└── experiments/
```

- `configs/flow.yaml` is the v2 entrypoint. It lists the enabled S01-S20 step files, parameter TXT load order, and the ONON alias source.
- `configs/steps/*.yaml` defines each process step. Every node must explicitly declare `action: run`, `action: inherit`, `action: default_from`, or `action: alias`.
- `configs/templates.yaml` registers COMSOL template paths and explicit material/stress input target tags.
- `configs/extractors.yaml` registers study/evaluation tags.
- `configs/params/*.txt` stores COMSOL-style parameters. YAML is not used for the active runtime parameter source.
- `configs/calibration_space.yaml` stores optimizer bounds, priors, scales, and units.

Legacy compatibility files live under `archive/legacy_config_scheme/`.
The old layered `device_main/device_aux/mat1..mat4/onon_device` step files have been archived there. Active configs should use only the canonical v2 names: `pillar`, `sc`, `decap1`, `decap2`, `decap3`, `fecap`, `die`, `wafer`, and `onon`.

## Parameter Contract

For each step, the orchestrator copies parameter TXT files into the step output directory and records:

- `parameter_txt_paths`: actual staged TXT file paths.
- `parameter_txt_order`: load order, normally `global_params -> step params -> calibration_override`.

When no explicit `--params` file is supplied, each v2 step uses parameters parsed from its TXT stack. An explicit params YAML is treated as a trial/global override for calibration. Cache keys include staged TXT file content hashes, so COMSOL-only TXT changes invalidate cached step outputs.

TXT parsing rules:

1. Empty lines and `#` comments are ignored.
2. Each row is `name expression description...`.
3. COMSOL expressions such as `100[MPa]` and `120[nm]` are preserved for the worker.
4. Later files override earlier files with the same parameter name.

## RVE State Contract

Each `state_out.json` contains:

- `rve.pillar`
- `rve.sc`
- `rve.decap1` through `rve.decap3`
- `rve.fecap`
- `rve.die`
- `rve.onon`
- `wafer_result`
- `materials_state`
- `geometry_state`
- `history`

The die node consumes the configured decap/fecap inputs. The wafer node consumes only `rve.die` and `rve.onon`. Inherited nodes must already exist in `state_in` and have `valid=true`.

Each RVE keeps the full symmetric 6x6 `D` matrix in state for readability and downstream validation. Java-facing input payloads also include `D_upper21`, the 21-value upper-triangular representation, so `step_input.json` avoids duplicating mirrored stiffness entries at every input slot.

The Python validator rejects legacy node names in v2 configs, checks node actions, outputs, required inputs, template resolution, and same-step/upstream RVE references before dispatching a backend.

## Outputs

Each mock or COMSOL step should write:

```text
step_input.json
state_out.json
step_result.json
manifest.json
*_rve.json
wafer_result.json
logs/step.log
```

`logs/step.log` records the step id, model/template path, RVE source and target information, interface tags, node outputs, wafer inputs, and bow results. The COMSOL backend also appends the Java command and worker exit status.

Global calibration cache lives under `runs/<run-id>/.cache`. Staged calibration cache lives under `runs/<run-id>/out/<step>_<group>/.cache`.

## COMSOL Worker

`java/ComsolStepWorker.java` follows the active v2 contract: it loads TXT parameters in `parameter_txt_order`, runs or inherits nodes, writes configured node result files, writes `manifest.json`, preserves material/geometry/history state, and outputs the same high-level JSON shape as the mock backend.

For each expanded input slot, Python passes:

- scalar RVE values: `rho`, `sxx`, and `syy`
- stiffness values: full `D` plus compact `D_upper21`
- `target.material_tag`, `target.property_group`, `target.density_field`, and `target.stiffness_field`
- `target.stress_physics_tag`, `target.stress_feature_tag`, `target.sxx_field`, and `target.syy_field`

The Java skeleton maps these fields to COMSOL material/property groups and initial stress features instead of maintaining separate legacy name maps. Real model paths and tags in `configs/templates.yaml` are still provisional and should be replaced with the exported COMSOL 6.3 tags before production calibration.

## Design Notes

- `comsol_20step_warpage_dev_spec.md` is the source process note for the v2 flow.
- `docs/superpowers/specs/2026-06-06-comsol-v2-rve-interface-design.md` records the RVE/interface design decisions.
- `docs/superpowers/plans/2026-06-06-comsol-v2-rve-interface.md` records the implementation plan used for this update.
- `docs/roadmap.md` tracks remaining work, especially real COMSOL tag validation and checkpointed staged reruns.
