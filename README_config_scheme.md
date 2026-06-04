# COMSOL 20-Step DAG 配置方案：YAML 流程配置 + TXT 参数文件

## 1. 分层配置

```text
configs/
├── flow.yaml                 # 全局流程编排
├── templates.yaml            # COMSOL 模板注册表
├── extractors.yaml           # study/evaluation/extractor tag 注册表
├── steps/                    # 每一步工艺 DAG 配置
├── params/                   # COMSOL 参数 TXT 文件
└── experiments/              # 实验 bow 数据
```

核心原则：

- `flow.yaml` 只负责串联工艺步骤。
- `steps/Sxx_xxx.yaml` 负责单步 DAG：哪些 node run，哪些 node inherit。
- `templates.yaml` 负责模板路径。
- `extractors.yaml` 负责 study tag、evaluation tag、matrix evaluation tag。
- `params/*.txt` 保存 COMSOL 参数，不使用 YAML。
- Python Orchestrator 负责把 YAML 配置和 TXT 参数合并成 `step_input.json`。
- Java COMSOL Worker 只读取 `step_input.json`，不直接解析 flow.yaml 或 params/*.txt。

## 2. TXT 参数格式

推荐格式：

```text
# name                  expression          description
sigma_O_base            100[MPa]            ONON oxide base intrinsic stress
sigma_N_base           -300[MPa]            ONON nitride base intrinsic stress
pillar_diameter         120[nm]             pillar diameter
```

解析规则：

1. 空行跳过。
2. `#` 开头为注释。
3. 每行至少两列：`name` 和 `expression`。
4. 第三列及以后合并为 description。
5. expression 保留 COMSOL 语法，例如 `100[MPa]`、`120[nm]`。
6. 多个 TXT 文件合并时，后读入的同名参数覆盖先读入的参数。

推荐合并顺序：

```text
global_params.txt
→ step-specific params.txt
→ calibration_override.txt
```

## 3. 标准 DAG

```text
device_main
    ├── mat1
    ├── mat2
    └── mat3

device_aux
    └── mat4

mat1 + mat2 + mat3 + mat4
    ↓
die

die + onon_device
    ↓
wafer
```

其中：

- `device_main` 默认输入 mat1/mat2/mat3。
- `device_aux` 默认输入 mat4。
- `onon_device` 只进入 wafer。
- `die` 接收 mat1~4。
- `wafer` 只接收 die RVE 和 onon_device RVE。

## 4. action 规则

每个 node 必须显式指定：

```yaml
action: run
```

或：

```yaml
action: inherit
```

`run` 表示本步骤重新打开模板、注入参数、求解并提取结果。

`inherit` 表示本步骤不运行该模型，从上一工艺 `state_in.json` 继承该 RVE。继承时必须检查上一状态对应 RVE `valid=true`，否则报错。

## 5. ProcessState

每步输出 `state_out.json`，建议结构：

```json
{
  "step_id": "S02",
  "step_name": "trench_etch",
  "rve": {
    "device_main": {},
    "device_aux": {},
    "onon_device": {},
    "mat1": {},
    "mat2": {},
    "mat3": {},
    "mat4": {},
    "die": {}
  },
  "wafer_result": {
    "bow_x_um": 0.0,
    "bow_y_um": 0.0,
    "kx": 0.0,
    "ky": 0.0
  },
  "materials_state": {},
  "geometry_state": {},
  "history": []
}
```

所有 RVE 统一格式：

```json
{
  "valid": true,
  "active": true,
  "source_step": "S02",
  "source_template": "mat.mat_tpl01",
  "rho": 3000.0,
  "stress_eff": {
    "sxx": 95000000.0,
    "syy": 90000000.0
  },
  "D": [[... 6x6 ...]],
  "meta": {
    "hash": "optional"
  }
}
```

## 6. Python Orchestrator 解析顺序

```text
flow.yaml
→ templates.yaml
→ extractors.yaml
→ global_params.txt
→ steps/Sxx_xxx.yaml
→ step-specific params txt
→ calibration_override.txt
→ step_input.json
```

## 7. 输出文件

每个 step 目录建议输出：

```text
step_input.json
state_out.json
step_result.json
manifest.json
device_main_rve.json
mat1_rve.json
...
wafer_result.json
```

## 8. manifest.json

每步记录每个 node 的执行信息：

```json
{
  "step_id": "S02",
  "nodes": {
    "device_main": {
      "action": "run",
      "status": "success",
      "template": "device.dev04",
      "source_step": "S02",
      "output": "rve.device_main"
    },
    "mat2": {
      "action": "inherit",
      "status": "success",
      "source_step": "S01",
      "output": "rve.mat2"
    }
  }
}
```

## 9. 验收条件

1. 参数文件位于 `configs/params/*.txt`。
2. `flow.yaml` 只串联步骤，不展开所有 node。
3. 每个 step 的 DAG 独立保存在 `configs/steps/*.yaml`。
4. 每个 node 可以手动指定 `run` 或 `inherit`。
5. wafer 只接收 `rve.die` 和 `rve.onon_device`。
6. die 只接收 `rve.mat1` ~ `rve.mat4`。
7. Mock Backend 和 Java COMSOL Backend 输入输出结构一致。
