# comsol_opt 用户手册

本文面向第一次使用 `comsol_opt` 的用户，介绍这个工具解决什么问题、项目由哪些模块组成、如何运行一次流程、如何查看结果、如何做校准，以及接入真实 COMSOL 后端时需要准备什么。

## 1. 工具简介

`comsol_opt` 是一个用于多尺度 COMSOL 晶圆翘曲校准流程的 Python 编排层。它不负责在 Python 里重建 COMSOL 几何或物理场，而是把工艺步骤、参数文件、RVE 状态传递、后端调度、结果汇总和校准逻辑组织成可重复执行的流程。

当前主流程是 v2 的 20 步工艺链：

```text
S01..S20 工艺步骤
  -> pillar / sc RVE
  -> decap1 / decap2 / decap3 / fecap RVE
  -> die RVE
  -> wafer model
  -> bow_x_um / bow_y_um 汇总
  -> staged calibration
```

你可以把本工具理解成三层：

1. **配置层**：用 YAML 和 COMSOL 参数 TXT 描述流程、模板、抽取器、实验数据和校准空间。
2. **编排层**：Python 根据配置展开每一步输入，生成 `step_input.json`，传递上一步状态，调用后端。
3. **后端层**：`dryrun` 只生成输入，`mock` 用确定性的 Python 模拟物理输出，`comsol` 调用 Java COMSOL worker。

## 2. 适用场景

适合使用本工具的情况：

- 你有一条由多个 COMSOL 模型组成的晶圆翘曲仿真链。
- 每个步骤需要继承或更新若干 RVE 状态。
- 你希望每次运行都留下可审计的输入、输出、日志和 summary。
- 你希望用实验 bow 数据对参数做全局或分阶段校准。
- 你希望先用 mock 后端验证流程结构，再切换到真实 COMSOL。

不适合把它当成：

- COMSOL 模型自动建模器。
- 通用有限元求解器。
- 自动识别 COMSOL 标签的工具。真实模型路径、study、material tag、stress tag、extractor tag 仍需在配置里显式登记。

## 3. 环境准备

### 3.1 Python

项目主要使用 Python 标准库。可选依赖在 `requirements.txt` 中：

```bash
python3 -m pip install -r requirements.txt
```

如果安装了 Optuna，校准默认使用 Optuna TPE sampler；如果没有安装，代码会自动退回确定性随机搜索。单元测试不依赖 `pytest`。

### 3.2 COMSOL

如果只运行 `dryrun` 或 `mock`，不需要 COMSOL。

如果使用真实 `comsol` 后端，你需要：

- 可运行的 COMSOL 6.3 命令行环境。
- 已编译或可调用的 `java/ComsolStepWorker.java` worker。
- `configs/templates.yaml` 中所有真实模型路径和 COMSOL 标签已替换为有效值。
- `step_input.json` 中不能存在任何以 `TODO_` 开头的字段。COMSOL 后端会在调用 Java 前检查并失败退出。

## 4. 快速开始

在仓库根目录执行：

```bash
python3 -m unittest tests/test_workflow.py -v
```

生成 20 步输入文件但不执行后端：

```bash
python3 python/run_flow.py --backend dryrun --run-id dryrun_001
```

运行完整 mock 流程：

```bash
python3 python/run_flow.py --backend mock --run-id mock_001
```

计算 mock 结果相对实验数据的 loss：

```bash
python3 python/compute_loss.py --summary runs/mock_001/summary.csv
```

运行一次 mock 全局校准：

```bash
python3 python/calibration_optuna.py --backend mock --run-id calib_001 --n-trials 5
```

运行选定阶段的 staged calibration：

```bash
python3 python/calibration_optuna.py \
  --backend mock \
  --mode staged \
  --stages G2_trench_release,G10_final \
  --run-id staged_001 \
  --n-trials 5
```

## 5. 项目目录

```text
configs/
  flow.yaml
  templates.yaml
  extractors.yaml
  calibration_space.yaml
  steps/
  params/
  experiments/
python/
  run_flow.py
  compute_loss.py
  calibration_optuna.py
  comsol_opt/
java/
  ComsolStepWorker.java
tests/
  test_workflow.py
docs/
  roadmap.md
archive/
```

重要目录和文件：

- `configs/flow.yaml`：当前 v2 20 步流程入口，列出步骤文件、参数 TXT 加载顺序、模板注册表和 ONON alias 来源。
- `configs/steps/S01_*.yaml` 到 `S20_*.yaml`：20 个工艺步骤配置。通常使用简写 `run:`、`decap:` 和 `preset: full_chain`。
- `configs/templates.yaml`：COMSOL 模板注册表，包含模型路径、节点类型、study、extractor、material target、stress target。
- `configs/extractors.yaml`：抽取器标签注册。
- `configs/params/`：当前 v2 流程使用的 COMSOL 参数 TXT。
- `configs/experiments/bow_experiment.csv`：实验 bow 数据。
- `configs/calibration_space.yaml`：校准参数分组、上下界、先验值、正则化尺度和单位。
- `python/comsol_opt/`：核心 Python 包。
- `java/ComsolStepWorker.java`：真实 COMSOL worker 的初版骨架。
- `runs/`：默认运行输出目录。该目录是生成物，不应提交。
- `archive/legacy_config_scheme/`：旧 v1 配置和兼容材料，仅作历史参考或旧流程支持。

## 6. 核心概念

### 6.1 Canonical runtime node

v2 活跃流程只使用以下运行节点名：

```text
pillar
sc
decap1
decap2
decap3
fecap
die
wafer
onon
```

不要在新配置、新日志、新测试或 active code 中使用旧名：

```text
device_main
device_aux
onon_device
mat1
mat2
mat3
mat4
```

历史映射仅用于理解旧材料：

```text
mat1 -> decap1
mat2 -> decap2
mat3 -> decap3
mat4 -> fecap
onon_device -> onon
```

Python v2 validator 会拒绝 active v2 配置中的旧节点名。

### 6.2 RVE 状态链

默认依赖关系如下：

- `pillar` 通常输入到 `decap1`、`decap2`、`decap3` 和 `fecap`。
- `sc` 在 fecap 模板声明需要时输入到 `fecap`。
- `decap1`、`decap2`、`decap3` 消耗 `pillar + onon`。
- `fecap` 消耗 `pillar + sc + onon`。
- `die` 消耗 `decap1 + decap2 + decap3 + fecap + onon`。
- `wafer` 消耗 `die + onon`。
- `onon` 是 alias/snapshot，不是独立 COMSOL run 节点。

每个 RVE 状态保留：

- `rho`
- `stress_eff.sxx`
- `stress_eff.syy`
- 完整 6x6 对称 `D` 矩阵
- Java 输入用的 `D_upper21`

`D_upper21` 是 6x6 对称矩阵上三角 21 个值的紧凑表示。代码中应使用 `python/comsol_opt/v2_contract.py` 的 helper，不要手写 flatten 逻辑。

### 6.3 节点 action

每个 active step node 必须声明以下 action 之一：

- `run`：本步骤运行该节点模板并产生新结果。
- `inherit`：从上一步状态继承同名 RVE。
- `default_from`：从当前或上游某个节点复制默认状态。
- `alias`：创建别名快照，典型用法是 `onon` 指向 S02 的 `pillar`。

v2 中 `wafer` 每个启用步骤都应运行；`onon` 不能作为 COMSOL 模型节点运行。

## 7. 配置文件详解

### 7.1 `configs/flow.yaml`

这是主入口。当前结构示例：

```yaml
version: 2
run_id_default: dev_20step

parameter_txt_order:
  - configs/params/global_params.txt
  - configs/params/calibration_override.txt

templates: configs/templates.yaml

steps:
  - configs/steps/S01_w_plug_cmp.yaml
  - configs/steps/S02_onon_dep.yaml
  ...
  - configs/steps/S20_feslit_buff_cmp.yaml

onon:
  source_step: S02
  source_node: pillar
```

关键字段：

- `version: 2`：启用 v2 流程展开和校验。
- `parameter_txt_order`：COMSOL 参数 TXT 加载顺序。后面的文件覆盖前面的同名参数。
- `templates`：模板注册表路径。
- `steps`：按执行顺序列出的步骤配置路径。
- `onon`：全局 ONON alias 来源。
- `runtime`：运行时保留策略和输入缺失处理策略。

### 7.2 `configs/steps/*.yaml`

步骤配置通常写得很短，让 loader 展开标准链。

示例：只运行 `sc` 与 `fecap`，随后自动运行 `die` 和 `wafer`。

```yaml
step_id: S04
step_name: sc_etch
run:
  sc: p03_SC_form
  fecap: fecap_model_all
```

示例：标准 pillar -> decap/fecap -> die -> wafer 链。

```yaml
step_id: S06
step_name: pillar_etch
preset: full_chain
run:
  pillar: p04p01_pillar_etch
```

示例：一次性声明三个 decap 分支。

```yaml
run:
  decap: decap_model
```

loader 会展开为：

```text
decap_model_decap1
decap_model_decap2
decap_model_decap3
```

如果需要给三个分支不同模板，也可写成 mapping：

```yaml
run:
  decap:
    decap1: custom_decap1_template
    decap2: custom_decap2_template
    decap3: custom_decap3_template
```

### 7.3 `configs/templates.yaml`

模板注册表告诉后端每个逻辑模板对应哪个 COMSOL 模型、哪个节点类型、哪些 study 和 extractor，以及如何把上游 RVE 写入 COMSOL 材料和初始应力字段。

常见字段：

- `path`：模型路径，通常应指向 `models/process_models/.../*.mph`。
- `node_type`：目标节点类型，例如 `pillar`、`fecap`、`wafer`。
- `component`：COMSOL component tag。
- `physics`：COMSOL physics tag。
- `studies.stress`：应力求解 study tag。
- `studies.cp`：均匀化/CP/D 抽取 study tag；stress-only 节点可为空。
- `extractors`：结果抽取标签。
- `input_targets`：上游 RVE 输入对应的 material/stress 写入位置。

`template_families` 用于减少重复配置。例如 decap 三个分支可以共享 base，并在 members 中覆盖 component、physics 或 study。

注意：任何 `TODO_*` 字段都表示真实 COMSOL 标签尚未确认。`dryrun` 和 `mock` 可以运行；`comsol` 后端会拒绝执行。

### 7.4 `configs/params/*.txt`

当前 v2 运行时参数源是 COMSOL 风格 TXT 文件，不是 YAML。

解析规则：

1. 空行、`#` 注释和 `%` 注释会被忽略。
2. 每行通常是 `name expression description...`。
3. 支持简单单位表达式，例如 `100[MPa]`、`120[nm]`。
4. 后加载的 TXT 会覆盖先加载的同名参数。

运行时每一步都会把参数 TXT 复制到步骤目录：

```text
runs/<run-id>/<step-id>/parameters/
```

同时在 `step_input.json` 中记录：

- `parameter_txt_order`
- `parameter_txt_paths`
- `raw_parameters`
- `parameters`

如果通过 CLI 传入 `--params some.yaml`，该 YAML 会作为 trial/global override 使用，主要用于校准流程。

### 7.5 `configs/experiments/bow_experiment.csv`

实验数据用于 summary 和 loss。当前列：

```csv
step_id,step_name,bow_x_um,bow_y_um,temperature_C,weight_x,weight_y
```

只有 CSV 中有实验值的步骤会参与 loss 计算。当前默认实验文件包含 S00、S01、S02、S20，其中 active v2 flow 实际会匹配启用步骤里的实验步。

### 7.6 `configs/calibration_space.yaml`

校准空间由多个 group 组成，每组包含：

- `name`：阶段名。
- `start_step`：阶段起点标记。
- `target_step`：该组用哪个 summary step 计算 stage loss。
- `params`：本阶段采样的参数。

每个参数包含：

- `key`：点分路径，例如 `release.final_ONON`。
- `low` / `high`：采样上下界。
- `prior`：先验/名义值。
- `scale`：正则化归一化尺度。
- `unit`：单位说明。

默认校准只使用 `target_step` 存在于当前 `configs/flow.yaml` 的组。显式请求 disabled 或 missing step 的 stage 会提前报错。

## 8. 命令行工具

### 8.1 `python/run_flow.py`

主流程运行入口。

```bash
python3 python/run_flow.py --backend mock --run-id mock_001
```

常用参数：

- `--flow`：流程入口，默认 `configs/flow.yaml`。
- `--params`：可选 YAML 参数覆盖文件，主要用于 trial。
- `--experiment`：实验数据 CSV，默认 `configs/experiments/bow_experiment.csv`。
- `--backend`：`dryrun`、`mock` 或 `comsol`。
- `--comsol-command`：真实 COMSOL 后端命令。
- `--run-id`：运行 ID，必填。
- `--runs-root`：输出根目录，默认 `runs`。
- `--use-cache`：启用 step cache。
- `--parameter-map`：旧流程兼容参数映射，v2 一般不需要。

后端说明：

- `dryrun`：生成每一步 `step_input.json`，不生成 `state_out.json` 和 summary。
- `mock`：完整执行 mock worker，生成状态、结果、日志和 `summary.csv`。
- `comsol`：调用外部 Java/COMSOL worker。需要 `--comsol-command`。

COMSOL 后端示意：

```bash
python3 python/run_flow.py \
  --backend comsol \
  --comsol-command "comsol batch -inputfile java/ComsolStepWorker.java" \
  --run-id real_001
```

实际命令需要按你的 COMSOL 安装和 worker 打包方式调整。代码会把 `step_input.json` 路径追加到命令末尾。

### 8.2 `python/compute_loss.py`

从 summary 计算 loss：

```bash
python3 python/compute_loss.py --summary runs/mock_001/summary.csv
```

可调尺度：

```bash
python3 python/compute_loss.py \
  --summary runs/mock_001/summary.csv \
  --scale-x 30 \
  --scale-y 30
```

loss 计算逻辑：

```text
weight_x * ((bow_x_sim - bow_x_exp) / scale_x)^2
+ weight_y * ((bow_y_sim - bow_y_exp) / scale_y)^2
```

只有同时包含 `bow_x_exp_um` 和 `bow_y_exp_um` 的 summary 行会参与计算。

### 8.3 `python/calibration_optuna.py`

校准入口。

全局校准：

```bash
python3 python/calibration_optuna.py \
  --backend mock \
  --run-id calib_001 \
  --n-trials 5
```

随机搜索：

```bash
python3 python/calibration_optuna.py \
  --backend mock \
  --optimizer random \
  --run-id calib_random_001 \
  --n-trials 5
```

分阶段校准：

```bash
python3 python/calibration_optuna.py \
  --backend mock \
  --mode staged \
  --stages G2_trench_release,G10_final \
  --run-id staged_001 \
  --n-trials 5
```

常用参数：

- `--flow`：流程入口。
- `--params`：起始参数 YAML。
- `--calibration-space`：校准空间 YAML。
- `--experiment`：实验 bow CSV。
- `--backend`：`mock` 或 `comsol`。
- `--comsol-command`：真实 COMSOL 后端命令。使用 `--backend comsol` 时必须提供。
- `--run-id`：校准运行 ID。
- `--runs-root`：输出根目录。
- `--n-trials`：每个全局/阶段 trial 数。
- `--seed`：随机种子，默认 17。
- `--optimizer`：`auto` 或 `random`。`auto` 会优先使用 Optuna，缺失时回退 random。
- `--mode`：`global` 或 `staged`。
- `--stages`：逗号分隔的 group 名或 step ID。

COMSOL 校准示意：

```bash
python3 python/calibration_optuna.py \
  --backend comsol \
  --comsol-command "comsol batch -inputfile java/ComsolStepWorker.java" \
  --run-id real_calib_001 \
  --n-trials 5
```

实际命令需要按你的 COMSOL 安装和 worker 打包方式调整。校准会把该命令传给每个 trial 的 backend，backend 再把当前步骤的 `step_input.json` 路径追加到命令末尾。

## 9. 输出文件说明

一次 mock 运行的默认目录：

```text
runs/<run-id>/
  _initial_state.json
  S01/
    step_input.json
    state_out.json
    step_result.json
    manifest.json
    *_rve.json
    wafer_result.json
    parameters/
    logs/
      step.log
      interface.json
  ...
  S20/
  summary.csv
```

### 9.1 `step_input.json`

后端消费的核心输入。v2 中采用紧凑契约：

- `template_registry`：模板注册表来源。
- `template_specs`：本步骤实际使用的模板规格。
- `rve_inputs`：本步骤用到的上游 RVE payload，按 source 去重。
- `runs`：紧凑执行计划，每个 run 包含 `node`、`template` 和 `inputs`。
- `nodes`：展开后的节点 action 列表。
- `parameter_txt_paths`：已复制到步骤目录的参数 TXT。
- `parameter_txt_order`：COMSOL worker 应按此顺序加载 TXT。
- `state_in`：上一步状态文件路径。
- `output_dir`：本步骤输出目录。

### 9.2 `state_out.json`

步骤完成后的完整状态，供下一步骤继承。v2 关键字段：

- `rve.pillar`
- `rve.sc`
- `rve.decap1`
- `rve.decap2`
- `rve.decap3`
- `rve.fecap`
- `rve.die`
- `rve.onon`
- `wafer_result`
- `materials_state`
- `geometry_state`
- `history`

### 9.3 `step_result.json`

后端返回的高层结果，形状应在 mock 和 COMSOL 后端之间保持一致。通常用于判断步骤状态和定位输出文件。

### 9.4 `manifest.json`

记录每个节点的输入来源、输出文件和运行信息。用于追踪某个 RVE 是从哪一步、哪个节点、哪个模板产生的。

### 9.5 `logs/step.log`

人类可读日志。用于审计完整仿真路径，包含：

- step id/name
- backend
- node action
- template/model path
- RVE input source
- material/stress target tag
- wafer input
- bow 输出
- COMSOL worker 命令、状态和失败信息

### 9.6 `logs/interface.json`

机器可读接口审计日志。重点记录 COMSOL wiring：

- run node
- template
- model path
- studies
- extractors
- input RVE source/source step/source node
- material target
- stress target
- result file

默认不存完整 RVE 矩阵，避免日志过大。

### 9.7 `summary.csv`

mock 或 COMSOL 运行完成后生成。v2 列包括：

- `step_id`
- `step_name`
- `pillar_status`
- `sc_status`
- `decap1_status`
- `decap2_status`
- `decap3_status`
- `fecap_status`
- `die_status`
- `wafer_status`
- `onon_source_step`
- `onon_source_node`
- `bow_x_um`
- `bow_y_um`
- `bow_x_exp_um`
- `bow_y_exp_um`
- `weight_x`
- `weight_y`
- `warnings`
- `errors`

## 10. 校准输出

### 10.1 全局校准

默认目录：

```text
runs/<run-id>/
  .cache/
  trials/
    trial_0000/
      params_trial.yaml
      repo/
        configs/
          params/
            calibration_override.txt
      flow/
        summary.csv
        S01/
        ...
    trial_0001/
  calibration_history.csv
  best_params.yaml
  best_summary.csv
```

说明：

- `trial_0000` 通常是名义参数。
- `.cache` 是跨 trial 共享 step cache。
- 每个 trial 都会生成自己的临时 `repo/configs/params/calibration_override.txt`。`run_flow` 使用这份 trial 配置副本，因此 YAML trial 参数会同时进入 `step_input.parameters` 和 COMSOL 参数 TXT 栈。
- `best_params.yaml` 是当前最优参数。
- `best_summary.csv` 是最优 trial 的 summary。
- `calibration_history.csv` 记录每个 trial 的 `loss`、`bow_loss`、`reg_loss` 和 run 目录。

### 10.2 分阶段校准

默认目录：

```text
runs/<run-id>/
  out/
    S02_G2_trench_release/
      .cache/
      stage.log
      trials/
      calibration_history.csv
      best_params.yaml
      best_summary.csv
    S20_G10_final/
      .cache/
      stage.log
      trials/
      calibration_history.csv
      best_params.yaml
      best_summary.csv
    final_params.yaml
    calibration_history.csv
```

分阶段逻辑：

- 每个 stage 只采样该组参数。
- 每个 stage 的 loss 默认只看 `target_step` 对应的 summary 行。
- 后一个 stage 从前一个 stage 的 `best_params.yaml` 继续。
- stage cache 放在该 stage 目录下的 `.cache`，不是 trial flow 目录内。

## 11. 典型工作流

### 11.1 首次验证

1. 运行单元测试：

```bash
python3 -m unittest tests/test_workflow.py -v
```

2. 生成 dryrun：

```bash
python3 python/run_flow.py --backend dryrun --run-id dryrun_check
```

3. 检查某一步输入：

```text
runs/dryrun_check/S04/step_input.json
```

4. 运行 mock：

```bash
python3 python/run_flow.py --backend mock --run-id mock_check
```

5. 检查 summary：

```text
runs/mock_check/summary.csv
```

6. 计算 loss：

```bash
python3 python/compute_loss.py --summary runs/mock_check/summary.csv
```

### 11.2 修改一个工艺步骤

1. 打开对应的 `configs/steps/Sxx_*.yaml`。
2. 修改 `run:` 中的模板 key，或增加 `preset: full_chain` / `decap:`。
3. 确认模板 key 存在于 `configs/templates.yaml`。
4. 运行 dryrun 验证展开：

```bash
python3 python/run_flow.py --backend dryrun --run-id dryrun_step_edit
```

5. 检查该步骤 `step_input.json` 中的 `runs`、`template_specs` 和 `rve_inputs`。
6. 运行测试和 mock。

### 11.3 增加或替换 COMSOL 模板

1. 在 `configs/templates.yaml` 增加模板条目。
2. 填写真实 `path`、`node_type`、`component`、`physics`、`studies`、`extractors`。
3. 为所有输入 slot 填写 `input_targets`。
4. 确认 material target 包含：

```text
component
tag
elastic_group
elastic_property
elasticity_order
D_format: symmetric_upper21
```

5. 确认 stress target 包含：

```text
component
physics
feature
property
```

6. 在步骤 YAML 中引用新模板。
7. 运行 dryrun，确认 `step_input.json` 没有解析错误。
8. 运行 mock，确认流程层没有问题。
9. 只有全部 `TODO_*` 被替换后，再使用 `comsol` 后端。

### 11.4 调整实验数据

1. 编辑 `configs/experiments/bow_experiment.csv`。
2. 保持列名不变。
3. 确保 `step_id` 与 active flow 的步骤 ID 匹配。
4. 重新运行 mock 或 COMSOL。
5. 用 `compute_loss.py` 重新计算 loss。

### 11.5 增加校准参数

1. 在 `configs/calibration_space.yaml` 找到或新增 group。
2. 增加参数 spec：

```yaml
- key: some.group.param
  low: 0.0
  high: 1.0
  prior: 0.5
  scale: 0.5
  unit: ratio
```

3. 确认该 key 能在参数结构中被设置。默认基础参数来自参数 TXT 解析后的嵌套结构。
4. 确认 group 的 `target_step` 是 active flow 中启用的步骤。
5. 运行少量 trial：

```bash
python3 python/calibration_optuna.py \
  --backend mock \
  --optimizer random \
  --run-id calib_smoke \
  --n-trials 2
```

## 12. Python 模块说明

`python/comsol_opt/` 中主要模块：

- `flow_runner.py`：加载 flow、展开 v2 step、准备参数 TXT、循环执行步骤、写 summary。
- `flow.py`：对外导出常用 flow API。
- `step_input.py`：校验 flow，构建后端消费的 `step_input.json`。
- `v2_contract.py`：v2 canonical node、输入链、D 矩阵紧凑表示和 RVE payload helper。
- `backends.py`：`dryrun`、`mock`、`comsol` 后端接口与工厂。
- `mock_comsol_worker.py`：确定性的 mock worker，实现 v2 状态传递和日志输出。
- `state.py`：初始状态构造。
- `schemas.py`：输入和状态数据的基本 schema 校验。
- `summary.py`：读取实验数据并写 `summary.csv`。
- `loss.py`：从 summary 行计算 bow loss。
- `calibration.py`：全局和分阶段校准逻辑、Optuna fallback、cache 复用。
- `cache_manager.py`：step cache key、store、restore。
- `parameter_txt.py`：COMSOL 参数 TXT 解析和旧流程 trial TXT 写入。
- `params.py`：参数 flatten/copy/set，以及从 COMSOL TXT 参数转成模型参数结构。
- `config_io.py`：JSON/YAML 读写。新增 JSON/YAML 写入应使用 `dump_data`。
- `process.py`、`rve.py`：旧流程兼容和基础 RVE 逻辑。

CLI 入口：

- `python/run_flow.py`
- `python/compute_loss.py`
- `python/calibration_optuna.py`

## 13. 后端契约

所有后端实现同一个接口：

```python
SimulationBackend.run_step(step_input_path) -> dict
```

后端应该读取 `step_input.json`，在 `output_dir` 写出约定文件，并返回状态字典。

### 13.1 DryRunBackend

用途：

- 检查 flow 是否能加载和展开。
- 检查 `step_input.json` 是否符合 schema。
- 检查参数 TXT 是否按顺序复制。

不会写：

- `state_out.json`
- `summary.csv`
- node result

### 13.2 MockBackend

用途：

- 在没有 COMSOL 时验证完整流程。
- 为测试和校准开发提供确定性输出。
- 验证日志、summary、loss、cache 和 staged calibration。

会写完整步骤输出和 run summary。

### 13.3 ComsolBackend

用途：

- 调用真实 COMSOL worker。

运行前检查：

- 必须提供 `--comsol-command`。
- `step_input.json` 内不能有 `TODO_*`。

运行过程：

1. 读取 `step_input.json`。
2. 记录 COMSOL 命令到 `logs/step.log`。
3. 检查 unresolved TODO tags。
4. 调用外部命令，命令末尾追加 `step_input.json`。
5. 读取 `step_result.json`。
6. 把 worker status 追加到 `logs/step.log`。

真实 worker 应输出与 mock 后端同形状的 JSON。

## 14. COMSOL worker 输入重点

Java worker 应按 `step_input.json` 中的 `parameter_txt_order` 加载参数 TXT。

对每个 run node：

1. 从 `template_specs` 找模板。
2. 从 `rve_inputs` 找上游 RVE payload。
3. 根据 `runs[].inputs` 把 source 绑定到 slot。
4. 根据模板 `input_targets` 写 COMSOL material 和 initial stress。
5. 运行配置中的 studies。
6. 用 extractors 读出 `rho`、`stress_eff`、`D` 或 wafer bow。
7. 写 result file、manifest、state_out 和 step_result。

RVE payload 中与 COMSOL 最相关的字段：

```text
rho
sxx
syy
D
D_upper21
symmetric_upper21
```

`D_upper21` 的顺序由 `v2_contract.py` 统一定义，Java 侧必须按同一顺序写入各向异性刚度矩阵。

## 15. 缓存

`run_flow.py` 可以用 `--use-cache` 启用 step cache：

```bash
python3 python/run_flow.py \
  --backend mock \
  --run-id mock_cached \
  --use-cache
```

cache key 基于 `step_input.json`，并包含 staged 参数 TXT 内容 hash。因此只改 COMSOL TXT 参数也会使相关步骤 cache 失效。

校准默认启用共享 cache：

- 全局校准：`runs/<run-id>/.cache`
- 分阶段校准：`runs/<run-id>/out/<step>_<group>/.cache`

不要把生成的 cache 提交到版本库。

## 16. 常见问题

### 16.1 dryrun 成功，但 mock 或 COMSOL 找不到 state

`dryrun` 不生成 `state_out.json`，因此它只适合检查输入展开。需要完整状态链时使用 `mock` 或 `comsol`。

### 16.2 v2 配置报 legacy node name

active v2 配置只能使用 canonical names。把旧名替换为：

```text
mat1/mat2/mat3/mat4 -> decap1/decap2/decap3/fecap
onon_device -> onon
```

### 16.3 COMSOL 后端提示 unresolved TODO tags

说明 `step_input.json` 中还有 `TODO_*` 值。需要回到 `configs/templates.yaml`，用真实 COMSOL export 中的 tag 替换。mock 和 dryrun 允许 TODO，用于流程开发；真实 COMSOL 不允许。

### 16.4 校准显式指定某个 stage 报 missing/disabled

`--stages` 可以指定 group 名、start step 或 target step，但目标 step 必须在当前 flow 中启用。比如配置里仍有历史 group 指向 S00 或 S05a，而当前 v2 flow 是 S01 到 S20，这类 group 默认会被过滤；显式指定则会报错。

### 16.5 loss 的步数比 summary 行数少

只有同时含有实验 `bow_x_exp_um` 和 `bow_y_exp_um` 的 summary 行参与 loss。没有实验值的步骤会被跳过。

### 16.6 参数 TXT 改了但结果没变

如果没有启用 cache，确认运行目录是不是旧的。如果启用了 cache，正常情况下 TXT 内容 hash 会进入 key；仍有问题时删除该 run 的 `.cache` 后重跑。

### 16.7 Windows 下 `python3` 不可用

可尝试把命令中的 `python3` 替换为 `python`：

```bash
python -m unittest tests/test_workflow.py -v
python python/run_flow.py --backend mock --run-id mock_001
```

## 17. 开发和变更规则

修改项目时建议遵循：

- 改 active v2 流程时优先修改 `configs/flow.yaml`、`configs/steps/`、`configs/templates.yaml` 和 `configs/params/`。
- 不要把旧 v1 runtime node 名带回 active config。
- 新增 JSON/YAML 写入使用 `dump_data`。
- 生成输出放在 `runs/` 或临时目录，不提交。
- 改 workflow scope 或后端契约时，同步更新 `README.md`、`AGENTS.md` 和 `docs/roadmap.md`。
- 完成前运行：

```bash
python3 -m unittest tests/test_workflow.py -v
```

## 18. 推荐阅读顺序

第一次接手该项目时，推荐按这个顺序看：

1. 本文档。
2. `README.md`。
3. `configs/flow.yaml`。
4. `configs/steps/S01_*.yaml` 到 `S20_*.yaml`。
5. `configs/templates.yaml`。
6. `python/run_flow.py`。
7. `python/comsol_opt/flow_runner.py`。
8. `python/comsol_opt/step_input.py`。
9. `python/comsol_opt/mock_comsol_worker.py`。
10. `python/comsol_opt/calibration.py`。
11. `docs/roadmap.md`。

## 19. 当前限制

截至当前基线，仍需注意：

- `java/ComsolStepWorker.java` 是第一版 COMSOL 6.3 worker 骨架，仍需要真实 COMSOL 模型和 tag 验证。
- `configs/templates.yaml` 中仍有 `TODO_*` 字段，真实 COMSOL 后端会因此失败。
- 部分模板路径仍是占位或待确认路径。
- 分阶段校准目前会运行完整 flow，并按 stage target step 取 loss；checkpointed staged rerun 仍在 roadmap。
- Java worker 的编译/执行测试需要本地或 CI 提供 Java/COMSOL stub 后再完善。

## 20. 最小自检清单

在把一次改动交给别人使用前，至少确认：

- `python3 -m unittest tests/test_workflow.py -v` 通过。
- `python3 python/run_flow.py --backend dryrun --run-id dryrun_check` 能生成 20 个步骤输入。
- `python3 python/run_flow.py --backend mock --run-id mock_check` 能生成 `summary.csv`。
- `python3 python/compute_loss.py --summary runs/mock_check/summary.csv` 能输出 loss。
- 新增或修改的 step 没有 legacy node name。
- 新增模板 key 能在 `configs/templates.yaml` 中解析。
- COMSOL 真实运行前，目标步骤的 `step_input.json` 中没有 `TODO_*`。
