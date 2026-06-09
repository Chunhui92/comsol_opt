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
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest tests/test_workflow.py -v
.venv/bin/python python/run_flow.py --backend dryrun --run-id dryrun_001
.venv/bin/python python/run_flow.py --backend mock --run-id mock_001
.venv/bin/python python/compute_loss.py --summary runs/mock_001/summary.csv
.venv/bin/python python/calibration_optuna.py --backend mock --run-id calib_001 --n-trials 5
```

Run selected staged calibration:

```bash
.venv/bin/python python/calibration_optuna.py \
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
├── calibration_space.yaml
├── steps/
├── params/
└── experiments/
```

- `configs/flow.yaml` is the v2 entrypoint. It lists the enabled S01-S20 step files, parameter TXT load order, and the ONON alias source.
- `configs/steps/*.yaml` defines each process step in shorthand form. Most steps only list `run:` template changes; Python expands fixed-chain dependencies, implicit inheritance, ONON inputs, die, and wafer. Repeated chains can use `preset: full_chain`, and the three decap branches can be written once as `decap: <family>`.
- `configs/templates.yaml` registers COMSOL template paths, study tags, and output TXT/result filenames. Repeated model variants can be declared under `template_families`; Python expands them into ordinary template specs at load time.
- `configs/params/*.txt` stores active COMSOL-style parameters. YAML is not used for the active runtime parameter source.
- `configs/params/rve_transfer_template.txt` documents generated RVE transfer variable names.
- `configs/calibration_space.yaml` stores optimizer bounds, priors, scales, and units.

Legacy compatibility files live under `archive/legacy_config_scheme/`.
The old layered `device_main/device_aux/mat1..mat4/onon_device` step files have been archived there as reference material only. Active configs should use only the canonical v2 names: `pillar`, `sc`, `decap1`, `decap2`, `decap3`, `fecap`, `die`, `wafer`, and `onon`.

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

The v2 chain uses ONON as a global RVE input after S02:

- `decap1`, `decap2`, and `decap3` consume `pillar + onon`.
- `fecap` consumes `pillar + sc + onon`.
- `die` consumes `decap1 + decap2 + decap3 + fecap + onon`.
- `wafer` consumes `die + onon`.

Inherited nodes must already exist in `state_in` and have `valid=true`.

Each RVE keeps the full symmetric 6x6 `D` matrix in state for readability and downstream validation. Runtime RVE transfer TXT files carry only the 21 upper-triangular stiffness entries using `rve_<slot>_D11 ... rve_<slot>_D66`.

The Python validator rejects legacy node names in v2 configs, checks node actions, outputs, required inputs, template resolution, and same-step/upstream RVE references before dispatching a backend.

## Outputs

Each step input uses a compact execution contract:

- `template_registry`: source template registry path.
- `template_specs`: template specs used by this step, de-duplicated by template key.
- `runs`: compact execution plan. Each run lists `node`, `template`, input source names, generated input parameter TXT paths, and output TXT path.

Active step YAML files intentionally avoid repeating the full chain. For example:

```yaml
step_id: S04
step_name: sc_etch
run:
  sc: p03_SC_form
  fecap: fecap_model_all
```

The loader expands this into the needed execution plan: `sc`, `fecap`, `die`, and `wafer`; unchanged RVE state is carried forward implicitly from `state_in`.

For steps that rerun the standard pillar -> decap/fecap -> die -> wafer chain, use:

```yaml
step_id: S06
step_name: pillar_etch
preset: full_chain
run:
  pillar: p04p01_pillar_etch
```

This expands `decap1/2/3` from `decap_model`, `fecap` from `fecap_model_all`, and then appends `die` and `wafer`. For one-off decap groups, use `decap: p03_cap_DTI` or `decap: decap_model`; the loader maps that to `<family>_decap1`, `<family>_decap2`, and `<family>_decap3`.

Each mock or COMSOL step should write:

```text
step_input.json
state_out.json
step_result.json
manifest.json
*_rve.json
wafer_result.json
logs/step.log
logs/interface.json
```

`logs/step.log` records the step id, model/template path, RVE source, parameter TXT paths, node outputs, wafer inputs, and bow results. `logs/interface.json` is the machine-readable version of the same interface audit: run node, model path, studies, input RVE source, generated parameter TXT, output TXT, and result file. The default location is `runs/<run-id>/<step-id>/logs/`; `python/run_flow.py` prints the latest step log path after mock/COMSOL runs. Unit tests write these logs under temporary directories, so test logs disappear when the test process exits.

Global calibration cache lives under `runs/<run-id>/.cache`. Staged calibration cache lives under `runs/<run-id>/out/<step>_<group>/.cache`.

## COMSOL Worker

`java/ComsolStepWorker.java` follows the active txt-driven v2 contract: it loads process parameter TXT files and generated RVE input TXT files, runs studies, reads COMSOL-exported result TXT files, writes configured node result files, writes `manifest.json`, preserves material/geometry/history state, and outputs the same high-level JSON shape as the mock backend.

Java does not write COMSOL material, property-group, physics, or stress-feature tags. Those links now live inside each COMSOL model through parameter references.

## Design Notes

- `comsol_20step_warpage_dev_spec.md` is the source process note for the v2 flow.
- `docs/comsol_parameter_txt_simplification_plan.md` records the txt-driven RVE transfer design.
- `archive/project_notes/2026-06-06-comsol-v2-rve-interface-design.md` records the archived RVE/interface design decisions.
- `archive/project_notes/2026-06-06-comsol-v2-rve-interface-plan.md` records the archived implementation plan used for the v2 update.
- `docs/roadmap.md` tracks remaining work, especially real COMSOL params/output TXT validation and checkpointed staged reruns.
