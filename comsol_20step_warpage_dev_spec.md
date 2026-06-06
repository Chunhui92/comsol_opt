# 20步 COMSOL 晶圆翘曲自动仿真链开发方案

> Version: v2.0  
> Date: 2026-06-05  
> Status: Development Spec for Codex  
> Scope: 只实现 20 步工艺自动仿真流程，不实现参数校准、loss、Optuna。  
> Backend target: `dryrun` / `mock` / `comsol` 三后端统一接口。  

---

## Implementation Status Update

当前仓库已按本文档落地 v2 20-step 基线，并做了命名归一化：

- active 配置以 `configs/flow.yaml`、`configs/templates.yaml`、`configs/steps/S01_*.yaml` 到 `S20_*.yaml` 为准。
- canonical 节点名固定为 `pillar`、`sc`、`decap1`、`decap2`、`decap3`、`fecap`、`die`、`wafer`、`onon`。
- 本文档正文中出现的 `mat1/mat2/mat3/mat4`、`onon_device`、`device_main/device_aux` 是开发过程中的历史/COMSOL 原始术语；新代码和 active YAML 不再使用这些名字作为运行时节点。
- `mat1 -> decap1`，`mat2 -> decap2`，`mat3 -> decap3`，`mat4 -> fecap`，`onon_device -> onon`。
- Java 输入不再展开重复的 6x6 对称矩阵元素；Python 在 `step_input.json` 中同时保留完整 `D` 和紧凑 `D_upper21`，供 COMSOL 6.3 worker 写入指定材料/应力 tag。
- 每个 step 输出 `logs/step.log`，用于审计模型、RVE 来源、接口 tag、node 输出和 wafer bow。

---

## 0. 本文档用途

本文档用于指导 Codex 开发 20 步 COMSOL wafer warpage 自动仿真链。目标是将当前已有的 Python DAG 编排框架、本地 COMSOL 脚本识别出的模型接口、以及最新确认的工艺依赖规则统一成一套可实现方案。

开发优先级：

1. 先跑通 `dryrun`：验证 20 步配置、依赖、状态传递、模板引用。
2. 再跑通 `mock`：验证 20 步状态继承、run/inherit 合并、summary 输出、resume。
3. 最后接入 `comsol`：逐步执行真实 COMSOL 模型、提取 RVE 和 wafer bow。

暂时不做：

- 参数校准；
- bow loss 计算；
- Optuna；
- staged calibration；
- 复杂失败回滚；
- 多 run_id 之间结果比较。

---

## 1. 总体目标

实现如下自动仿真链：

```text
S01 ~ S20 process step
  -> pillar / sc
  -> decap mat1/mat2/mat3 或 fecap mat4
  -> die
  -> wafer
  -> bow_x / bow_y 输出
  -> state_out.json 继承到下一步
```

核心要求：

- 每一步都要得到 wafer bow。
- 每一步都要输出完整状态 `state_out.json`。
- S02 使用 pillar/onon RVE 初始化所有层级 RVE。
- 后续某一级 RVE 更新后，所有下游模型必须重新 run。
- 没有更新的节点继承上一步有效 RVE。
- `onon_device` 不是独立 COMSOL 模型，而是指定某个 pillar 模型结果作为 wafer 级输入。
- stress 只保存和传递 `sxx`、`syy`，不要 `szz/sxy/syz/sxz`。
- 是否提取 D6x6 不靠 `only_stress` 标签，而由 COMSOL `study_cp` / extractor 配置决定。
- 若某模型只有 stress study、没有 CP study，则本步只更新 `stress_eff` 和 `rho`，`D` 从上一步同节点状态继承。

---

## 2. 推荐仓库结构

沿用现有 Python orchestration 项目结构，不新建完全独立的 20 步系统。

```text
repo/
├── configs/
│   ├── flow.yaml
│   ├── templates.yaml
│   ├── extractors.yaml              # 可选：若希望模板和 extractor 分离
│   ├── params/
│   │   ├── global.txt
│   │   ├── materials.txt
│   │   └── process.txt
│   └── steps/
│       ├── S01_w_plug_cmp.yaml
│       ├── S02_onon_dep.yaml
│       ├── S03_dti_etch.yaml
│       ├── ...
│       └── S20_feslit_buff_cmp.yaml
│
├── python/
│   ├── run_flow.py
│   └── comsol_opt/
│       ├── flow_runner.py
│       ├── state_manager.py
│       ├── backend_base.py
│       ├── backend_dryrun.py
│       ├── backend_mock.py
│       ├── backend_comsol.py
│       ├── step_validator.py
│       └── config_io.py
│
├── java/
│   ├── ComsolStepWorker.java
│   ├── RveResult.java
│   ├── TransferData.java
│   ├── ComsolChainUtils.java
│   └── JsonParser.java
│
├── models/
│   └── process_models/
│       ├── pillar_models/
│       ├── cap_models/
│       ├── sc_models/
│       ├── die_model.mph
│       ├── p01_wafer_init.mph
│       └── wafer_warp_model.mph
│
├── runs/
└── tests/
    └── test_workflow.py
```

运行输出建议按 `run_id` 隔离：

```text
runs/<run_id>/
├── summary.csv
├── S01_w_plug_cmp/
│   ├── step_input.json
│   ├── state_in.json
│   ├── state_out.json
│   ├── step_result.json
│   ├── manifest.json
│   ├── logs/
│   └── exports/
├── S02_onon_dep/
│   └── ...
└── S20_feslit_buff_cmp/
    ├── state_out.json
    ├── exports/wafer_bow.txt
    └── exports/wafer_bow.png
```

不要采用全局 `state/S01/step_state.json` 作为唯一状态目录；否则不同 run_id 会互相污染。可以保留全局 state 作为兼容模式，但主实现应以 `runs/<run_id>/<step>/state_out.json` 为准。

---

## 3. Canonical 节点命名

最终统一使用以下节点名。不要在新代码中混用 `cap1/cap2/cap3/cap4` 和 `mat1/mat2/mat3/mat4` 两套名称。

| 节点名 | 物理含义 | 是否独立 COMSOL 模型 | 主要用途 |
|---|---|---:|---|
| `pillar` | pillar/device RVE | 是 | decap、fecap、onon_device 的来源 |
| `sc` | SC RVE | 是 | fecap 的额外输入 |
| `mat1` | decap1st1 | 是 | die 输入 |
| `mat2` | decap2 | 是 | die 输入 |
| `mat3` | decap1st3 | 是 | die 输入 |
| `mat4` | fecap | 是 | die 输入，需要 pillar + sc |
| `die` | die RVE | 是 | wafer 输入 |
| `wafer` | wafer 翘曲模型 | 是 | 输出 bow_x/bow_y |
| `onon_device` | wafer 级 ONON RVE 输入 | 否 | 指定某个 pillar 结果的 alias/snapshot |

`onon_device` 的规则：

```text
onon_device 不是单独模型。
onon_device = 手动指定的某个 pillar RVE 结果。
默认建议：onon_device = S02 pillar/onon_dep 的 RVE 快照。
```

---

## 4. 模型依赖关系

固定依赖图：

```text
pillar ─┬─> mat1 / decap1st1
        ├─> mat2 / decap2
        ├─> mat3 / decap1st3
        └─> mat4 / fecap
              ▲
              │
             sc

mat1 + mat2 + mat3 + mat4 ─> die

die + onon_device ─> wafer

onon_device = 指定 pillar RVE 的 alias/snapshot
```

输入规则：

```text
decap mat1/mat2/mat3 输入：
  pillar

fecap mat4 输入：
  pillar + sc

die 输入：
  mat1 + mat2 + mat3 + mat4

wafer 输入：
  die + onon_device
```

---

## 5. 分层触发规则

每个 step 内部的逻辑不是完全独立 node，而是层级触发。

### 5.1 层级顺序

```text
Level 0: pillar / sc
Level 1: mat1 / mat2 / mat3 / mat4
Level 2: die
Level 3: wafer
```

### 5.2 触发原则

总原则：

```text
只要前一级模型 run，后面所有相关下游模型都必须 run。
每一步 wafer 都必须 run，以输出当步 bow。
```

具体规则：

```text
pillar run
  => mat1/mat2/mat3/mat4 根据当前步骤配置全部或部分 run
  => die run
  => wafer run

sc run
  => mat4 run
  => die run
  => wafer run

任一 mat run
  => die run
  => wafer run

die run
  => wafer run

wafer
  => 每一步都 run
```

本步内引用 `rve.xxx` 时必须优先使用本步骤已经更新的状态。例如：

```text
S04: sc run -> mat4 run -> die run -> wafer run
mat4 必须使用 S04 刚生成的 sc RVE，而不是 S03 的旧 sc。
```

---

## 6. S02 初始化规则

S02 是特殊初始化步骤。

执行逻辑：

```text
1. run pillar/onon 模型。
2. 提取 pillar RVE：rho、stress_eff.sxx、stress_eff.syy、D6x6。
3. 生成 onon_device alias：onon_device = S02 pillar RVE 快照。
4. 用该 RVE 初始化所有尚未真实运行的层级：
   - sc
   - mat1
   - mat2
   - mat3
   - mat4
   - die
5. run wafer：输入 die = 默认 onon/pillar RVE，输入 onon_device = S02 pillar RVE。
6. 输出 S02 state_out.json。
```

S02 之后状态必须完整：

```json
{
  "rve": {
    "pillar": {"status": "run", "source_step": "S02", "source_node": "pillar"},
    "onon_device": {"status": "alias", "source_step": "S02", "source_node": "pillar"},
    "sc": {"status": "default_from_onon", "source_step": "S02", "source_node": "pillar"},
    "mat1": {"status": "default_from_onon", "source_step": "S02", "source_node": "pillar"},
    "mat2": {"status": "default_from_onon", "source_step": "S02", "source_node": "pillar"},
    "mat3": {"status": "default_from_onon", "source_step": "S02", "source_node": "pillar"},
    "mat4": {"status": "default_from_onon", "source_step": "S02", "source_node": "pillar"},
    "die": {"status": "default_from_onon", "source_step": "S02", "source_node": "pillar"}
  }
}
```

---

## 7. RVE 状态 schema

### 7.1 RVE 数据结构

stress 只保留 `sxx/syy`。

```json
{
  "valid": true,
  "active": true,
  "status": "run",
  "source_step": "S06",
  "source_node": "mat1",
  "source_model": "xxx.mph",
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
  "meta": {
    "component": "comp6",
    "physics": "solid6",
    "study_stress": "std3",
    "study_cp": "solid6cp1std"
  }
}
```

禁止在新 schema 中写入：

```text
szz
sxy
syz
sxz
```

### 7.2 stress-only 节点

如果某模型没有 CP study，只更新 stress/rho，例如 S16 feslit_etch：

Java 输出：

```json
{
  "node": "mat4",
  "status": "success",
  "rho": 2330.0,
  "stress_eff": {
    "sxx": 1.1e8,
    "syy": -2.8e8
  },
  "D": null
}
```

Python 合并状态时应从上一步同节点继承 D，并在最终 `state_out.json` 中写成完整可用 RVE：

```json
{
  "node": "mat4",
  "status": "run_stress_only",
  "rho": 2330.0,
  "stress_eff": {
    "sxx": 1.1e8,
    "syy": -2.8e8
  },
  "D": [["copied from previous valid mat4"]],
  "D_source_step": "S15"
}
```

最终原则：

```text
state_out.json 中每个 active/valid RVE 都必须是下游可直接使用的完整状态。
下游不应该再自己追溯 D。
```

---

## 8. COMSOL 模型接口信息

本节记录当前已知的实际 COMSOL 模型路径、component、physics、study 和特殊接口。

### 8.1 cap / mat 模型接口

根据本地 `cap_model_tags.json` 与 `die_model_tags.json` 汇总，mat1-4 映射如下：

| node | 物理名称 | cap_source | .mph 文件 | component | physics | stress study | CP study | 输入 |
|---|---|---|---|---|---|---|---|---|
| `mat1` | decap1st1 | decap1st / decap11 | `decap_model_all.mph` | `comp6` | `solid6` | `std3` | `solid6cp1std` | `pillar` |
| `mat2` | decap2 | decap2nd / decap2 | `decap_model_all.mph` | `comp5` | `solid5` | `std1` | `solid5cp1std` | `pillar` |
| `mat3` | decap1st3 | decap1st3 / decap13 | `decap_model_all.mph` | `comp7` | `solid4` | `std2` | `solid4cp1std` | `pillar` |
| `mat4` | fecap | fecap | `fecap_model_all.mph` | `comp8` | `solid8` | `std4` | `solid8cp1std` | `pillar + sc` |

特殊变体：

| step | 模型 | 说明 |
|---|---|---|
| S03 | `p03_decap_model_DTI_etch.mph` | 包含 `mat1/mat2/mat3`，无 `mat4/fecap`。S03 的 mat4 使用 S02 默认 RVE 或上一有效状态。 |
| S16 | `fecap_model_all.mph` / `feslit_etch` | 无 CP study，只更新 `stress_eff.sxx/syy` 与 `rho`，`D` 继承上一有效 mat4。 |

### 8.2 pillar 模型接口

已知 20 步工艺中 pillar 模型名如下。实际 component、physics、study、extractor 标签应写入 `configs/templates.yaml`，不要硬编码在 Java 里。

| step | node | template key 建议 | .mph / 模型名 | 说明 |
|---|---|---|---|---|
| S02 | `pillar` | `p02_pillar_ONONdep` | `p02_pillar_ONONdep` / `p02_pillar_model_ONONdep.mph` | ONON dep，生成 S02 默认 RVE 和 `onon_device` |
| S03 | `pillar` | `p03_pillar_SCDTI` | `p03_pillar_SCDTI` | DTI etch 后 pillar |
| S06 | `pillar` | `p04p01_pillar_etch` | `p04p01_pillar_etch` | pillar etch |
| S07 | `pillar` | `p04p02_pillar_aldOx` | `p04p02_pillar_aldOx` | SAC oxide dep |
| S08 | `pillar` | `p04p03_pillar_TiN` | `p04p03_pillar_TiN` | ALD SAC TiN |
| S09 | `pillar` | `p04p04_pillar_NbO` | `p04p04_pillar_NbO` | NbO dep |
| S10 | `pillar` | `p04p05_pillar_TiN2` | `p04p05_pillar_TiN2` | second ALD TiN |
| S11 | `pillar` | `p04p06_pillar_W` | `p04p06_pillar_W` | pillar W CMP |
| S15 | `pillar` | `p06p01_pillar_Feslit` | `p06p01_pillar_Feslit` | feslit ASI dep |
| S16 | `pillar` | `p06p02_pillar_feslit_etch` | 若实际无 pillar 模型则改为对应 S16 pillar/feslit 模板 | S16 起全链 run |
| S17 | `pillar` | `p06p02_pillar_SiNrmv` | `p06p02_pillar_SiNrmv` | SiN removal |
| S18 | `pillar` | `p06p03_pillar_SACoxrmv` | `p06p03_pillar_SACoxrmv` | SAC oxide removal |
| S19 | `pillar` | `p06p04_pillar_metaldep` | `p06p04_pillar_metaldep` | W recess / metal dep |
| S20 | `pillar` | `p06p04_or_final_pillar` | 若 S20 无独立 pillar 模型，可复用 S19 或配置 final pillar 模板 | S20 要全链 run，需配置明确模板 |

待由本地脚本补全字段：

```yaml
component: <pillar component tag>
physics: <pillar solid physics tag>
studies:
  stress: <stress study tag>
  cp: <cell periodicity study tag or null>
extractors:
  rho: <rho evaluator tag>
  stress: <volume average stress tag>
  D: <global matrix evaluation tag or null>
```

### 8.3 SC 模型接口

| step | node | template key 建议 | .mph / 模型名 | 输入 | 输出 |
|---|---|---|---|---|---|
| S04 | `sc` | `p03_SC_form` | `p03_SC_form` | 可接收上一 `sc` 或默认 RVE | `sc` RVE，后续供 `mat4/fecap` 使用 |

SC 与 pillar 类似，是 fecap 的上游输入。`mat4/fecap` 必须接收：

```text
pillar + sc
```

### 8.4 die 模型接口

| node | template key 建议 | .mph 文件 | 输入 | 输出 |
|---|---|---|---|---|
| `die` | `die_model` | `die_model.mph` | `mat1 + mat2 + mat3 + mat4` | `die` RVE |

待由本地脚本补全字段：

```yaml
component: <die component tag>
physics: <die solid physics tag>
studies:
  stress: <die stress study tag>
  cp: <die CP study tag or null>
extractors:
  rho: <rho evaluator tag>
  stress: <volume average stress tag>
  D: <global matrix evaluation tag or null>
```

### 8.5 wafer 模型接口

| step | node | template key 建议 | .mph / 模型名 | 输入 | 输出 |
|---|---|---|---|---|---|
| S01 | `wafer` | `p01_wafer_init` | `p01_wafer_init.mph` | 初始参数 | bow_x/bow_y |
| S02 | `wafer` | `p02_wafer_ONONdep` | `p02_wafer_ONONdep.mph` | `die` 默认 RVE + `onon_device` | bow_x/bow_y |
| S03-S20 | `wafer` | `wafer_warp_model` 或步骤专用 wafer 模板 | `wafer_warp_model.mph` | `die + onon_device` | bow_x/bow_y，TXT/PNG |

wafer 模型只接收：

```text
die + onon_device
```

不要直接把 mat1-4、pillar、sc 注入 wafer，除非某个 wafer 模板明确要求。

---

## 9. 参数注入命名规范

所有 RVE 输入由 Java 按 slot 注入 COMSOL 参数。slot 由 step YAML 的 inputs 声明决定。

### 9.1 通用参数格式

```text
input_<slot>_rho
input_<slot>_sxx
input_<slot>_syy
input_<slot>_D11 ... input_<slot>_D66
```

不注入：

```text
input_<slot>_szz
input_<slot>_sxy
input_<slot>_syz
input_<slot>_sxz
```

### 9.2 decap 输入 pillar

```text
input_pillar_rho
input_pillar_sxx
input_pillar_syy
input_pillar_D11 ... input_pillar_D66
```

### 9.3 fecap 输入 pillar + sc

```text
input_pillar_rho
input_pillar_sxx
input_pillar_syy
input_pillar_D11 ... input_pillar_D66

input_sc_rho
input_sc_sxx
input_sc_syy
input_sc_D11 ... input_sc_D66
```

### 9.4 die 输入 mat1-4

```text
input_mat1_rho
input_mat1_sxx
input_mat1_syy
input_mat1_D11 ... input_mat1_D66

input_mat2_rho
input_mat2_sxx
input_mat2_syy
input_mat2_D11 ... input_mat2_D66

input_mat3_rho
input_mat3_sxx
input_mat3_syy
input_mat3_D11 ... input_mat3_D66

input_mat4_rho
input_mat4_sxx
input_mat4_syy
input_mat4_D11 ... input_mat4_D66
```

### 9.5 wafer 输入 die + onon_device

```text
input_die_rho
input_die_sxx
input_die_syy
input_die_D11 ... input_die_D66

input_onon_rho
input_onon_sxx
input_onon_syy
input_onon_D11 ... input_onon_D66
```

---

## 10. 20 步最终调度表

| Step | 工艺名称 | run 节点 | inherit/default 节点 | 说明 |
|---|---|---|---|---|
| S01 | `w_plug_cmp` | `wafer` | 其他 inactive | 初始 wafer bow |
| S02 | `onon_dep` | `pillar`, `wafer` | `sc/mat1/mat2/mat3/mat4/die` 用 pillar/onon RVE 初始化 | 生成全局默认 RVE 和 `onon_device` |
| S03 | `dti_etch` | `pillar`, `mat1`, `mat2`, `mat3`, `die`, `wafer` | `mat4/sc` 继承默认 RVE | DTI decap 更新；mat4 用默认/继承 |
| S04 | `sc_etch` | `sc`, `mat4`, `die`, `wafer` | `pillar/mat1/mat2/mat3` 继承 | SC 更新触发 fecap、die、wafer |
| S05 | `sc_asi_etch` | `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `pillar/sc` 继承 | 全 mat 更新 |
| S06 | `pillar_etch` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc` 继承 | pillar 变，所有下游 run |
| S07 | `sac_ox_dep` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc` 继承 | 全链更新 |
| S08 | `ald_sac_tin_depo` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc` 继承 | 全链更新 |
| S09 | `nbo_depo` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc` 继承 | 全链更新 |
| S10 | `ald_tin_depo` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc` 继承 | 全链更新 |
| S11 | `pillar_w_cmp` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc` 继承 | 全链更新 |
| S12 | `dpl_etch` | `mat4`, `die`, `wafer` | `pillar/sc/mat1/mat2/mat3` 继承 | fecap、die、wafer 更新 |
| S13 | `dpl_cmp` | `mat4`, `die`, `wafer` | `pillar/sc/mat1/mat2/mat3` 继承 | fecap、die、wafer 更新 |
| S14 | `lpad_w_cmp` | `mat4`, `die`, `wafer` | `pillar/sc/mat1/mat2/mat3` 继承 | fecap、die、wafer 更新 |
| S15 | `feslit_asi_dep` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc` 继承，或按实际需要 run | 全链更新 |
| S16 | `feslit_etch` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc` 继承 | mat4 只更新 stress，D 继承 |
| S17 | `sin_rmv` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc` 继承 | 全链更新 |
| S18 | `sac_ox_remove` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc` 继承 | 全链更新 |
| S19 | `w_recess` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc` 继承 | 全链更新 |
| S20 | `feslit_buff_cmp` | `pillar`, `mat1`, `mat2`, `mat3`, `mat4`, `die`, `wafer` | `sc/onon_device` 继承 | 最终 bow 输出 |

---

## 11. flow.yaml 示例

```yaml
version: 2
run_id_default: dev_20step

parameter_txt_order:
  - configs/params/global.txt
  - configs/params/materials.txt
  - configs/params/process.txt

templates: configs/templates.yaml

steps:
  - configs/steps/S01_w_plug_cmp.yaml
  - configs/steps/S02_onon_dep.yaml
  - configs/steps/S03_dti_etch.yaml
  - configs/steps/S04_sc_etch.yaml
  - configs/steps/S05_sc_asi_etch.yaml
  - configs/steps/S06_pillar_etch.yaml
  - configs/steps/S07_sac_ox_dep.yaml
  - configs/steps/S08_ald_sac_tin_depo.yaml
  - configs/steps/S09_nbo_depo.yaml
  - configs/steps/S10_ald_tin_depo.yaml
  - configs/steps/S11_pillar_w_cmp.yaml
  - configs/steps/S12_dpl_etch.yaml
  - configs/steps/S13_dpl_cmp.yaml
  - configs/steps/S14_lpad_w_cmp.yaml
  - configs/steps/S15_feslit_asi_dep.yaml
  - configs/steps/S16_feslit_etch.yaml
  - configs/steps/S17_sin_rmv.yaml
  - configs/steps/S18_sac_ox_remove.yaml
  - configs/steps/S19_w_recess.yaml
  - configs/steps/S20_feslit_buff_cmp.yaml

onon_device:
  source_step: S02
  source_node: pillar

runtime:
  cleanup_mph: true
  keep_json: true
  keep_logs: true
  keep_exports: true
  fail_fast_on_required_input_missing: true
```

---

## 12. templates.yaml 示例

下面是模板 registry 的建议结构。Codex 开发时应支持该结构，而不是在 Java 中硬编码路径和标签。

```yaml
templates:
  p01_wafer_init:
    path: models/process_models/p01_wafer_init.mph
    node_type: wafer
    component: null
    physics: null
    studies:
      stress: std1
      cp: null
    extractors:
      bow_x: wafer_bow_x
      bow_y: wafer_bow_y
      rho: null
      stress: null
      D: null
    exports:
      txt: true
      png: true

  p02_pillar_ONONdep:
    path: models/process_models/pillar_models/p02_pillar_model_ONONdep.mph
    node_type: pillar
    component: TODO_PILLAR_COMPONENT
    physics: TODO_PILLAR_PHYSICS
    studies:
      stress: TODO_PILLAR_STRESS_STUDY
      cp: TODO_PILLAR_CP_STUDY
    extractors:
      rho: TODO_RHO_EVAL
      stress: TODO_STRESS_AVERAGE
      D: TODO_D_MATRIX_EVAL

  p03_cap_DTI_mat1:
    path: models/process_models/cap_models/p03_decap_model_DTI_etch.mph
    node_type: mat1
    component: comp6
    physics: solid6
    studies:
      stress: std3
      cp: solid6cp1std
    extractors:
      rho: TODO_RHO_EVAL
      stress: TODO_STRESS_AVERAGE
      D: TODO_D_MATRIX_EVAL

  p03_cap_DTI_mat2:
    path: models/process_models/cap_models/p03_decap_model_DTI_etch.mph
    node_type: mat2
    component: comp5
    physics: solid5
    studies:
      stress: std1
      cp: solid5cp1std
    extractors:
      rho: TODO_RHO_EVAL
      stress: TODO_STRESS_AVERAGE
      D: TODO_D_MATRIX_EVAL

  p03_cap_DTI_mat3:
    path: models/process_models/cap_models/p03_decap_model_DTI_etch.mph
    node_type: mat3
    component: comp7
    physics: solid4
    studies:
      stress: std2
      cp: solid4cp1std
    extractors:
      rho: TODO_RHO_EVAL
      stress: TODO_STRESS_AVERAGE
      D: TODO_D_MATRIX_EVAL

  decap_model_mat1:
    path: models/process_models/cap_models/decap_model_all.mph
    node_type: mat1
    component: comp6
    physics: solid6
    studies:
      stress: std3
      cp: solid6cp1std
    extractors:
      rho: TODO_RHO_EVAL
      stress: TODO_STRESS_AVERAGE
      D: TODO_D_MATRIX_EVAL

  decap_model_mat2:
    path: models/process_models/cap_models/decap_model_all.mph
    node_type: mat2
    component: comp5
    physics: solid5
    studies:
      stress: std1
      cp: solid5cp1std
    extractors:
      rho: TODO_RHO_EVAL
      stress: TODO_STRESS_AVERAGE
      D: TODO_D_MATRIX_EVAL

  decap_model_mat3:
    path: models/process_models/cap_models/decap_model_all.mph
    node_type: mat3
    component: comp7
    physics: solid4
    studies:
      stress: std2
      cp: solid4cp1std
    extractors:
      rho: TODO_RHO_EVAL
      stress: TODO_STRESS_AVERAGE
      D: TODO_D_MATRIX_EVAL

  fecap_model_all:
    path: models/process_models/cap_models/fecap_model_all.mph
    node_type: mat4
    component: comp8
    physics: solid8
    studies:
      stress: std4
      cp: solid8cp1std
    extractors:
      rho: TODO_RHO_EVAL
      stress: TODO_STRESS_AVERAGE
      D: TODO_D_MATRIX_EVAL

  fecap_feslit_etch:
    path: models/process_models/cap_models/fecap_model_all.mph
    node_type: mat4
    component: comp8
    physics: solid8
    studies:
      stress: std4
      cp: null
    extractors:
      rho: TODO_RHO_EVAL
      stress: TODO_STRESS_AVERAGE
      D: null

  p03_SC_form:
    path: models/process_models/sc_models/p03_SC_form.mph
    node_type: sc
    component: TODO_SC_COMPONENT
    physics: TODO_SC_PHYSICS
    studies:
      stress: TODO_SC_STRESS_STUDY
      cp: TODO_SC_CP_STUDY
    extractors:
      rho: TODO_RHO_EVAL
      stress: TODO_STRESS_AVERAGE
      D: TODO_D_MATRIX_EVAL

  die_model:
    path: models/process_models/die_model.mph
    node_type: die
    component: TODO_DIE_COMPONENT
    physics: TODO_DIE_PHYSICS
    studies:
      stress: TODO_DIE_STRESS_STUDY
      cp: TODO_DIE_CP_STUDY
    extractors:
      rho: TODO_RHO_EVAL
      stress: TODO_STRESS_AVERAGE
      D: TODO_D_MATRIX_EVAL

  wafer_warp_model:
    path: models/process_models/wafer_warp_model.mph
    node_type: wafer
    component: TODO_WAFER_COMPONENT
    physics: TODO_WAFER_PHYSICS
    studies:
      stress: TODO_WAFER_STUDY
      cp: null
    extractors:
      bow_x: TODO_BOW_X_EVAL
      bow_y: TODO_BOW_Y_EVAL
    exports:
      txt: true
      png: true
```

---

## 13. step YAML 示例

### 13.1 S02_onon_dep.yaml

```yaml
step_id: S02
step_name: onon_dep
special:
  initialize_defaults_from: pillar
  create_onon_device_alias: true

nodes:
  pillar:
    action: run
    template: p02_pillar_ONONdep

  sc:
    action: default_from
    source: pillar

  mat1:
    action: default_from
    source: pillar

  mat2:
    action: default_from
    source: pillar

  mat3:
    action: default_from
    source: pillar

  mat4:
    action: default_from
    source: pillar

  die:
    action: default_from
    source: pillar

  onon_device:
    action: alias
    source: pillar

  wafer:
    action: run
    template: p02_wafer_ONONdep
    required: true
    inputs:
      die:
        ref: rve.die
        slot: die
      onon_device:
        ref: rve.onon_device
        slot: onon
```

### 13.2 S04_sc_etch.yaml

```yaml
step_id: S04
step_name: sc_etch

nodes:
  pillar:
    action: inherit
    from: latest_valid

  sc:
    action: run
    template: p03_SC_form
    inputs:
      base:
        ref: rve.sc
        slot: sc

  mat1:
    action: inherit
    from: latest_valid

  mat2:
    action: inherit
    from: latest_valid

  mat3:
    action: inherit
    from: latest_valid

  mat4:
    action: run
    template: fecap_model_all
    inputs:
      pillar:
        ref: rve.pillar
        slot: pillar
      sc:
        ref: rve.sc
        slot: sc

  die:
    action: run
    template: die_model
    inputs:
      mat1:
        ref: rve.mat1
        slot: mat1
      mat2:
        ref: rve.mat2
        slot: mat2
      mat3:
        ref: rve.mat3
        slot: mat3
      mat4:
        ref: rve.mat4
        slot: mat4

  wafer:
    action: run
    template: wafer_warp_model
    required: true
    inputs:
      die:
        ref: rve.die
        slot: die
      onon_device:
        ref: rve.onon_device
        slot: onon
```

### 13.3 S05_sc_asi_etch.yaml

```yaml
step_id: S05
step_name: sc_asi_etch

nodes:
  pillar:
    action: inherit
    from: latest_valid

  sc:
    action: inherit
    from: latest_valid

  mat1:
    action: run
    template: decap_model_mat1
    inputs:
      pillar:
        ref: rve.pillar
        slot: pillar

  mat2:
    action: run
    template: decap_model_mat2
    inputs:
      pillar:
        ref: rve.pillar
        slot: pillar

  mat3:
    action: run
    template: decap_model_mat3
    inputs:
      pillar:
        ref: rve.pillar
        slot: pillar

  mat4:
    action: run
    template: fecap_model_all
    inputs:
      pillar:
        ref: rve.pillar
        slot: pillar
      sc:
        ref: rve.sc
        slot: sc

  die:
    action: run
    template: die_model
    inputs:
      mat1: {ref: rve.mat1, slot: mat1}
      mat2: {ref: rve.mat2, slot: mat2}
      mat3: {ref: rve.mat3, slot: mat3}
      mat4: {ref: rve.mat4, slot: mat4}

  wafer:
    action: run
    template: wafer_warp_model
    required: true
    inputs:
      die: {ref: rve.die, slot: die}
      onon_device: {ref: rve.onon_device, slot: onon}
```

### 13.4 S16_feslit_etch.yaml

```yaml
step_id: S16
step_name: feslit_etch

nodes:
  pillar:
    action: run
    template: p06p02_pillar_feslit_etch

  sc:
    action: inherit
    from: latest_valid

  mat1:
    action: run
    template: decap_model_mat1
    inputs:
      pillar: {ref: rve.pillar, slot: pillar}

  mat2:
    action: run
    template: decap_model_mat2
    inputs:
      pillar: {ref: rve.pillar, slot: pillar}

  mat3:
    action: run
    template: decap_model_mat3
    inputs:
      pillar: {ref: rve.pillar, slot: pillar}

  mat4:
    action: run
    template: fecap_feslit_etch
    inputs:
      pillar: {ref: rve.pillar, slot: pillar}
      sc: {ref: rve.sc, slot: sc}
    merge:
      D: inherit_previous

  die:
    action: run
    template: die_model
    inputs:
      mat1: {ref: rve.mat1, slot: mat1}
      mat2: {ref: rve.mat2, slot: mat2}
      mat3: {ref: rve.mat3, slot: mat3}
      mat4: {ref: rve.mat4, slot: mat4}

  wafer:
    action: run
    template: wafer_warp_model
    required: true
    inputs:
      die: {ref: rve.die, slot: die}
      onon_device: {ref: rve.onon_device, slot: onon}
```

---

## 14. Python 编排器职责

Python 是主控层，负责：

1. 读取 `flow.yaml`。
2. 读取所有 `configs/steps/*.yaml`。
3. 读取 `configs/templates.yaml`。
4. 校验 step DAG：
   - node name 合法；
   - action 合法；
   - template 存在；
   - required input 可解析；
   - wafer 每步 run；
   - onon_device 是 alias，不是 run node；
   - S02 后状态完整。
5. 加载上一 step `state_out.json`。
6. 根据本 step action 生成 step 内部执行计划。
7. 生成 `step_input.json`。
8. 调用 backend：dryrun/mock/comsol。
9. 合并 Java 输出与继承状态。
10. 对 stress-only 节点补齐上一状态 D。
11. 写 `state_out.json`。
12. 写 `step_result.json`。
13. 追加 `summary.csv`。
14. 清理临时 `.mph` 文件。
15. 支持 `--step`、`--step-range`、`--resume`。

### 14.1 step 内部执行顺序

固定顺序：

```python
ORDER = [
    "pillar",
    "sc",
    "mat1",
    "mat2",
    "mat3",
    "mat4",
    "die",
    "wafer",
]
```

其中 `mat1/mat2/mat3/mat4` 理论上可以并行，但第一版建议串行，先保证可跑通。

### 14.2 状态引用规则

每个 node 执行时，`inputs.ref` 应从当前 step 的 working state 读取，而不是固定从 state_in 读取。

```python
working_state = copy(state_in)

for node in ORDER:
    if action == "inherit":
        working_state[node] = inherit_latest_valid(node)
    elif action == "default_from":
        working_state[node] = copy(working_state[source])
    elif action == "alias":
        working_state[node] = alias_or_snapshot(working_state[source])
    elif action == "run":
        inputs = resolve_inputs_from(working_state)
        result = backend.run_node(node, inputs)
        working_state[node] = merge_result(result, previous=working_state[node])
```

---

## 15. Java worker 职责

Java COMSOL worker 只负责执行当前 step 中需要 run 的 COMSOL 节点，不负责全局 20 步继承追溯。

Java 负责：

1. 读取 `step_input.json`。
2. 对每个 run node：
   - 加载 `.mph` 模板；
   - 按 `parameter_txt_order` 加载参数 TXT；
   - 将上游 RVE 注入 COMSOL 参数；
   - 运行 stress study；
   - 如果 `study_cp != null`，运行 CP study；
   - 提取 `rho`、`stress_eff.sxx/syy`、`D6x6`；
   - 对 wafer 提取 `bow_x/bow_y` 并导出 TXT/PNG；
   - 写 node result。
3. 写 `manifest.json`。
4. 释放 COMSOL model。

Java 不负责：

- 20 步主循环；
- `latest_valid` 查找；
- S02 默认状态初始化；
- stress-only D 继承；
- summary.csv；
- 参数校准。

---

## 16. Java 输入输出 JSON

### 16.1 step_input.json 简化示例

```json
{
  "step_id": "S04",
  "step_name": "sc_etch",
  "run_dir": "runs/dev_20step/S04_sc_etch",
  "parameter_txt_order": [
    "runs/dev_20step/S04_sc_etch/params/global.txt",
    "runs/dev_20step/S04_sc_etch/params/materials.txt",
    "runs/dev_20step/S04_sc_etch/params/process.txt"
  ],
  "nodes": [
    {
      "node": "sc",
      "action": "run",
      "template": "p03_SC_form",
      "model_path": "models/process_models/sc_models/p03_SC_form.mph",
      "component": "TODO_SC_COMPONENT",
      "physics": "TODO_SC_PHYSICS",
      "studies": {"stress": "TODO_SC_STRESS_STUDY", "cp": "TODO_SC_CP_STUDY"},
      "extractors": {"rho": "TODO_RHO_EVAL", "stress": "TODO_STRESS_AVERAGE", "D": "TODO_D_MATRIX_EVAL"},
      "inputs": {}
    },
    {
      "node": "mat4",
      "action": "run",
      "template": "fecap_model_all",
      "model_path": "models/process_models/cap_models/fecap_model_all.mph",
      "component": "comp8",
      "physics": "solid8",
      "studies": {"stress": "std4", "cp": "solid8cp1std"},
      "inputs": {
        "pillar": {"slot": "pillar", "rve": "... expanded RVE ..."},
        "sc": {"slot": "sc", "rve": "... expanded RVE ..."}
      }
    }
  ]
}
```

### 16.2 node_result.json 示例

```json
{
  "node": "mat4",
  "status": "success",
  "rho": 2330.0,
  "stress_eff": {
    "sxx": 1.1e8,
    "syy": -2.8e8
  },
  "D": [
    [1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
  ],
  "exports": [],
  "manifest": {
    "model_path": "models/process_models/cap_models/fecap_model_all.mph",
    "component": "comp8",
    "physics": "solid8",
    "study_stress": "std4",
    "study_cp": "solid8cp1std"
  }
}
```

### 16.3 wafer result 示例

```json
{
  "node": "wafer",
  "status": "success",
  "bow_x": 1.23e-4,
  "bow_y": 1.05e-4,
  "exports": [
    "exports/wafer_bow.txt",
    "exports/wafer_bow.png"
  ],
  "manifest": {
    "model_path": "models/process_models/wafer_warp_model.mph",
    "study": "TODO_WAFER_STUDY",
    "bow_x_eval": "TODO_BOW_X_EVAL",
    "bow_y_eval": "TODO_BOW_Y_EVAL"
  }
}
```

---

## 17. CLI 设计

建议保留或新增以下命令：

```bash
# 仅检查配置和生成 step_input，不调用 COMSOL
python3 python/run_flow.py --backend dryrun --run-id dryrun_20step

# 跑 mock 20 步
python3 python/run_flow.py --backend mock --run-id mock_20step

# 跑真实 COMSOL 20 步
python3 python/run_flow.py --backend comsol --run-id comsol_20step

# 只跑某一步
python3 python/run_flow.py --backend dryrun --run-id debug_s04 --step S04

# 跑步骤范围
python3 python/run_flow.py --backend comsol --run-id debug_s01_s05 --step-range S01:S05

# 从 S10 恢复
python3 python/run_flow.py --backend comsol --run-id comsol_20step --resume S10
```

resume 规则：

```text
--resume S10
  1. 检查 runs/<run_id>/S09_*/state_out.json 是否存在。
  2. 加载 S09 state_out 作为 state_in。
  3. 从 S10 开始继续执行。
  4. 覆盖或新建 S10-S20 输出。
```

---

## 18. cleanup 策略

`.mph` 文件很大，必须严格清理中间文件。

保留：

```text
state_in.json
state_out.json
step_input.json
step_result.json
manifest.json
summary.csv
logs/*.log
exports/*.txt
exports/*.png
```

删除：

```text
runs/<run_id>/<step>/temp/*.mph
runs/<run_id>/<step>/work/*.mph
```

不要删除原始模板：

```text
models/process_models/**/*.mph
```

---

## 19. 失败策略

第一版实现建议：

```text
required node 失败：step 失败，流程停止。
非 required node 失败：记录 failed，若有上一有效状态则 fallback inherit，否则 step 失败。
```

强制 required：

```text
S01 wafer
S02 pillar
S02 wafer
每一步 wafer
所有被 die 依赖且找不到上一有效状态的 mat 节点
所有被 wafer 依赖且找不到上一有效状态的 die/onon_device
```

不要盲目“失败继续”，否则 S20 bow 可能没有物理意义。

---

## 20. summary.csv 字段

每步追加一行：

```text
step_id
step_name
pillar_status
sc_status
mat1_status
mat2_status
mat3_status
mat4_status
die_status
wafer_status
onon_device_source_step
onon_device_source_node
bow_x
bow_y
warnings
errors
```

示例：

```csv
step_id,step_name,pillar_status,sc_status,mat1_status,mat2_status,mat3_status,mat4_status,die_status,wafer_status,onon_device_source_step,onon_device_source_node,bow_x,bow_y,warnings,errors
S04,sc_etch,inherit,run,inherit,inherit,inherit,run,run,run,S02,pillar,1.2e-4,1.0e-4,,
```

---

## 21. 验收标准

### 21.1 dryrun 验收

必须验证：

- S01 只运行 wafer。
- S02 运行 pillar + wafer，并创建所有默认 RVE。
- S03 运行 pillar + mat1/2/3 + die + wafer。
- S04 运行 sc + mat4 + die + wafer。
- S05 运行 mat1/2/3/4 + die + wafer。
- S06-S11 每步运行 pillar + mat1/2/3/4 + die + wafer。
- S12-S14 每步运行 mat4 + die + wafer。
- S15-S20 每步运行 pillar + mat1/2/3/4 + die + wafer。
- 每一步 wafer 都 run。
- 每一步 `state_out.json` 都有完整可用的：
  - pillar
  - sc
  - mat1
  - mat2
  - mat3
  - mat4
  - die
  - onon_device
  - wafer_result

### 21.2 mock 验收

必须能跑完 20 步，并生成：

```text
runs/<run_id>/summary.csv
runs/<run_id>/S01_*/state_out.json
...
runs/<run_id>/S20_*/state_out.json
```

### 21.3 COMSOL 验收

建议分阶段：

第一阶段：

```text
S01
S02
S03
S04
S05
S20
```

第二阶段：

```text
S06-S11
```

第三阶段：

```text
S12-S19
```

最终要求：

- 每一步 wafer bow 都能输出。
- S20 有最终 TXT/PNG。
- 所有中间 `.mph` 被清理。
- 可从任意步骤 resume。
- S16 mat4 能只更新 stress，同时继承上一状态 D。

---

## 22. Codex 开发任务拆分

### Task 1: 配置 schema 与 validator

实现：

- `configs/flow.yaml` 解析；
- `configs/templates.yaml` 解析；
- `configs/steps/*.yaml` 解析；
- node/action/template/input 校验；
- S02 初始化规则校验；
- wafer 每步 run 校验。

### Task 2: state manager

实现：

- `load_state_in()`；
- `write_state_out()`；
- `inherit_latest_valid()`；
- `default_from(source)`；
- `alias(source)`；
- `merge_run_result()`；
- stress-only D 继承。

### Task 3: dryrun backend

实现：

- 生成 step_input.json；
- 不调用 COMSOL；
- 用 mock RVE 填充 run 节点，或只输出计划；
- 验证 20 步执行计划。

### Task 4: mock backend

实现：

- deterministic mock RVE；
- deterministic bow_x/bow_y；
- 完整 20 步状态传递。

### Task 5: comsol backend wrapper

实现：

- Python 调 Java worker；
- 传入 step_input.json；
- 读取 node_result；
- 捕获日志和错误码；
- cleanup temp mph。

### Task 6: Java ComsolStepWorker

实现：

- 读取 step_input.json；
- 加载参数 TXT；
- 注入 RVE 参数；
- 运行 stress/CP study；
- 提取 rho、sxx、syy、D；
- 导出 wafer bow TXT/PNG；
- 输出 JSON。

### Task 7: 测试

至少覆盖：

- S02 初始化所有 RVE；
- S04 sc -> mat4 -> die -> wafer 的本步依赖；
- S16 mat4 stress-only + D 继承；
- S20 仍使用 S02 onon_device；
- resume from S10；
- step-range S03:S05；
- required input 缺失时报错。

---

## 23. 最终一句话方案

使用现有 Python DAG 编排框架实现 S01-S20 工艺链；S02 由 pillar/onon RVE 初始化所有层级 RVE，并将 `onon_device` 作为指定 pillar 结果的 wafer 输入 alias；后续每步按 `pillar/sc -> mat1-4 -> die -> wafer` 的层级依赖执行，只要上游 run，下游必须 run。decap mat1/2/3 输入 pillar，fecap mat4 输入 pillar+sc，die 输入 mat1-4，wafer 输入 die+onon_device。RVE stress 只保存和注入 sxx/syy，D 是否提取由 study/extractor 标签决定；无 CP study 时只更新 stress/rho，D 由 Python 从上一状态继承。每一步都 run wafer 并输出 bow，参数校准暂时关闭。
