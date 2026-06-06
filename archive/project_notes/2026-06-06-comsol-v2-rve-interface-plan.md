# COMSOL V2 RVE Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the first production slice of the v2 RVE transfer contract: canonical node names, compact D transfer, expanded node input targets, and mock/dryrun validation.

**Architecture:** Python remains the authoritative orchestration layer. It validates canonical v2 configs, stores full 6x6 RVE state, expands run-node inputs into concrete Java-ready payloads, and handles alias/default/stress-only merging. Java receives expanded values and target tags rather than resolving state or inventing `input_<slot>_*` globals.

**Tech Stack:** Python standard library, unittest, project YAML loader, existing mock backend, COMSOL Java worker skeleton.

---

### Task 1: Add V2 RVE Interface Helpers

**Files:**
- Create: `python/comsol_opt/v2_contract.py`
- Test: `tests/test_workflow.py`

- [ ] **Step 1: Write failing tests**

Add tests that import `matrix_to_upper21`, `upper21_to_matrix`, and `CANONICAL_NODES`, then assert:

```python
matrix = [[float(10 * row + col) for col in range(6)] for row in range(6)]
for row in range(6):
    for col in range(row):
        matrix[row][col] = matrix[col][row]
upper = matrix_to_upper21(matrix)
self.assertEqual(len(upper), 21)
self.assertEqual(upper[0], matrix[0][0])
self.assertEqual(upper[1], matrix[0][1])
self.assertEqual(upper[-1], matrix[5][5])
self.assertEqual(upper21_to_matrix(upper), matrix)
self.assertEqual(CANONICAL_NODES, ("pillar", "sc", "decap1", "decap2", "decap3", "fecap", "die", "wafer", "onon"))
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_workflow.V2ContractTests -v
```

Expected: fails because `comsol_opt.v2_contract` does not exist.

- [ ] **Step 3: Implement helpers**

Create `v2_contract.py` with canonical constants, legacy-name set, `matrix_to_upper21`, `upper21_to_matrix`, and `rve_payload_for_java`.

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
python3 -m unittest tests.test_workflow.V2ContractTests -v
```

Expected: all V2 contract tests pass.

### Task 2: Validate And Expand V2 Steps

**Files:**
- Modify: `python/comsol_opt/flow_runner.py`
- Modify: `python/comsol_opt/step_input.py`
- Test: `tests/test_workflow.py`

- [ ] **Step 1: Write failing tests**

Add tests that build an in-memory `version: 2` step using canonical nodes and assert:

- legacy names are rejected;
- `onon` cannot `run`;
- `wafer` must `run`;
- expanded `step_input.json` contains `inputs.pillar.rve.D_upper21` and `target.material.elastic_property`.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
python3 -m unittest tests.test_workflow.V2FlowTests -v
```

Expected: fails because v2 validation/expansion is not implemented.

- [ ] **Step 3: Implement v2 loading and validation**

Add `version: 2` branch in `load_flow_definition`, `_resolve_v2_node`, `validate_flow`, and `build_step_input`.

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
python3 -m unittest tests.test_workflow.V2FlowTests -v
```

Expected: all V2 flow tests pass.

### Task 3: Add V2 Mock Execution

**Files:**
- Modify: `python/comsol_opt/state.py`
- Modify: `python/comsol_opt/mock_comsol_worker.py`
- Modify: `python/comsol_opt/summary.py`
- Test: `tests/test_workflow.py`

- [ ] **Step 1: Write failing tests**

Add tests for a small v2 S02/S04/S16 fixture:

- S02 creates `pillar`, `onon`, defaults `sc/decap1/decap2/decap3/fecap/die`, and runs wafer.
- S04 `sc` run is consumed by same-step `fecap`.
- S16 stress-only `fecap` inherits previous D.
- Summary uses canonical `decap1_status`, `fecap_status`, `onon_source_step`, `bow_x_um`, `bow_y_um`.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
python3 -m unittest tests.test_workflow.V2MockFlowTests -v
```

Expected: fails because mock v2 actions are not implemented.

- [ ] **Step 3: Implement minimal v2 mock**

Support `run`, `inherit`, `default_from`, `alias`, expanded inputs, and stress-only D merge.

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
python3 -m unittest tests.test_workflow.V2MockFlowTests -v
```

Expected: all v2 mock tests pass.

### Task 4: Update Java Worker Contract Skeleton

**Files:**
- Modify: `java/ComsolStepWorker.java`
- Test: `tests/test_workflow.py`

- [ ] **Step 1: Write failing text-contract test**

Add assertions that the worker contains:

```text
applyMaterialTarget
applyStressTarget
propertyGroup(elasticGroupTag)
elasticPropertyName
D_upper21
symmetric_upper21
```

and no longer contains the old core transfer strings:

```text
input_%s_d11
input_%s_d22
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
python3 -m unittest tests.test_workflow.ConfigTests.test_comsol_worker_draft_contains_expected_api_shape -v
```

Expected: fails on missing new worker contract strings.

- [ ] **Step 3: Update Java skeleton**

Refactor injection helpers into material and stress target helpers. Keep code compile-oriented but still skeleton-level until real exported Java tags are available.

- [ ] **Step 4: Run test and verify pass**

Run the same unittest target.

### Task 5: Full Verification

**Files:**
- All touched files

- [ ] **Step 1: Run full test suite**

Run:

```bash
python3 -m unittest tests/test_workflow.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Review diff**

Run:

```bash
git diff --stat
git diff -- archive/project_notes/2026-06-06-comsol-v2-rve-interface-design.md archive/project_notes/2026-06-06-comsol-v2-rve-interface-plan.md python/comsol_opt tests/test_workflow.py java/ComsolStepWorker.java
```

Expected: changes are limited to the v2 interface slice and docs.
