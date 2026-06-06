# COMSOL V2 RVE Interface Design

> Status: draft for review
> Date: 2026-06-06
> Scope: 20-step wafer warpage flow interface contract, naming, and COMSOL Java transfer strategy

## Goal

Define the v2 contract for transferring homogenized RVE results between COMSOL models in the 20-step wafer warpage flow.

This design replaces the provisional `input_<slot>_rho`, `input_<slot>_sxx`, `input_<slot>_syy`, `input_<slot>_D11...D66` global-parameter interface with a template-driven COMSOL target contract. The Python layer remains responsible for orchestration, state inheritance, alias/default handling, and stress-only D inheritance. The Java worker is responsible only for applying configured values to the current COMSOL model and running/extracting configured studies.

## Design Principles

- Use one canonical naming vocabulary across YAML, Python state, Java input, result files, summary, and docs.
- Do not maintain parallel names such as `device`, `pillar`, `mat`, `decap`, and `fecap` for the same object.
- Keep full RVE state explicit and validated in Python.
- Compress Java input where the COMSOL contract naturally supports it.
- Do not hard-code COMSOL material, physics, study, feature, or extractor tags in Java.
- Prefer direct COMSOL material/physics feature assignment over global proxy parameters.

## Canonical Names

Use these node names everywhere:

| Canonical name | Meaning | COMSOL model? | Notes |
|---|---|---:|---|
| `pillar` | pillar/device RVE | yes | Replaces `device`, `device_main`, and related aliases. |
| `sc` | SC RVE | yes | Feeds `fecap`. |
| `decap1` | decap first branch 1 | yes | Replaces `mat1`. |
| `decap2` | decap second branch | yes | Replaces `mat2`. |
| `decap3` | decap first branch 3 | yes | Replaces `mat3`. |
| `fecap` | FE capacitor branch | yes | Replaces `mat4`. |
| `die` | die-level RVE | yes | Feeds wafer. |
| `wafer` | wafer bow model | yes | Runs every process step. |
| `onon` | wafer-level ONON RVE alias | no | Snapshot/alias of S02 `pillar`. |

Forbidden in new v2 configs and code paths:

- `device`
- `device_main`
- `device_aux`
- `onon_device`
- `mat1`
- `mat2`
- `mat3`
- `mat4`
- `cap1`
- `cap2`
- `cap3`
- `cap4`

The only acceptable use of those legacy names is inside archived fixtures or explicit migration code that converts old state into v2 state.

## Dependency Graph

```text
pillar -> decap1
pillar -> decap2
pillar -> decap3
pillar + sc -> fecap

decap1 + decap2 + decap3 + fecap -> die
die + onon -> wafer

onon = alias(snapshot(S02.pillar))
```

Step execution order is fixed:

```text
pillar, sc, decap1, decap2, decap3, fecap, die, wafer
```

Within one step, inputs must resolve from the current working state. If `sc` runs in S04, then S04 `fecap` must consume the S04 `sc`, not the previous step's `sc`.

## RVE State Schema

Python stores a complete, downstream-ready RVE:

```json
{
  "valid": true,
  "active": true,
  "status": "run",
  "source_step": "S06",
  "source_node": "decap1",
  "source_model": "decap_model_all.mph",
  "rho": 2330.0,
  "stress_eff": {
    "sxx": 1.2e8,
    "syy": -3.5e8
  },
  "D": [
    [1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
  ],
  "D_format": "full_6x6",
  "elasticity_order": "standard",
  "meta": {
    "component": "comp6",
    "physics": "solid6",
    "study_stress": "std3",
    "study_cp": "solid6cp1std"
  }
}
```

Rules:

- `stress_eff` contains only `sxx` and `syy`.
- Do not store or pass `szz`, `sxy`, `syz`, or `sxz`.
- Python validates `D` as a symmetric 6x6 numeric matrix.
- Python may serialize a compressed D representation into `step_input.json`, but `state_out.json` keeps full 6x6 for readability and validation.
- If a run node is stress-only and returns `D: null`, Python copies `D` from the previous valid state of the same canonical node and marks `status: run_stress_only`.

## D Matrix Transfer

COMSOL 6.3 treats the anisotropic solid mechanics elasticity matrix as a symmetric 6x6 matrix. For a fully anisotropic material, only 21 independent components are required. The Java API supports setting vector or matrix material properties with string arrays, and COMSOL documents that matrix values are handled in column-wise order.

Python should convert the full 6x6 state matrix to a symmetric upper-triangle payload for Java:

```json
{
  "D_format": "symmetric_upper21",
  "elasticity_order": "standard",
  "D_upper21": [
    "D11", "D12", "D13", "D14", "D15", "D16",
    "D22", "D23", "D24", "D25", "D26",
    "D33", "D34", "D35", "D36",
    "D44", "D45", "D46",
    "D55", "D56",
    "D66"
  ]
}
```

Use row-major upper-triangle naming in the JSON contract because it is clear for humans and validation. The Java worker converts this to the order required by the target COMSOL property.

Required config:

```yaml
elasticity_order: standard  # standard | voigt
D_format: symmetric_upper21
```

`standard` means COMSOL Standard material data ordering:

```text
11, 22, 33, 12, 23, 13
```

`voigt` means COMSOL Voigt material data ordering:

```text
11, 22, 33, 23, 13, 12
```

Do not infer this from the node name. It must come from the template config.

## COMSOL Target Contract

Each run node template declares where incoming RVE values should be written.

Example for `fecap`:

```yaml
inputs:
  pillar:
    ref: rve.pillar
    target:
      material:
        component: comp8
        tag: mat_pillar_eff
        density_group: def
        density_property: density
        elastic_group: anisotropic_eff
        elastic_property: D
        elasticity_order: standard
        D_format: symmetric_upper21
      stress:
        component: comp8
        physics: solid8
        parent_feature: lemm1
        feature: initstress_pillar
        property: S0
        stress_format: diag_sxx_syy

  sc:
    ref: rve.sc
    target:
      material:
        component: comp8
        tag: mat_sc_eff
        density_group: def
        density_property: density
        elastic_group: anisotropic_eff
        elastic_property: D
        elasticity_order: standard
        D_format: symmetric_upper21
      stress:
        component: comp8
        physics: solid8
        parent_feature: lemm1
        feature: initstress_sc
        property: S0
        stress_format: diag_sxx_syy
```

The template config must use real tags exported from the corresponding `.mph` model. Java must fail fast if a configured material, property group, physics, feature, study, or extractor tag is missing.

## Java Write Strategy

### Load Model And Parameters

The existing parameter TXT loading remains valid:

```java
Model model = ModelUtil.load("model_" + sanitize(node.id), node.templatePath);
for (Path path : input.parameterFiles) {
    model.param().loadFile(path.toString());
}
```

Official COMSOL 6.3 docs describe `model.param().set(...)` and `model.param().loadFile(...)` for global model parameters.

### Write Density

Density should be written to the configured material property group:

```java
model.component(componentTag)
     .material(materialTag)
     .propertyGroup(densityGroupTag)
     .set(densityPropertyName, new String[] { rhoExpr });
```

Default config values:

```yaml
density_group: def
density_property: density
```

COMSOL's material API uses `model.component(<ctag>).material(<tag>).propertyGroup(<mtag>).set(...)` for material properties, and the general physical quantity name for density is `density`.

### Write Elasticity Matrix D

The default v2 contract writes anisotropic solid mechanics elasticity:

```java
model.component(componentTag)
     .material(materialTag)
     .propertyGroup(elasticGroupTag)
     .set(elasticPropertyName, dExprsForComsol);
```

For most templates:

```yaml
elastic_property: D
```

For templates using COMSOL's Voigt-specific material property group:

```yaml
elastic_property: DV0
elasticity_order: voigt
```

The Java worker must:

1. Read `D_upper21`.
2. Reconstruct or reorder to the configured `elasticity_order`.
3. Write a `String[]` expression array with units, for example `1.23e11[Pa]`.
4. Record the actual property group and property name in `manifest.json`.

### Write Initial Stress

Stress should be written to a configured Solid Mechanics feature, normally an Initial Stress and Strain feature under a Linear Elastic Material feature:

```java
model.component(componentTag)
     .physics(physicsTag)
     .feature(parentFeatureTag)
     .feature(initialStressFeatureTag)
     .set(stressPropertyName, stressExprs);
```

Default config:

```yaml
property: S0
stress_format: diag_sxx_syy
```

For a 3D stress tensor, `diag_sxx_syy` expands to:

```text
Sxx = sxx
Syy = syy
Szz = 0
Sxy = 0
Syz = 0
Sxz = 0
```

The exact expression order for `S0` must be verified by exporting Java from one representative real COMSOL model. Until that export is checked in, Java should keep the order behind a helper function and require `stress_format` in YAML.

If a model uses External Stress instead of Initial Stress and Strain, add a second supported target type:

```yaml
stress:
  type: external_stress
  component: comp8
  physics: solid8
  feature: extstress_pillar
  property: F
  stress_format: diag_sxx_syy
```

The first implementation should support `initial_stress` only unless a real template needs `external_stress`.

## Step Input Shape

Python expands YAML references into concrete RVE payloads before Java runs:

```json
{
  "node": "fecap",
  "action": "run",
  "template": "fecap_model_all",
  "model_path": "models/process_models/cap_models/fecap_model_all.mph",
  "inputs": {
    "pillar": {
      "source": "rve.pillar",
      "rve": {
        "rho": "2330.0[kg/m^3]",
        "stress_eff": {
          "sxx": "1.2e8[Pa]",
          "syy": "-3.5e8[Pa]"
        },
        "D_format": "symmetric_upper21",
        "elasticity_order": "standard",
        "D_upper21": ["... 21 Pa expressions ..."]
      },
      "target": {
        "material": {
          "component": "comp8",
          "tag": "mat_pillar_eff",
          "density_group": "def",
          "density_property": "density",
          "elastic_group": "anisotropic_eff",
          "elastic_property": "D",
          "elasticity_order": "standard",
          "D_format": "symmetric_upper21"
        },
        "stress": {
          "type": "initial_stress",
          "component": "comp8",
          "physics": "solid8",
          "parent_feature": "lemm1",
          "feature": "initstress_pillar",
          "property": "S0",
          "stress_format": "diag_sxx_syy"
        }
      }
    }
  }
}
```

Java should not load `state_in.json` to find input RVEs for run nodes. `state_in.json` can remain available for history preservation, but all values needed by a run node must be present in its `step_input.json` node entry.

## Template Registry Shape

Use a flat registry keyed by canonical template id:

```yaml
templates:
  fecap_model_all:
    path: models/process_models/cap_models/fecap_model_all.mph
    node_type: fecap
    component: comp8
    physics: solid8
    studies:
      stress: std4
      cp: solid8cp1std
    extractors:
      rho: gev_rho
      stress: gev_stress_avg
      D: gev_D
    outputs:
      result_file: fecap_rve.json
    input_targets:
      pillar:
        material:
          component: comp8
          tag: mat_pillar_eff
          density_group: def
          density_property: density
          elastic_group: anisotropic_eff
          elastic_property: D
          elasticity_order: standard
          D_format: symmetric_upper21
        stress:
          type: initial_stress
          component: comp8
          physics: solid8
          parent_feature: lemm1
          feature: initstress_pillar
          property: S0
          stress_format: diag_sxx_syy
      sc:
        material:
          component: comp8
          tag: mat_sc_eff
          density_group: def
          density_property: density
          elastic_group: anisotropic_eff
          elastic_property: D
          elasticity_order: standard
          D_format: symmetric_upper21
        stress:
          type: initial_stress
          component: comp8
          physics: solid8
          parent_feature: lemm1
          feature: initstress_sc
          property: S0
          stress_format: diag_sxx_syy
```

Step YAML may override `input_targets` only when a specific step template uses different tags. Overrides must be explicit; no implicit node-name mapping.

## Extractor Contract

RVE node extractors:

```yaml
extractors:
  rho: gev_rho
  stress: gev_stress_avg
  D: gev_D
```

The Java worker interprets `stress` as a two-value extraction:

```text
[sxx, syy]
```

If `studies.cp` is `null`, Java must not attempt to extract D and should return:

```json
{
  "D": null,
  "status": "success_stress_only"
}
```

Python then merges D from the previous valid state for the same canonical node.

Wafer extractors:

```yaml
extractors:
  bow_x_um: gev_bow_x
  bow_y_um: gev_bow_y
```

Use `bow_x_um` and `bow_y_um` everywhere in state and summary. If a downstream tool needs `bow_x` and `bow_y`, add those only as export aliases, not as internal state names.

## Summary Contract

Each row in `summary.csv` should use canonical names:

```text
step_id
step_name
pillar_status
sc_status
decap1_status
decap2_status
decap3_status
fecap_status
die_status
wafer_status
onon_source_step
onon_source_node
bow_x_um
bow_y_um
warnings
errors
```

Do not use `mat1_status`, `onon_device_source_step`, `bow_x_sim_um`, or other legacy aliases in the v2 default summary.

## Runtime Log Contract

Every executed step must write a human-readable log at:

```text
runs/<run_id>/<step>/logs/step.log
```

The log is for process audit and COMSOL template debugging. It should let a user verify the run without opening `step_input.json`, `manifest.json`, and `state_out.json` side by side.

Required content:

- Step identity: `step_id`, `step_name`, output directory, and node order.
- One line per node with canonical node name, action, template key, and model path.
- For `inherit`, `default_from`, and `alias`: source node and source step.
- For every run-node input: slot, `rve.<node>` source, source step, source node, and selected RVE summary.
- For every material target: component tag, material tag, density group/property, elastic group/property, D format, and elasticity order.
- For every stress target: target type, component tag, physics tag, parent feature tag, feature tag, property name, and stress format.
- For every output RVE: status, source step, `rho`, `sxx`, `syy`, representative D entries, and any inherited `D_source_step`.
- For wafer: `bow_x_um`, `bow_y_um`, `kx`, and `ky`.

`manifest.json` remains the machine-readable execution record. `logs/step.log` is intentionally redundant and optimized for inspection during real COMSOL template bring-up.

## Validation Rules

The Python validator must reject:

- Any v2 node outside the canonical names list.
- Any v2 output path using legacy names.
- `onon` with `action: run`.
- `wafer` with any action other than `run`.
- `D_format` other than `symmetric_upper21` for Java input.
- `elasticity_order` missing from any material target that receives D.
- Missing `material.component`, `material.tag`, `elastic_group`, or `elastic_property`.
- Missing `stress.component`, `stress.physics`, `stress.feature`, or `stress.property` for nodes that require stress transfer.
- Mixed use of legacy names and canonical names in one flow.
- `studies.cp: null` without `merge.D: inherit_previous` for a node whose downstream consumers require D.

The Java worker must fail fast when a configured target tag is absent in the model. It should include the missing tag, node name, input slot, and template key in the exception message.

## Migration Strategy

1. Keep legacy two-step config as archived or test fixture only.
2. Create v2 active flow using only canonical names.
3. Add a small one-way migration helper only if old run directories need to be read.
4. Do not support long-term dual naming in production code.

Legacy state conversion, if needed:

| Legacy | V2 |
|---|---|
| `device_main` | `pillar` |
| `device_aux` | not carried unless a real v2 role is defined |
| `onon_device` | `onon` |
| `mat1` | `decap1` |
| `mat2` | `decap2` |
| `mat3` | `decap3` |
| `mat4` | `fecap` |

This mapping belongs only in a migration utility or archived tests, not in the normal v2 execution path.

## Implementation Phases

### Phase 1: Spec And Config

- Update `comsol_20step_warpage_dev_spec.md` to use canonical names.
- Update the parameter transfer section to use material/stress targets.
- Convert `templates.yaml` to the flat v2 registry.
- Add v2 flow and representative step YAML for S01, S02, S04, S05, and S16 first.

### Phase 2: Python Orchestration

- Add canonical node constants and dependency order.
- Update state initialization to v2 nodes.
- Add D full-to-upper21 and upper21-to-full helpers.
- Expand node inputs into concrete RVE payloads in `step_input.json`.
- Implement `default_from`, `alias`, and stress-only D inheritance.
- Update summary fields.

### Phase 3: Mock Backend

- Run v2 nodes in canonical order.
- Consume expanded input payloads, not legacy `inputs.device` strings.
- Return deterministic RVE and wafer outputs using canonical names.
- Add tests for S02 initialization, S04 current-step `sc -> fecap`, S16 stress-only D merge, and S20 `onon` alias preservation.

### Phase 4: Java Worker

- Parse expanded input payloads.
- Write rho to material density.
- Write D to configured material elasticity property.
- Write sxx/syy to configured initial stress feature.
- Support `studies.cp: null` by returning `D: null`.
- Emit a manifest that records every material/stress target touched.

### Phase 5: Real Template Verification

- Export Java from one representative COMSOL model for each class: `pillar`, `sc`, `decap`, `fecap`, `die`, `wafer`.
- Confirm material tags, property group tags, elasticity property names, stress feature tags, and `S0` ordering.
- Replace provisional tags in YAML.
- Run dryrun and mock acceptance before running COMSOL.

## Open Questions

1. Does every RVE-consuming COMSOL template already contain target material nodes for each incoming slot, or should Java create them if missing?
2. Do the real templates use Initial Stress and Strain everywhere, or do any use External Stress?
3. Is the elasticity matrix expected in COMSOL Standard order for all current templates, or do some use Voigt-specific `DV0`?
4. Should `onon` always remain S02 `pillar`, or will future flows allow a later step to refresh the wafer-level ONON alias?

## Official COMSOL References

- COMSOL 6.3 material API: https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_api_general.47.40.html
- COMSOL 6.3 model parameter API: https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_api_general.47.50.html
- COMSOL 6.3 physics API: https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_api_general.47.51.html
- COMSOL 6.3 Solid Mechanics material properties: https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/comsol_ref_materials.25.48.html
- COMSOL 6.3 Linear Elastic Material theory: https://doc.comsol.com/6.3/doc/com.comsol.help.sme/sme_ug_theory.06.026.html
- COMSOL 6.3 Initial Stress and Strain: https://doc.comsol.com/6.3/doc/com.comsol.help.sme/sme_ug_solid.07.036.html
