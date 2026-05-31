# comsol_opt

Automation framework for a multi-scale COMSOL wafer-warpage flow.

The first implementation focuses on the Python orchestration layer:

- Generate per-step `step_input.json` files from `configs/flow.yaml`.
- Maintain `state_out.json` across S00-S10 with inherited RVE results.
- Generate wafer input inheritance from the top-level `wafer_slots` list, so each step only declares changed slots.
- Update three COMSOL parameter text files before each run:
  - `struct.txt`
  - `stress.txt`
  - `temp.txt`
- Run `dryrun`, `mock`, or externally configured `comsol` backends.
- Emit `summary.csv` and compute bow loss against `exp/bow_experiment.csv`.
- Run a calibration loop with Optuna when installed, otherwise a deterministic random fallback.

## Quick Start

Create and populate a local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

```bash
.venv/bin/python python/run_flow.py --backend dryrun --run-id dryrun_001
.venv/bin/python python/run_flow.py --backend mock --run-id mock_001
.venv/bin/python python/compute_loss.py --summary runs/mock_001/summary.csv
.venv/bin/python python/calibration_optuna.py --backend mock --run-id calib_001 --n-trials 5
```

Run staged calibration for selected process steps/groups:

```bash
.venv/bin/python python/calibration_optuna.py \
  --backend mock \
  --mode staged \
  --stages G0_init,G1_ONON_base \
  --run-id staged_001 \
  --n-trials 5
```

Staged outputs are written under `runs/<run-id>/out/<step>_<group>/`, including `stage.log`, `calibration_history.csv`, `best_params.yaml`, `best_summary.csv`, and each trial's full mock/COMSOL flow output.

Each staged calibration group uses a shared cache at `runs/<run-id>/out/<step>_<group>/.cache`. Global calibration uses `runs/<run-id>/.cache`. This lets repeated trial steps reuse prior `state_out.json` and `step_result.json` when the step input and inherited state are identical.

## Flow Configuration

`configs/flow.yaml` declares all wafer slots once in `wafer_slots`. Each process step lists only `wafer_inputs.update`; the Python layer computes `wafer_inputs.inherit` as every slot not updated by that step.

`run_wafer: false` means the backend should skip the wafer solve, preserve the incoming `wafer_result`, and mark `step_result.json` with `wafer_skipped: true`. This is useful for future RVE-only steps.

## COMSOL Parameters

The Python layer treats the three parameter txt files as the write boundary for COMSOL. For every step/trial, it writes fresh copies under that step directory and records them in `step_input.json` as `parameter_txt_paths`.

The Java worker should load all three paths from `parameter_txt_paths` before solving the device, mat, or wafer model.

`configs/parameter_map.yaml` is the source of truth for routing Python parameter keys into COMSOL txt files. `configs/calibration_space.yaml` is the source of truth for optimizer bounds. In calibration space, `prior` is the nominal or expected value, `scale` normalizes distance from that prior for regularization, and `unit` documents/reviews the range.

## Assumed COMSOL Tags

Initial assumed tags live in `configs/template_tags.yaml`:

- study: `std1`
- stress global evaluation: `gev1`
- density global evaluation: `gev_rho`
- stiffness matrix group: `solidcp2stdEg`
- stiffness matrix evaluation: `gmevescp2`
- wafer bow evaluation: `gev_bow`

These are intentionally config-driven so exported Java scripts can replace them without changing Python orchestration code.

## COMSOL Java Worker

`java/ComsolStepWorker.java` is a first-pass worker skeleton. The intended COMSOL 6.3 style workflow is:

```bash
comsolcompile java/ComsolStepWorker.java
comsolbatch -inputfile ComsolStepWorker.class -args runs/<run-id>/<step>/step_input.json
```

Depending on the local COMSOL installation, the exact command wrapper may need the full `comsol` binary path and platform-specific flags. The worker reads `step_input.json`, loads the MPH templates with `ModelUtil.load(...)`, loads `struct.txt`, `stress.txt`, and `temp.txt` via `model.param().loadFile(...)`, runs `study("std1")`, extracts `gev1`/`gev_rho`/`gmevescp2`/`gev_bow`, and writes `step_result.json` plus `state_out.json`.

The Java worker currently respects `run_devices`, `run_mats`, and `run_wafer`, but it is still a first-pass skeleton. See `docs/roadmap.md` for the remaining COMSOL integration work.
