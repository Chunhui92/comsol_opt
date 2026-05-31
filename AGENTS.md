# AGENTS.md

## Project Summary

This repository implements the Python orchestration layer for a multi-scale COMSOL wafer-warpage calibration workflow.

The intended flow is:

```text
process step
  -> device RVE
  -> parallel mat1/mat2/mat3 RVEs
  -> wafer model
  -> bow_x/bow_y comparison
  -> staged calibration
```

COMSOL model geometry, studies, and evaluation tags are assumed to be stable. The Python layer owns state transfer, parameter file generation, backend dispatch, mock validation, loss calculation, and calibration.

## Important Commands

Run all tests:

```bash
python3 -m unittest tests/test_workflow.py -v
```

Generate step inputs only:

```bash
python3 python/run_flow.py --backend dryrun --run-id dryrun_001
```

Run the full mock flow:

```bash
python3 python/run_flow.py --backend mock --run-id mock_001
```

Compute loss from a mock run:

```bash
python3 python/compute_loss.py --summary runs/mock_001/summary.csv
```

Run mock calibration:

```bash
python3 python/calibration_optuna.py --backend mock --run-id calib_001 --n-trials 5
```

Run selected staged calibration:

```bash
python3 python/calibration_optuna.py --backend mock --mode staged --stages G0_init,S01 --run-id staged_001 --n-trials 5
```

## Repository Layout

- `configs/flow.yaml`: S00-S10 process flow, RVE dependencies, wafer template selection, calibration groups.
- `configs/params_nominal.yaml`: nominal process/material/geometry parameters.
- `configs/calibration_space.yaml`: parameter bounds for staged/global calibration.
- `configs/parameter_map.yaml`: Python parameter key to COMSOL txt file/name mapping.
- `configs/template_tags.yaml`: assumed COMSOL study/evaluation tags.
- `params/struct.txt`: COMSOL structural parameter file.
- `params/stress.txt`: COMSOL stress/release parameter file.
- `params/temp.txt`: COMSOL temperature parameter file.
- `python/run_flow.py`: flow orchestrator CLI.
- `python/calibration_optuna.py`: calibration CLI with optional Optuna and deterministic fallback.
- `python/comsol_opt/`: orchestration package.
- `java/ComsolStepWorker.java`: first-pass COMSOL Java worker skeleton.
- `tests/test_workflow.py`: unittest coverage for parameter txt updates, state inheritance, mock flow, and calibration.

## COMSOL Parameter Contract

The Python layer writes three parameter txt files for each step/trial:

- `struct`
- `stress`
- `temp`

The paths are recorded in `step_input.json` under `parameter_txt_paths`. A real COMSOL Java worker should load all three files before solving any device, mat, or wafer model.

Do not hard-code parameter values in Java. Treat the txt files as the runtime parameter source.

Keep parameter routing in `configs/parameter_map.yaml`. Keep calibration bounds, `prior`, `scale`, and `unit` in `configs/calibration_space.yaml`. `prior` is the nominal value; `scale` is the denominator for regularization against that prior.

## RVE and State Rules

- `mat1`, `mat2`, and `mat3` are parallel wafer inputs, not a serial chain.
- `mat1` and `mat2` use `device_default`.
- `mat3` uses `device2_for_mat3`.
- If a step does not update a wafer input slot, inherit it from the previous `state_out.json`.
- `configs/flow.yaml` declares `wafer_slots` once; steps should normally list only `wafer_inputs.update`.
- `step_input.py` computes `wafer_inputs.inherit` from `wafer_slots` before writing `step_input.json`.
- If `run_wafer` is false, the backend should preserve the incoming `wafer_result`, skip history append, and set `wafer_skipped=true`.
- `ONON.sigma_O_base` and `ONON.sigma_N_base` are only set at S01.
- Later O/N changes use release factors only.
- S07 uses `wafer_with_asi_template.mph`.
- S08 switches back to `wafer_base_template.mph` and sets `aSi_layer.active=false`.

## Backend Notes

Backends share this interface:

```python
SimulationBackend.run_step(step_input_path) -> dict
```

Available backends:

- `dryrun`: validates and writes `step_input.json` only.
- `mock`: runs deterministic Python mock physics and writes `state_out.json` / `step_result.json`.
- `comsol`: reserved for invoking the Java COMSOL worker.

The Java COMSOL worker must output the same JSON shape as the mock backend.

Staged calibration writes under `runs/<run-id>/out/<step>_<group>/`. Each stage must contain `stage.log`, per-trial flow outputs, `calibration_history.csv`, `best_params.yaml`, and `best_summary.csv`. Later stages must start from the previous stage's best params.

Stage trials share cache entries through `runs/<run-id>/out/<step>_<group>/.cache`. Global calibration uses `runs/<run-id>/.cache`. Do not put caches under individual trial flow directories unless deliberately debugging cache isolation.

## Current Roadmap

Keep `docs/roadmap.md` current when changing workflow scope. The largest remaining items are COMSOL calibration command plumbing, complete Java worker state inheritance, and start/stop checkpointed staged reruns.

## Development Rules

- Use standard library compatibility where practical. Tests currently do not require `pytest`.
- `PyYAML` is required because project configuration files use real YAML syntax.
- If `Optuna` is absent, calibration falls back to deterministic random search.
- Before reporting completion, run `python3 -m unittest tests/test_workflow.py -v`.
- Use `dump_data` for new JSON/YAML writes. `dump_json` remains only as a compatibility alias.
- Keep generated run outputs under `runs/` or `/private/tmp`; do not commit generated run directories.
