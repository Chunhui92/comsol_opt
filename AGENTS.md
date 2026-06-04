# AGENTS.md

## Project Summary

This repository implements the Python orchestration layer for a multi-scale COMSOL wafer-warpage calibration workflow.

The intended flow is:

```text
process step
  -> device_main/device_aux/onon_device RVEs
  -> parallel mat1/mat2/mat3/mat4 RVEs
  -> die RVE
  -> wafer model
  -> bow_x/bow_y comparison
  -> staged calibration
```

COMSOL model geometry, studies, and evaluation tags are assumed to be stable. The Python layer owns state transfer, parameter file generation, backend dispatch, mock validation, loss calculation, and calibration.

Current implementation status:

- The Python orchestrator and mock backend follow the layered step DAG in `configs/steps/*.yaml`: each node reads its declared template/input references, stages parameter TXT files, runs or inherits, writes node result files, updates `state_out.json`, and finally runs wafer after die.
- The Java COMSOL worker is still a legacy first-pass skeleton. It must be updated to parse the active `nodes` contract, honor `run` / `inherit`, load `parameter_txt_order`, write `manifest.json`, and write each node's configured `result_file`.
- Real COMSOL RVE transfer is not implemented yet in Java. The worker still needs to read upstream `rve.*` results from `state_in.json`, inject those effective stress/stiffness/density values into downstream COMSOL models, then extract and persist the next RVE before advancing to die and wafer.

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
python3 python/calibration_optuna.py --backend mock --mode staged --stages G0_init,G2_trench_release --run-id staged_001 --n-trials 5
```

## Repository Layout

- `configs/flow.yaml`: layered DAG entrypoint; references enabled per-step YAML files.
- `configs/steps/`: per-step DAG configs with explicit node `run` / `inherit` actions.
- `configs/templates.yaml`: COMSOL template registry.
- `configs/extractors.yaml`: study/evaluation/extractor tag registry.
- `configs/params/`: COMSOL parameter TXT files merged in runtime order.
- `configs/calibration_space.yaml`: parameter bounds for staged/global calibration.
- `configs/experiments/`: experiment bow data for summaries and loss.
- `archive/legacy_config_scheme/`: old nominal YAML, parameter map, template tags, and three-file TXT parameter scheme.
- `python/run_flow.py`: flow orchestrator CLI.
- `python/calibration_optuna.py`: calibration CLI with optional Optuna and deterministic fallback.
- `python/comsol_opt/`: orchestration package.
- `java/ComsolStepWorker.java`: first-pass COMSOL Java worker skeleton.
- `tests/test_workflow.py`: unittest coverage for parameter txt updates, state inheritance, mock flow, and calibration.

## COMSOL Parameter Contract

The layered Python flow copies COMSOL parameter TXT files into each step directory.
The load order is recorded in `step_input.json` as `parameter_txt_order`, and paths are recorded under `parameter_txt_paths`.
A real COMSOL Java worker should load these files in order before solving any device, mat, die, or wafer model.

Do not hard-code parameter values in Java. Treat the txt files as the runtime parameter source.

Keep calibration bounds, `prior`, `scale`, and `unit` in `configs/calibration_space.yaml`.
`prior` is the nominal value; `scale` is the denominator for regularization against that prior.
`archive/legacy_config_scheme/parameter_map.yaml` remains for the legacy three-file parameter writer.

## RVE and State Rules

- `mat1`, `mat2`, `mat3`, and `mat4` are parallel die inputs, not a serial chain.
- `device_main` normally feeds mat1/mat2/mat3; `device_aux` normally feeds mat4.
- `die` receives mat1~4. `wafer` receives only `rve.die` and `rve.onon_device`.
- Each node in `configs/steps/*.yaml` must explicitly declare `action: run` or `action: inherit`.
- Inherited nodes must exist in the previous `state_out.json` under `rve.<node>` and must have `valid=true`.
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
- `mock`: runs deterministic Python mock physics and writes `state_out.json`, `step_result.json`, `manifest.json`, and node result files.
- `comsol`: reserved for invoking the Java COMSOL worker. The current Java file is not yet equivalent to the active DAG mock backend.

The Java COMSOL worker must output the same JSON shape as the mock backend.

Staged calibration writes under `runs/<run-id>/out/<step>_<group>/`. Each stage must contain `stage.log`, per-trial flow outputs, `calibration_history.csv`, `best_params.yaml`, and `best_summary.csv`. Later stages must start from the previous stage's best params.

Stage trials share cache entries through `runs/<run-id>/out/<step>_<group>/.cache`. Global calibration uses `runs/<run-id>/.cache`. Do not put caches under individual trial flow directories unless deliberately debugging cache isolation.

## Current Roadmap

Keep `docs/roadmap.md` current when changing workflow scope. The largest remaining items are Java DAG worker parity with the mock backend, real COMSOL RVE injection between node models, remaining real step configs, and start/stop checkpointed staged reruns.

## Development Rules

- Use standard library compatibility where practical. Tests currently do not require `pytest`.
- Project configuration files use real YAML syntax. `config_io` prefers `PyYAML` and falls back to `ruamel.yaml` when available.
- If `Optuna` is absent, calibration falls back to deterministic random search.
- Before reporting completion, run `python3 -m unittest tests/test_workflow.py -v`.
- Use `dump_data` for new JSON/YAML writes. `dump_json` remains only as a compatibility alias.
- Keep generated run outputs under `runs/` or `/private/tmp`; do not commit generated run directories.
