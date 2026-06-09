# COMSOL RVE Parameter TXT Simplification Plan

## Summary

COMSOL models now read RVE effective density, residual stress, and stiffness values from model parameters. The Python/Java orchestration should therefore transfer RVE data through generated COMSOL parameter TXT files, not by writing COMSOL material/property/physics feature tags through Java.

The active flow remains the v2 20-step DAG. The interface between levels changes to:

```text
state_out.json
  -> generated inputs/<node>_<slot>.txt
  -> Java model.param().loadFile(...)
  -> COMSOL study run and model-exported outputs/*.txt
  -> state_out.json for the next step
```

## Parameter TXT Contract

Runtime RVE input files use slot-prefixed variable names. For an input slot such as `pillar`, Python writes:

```text
rve_pillar_rho
rve_pillar_sxx
rve_pillar_syy
rve_pillar_D11 ... rve_pillar_D66
```

Only the 21 upper-triangular stiffness values are written:

```text
11, 12, 13, 14, 15, 16,
22, 23, 24, 25, 26,
33, 34, 35, 36,
44, 45, 46,
55, 56,
66
```

`configs/params/rve_transfer_template.txt` documents this default variable set. If a real COMSOL template uses different variable names, update the template/mapping layer while keeping the txt-driven transfer pattern.

COMSOL output TXT files use node-local result names:

```text
rho
sxx
syy
D11 ... D66
```

Wafer output TXT files use:

```text
bow_x_um
bow_y_um
kx
ky
```

## Implementation Shape

- `configs/templates.yaml` should carry model path, node type, study tags, and output txt/json names only.
- `step_input.json` should contain `input_parameter_txt_paths` and `output_txt_path` per run.
- `logs/interface.json` should audit txt file paths, input slot sources, variable prefixes, model path, study tags, and result file paths.
- Java should only load parameter txt files, run studies, parse output txt files, and write JSON outputs.
- Python remains responsible for state, cache keys, summaries, calibration, and generating upstream RVE input txt files.

## Removed Complexity

The active flow no longer needs COMSOL material tags, property groups, elastic group/property names, physics tags, initial-stress feature tags, extractor registries, or fail-fast checks for unresolved `TODO_*` COMSOL tag placeholders.

## Implementation Status

Implemented in the current branch:

- Added `python/comsol_opt/rve_txt.py` for RVE input TXT generation and output TXT parsing.
- Added `configs/params/rve_transfer_template.txt` as the canonical default variable-name fixture.
- Simplified `configs/templates.yaml` to model paths, node type, study tags, output TXT names, and result JSON names.
- Changed `step_input.json` to use `input_parameter_txt_paths` and `output_txt_path` instead of inline RVE payloads.
- Updated the mock backend to write generated RVE input TXT files, output TXT files, `logs/step.log`, and `logs/interface.json`.
- Updated `java/ComsolStepWorker.java` to load parameter TXT files, write same-step RVE input TXT files, run studies, parse COMSOL-exported TXT files, and write v2 state JSON.
- Archived `configs/extractors.yaml` under `archive/legacy_config_scheme/`.
- Removed active runtime support for old tag/extractor-driven RVE injection, old `parameter_map` runtime compatibility, old `device_rves` / `wafer_inputs` state aliases, and old non-v2 mock flow logic.

## Open Improvements

1. Multi-component / multi-study execution contract.
   Some COMSOL models contain multiple components and multiple studies. Add an explicit run sequence to `configs/templates.yaml`, for example:

   ```yaml
   run_sequence:
     - component: comp6
       study: std3
       kind: stress
     - component: comp6
       study: solid6cp1std
       kind: cp
   ```

   The Java worker should execute exactly this sequence, log each component/study pair, and fail fast when a requested study is missing. The current `studies.stress/cp` form can remain as shorthand for simple single-component templates.

2. Real COMSOL output TXT validation.
   Confirm that each real `.mph` exports `rho/sxx/syy/D11...D66` or `bow_x_um/bow_y_um/kx/ky` to the configured `output_txt_path`.

3. Java parser hardening.
   Replace the current regex JSON parsing skeleton with a small JSON library or generated parser before production COMSOL runs.

4. Calibration metadata persistence.
   Add optimizer metadata per stage with sampler name, seed, bounds, selected parameters, cache path, and code/config hash.

## Validation

The acceptance test is:

```bash
.venv/bin/python -m unittest tests/test_workflow.py -v
```

The suite should pass with no failures and no skipped tests. The mock backend should generate both input and output TXT files so the runtime directory mirrors the real COMSOL handoff.
