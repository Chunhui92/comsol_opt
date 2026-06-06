# AGENTS.md

## Project Summary

This repository implements the Python orchestration layer for a multi-scale COMSOL wafer-warpage calibration workflow.

The active flow is the v2 20-step process chain:

```text
process step S01..S20
  -> pillar / sc RVEs
  -> decap1 / decap2 / decap3 / fecap RVEs
  -> die RVE
  -> wafer model
  -> bow_x_um / bow_y_um comparison
  -> staged calibration
```

The Python layer owns config expansion, parameter TXT staging, state transfer, backend dispatch, mock validation, runtime logs, summary/loss calculation, and calibration. COMSOL model geometry, studies, extractor tags, and material/stress interface tags are expected to be provided by COMSOL templates and registered in config.

Current implementation status:

- `configs/flow.yaml` is a v2 20-step entrypoint. It lists S01-S20 step files, shared parameter TXT order, template registry path, and the global ONON alias source.
- Active step configs live in `configs/steps/S01_*.yaml` through `S20_*.yaml`.
- Old v1 layered step configs were moved to `archive/legacy_config_scheme/steps/`.
- The Python validator rejects legacy runtime node names in v2 configs.
- The mock backend supports v2 `run`, `inherit`, `default_from`, and `alias` actions, including same-step working-state references and stress-only D inheritance.
- The COMSOL backend writes worker dispatch/status information into `logs/step.log`.
- `java/ComsolStepWorker.java` is a first-pass COMSOL 6.3 skeleton for expanded RVE inputs, material target writes, stress target writes, and `D_upper21` handling. It still needs real COMSOL export/tag validation before production use.

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
python3 python/calibration_optuna.py --backend mock --mode staged --stages G2_trench_release,G10_final --run-id staged_001 --n-trials 5
```

## Repository Layout

- `README.md`: active project overview.
- `AGENTS.md`: project-specific instructions for coding agents.
- `configs/flow.yaml`: active v2 flow entrypoint.
- `configs/steps/`: active v2 S01-S20 step configs. These use shorthand `run:` declarations, `decap` groups, and `preset: full_chain`; do not re-expand them into full DAG YAML unless debugging.
- `configs/templates.yaml`: COMSOL template registry plus material/stress input target tags. Repeated variants may live under `template_families`; loader expansion must still produce ordinary `templates` entries for validation/backends. Values copied from `comsol_20step_warpage_dev_spec.md` should be treated as authoritative; unresolved values are marked `TODO_*`.
- `configs/extractors.yaml`: study/evaluation/extractor tag registry.
- `configs/params/`: active COMSOL parameter TXT files merged in runtime order.
- `configs/calibration_space.yaml`: calibration bounds, priors, scales, units, and target steps.
- `configs/experiments/`: experiment bow data used by summaries and loss.
- `archive/legacy_config_scheme/`: old v1 configs, parameter maps, tags, docs, archived step files, and old local step parameter TXT files.
- `archive/project_notes/`: archived implementation notes and plans that are no longer active project entrypoints.
- `python/run_flow.py`: flow orchestrator CLI.
- `python/calibration_optuna.py`: calibration CLI with optional Optuna and deterministic fallback.
- `python/comsol_opt/`: orchestration package.
- `python/comsol_opt/v2_contract.py`: canonical v2 node names and RVE payload helpers.
- `java/ComsolStepWorker.java`: first-pass COMSOL Java worker skeleton.
- `tests/test_workflow.py`: unittest coverage for config loading, validation, mock flow, logging, loss, and calibration.
- `docs/roadmap.md`: current roadmap and known remaining work.
- `archive/project_notes/2026-06-06-comsol-v2-rve-interface-design.md`: archived v2 RVE/interface design notes.
- `archive/project_notes/2026-06-06-comsol-v2-rve-interface-plan.md`: archived implementation plan used for the v2 update.
- `comsol_20step_warpage_dev_spec.md`: source process note; it contains historical terminology, so active code/config should defer to the canonical v2 names below.

## Canonical Names

Use these runtime node names everywhere in active Python code, active YAML, summaries, logs, and new tests:

- `pillar`
- `sc`
- `decap1`
- `decap2`
- `decap3`
- `fecap`
- `die`
- `wafer`
- `onon`

Do not introduce active runtime mappings for legacy names such as `device_main`, `device_aux`, `onon_device`, `mat1`, `mat2`, `mat3`, or `mat4`. Those may appear only in archived docs/configs or in tests that intentionally verify legacy-name rejection.

Historical mapping:

- `mat1 -> decap1`
- `mat2 -> decap2`
- `mat3 -> decap3`
- `mat4 -> fecap`
- `onon_device -> onon`

## COMSOL Parameter Contract

The Python flow copies COMSOL parameter TXT files into each step directory.
The load order is recorded in `step_input.json` as `parameter_txt_order`, and paths are recorded under `parameter_txt_paths`.
A real COMSOL Java worker should load these files in order before solving any model.

When no explicit `--params` file is supplied, each v2 step uses parameters parsed from its TXT stack.
When calibration supplies a params YAML, that file is treated as the trial/global override for enabled steps.
Step cache keys include staged parameter TXT content hashes, not just mapped Python parameters.

Do not hard-code process/material parameter values in Java. Treat TXT files as the runtime parameter source.

Keep calibration bounds, `prior`, `scale`, and `unit` in `configs/calibration_space.yaml`.
`prior` is the nominal value; `scale` is the denominator for regularization against that prior.
`archive/legacy_config_scheme/parameter_map.yaml` remains only for the legacy three-file parameter writer. Old local step TXT files belong under `archive/legacy_config_scheme/params/`; keep `configs/params/` limited to files referenced by the active v2 flow.

## RVE and State Rules

- `decap1`, `decap2`, `decap3`, and `fecap` are parallel die inputs, not a serial chain.
- `pillar` normally feeds `decap1`, `decap2`, `decap3`, and `fecap`.
- `sc` feeds `fecap` when the fecap template declares an `sc` input.
- `decap1`, `decap2`, and `decap3` consume `pillar + onon`.
- `fecap` consumes `pillar + sc + onon`.
- `die` consumes `decap1 + decap2 + decap3 + fecap + onon`.
- `wafer` consumes `rve.die + rve.onon` unless a real wafer template explicitly requires more.
- `onon` is an alias/snapshot, not a standalone COMSOL run node. The default source is configured in `configs/flow.yaml` as S02 `pillar`.
- Each active step node must explicitly declare `action: run`, `action: inherit`, `action: default_from`, or `action: alias`.
- Inherited nodes must exist in the prior state under `rve.<node>` and have `valid=true`.
- In v2, wafer is expected to run every enabled step.
- If a model is stress-only and has no CP/D extractor for the current step, update `rho` and `stress_eff`, then inherit `D` from the previous valid same-node RVE.
- Active step YAML normally uses shorthand `run:`. The loader expands it into run/default/alias nodes and treats unchanged nodes as implicit inheritance via the copied `state_in`.
- Use `decap: <family>` to run all three decap branches. The loader expands it to `<family>_decap1`, `<family>_decap2`, and `<family>_decap3`.
- Use `preset: full_chain` for repeated pillar -> decap/fecap -> die -> wafer steps. Step-local `run:` entries override preset defaults.

## D Matrix and Java Input Rules

Each RVE state keeps the full symmetric 6x6 `D` matrix for readability and validation.
The compact Java-facing `step_input.json` contract should use:

- `template_registry`: source template registry path.
- `template_specs`: only the template specs used by this step, keyed by template name.
- `rve_inputs`: upstream RVE payloads used by this step, keyed once by source name.
- `runs`: compact execution plan with `node`, `template`, and `inputs`.

RVE payloads in `rve_inputs` include:

- `rho`
- `sxx`
- `syy`
- `D`
- `D_upper21`
- `symmetric_upper21`

`D_upper21` is the compact 21-value upper-triangular representation of the symmetric 6x6 matrix. Use helpers in `python/comsol_opt/v2_contract.py` instead of open-coded matrix flattening.

For COMSOL 6.3 Java work:

- Load model templates from `template_specs` or `configs/templates.yaml`.
- Write `rho` into the configured material density field.
- Write `D_upper21` into the configured anisotropic material stiffness field using the template's expected ordering.
- Write `sxx` and `syy` into the configured initial-stress feature/tag.
- Do not maintain separate Python-side alias maps for material names, model names, or bow names. Put real COMSOL tags in `configs/templates.yaml`.
- The cap/fecap component, physics, stress-study, and CP-study tags available in `comsol_20step_warpage_dev_spec.md` are already written into `configs/templates.yaml`.
- Treat every `TODO_*` field as unresolved. The COMSOL backend must fail fast before invoking Java if any `TODO_*` value remains in `step_input.json`.

## Runtime Logs

Every mock or COMSOL step should write:

```text
logs/step.log
logs/interface.json
```

The human log should be useful for auditing the full simulation path. Include the step id/name, backend, model/template path, node action, RVE source, input slots, material/stress target tags, result files, wafer inputs, and bow output.

`logs/interface.json` is the machine-readable interface audit. Keep it focused on COMSOL wiring: run node, template, model path, studies, extractors, input RVE source/source step/source node, material target, stress target, and result file. Do not store full RVE matrices there unless debugging a specific failure.

The default location is `runs/<run-id>/<step-id>/logs/step.log`. Unit tests use temporary run roots, so test logs are normally deleted when tests finish. The COMSOL backend should also log the Java command, worker status, unresolved `TODO_*` blocks, and any worker failure details.

## Backend Notes

Backends share this interface:

```python
SimulationBackend.run_step(step_input_path) -> dict
```

Available backends:

- `dryrun`: validates and writes `step_input.json` only.
- `mock`: runs deterministic Python mock physics and writes `state_out.json`, `step_result.json`, `manifest.json`, node result files, wafer result, `logs/step.log`, and `logs/interface.json`.
- `comsol`: invokes the Java COMSOL worker. The current Java file follows the v2 input contract but still needs real COMSOL template/tag validation.

The Java COMSOL worker must output the same high-level JSON shape as the mock backend.

Staged calibration writes under `runs/<run-id>/out/<step>_<group>/`. Each stage must contain `stage.log`, per-trial flow outputs, `calibration_history.csv`, `best_params.yaml`, and `best_summary.csv`. Later stages must start from the previous stage's best params.

Stage trials share cache entries through `runs/<run-id>/out/<step>_<group>/.cache`. Global calibration uses `runs/<run-id>/.cache`. Do not put caches under individual trial flow directories unless deliberately debugging cache isolation.

Default calibration only uses groups whose `target_step` is enabled in `configs/flow.yaml`.
If a user explicitly requests a stage targeting a disabled or missing step, fail early instead of silently sampling unused parameters.

## Current Roadmap

Keep `docs/roadmap.md` current when changing workflow scope.

Largest remaining items:

- Validate real COMSOL 6.3 model paths, studies, material tags, stress tags, and extractor tags.
- Replace provisional tags in `configs/templates.yaml` with tags exported from real COMSOL models.
- Harden `java/ComsolStepWorker.java` parsing and compile/runtime behavior against COMSOL 6.3.
- Add calibration COMSOL command plumbing where still missing.
- Add start/stop checkpointed staged reruns.
- Add Java worker execution coverage once CI/local tooling has Java/COMSOL stubs.

## Development Rules

- Use standard library compatibility where practical. Tests currently do not require `pytest`.
- Project configuration files use real YAML syntax. `config_io` prefers `PyYAML` and falls back to `ruamel.yaml` when available.
- If `Optuna` is absent, calibration falls back to deterministic random search.
- Before reporting completion, run `python3 -m unittest tests/test_workflow.py -v`.
- Use `dump_data` for new JSON/YAML writes. `dump_json` remains only as a compatibility alias.
- Keep generated run outputs under `runs/` or `/private/tmp`; do not commit generated run directories.
- Keep `.serena/`, caches, bytecode, and local tooling artifacts out of commits.
- Update `README.md`, `AGENTS.md`, and `docs/roadmap.md` when changing workflow scope or backend contracts.
