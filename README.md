# comsol_opt

Python orchestration for a multi-scale COMSOL wafer-warpage calibration flow.

The active flow is configured as a layered DAG:

```text
process step
  -> device_main / device_aux / onon_device RVEs
  -> mat1 / mat2 / mat3 / mat4 RVEs
  -> die RVE
  -> wafer model
  -> bow_x / bow_y comparison
  -> staged calibration
```

The Python layer owns config expansion, parameter TXT staging, state inheritance, backend dispatch, mock validation, loss calculation, and calibration. A real COMSOL worker should consume only `step_input.json` plus the parameter TXT files referenced there.

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
  --stages G0_init,G2_trench_release \
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

- `configs/flow.yaml` is the global entrypoint. It only lists enabled process steps and global config files.
- `configs/steps/*.yaml` defines each step DAG. Every node must explicitly declare `action: run` or `action: inherit`.
- `configs/templates.yaml` registers COMSOL template paths.
- `configs/extractors.yaml` registers study/evaluation tags.
- `configs/params/*.txt` stores COMSOL-style parameters. YAML is not used for the active runtime parameter source.
- `configs/calibration_space.yaml` stores optimizer bounds, priors, scales, and units.

Legacy compatibility files live under `archive/legacy_config_scheme/`.
The active seed flow currently enables S00 and S02. `configs/calibration_space.yaml` can keep planned later stages, but default calibration only selects groups whose target step is enabled in `configs/flow.yaml`.

## Parameter Contract

For each step, the orchestrator copies parameter TXT files into the step output directory and records:

- `parameter_txt_paths`: actual staged TXT file paths.
- `parameter_txt_order`: load order, normally `global_params -> step params -> calibration_override`.

When no explicit `--params` file is supplied, each layered DAG step uses parameters parsed from its own TXT stack. An explicit params YAML is treated as a trial/global override for calibration. Cache keys include staged TXT file content hashes, so COMSOL-only TXT changes invalidate cached step outputs.

TXT parsing rules:

1. Empty lines and `#` comments are ignored.
2. Each row is `name expression description...`.
3. COMSOL expressions such as `100[MPa]` and `120[nm]` are preserved for the worker.
4. Later files override earlier files with the same parameter name.

## DAG State Contract

Each `state_out.json` contains:

- `rve.device_main`
- `rve.device_aux`
- `rve.onon_device`
- `rve.mat1` through `rve.mat4`
- `rve.die`
- `wafer_result`
- `materials_state`
- `geometry_state`
- `history`

The wafer node consumes only `rve.die` and `rve.onon_device`. The die node consumes only mat1 through mat4. Inherited nodes must already exist in `state_in` and have `valid=true`.

The Python validator checks DAG node actions, types, outputs, required inputs, template resolution, and topological RVE references before dispatching a backend.

## Outputs

Each mock or COMSOL step should write:

```text
step_input.json
state_out.json
step_result.json
manifest.json
*_rve.json
wafer_result.json
```

Global calibration cache lives under `runs/<run-id>/.cache`. Staged calibration cache lives under `runs/<run-id>/out/<step>_<group>/.cache`.

## COMSOL Worker

`java/ComsolStepWorker.java` follows the active `nodes` contract: it loads TXT parameters in `parameter_txt_order`, runs or inherits DAG nodes, writes configured node result files, writes `manifest.json`, preserves material/geometry/history state, and outputs the same high-level JSON shape as the mock backend.

The worker still uses provisional RVE transfer assumptions. It injects upstream values as `input_<slot>_sxx`, `input_<slot>_syy`, `input_<slot>_rho`, `input_<slot>_d11`, and `input_<slot>_d22`. Validate or replace these names against the real COMSOL templates before production calibration.
