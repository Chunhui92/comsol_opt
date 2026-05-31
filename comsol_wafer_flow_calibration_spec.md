# COMSOL 多尺度 Wafer 翘曲仿真链路与参数校准开发规格

## 0. 文档目的

本文档用于指导 Codex / 开发代理实现一个可验证、可扩展的多尺度 COMSOL 自动化仿真与参数校准框架。

目标流程是：

```text
工艺步骤
  → device RVE 提取
  → mat1/mat2/mat3 并列 RVE 提取
  → wafer 模板仿真
  → 与每一步实验 x/y bow 对比
  → 分阶段参数校准
```

当前设计要求：

1. 使用 Python 作为工艺流程调度器和校准器。
2. 使用 Java COMSOL Worker 作为真实 COMSOL 后端。
3. 本地无 COMSOL 环境时，使用 Python Mock Backend 验证流程、JSON、缓存和校准逻辑。
4. 复用少量 COMSOL 模板模型，不为每一步工艺生成独立 MPH。
5. wafer 模型维护两个模板，通过 JSON 在特定步骤选择。
6. `mat1/mat2/mat3` 是 wafer 中三个并列周期性结构，不是串联多级结构。
7. 每一步没有重新计算的 RVE 结果，应从上一步 `state_out.json` 继承。
8. 每一步实验均有 `bow_x_um` 和 `bow_y_um`，需要用于分阶段和全局校准。

---

## 1. 工艺链路定义

### 1.1 工艺步骤

当前工艺步骤如下：

| Step ID | 工艺名称 | 说明 | 主要待校准参数 |
|---|---|---|---|
| S00 | init | 初始 wafer 状态 | FEOL/init stress x/y |
| S01 | ONONdep | ONON 沉积 | O/N base init stress |
| S02 | trench_etch | trench 刻蚀，已有 O/N 释放 | O/N release factor |
| S03 | trench_fill | trench 填充 | Ox init stress |
| S04 | pillar_etch | pillar 刻蚀 | pillar diameter |
| S05a | pillar_dep1 | pillar 第 1 次沉积 | dep1 material init stress |
| S05b | pillar_dep2 | pillar 第 2 次沉积 | dep2 material init stress |
| S05c | pillar_dep3 | pillar 第 3 次沉积 | dep3 material init stress |
| S05d | pillar_dep4 | pillar 第 4 次沉积 | dep4 material init stress |
| S05e | pillar_dep5 | pillar 第 5 次沉积 | dep5 material init stress |
| S06 | dpillar_form | dpillar form，已有 O/N 释放 | O/N release factor |
| S07 | aSi_dep | aSi 沉积 | aSi init stress |
| S08 | mat_remove | 材料去除，已有 O/N 释放 | O/N release factor |
| S09 | W_fill | W 填充 | W init stress |
| S10 | final | 终态验证或小范围修正 | optional final O/N release |

### 1.2 关键物理关系

#### O/N stress 处理原则

`O/N init stress` 只在 `S01 ONONdep` 中定义。

后续步骤 `S02/S06/S08/S10` 不再定义新的 O/N init stress，只定义 release factor：

```text
S01:
    sigma_O_current = sigma_O_base
    sigma_N_current = sigma_N_base

S02:
    sigma_O_current *= r_ONON_trench_etch
    sigma_N_current *= r_ONON_trench_etch

S06:
    sigma_O_current *= r_ONON_dpillar_form
    sigma_N_current *= r_ONON_dpillar_form

S08:
    sigma_O_current *= r_ONON_mat_remove
    sigma_N_current *= r_ONON_mat_remove

S10:
    sigma_O_current *= r_ONON_final    # optional
    sigma_N_current *= r_ONON_final    # optional
```

这样可以避免多个工艺步骤同时校准 O/N init stress 造成参数冲突。

#### mat1/mat2/mat3 关系

`mat1/mat2/mat3` 是 wafer 中三个并列周期性结构：

```text
device RVE
    ├── mat1 RVE
    ├── mat2 RVE
    └── mat3 RVE

FEOL + mat1 + mat2 + mat3 + ONON_layer + optional aSi_layer
    ↓
wafer 模板
```

不是：

```text
device → mat1 → mat2 → mat3 → wafer
```

#### device2_for_mat3

通常：

```text
mat1 ← device_default
mat2 ← device_default
```

但：

```text
mat3 ← device2_for_mat3
```

某些步骤中 mat3 需要另一个 device2 RVE 输入，因此 state 和 flow 必须支持多个 device RVE。

---

## 2. 模板模型策略

### 2.1 需要维护的 COMSOL MPH 模板

建议第一版维护：

```text
models/templates/
    device_onon_template.mph
    device_trench_template.mph
    device_pillar_template.mph
    device_final_template.mph
    device2_mat3_template.mph

    mat1_template.mph
    mat2_template.mph
    mat3_template.mph

    wafer_base_template.mph
    wafer_with_asi_template.mph
```

说明：

1. device 拓扑变化较大，因此维护多个 device 模板。
2. mat1/mat2/mat3 拓扑变化相对较小，因此强复用模板。
3. wafer 维护两个模板：
   - `wafer_base_template.mph`
   - `wafer_with_asi_template.mph`
4. 在每一步 JSON 中选择使用哪个 wafer 模板。

### 2.2 wafer 模板选择原则

| Step | wafer_template |
|---|---|
| S00 | base |
| S01 | base |
| S02 | base |
| S03 | base |
| S04 | base |
| S05a~S05e | base |
| S06 | base |
| S07 | with_asi |
| S08 | base |
| S09 | base |
| S10 | base |

如果后续确认 aSi 在 S08 后仍应存在，则可将 S08 之后的步骤改为 `with_asi`，但当前设计采用两个模板并在 JSON 中显式选择。

---

## 3. 目录结构

建议项目结构如下：

```text
project/
├── configs/
│   ├── flow.yaml
│   ├── params_nominal.yaml
│   ├── calibration_space.yaml
│   └── template_tags.yaml
│
├── exp/
│   └── bow_experiment.csv
│
├── models/
│   └── templates/
│       ├── device_onon_template.mph
│       ├── device_trench_template.mph
│       ├── device_pillar_template.mph
│       ├── device_final_template.mph
│       ├── device2_mat3_template.mph
│       ├── mat1_template.mph
│       ├── mat2_template.mph
│       ├── mat3_template.mph
│       ├── wafer_base_template.mph
│       └── wafer_with_asi_template.mph
│
├── java/
│   ├── ComsolStepWorker.java
│   ├── StepInput.java
│   ├── WaferState.java
│   ├── RveResult.java
│   ├── WaferResult.java
│   └── JsonUtils.java
│
├── python/
│   ├── run_flow.py
│   ├── backends.py
│   ├── mock_comsol_worker.py
│   ├── build_step_input.py
│   ├── cache_manager.py
│   ├── compute_loss.py
│   ├── sensitivity.py
│   └── calibration_optuna.py
│
└── runs/
    └── run_0001/
        ├── S00/
        │   ├── step_input.json
        │   ├── step_result.json
        │   ├── state_out.json
        │   └── logs/
        ├── S01/
        ├── ...
        └── summary.csv
```

---

## 4. 核心 JSON Schema

### 4.1 RveResult

每个 RVE 结果统一为：

```json
{
  "valid": true,
  "active": true,
  "source_step": "S04",
  "source_model": "mat1_template",
  "rho": 3000.0,
  "stress_eff": {
    "sxx": 95000000.0,
    "syy": 90000000.0
  },
  "D": [
    [1.0e11, 3.0e10, 3.0e10, 0.0, 0.0, 0.0],
    [3.0e10, 1.0e11, 3.0e10, 0.0, 0.0, 0.0],
    [3.0e10, 3.0e10, 1.0e11, 0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 3.5e10, 0.0, 0.0],
    [0.0, 0.0, 0.0, 0.0, 3.5e10, 0.0],
    [0.0, 0.0, 0.0, 0.0, 0.0, 3.5e10]
  ],
  "meta": {
    "hash": "optional_hash",
    "note": ""
  }
}
```

字段要求：

| 字段 | 含义 |
|---|---|
| `valid` | 是否已有有效 RVE 结果 |
| `active` | 是否参与 wafer 模型 |
| `rho` | 等效密度 |
| `stress_eff.sxx/syy` | 等效 x/y 应力，来自 COMSOL `gev1` 或 mock |
| `D` | 6x6 等效刚度矩阵 |
| `source_step` | 该 RVE 最近一次由哪个 step 更新 |
| `source_model` | 来源模板 |

### 4.2 WaferState

`state_out.json` 结构：

```json
{
  "step_id": "S04",
  "step_name": "pillar_etch",

  "wafer_result": {
    "bow_x_um": 105.2,
    "bow_y_um": 91.4,
    "kx": 0.00125,
    "ky": 0.00108
  },

  "device_rves": {
    "device_default": {
      "valid": true,
      "active": true,
      "source_step": "S04",
      "source_model": "device_pillar_template",
      "rho": 2860.0,
      "stress_eff": {
        "sxx": 120000000.0,
        "syy": 110000000.0
      },
      "D": [[0,0,0,0,0,0]]
    },
    "device2_for_mat3": {
      "valid": true,
      "active": true,
      "source_step": "S04",
      "source_model": "device2_mat3_template",
      "rho": 2950.0,
      "stress_eff": {
        "sxx": 90000000.0,
        "syy": 86000000.0
      },
      "D": [[0,0,0,0,0,0]]
    }
  },

  "wafer_inputs": {
    "FEOL": {
      "valid": true,
      "active": true,
      "source_step": "S00",
      "source_model": "wafer_direct",
      "rho": 2330.0,
      "stress_eff": {
        "sxx": 180000000.0,
        "syy": 160000000.0
      },
      "D": [[0,0,0,0,0,0]]
    },

    "mat1": {
      "valid": true,
      "active": true,
      "source_step": "S04",
      "source_model": "mat1_template",
      "rho": 3000.0,
      "stress_eff": {
        "sxx": 95000000.0,
        "syy": 90000000.0
      },
      "D": [[0,0,0,0,0,0]]
    },

    "mat2": {
      "valid": true,
      "active": true,
      "source_step": "S04",
      "source_model": "mat2_template",
      "rho": 3150.0,
      "stress_eff": {
        "sxx": 80000000.0,
        "syy": 76000000.0
      },
      "D": [[0,0,0,0,0,0]]
    },

    "mat3": {
      "valid": true,
      "active": true,
      "source_step": "S04",
      "source_model": "mat3_template",
      "rho": 3300.0,
      "stress_eff": {
        "sxx": 70000000.0,
        "syy": 66000000.0
      },
      "D": [[0,0,0,0,0,0]]
    },

    "ONON_layer": {
      "valid": true,
      "active": true,
      "source_step": "S02",
      "source_model": "device_or_direct",
      "rho": 2800.0,
      "stress_eff": {
        "sxx": -220000000.0,
        "syy": -210000000.0
      },
      "D": [[0,0,0,0,0,0]]
    },

    "aSi_layer": {
      "valid": true,
      "active": false,
      "source_step": "S08",
      "source_model": "wafer_direct",
      "rho": 0.0,
      "stress_eff": {
        "sxx": 0.0,
        "syy": 0.0
      },
      "D": [[0,0,0,0,0,0]]
    }
  },

  "materials_state": {
    "FEOL": {
      "sigma_init_x": 180000000.0,
      "sigma_init_y": 160000000.0
    },

    "ONON": {
      "sigma_O_base": 100000000.0,
      "sigma_N_base": -300000000.0,
      "sigma_O_current": 75000000.0,
      "sigma_N_current": -225000000.0,
      "release_history": {
        "S02_trench_etch": 0.75
      }
    },

    "Ox": {
      "sigma_trench_fill": 120000000.0
    },

    "pillar_dep": {
      "dep1": {
        "sigma_init": 100000000.0
      },
      "dep2": {
        "sigma_init": -150000000.0
      },
      "dep3": {
        "sigma_init": 200000000.0
      },
      "dep4": {
        "sigma_init": -80000000.0
      },
      "dep5": {
        "sigma_init": 120000000.0
      }
    },

    "aSi": {
      "sigma": -150000000.0
    },

    "W": {
      "sigma_fill": 500000000.0
    }
  },

  "geometry_state": {
    "pillar_diameter": 1.2e-7
  },

  "history": [
    {
      "step_id": "S00",
      "step_name": "init",
      "bow_x_um": 100.0,
      "bow_y_um": 80.0
    }
  ]
}
```

实际实现时，`D` 必须是 6x6 矩阵。上面示例中部分 `D` 用占位写法，真实输出不能省略。

### 4.3 StepInput

每一步由 Python 生成 `step_input.json`：

```json
{
  "step_id": "S09",
  "step_name": "W_fill",
  "process_type": "fill",
  "update_rule": "w_fill",

  "templates": {
    "devices": {
      "device_default": "models/templates/device_final_template.mph",
      "device2_for_mat3": "models/templates/device2_mat3_template.mph"
    },
    "mats": {
      "mat1": "models/templates/mat1_template.mph",
      "mat2": "models/templates/mat2_template.mph",
      "mat3": "models/templates/mat3_template.mph"
    },
    "wafer": "models/templates/wafer_base_template.mph"
  },

  "run_devices": [
    {
      "name": "device_default",
      "template_key": "device_final"
    },
    {
      "name": "device2_for_mat3",
      "template_key": "device2_mat3"
    }
  ],

  "run_mats": ["mat1", "mat2", "mat3"],

  "mat_inputs": {
    "mat1": "device_default",
    "mat2": "device_default",
    "mat3": "device2_for_mat3"
  },

  "run_wafer": true,

  "wafer_inputs": {
    "update": ["mat1", "mat2", "mat3"],
    "inherit": ["FEOL", "ONON_layer", "aSi_layer"]
  },

  "parameters": {
    "W.sigma_fill": 500000000.0,
    "geometry.pillar_diameter": 1.2e-7
  },

  "state_in": "runs/run_0001/S08/state_out.json",
  "output_dir": "runs/run_0001/S09"
}
```

---

## 5. flow.yaml

建议 `configs/flow.yaml` 如下：

```yaml
flow_name: wafer_warpage_flow_v1

templates:
  devices:
    device_onon: "models/templates/device_onon_template.mph"
    device_trench: "models/templates/device_trench_template.mph"
    device_pillar: "models/templates/device_pillar_template.mph"
    device_final: "models/templates/device_final_template.mph"
    device2_mat3: "models/templates/device2_mat3_template.mph"

  mats:
    mat1: "models/templates/mat1_template.mph"
    mat2: "models/templates/mat2_template.mph"
    mat3: "models/templates/mat3_template.mph"

  wafers:
    base: "models/templates/wafer_base_template.mph"
    with_asi: "models/templates/wafer_with_asi_template.mph"

steps:
  - id: S00
    name: init
    process_type: init
    update_rule: init
    run_devices: []
    run_mats: []
    mat_inputs: {}
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["FEOL"]
      inherit: ["mat1", "mat2", "mat3", "ONON_layer", "aSi_layer"]
    calibration_group: G0_init
    experiment_step: S00

  - id: S01
    name: ONONdep
    process_type: deposition
    update_rule: onon_deposition
    run_devices:
      - name: device_default
        template_key: device_onon
    run_mats: []
    mat_inputs: {}
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["ONON_layer"]
      inherit: ["FEOL", "mat1", "mat2", "mat3", "aSi_layer"]
    calibration_group: G1_ONON_base
    experiment_step: S01

  - id: S02
    name: trench_etch
    process_type: etch
    update_rule: onon_trench_release
    run_devices:
      - name: device_default
        template_key: device_trench
    run_mats: ["mat1"]
    mat_inputs:
      mat1: device_default
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat1", "ONON_layer"]
      inherit: ["FEOL", "mat2", "mat3", "aSi_layer"]
    calibration_group: G2_trench_release
    experiment_step: S02

  - id: S03
    name: trench_fill
    process_type: fill
    update_rule: trench_ox_fill
    run_devices: []
    run_mats: ["mat2"]
    mat_inputs:
      mat2: device_default
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat2"]
      inherit: ["FEOL", "mat1", "mat3", "ONON_layer", "aSi_layer"]
    calibration_group: G3_ox_fill
    experiment_step: S03

  - id: S04
    name: pillar_etch
    process_type: etch
    update_rule: pillar_diameter_update
    run_devices:
      - name: device_default
        template_key: device_pillar
      - name: device2_for_mat3
        template_key: device2_mat3
    run_mats: ["mat1", "mat2", "mat3"]
    mat_inputs:
      mat1: device_default
      mat2: device_default
      mat3: device2_for_mat3
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat1", "mat2", "mat3"]
      inherit: ["FEOL", "ONON_layer", "aSi_layer"]
    calibration_group: G4_pillar_diameter
    experiment_step: S04

  - id: S05a
    name: pillar_dep1
    process_type: deposition
    update_rule: pillar_dep1
    run_devices: []
    run_mats: ["mat2"]
    mat_inputs:
      mat2: device_default
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat2"]
      inherit: ["FEOL", "mat1", "mat3", "ONON_layer", "aSi_layer"]
    calibration_group: G5_dep1
    experiment_step: S05a

  - id: S05b
    name: pillar_dep2
    process_type: deposition
    update_rule: pillar_dep2
    run_devices: []
    run_mats: ["mat2"]
    mat_inputs:
      mat2: device_default
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat2"]
      inherit: ["FEOL", "mat1", "mat3", "ONON_layer", "aSi_layer"]
    calibration_group: G5_dep2
    experiment_step: S05b

  - id: S05c
    name: pillar_dep3
    process_type: deposition
    update_rule: pillar_dep3
    run_devices: []
    run_mats: ["mat2"]
    mat_inputs:
      mat2: device_default
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat2"]
      inherit: ["FEOL", "mat1", "mat3", "ONON_layer", "aSi_layer"]
    calibration_group: G5_dep3
    experiment_step: S05c

  - id: S05d
    name: pillar_dep4
    process_type: deposition
    update_rule: pillar_dep4
    run_devices: []
    run_mats: ["mat2"]
    mat_inputs:
      mat2: device_default
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat2"]
      inherit: ["FEOL", "mat1", "mat3", "ONON_layer", "aSi_layer"]
    calibration_group: G5_dep4
    experiment_step: S05d

  - id: S05e
    name: pillar_dep5
    process_type: deposition
    update_rule: pillar_dep5
    run_devices: []
    run_mats: ["mat2"]
    mat_inputs:
      mat2: device_default
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat2"]
      inherit: ["FEOL", "mat1", "mat3", "ONON_layer", "aSi_layer"]
    calibration_group: G5_dep5
    experiment_step: S05e

  - id: S06
    name: dpillar_form
    process_type: form
    update_rule: dpillar_onon_release
    run_devices:
      - name: device_default
        template_key: device_pillar
      - name: device2_for_mat3
        template_key: device2_mat3
    run_mats: ["mat1", "mat2", "mat3"]
    mat_inputs:
      mat1: device_default
      mat2: device_default
      mat3: device2_for_mat3
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat1", "mat2", "mat3", "ONON_layer"]
      inherit: ["FEOL", "aSi_layer"]
    calibration_group: G6_dpillar_release
    experiment_step: S06

  - id: S07
    name: aSi_dep
    process_type: deposition
    update_rule: asi_deposition
    run_devices: []
    run_mats: []
    mat_inputs: {}
    run_wafer: true
    wafer_template: with_asi
    wafer_inputs:
      update: ["aSi_layer"]
      inherit: ["FEOL", "mat1", "mat2", "mat3", "ONON_layer"]
    calibration_group: G7_aSi
    experiment_step: S07

  - id: S08
    name: mat_remove
    process_type: remove
    update_rule: mat_remove_onon_release
    run_devices:
      - name: device_default
        template_key: device_final
      - name: device2_for_mat3
        template_key: device2_mat3
    run_mats: ["mat1", "mat2", "mat3"]
    mat_inputs:
      mat1: device_default
      mat2: device_default
      mat3: device2_for_mat3
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat1", "mat2", "mat3", "ONON_layer", "aSi_layer"]
      inherit: ["FEOL"]
    calibration_group: G8_mat_remove
    experiment_step: S08

  - id: S09
    name: W_fill
    process_type: fill
    update_rule: w_fill
    run_devices:
      - name: device_default
        template_key: device_final
      - name: device2_for_mat3
        template_key: device2_mat3
    run_mats: ["mat1", "mat2", "mat3"]
    mat_inputs:
      mat1: device_default
      mat2: device_default
      mat3: device2_for_mat3
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat1", "mat2", "mat3"]
      inherit: ["FEOL", "ONON_layer", "aSi_layer"]
    calibration_group: G9_W_fill
    experiment_step: S09

  - id: S10
    name: final
    process_type: final
    update_rule: final
    run_devices:
      - name: device_default
        template_key: device_final
      - name: device2_for_mat3
        template_key: device2_mat3
    run_mats: ["mat1", "mat2", "mat3"]
    mat_inputs:
      mat1: device_default
      mat2: device_default
      mat3: device2_for_mat3
    run_wafer: true
    wafer_template: base
    wafer_inputs:
      update: ["mat1", "mat2", "mat3"]
      inherit: ["FEOL", "ONON_layer", "aSi_layer"]
    calibration_group: G10_final
    experiment_step: S10
```

注意：

1. 某些步骤 `run_devices: []` 但 `mat_inputs` 仍可能指定 `device_default`。此时 worker 应从 `state_in.device_rves.device_default` 继承上一有效 device RVE。
2. `S08` 的 `wafer_inputs.update` 包含 `aSi_layer`，表示在 mat remove 后将 aSi 置为 inactive 或清零，并切换回 `wafer_base_template.mph`。

---

## 6. params_nominal.yaml

```yaml
FEOL:
  sigma_init_x: 180.0e6
  sigma_init_y: 160.0e6

ONON:
  sigma_O_base: 100.0e6
  sigma_N_base: -300.0e6

release:
  trench_etch_ONON: 0.75
  dpillar_form_ONON: 0.80
  mat_remove_ONON: 0.70
  final_ONON: 1.00

Ox:
  sigma_trench_fill: 120.0e6

pillar_dep:
  dep1:
    material: "dep1_material"
    sigma_init: 100.0e6
  dep2:
    material: "dep2_material"
    sigma_init: -150.0e6
  dep3:
    material: "dep3_material"
    sigma_init: 200.0e6
  dep4:
    material: "dep4_material"
    sigma_init: -80.0e6
  dep5:
    material: "dep5_material"
    sigma_init: 120.0e6

aSi:
  sigma: -150.0e6

W:
  sigma_fill: 500.0e6

geometry:
  pillar_diameter: 120.0e-9
```

---

## 7. experiment.csv

每一步都有实验 bow：

```csv
step_id,step_name,bow_x_um,bow_y_um,temperature_C,weight_x,weight_y
S00,init,100,80,25,1.0,1.0
S01,ONONdep,75,65,25,1.0,1.0
S02,trench_etch,88,72,25,1.0,1.0
S03,trench_fill,95,78,25,1.0,1.0
S04,pillar_etch,110,90,25,1.0,1.0
S05a,pillar_dep1,115,94,25,1.0,1.0
S05b,pillar_dep2,120,98,25,1.0,1.0
S05c,pillar_dep3,125,102,25,1.0,1.0
S05d,pillar_dep4,130,106,25,1.0,1.0
S05e,pillar_dep5,135,110,25,1.0,1.0
S06,dpillar_form,120,100,25,1.0,1.0
S07,aSi_dep,140,115,25,1.0,1.0
S08,mat_remove,105,90,25,1.0,1.0
S09,W_fill,70,55,25,1.0,1.0
S10,final,50,40,25,1.0,1.0
```

---

## 8. Python Orchestrator 设计

### 8.1 run_flow.py 职责

`run_flow.py` 负责：

1. 读取 `flow.yaml`。
2. 读取 `params.yaml`。
3. 读取上一 step 的 `state_out.json`。
4. 生成当前 step 的 `step_input.json`。
5. 调用 backend：
   - `mock`
   - `comsol`
6. 收集所有 step 的 `wafer_result`。
7. 输出 `summary.csv`。
8. 支持从任意 step 开始 rerun。
9. 支持缓存。

命令示例：

```bash
python python/run_flow.py \
  --flow configs/flow.yaml \
  --params configs/params_nominal.yaml \
  --backend mock \
  --run-id run_0001
```

真实 COMSOL：

```bash
python python/run_flow.py \
  --flow configs/flow.yaml \
  --params configs/params_nominal.yaml \
  --backend comsol \
  --run-id run_0001
```

### 8.2 backend 抽象

```python
class SimulationBackend:
    def run_step(self, step_input_path: str) -> dict:
        raise NotImplementedError


class MockBackend(SimulationBackend):
    def run_step(self, step_input_path: str) -> dict:
        # call mock_comsol_worker.py logic
        ...


class ComsolBackend(SimulationBackend):
    def run_step(self, step_input_path: str) -> dict:
        # call comsolbatch + ComsolStepWorker.class
        ...
```

---

## 9. Mock Backend 设计

由于本地没有 COMSOL，必须实现 `mock_comsol_worker.py`。

### 9.1 目的

Mock backend 不用于验证真实物理精度，而用于验证：

1. `flow.yaml` 是否正确。
2. `step_input.json` 是否正确。
3. `state_in/state_out` 是否正确继承。
4. `device_default/device2_for_mat3` 是否正确传递。
5. `mat1/mat2/mat3` 是否正确更新。
6. 两个 wafer 模板是否能在 JSON 中正确切换。
7. S08 是否能让 `aSi_layer.active=false`。
8. cache 是否正确。
9. calibration 是否能闭环。

### 9.2 Mock Step 逻辑

```python
def mock_run_step(step_input):
    state_in = load_json(step_input["state_in"])
    state_out = deepcopy(state_in)

    apply_process_update_mock(step_input, state_out)

    device_results = {}

    for dev_cfg in step_input.get("run_devices", []):
        rve = mock_device_rve(dev_cfg, step_input, state_out)
        device_results[dev_cfg["name"]] = rve
        state_out["device_rves"][dev_cfg["name"]] = rve

    for mat_name in step_input.get("run_mats", []):
        device_key = step_input["mat_inputs"][mat_name]

        if device_key in device_results:
            input_device_rve = device_results[device_key]
        else:
            input_device_rve = state_out["device_rves"][device_key]

        mat_rve = mock_mat_rve(mat_name, input_device_rve, step_input, state_out)
        state_out["wafer_inputs"][mat_name] = mat_rve

    wafer_result = mock_wafer_result(state_out, step_input)
    state_out["wafer_result"] = wafer_result

    append_history(state_out, step_input)

    save_json(state_out, output_dir / "state_out.json")
    save_json({"status": "success", "wafer_result": wafer_result}, output_dir / "step_result.json")
```

### 9.3 Mock Device RVE

```python
def mock_device_rve(dev_cfg, step_input, state):
    mat = state["materials_state"]
    geo = state["geometry_state"]

    sigma_onon = 0.5 * (
        mat["ONON"]["sigma_O_current"] +
        mat["ONON"]["sigma_N_current"]
    )

    sigma_w = mat.get("W", {}).get("sigma_fill", 0.0)
    diameter = geo.get("pillar_diameter", 120e-9)
    diameter_factor = diameter / 120e-9

    if dev_cfg["name"] == "device2_for_mat3":
        factor = 0.8
    else:
        factor = 1.0

    sigx = factor * (0.7 * sigma_onon + 0.3 * sigma_w) * diameter_factor
    sigy = factor * (0.65 * sigma_onon + 0.25 * sigma_w) / diameter_factor

    D0 = 100e9
    D11 = D0 * diameter_factor
    D22 = D0 / diameter_factor

    return make_rve(
        source_step=step_input["step_id"],
        source_model=dev_cfg["template_key"],
        sigx=sigx,
        sigy=sigy,
        D11=D11,
        D22=D22,
        rho=3000.0
    )
```

### 9.4 Mock Mat RVE

```python
def mock_mat_rve(mat_name, device_rve, step_input, state):
    mat_factor = {
        "mat1": 0.8,
        "mat2": 1.0,
        "mat3": 1.2,
    }[mat_name]

    sigx = mat_factor * device_rve["stress_eff"]["sxx"]
    sigy = mat_factor * device_rve["stress_eff"]["syy"]

    D = device_rve["D"]
    D11 = mat_factor * D[0][0]
    D22 = mat_factor * D[1][1]

    return make_rve(
        source_step=step_input["step_id"],
        source_model=f"{mat_name}_template",
        sigx=sigx,
        sigy=sigy,
        D11=D11,
        D22=D22,
        rho=3200.0
    )
```

### 9.5 Mock Wafer Result

```python
def mock_wafer_result(state, step_input):
    weights = {
        "FEOL": 0.30,
        "mat1": 0.20,
        "mat2": 0.25,
        "mat3": 0.25,
        "ONON_layer": 0.15,
        "aSi_layer": 0.10,
    }

    bow_x = 0.0
    bow_y = 0.0

    for slot, rve in state["wafer_inputs"].items():
        if not rve.get("valid", False):
            continue
        if not rve.get("active", True):
            continue

        w = weights.get(slot, 0.0)
        sigx_mpa = rve["stress_eff"]["sxx"] / 1e6
        sigy_mpa = rve["stress_eff"]["syy"] / 1e6

        bow_x += w * sigx_mpa
        bow_y += w * sigy_mpa

    if step_input["templates"]["wafer"].endswith("wafer_with_asi_template.mph"):
        bow_x *= 1.05
        bow_y *= 1.05

    return {
        "bow_x_um": 0.8 * bow_x,
        "bow_y_um": 0.75 * bow_y,
        "kx": bow_x * 1e-5,
        "ky": bow_y * 1e-5
    }
```

---

## 10. COMSOL Java Worker 设计

### 10.1 ComsolStepWorker 职责

`ComsolStepWorker.java` 负责：

1. 读取 `step_input.json`。
2. 读取 `state_in.json`。
3. 应用 `update_rule`：
   - 更新材料状态；
   - 更新几何状态；
   - 处理 aSi active/inactive；
   - 更新 O/N release。
4. 运行 `run_devices` 中声明的 device 模板。
5. 提取每个 device RVE。
6. 对 `run_mats` 中声明的 mat 模板逐个运行：
   - 根据 `mat_inputs` 选择输入 device RVE；
   - 如果本 step 没有计算该 device RVE，则从 `state_in.device_rves` 继承。
7. 将 `FEOL/mat1/mat2/mat3/ONON_layer/aSi_layer` 注入 wafer 模板。
8. 根据 `wafer_template` 选择 `wafer_base_template.mph` 或 `wafer_with_asi_template.mph`。
9. 运行 wafer。
10. 提取 wafer bow。
11. 输出：
    - `step_result.json`
    - `state_out.json`
    - solved mph 到当前 step 目录，可配置是否保存。

### 10.2 Java 执行伪代码

```java
public static void main(String[] args) throws Exception {
    StepInput input = StepInput.load(args[0]);
    WaferState stateIn = WaferState.load(input.stateIn);
    WaferState stateOut = stateIn.deepCopy();

    applyProcessUpdate(stateOut, input);

    Map<String, RveResult> deviceResults = new HashMap<>();

    for (DeviceRunConfig devCfg : input.runDevices) {
        Model deviceModel = ModelUtil.load(devCfg.templatePath);

        injectStateToDevice(deviceModel, stateOut);
        injectParameters(deviceModel, input.parameters);

        runStudy(deviceModel, devCfg.studyTag);

        RveResult rve = extractRveResult(deviceModel, devCfg.extractors);
        rve.sourceStep = input.stepId;
        rve.sourceModel = devCfg.templateKey;

        deviceResults.put(devCfg.name, rve);
        stateOut.deviceRves.put(devCfg.name, rve);

        maybeSaveModel(deviceModel, input.outputDir, devCfg.name);
    }

    for (String matName : input.runMats) {
        String deviceKey = input.matInputs.get(matName);

        RveResult inputDeviceRve;
        if (deviceResults.containsKey(deviceKey)) {
            inputDeviceRve = deviceResults.get(deviceKey);
        } else {
            inputDeviceRve = stateOut.deviceRves.get(deviceKey);
        }

        Model matModel = ModelUtil.load(input.templates.mats.get(matName));

        injectDeviceRveToMat(matModel, inputDeviceRve);
        injectStateToMat(matModel, stateOut);
        injectParameters(matModel, input.parameters);

        runStudy(matModel, input.matStudyTags.get(matName));

        RveResult matRve = extractRveResult(matModel, input.matExtractors.get(matName));
        matRve.sourceStep = input.stepId;
        matRve.sourceModel = matName + "_template";

        stateOut.waferInputs.put(matName, matRve);

        maybeSaveModel(matModel, input.outputDir, matName);
    }

    Model waferModel = ModelUtil.load(input.templates.wafer);

    for (String slot : Arrays.asList("FEOL", "mat1", "mat2", "mat3", "ONON_layer", "aSi_layer")) {
        RveResult slotRve = stateOut.waferInputs.get(slot);
        injectWaferSlot(waferModel, slot, slotRve);
    }

    injectParameters(waferModel, input.parameters);
    injectStateToWafer(waferModel, stateOut);

    runStudy(waferModel, input.waferStudyTag);

    WaferResult waferResult = extractWaferResult(waferModel, input.waferExtractors);
    stateOut.waferResult = waferResult;

    stateOut.appendHistory(input.stepId, input.stepName, waferResult);

    stateOut.save(input.outputDir + "/state_out.json");
    StepResult.writeSuccess(input.outputDir + "/step_result.json", waferResult);

    maybeSaveModel(waferModel, input.outputDir, "wafer");
}
```

---

## 11. COMSOL 提取与注入要求

### 11.1 RVE 提取

每个 RVE 模型应提取：

| 数据 | 来源 |
|---|---|
| `rho` | global evaluation，例如 `gev_rho` |
| `stress_eff.sxx` | `gev1` 或指定 global expression |
| `stress_eff.syy` | `gev1` 或指定 global expression |
| `D` | Cell Periodicity Study 的 Evaluation Group / Global Matrix Evaluation |

当前约定：等效应力来自 `gev1`。实现时建议在 `template_tags.yaml` 中明确每个模板的 tag：

```yaml
extractors:
  device_onon:
    rho: "gev_rho"
    stress: "gev1"
    matrix_group: "solidcp2stdEg"
    matrix_feature: "gmevescp2"

  mat1:
    rho: "gev_rho"
    stress: "gev1"
    matrix_group: "solidcp2stdEg"
    matrix_feature: "gmevescp2"
```

### 11.2 wafer 注入

每个 wafer slot 需要注入：

```text
rho_<slot>
sigx_<slot>
sigy_<slot>
D_<slot>_11 ... D_<slot>_66
```

slot 命名建议：

```text
feol
mat1
mat2
mat3
onon
asi
```

例如：

```text
rho_mat1
sigx_mat1
sigy_mat1
D_mat1_11
D_mat1_12
...
D_mat1_66
```

如果 `active=false`：

1. 若使用 `wafer_base_template`，通常不会包含 aSi slot。
2. 若仍注入 inactive slot，则应注入零应力、零厚度或按模板约定处理。
3. 对两个 wafer 模板的 tag 差异应在 `template_tags.yaml` 中配置。

---

## 12. 缓存机制

### 12.1 缓存对象

缓存粒度：

```text
device_rve_cache
mat1_rve_cache
mat2_rve_cache
mat3_rve_cache
wafer_result_cache
```

### 12.2 Cache Key

#### device cache key

```text
hash(
    step_id,
    device_template,
    parameters_used_by_device,
    materials_state_used_by_device,
    geometry_state_used_by_device
)
```

#### mat cache key

```text
hash(
    step_id,
    mat_name,
    mat_template,
    input_device_rve_hash,
    parameters_used_by_mat,
    materials_state_used_by_mat
)
```

#### wafer cache key

```text
hash(
    step_id,
    wafer_template,
    FEOL_hash,
    mat1_hash,
    mat2_hash,
    mat3_hash,
    ONON_layer_hash,
    aSi_layer_hash,
    wafer_specific_parameters
)
```

### 12.3 继承规则

每个 step 只更新 `flow.yaml` 中声明的 `wafer_inputs.update`。

未声明更新的 slot 从 `state_in` 继承。

示例：

```yaml
wafer_inputs:
  update: ["mat2"]
  inherit: ["FEOL", "mat1", "mat3", "ONON_layer", "aSi_layer"]
```

执行后：

```text
state_out.mat2 = current step result
state_out.FEOL = state_in.FEOL
state_out.mat1 = state_in.mat1
state_out.mat3 = state_in.mat3
state_out.ONON_layer = state_in.ONON_layer
state_out.aSi_layer = state_in.aSi_layer
```

---

## 13. 参数影响范围

用于判断从哪一步开始 rerun。

| 参数 | 起始影响 step |
|---|---|
| `FEOL.sigma_init_x` | S00 |
| `FEOL.sigma_init_y` | S00 |
| `ONON.sigma_O_base` | S01 |
| `ONON.sigma_N_base` | S01 |
| `release.trench_etch_ONON` | S02 |
| `Ox.sigma_trench_fill` | S03 |
| `geometry.pillar_diameter` | S04 |
| `pillar_dep.dep1.sigma_init` | S05a |
| `pillar_dep.dep2.sigma_init` | S05b |
| `pillar_dep.dep3.sigma_init` | S05c |
| `pillar_dep.dep4.sigma_init` | S05d |
| `pillar_dep.dep5.sigma_init` | S05e |
| `release.dpillar_form_ONON` | S06 |
| `aSi.sigma` | S07 |
| `release.mat_remove_ONON` | S08 |
| `W.sigma_fill` | S09 |
| `release.final_ONON` | S10 |

---

## 14. 校准策略

### 14.1 分阶段校准

推荐顺序：

```text
Stage 0:
    使用 nominal params 跑通全流程。

Stage 1:
    S00
    校准 FEOL.sigma_init_x / FEOL.sigma_init_y。

Stage 2:
    S01
    校准 ONON.sigma_O_base / ONON.sigma_N_base。
    若仅凭 bow 难以区分 O/N，可改为 ONON.effective_stress。

Stage 3:
    S02
    固定 ONON base stress，校准 release.trench_etch_ONON。

Stage 4:
    S03
    校准 Ox.sigma_trench_fill。

Stage 5:
    S04
    校准 geometry.pillar_diameter。
    注意 diameter 必须触发 device + mat1/mat2/mat3 + wafer 重跑。

Stage 6:
    S05a
    校准 pillar_dep.dep1.sigma_init。

Stage 7:
    S05b
    校准 pillar_dep.dep2.sigma_init。

Stage 8:
    S05c
    校准 pillar_dep.dep3.sigma_init。

Stage 9:
    S05d
    校准 pillar_dep.dep4.sigma_init。

Stage 10:
    S05e
    校准 pillar_dep.dep5.sigma_init。

Stage 11:
    S05a~S05e joint refinement。
    只允许 dep1~dep5 在顺序校准结果附近小范围调整。

Stage 12:
    S06
    校准 release.dpillar_form_ONON。

Stage 13:
    S07
    校准 aSi.sigma，使用 wafer_with_asi_template。

Stage 14:
    S08
    校准 release.mat_remove_ONON，切换回 wafer_base_template，并将 aSi_layer inactive。

Stage 15:
    S09
    校准 W.sigma_fill。

Stage 16:
    S10
    优先作为验证点。
    若 S00~S09 已拟合而 S10 偏差明显，再开放 release.final_ONON。

Stage 17:
    小范围全局联合优化。
```

### 14.2 Loss 函数

每一步均有实验 bow：

```text
Loss_bow =
Σ_step w_step [
    ((bow_x_sim - bow_x_exp) / scale_x)^2
  + ((bow_y_sim - bow_y_exp) / scale_y)^2
]
```

建议：

```text
scale_x = scale_y = 20~50 um
```

可选方向性约束：

```text
Loss_aniso =
Σ_step w_step [
    (((bow_x_sim - bow_y_sim) - (bow_x_exp - bow_y_exp)) / scale_aniso)^2
]
```

总损失：

```text
Loss =
    Loss_bow
  + lambda_aniso * Loss_aniso
  + lambda_reg * Loss_reg
```

参数正则：

```text
Loss_reg =
Σ_j lambda_j * ((p_j - p_j_prior) / p_j_range)^2
```

### 14.3 建议参数边界

| 参数 | 建议范围 |
|---|---|
| `FEOL.sigma_init_x` | -500 ~ 500 MPa |
| `FEOL.sigma_init_y` | -500 ~ 500 MPa |
| `ONON.sigma_O_base` | -300 ~ 300 MPa |
| `ONON.sigma_N_base` | -1000 ~ 500 MPa |
| `release.trench_etch_ONON` | 0.3 ~ 1.1 |
| `Ox.sigma_trench_fill` | -500 ~ 500 MPa |
| `geometry.pillar_diameter` | nominal ± 5% 或按量测范围 |
| `pillar_dep.dep*.sigma_init` | 根据具体材料设置，默认 -1000 ~ 1000 MPa |
| `release.dpillar_form_ONON` | 0.3 ~ 1.1 |
| `aSi.sigma` | -800 ~ 800 MPa |
| `release.mat_remove_ONON` | 0.2 ~ 1.1 |
| `W.sigma_fill` | -1500 ~ 1500 MPa |
| `release.final_ONON` | 0.8 ~ 1.05 |

---

## 15. 开发里程碑

### Milestone 1：Schema 与配置解析

实现：

1. 读取 `flow.yaml`。
2. 读取 `params_nominal.yaml`。
3. 生成每一步 `step_input.json`。
4. 初始化 `state_out.json`。
5. 校验所有 slot、device key、mat input 是否存在。

验收：

```bash
python python/run_flow.py --backend dryrun
```

输出每一步将执行的：

```text
step_id
run_devices
run_mats
mat_inputs
wafer_template
wafer_inputs.update
wafer_inputs.inherit
```

### Milestone 2：Mock Backend

实现：

1. `mock_comsol_worker.py`。
2. nominal 全流程可跑通。
3. 输出完整 `runs/run_xxx/summary.csv`。

验收：

```bash
python python/run_flow.py \
  --flow configs/flow.yaml \
  --params configs/params_nominal.yaml \
  --backend mock \
  --run-id mock_001
```

### Milestone 3：Mock Calibration

实现：

1. fake experiment 生成。
2. 分阶段校准。
3. Optuna 或 scipy 最小闭环。

验收：

```bash
python python/calibration_optuna.py \
  --flow configs/flow.yaml \
  --experiment exp/fake_experiment.csv \
  --backend mock
```

要求：

1. loss 能下降。
2. 参数能回到 hidden true params 附近。
3. S05a~S05e 可按顺序校准。
4. S07 使用 `wafer_with_asi_template`。
5. S08 切回 `wafer_base_template` 且 aSi inactive。

### Milestone 4：Java COMSOL Worker 框架

实现：

1. 读取 `step_input.json`。
2. 读取/写出 `WaferState`。
3. 支持 `run_devices`。
4. 支持 `run_mats`。
5. 支持 `mat_inputs`。
6. 支持两个 wafer 模板选择。
7. 支持 `gev1` stress 提取。
8. 支持 6x6 stiffness matrix 提取。
9. 支持 wafer bow 提取。

初期可不求解真实模型，先 stub 出 JSON 结构，确保与 Python 接口一致。

### Milestone 5：接入真实 COMSOL

将 backend 切换为：

```bash
python python/run_flow.py --backend comsol
```

先只跑：

```text
S00
S01
S02
```

确认后再跑全流程。

---

## 16. 关键验收条件

最终框架必须满足：

1. 可以用 mock backend 在无 COMSOL 本地环境跑通完整 S00~S10。
2. 每一步都有 `step_input.json`、`step_result.json`、`state_out.json`。
3. 每一步没有更新的 RVE slot 能从上一工艺正确继承。
4. `mat1/mat2` 能使用 `device_default`。
5. `mat3` 能使用 `device2_for_mat3`。
6. wafer 模板能根据 step 选择 `base` 或 `with_asi`。
7. S07 使用 `wafer_with_asi_template`。
8. S08 后切回 `wafer_base_template`，并将 `aSi_layer.active=false`。
9. S05a~S05e 可以分别校准 5 种材料 init stress。
10. O/N base stress 只在 S01 校准，后续只通过 release factor 更新。
11. pillar diameter 改变时，从 S04 开始重新运行 device + mat1/mat2/mat3 + wafer。
12. 输出 summary.csv，并可和 experiment.csv 计算 loss。
13. 后续能将 backend 从 mock 无缝替换成 comsol。

---

## 17. 推荐优先开发顺序

1. `flow.yaml`
2. `params_nominal.yaml`
3. `WaferState` schema
4. `StepInput` schema
5. `run_flow.py --backend dryrun`
6. `mock_comsol_worker.py`
7. `run_flow.py --backend mock`
8. `summary.csv + compute_loss.py`
9. `calibration_optuna.py`
10. `ComsolStepWorker.java`
11. 接真实 COMSOL 模板
12. 分阶段真实校准

---

## 18. 开发注意事项

1. 不要在 Java 中硬编码工艺步骤。工艺步骤必须来自 `flow.yaml`。
2. 不要在 Java 中硬编码某一步该跑哪些 mat。应读取 `step_input.json`。
3. 不要假设只有一个 device RVE。必须支持 `device_rves` 字典。
4. 不要假设 mat1/mat2/mat3 串联。它们是并列结构。
5. 不要每步重置所有 wafer input。未更新 slot 必须继承。
6. 不要在 S02/S06/S08/S10 新建 O/N init stress。只能用 release factor。
7. 不要一开始全局优化所有参数。必须先支持分阶段校准。
8. 不要依赖本地 COMSOL。先用 mock backend 验证软件架构。
9. Java Worker 和 Mock Worker 必须输出同样的 `state_out.json` 和 `step_result.json`。
10. 两个 wafer 模板选择必须完全由 JSON 驱动。
