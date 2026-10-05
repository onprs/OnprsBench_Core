# Dataset Protocol v1

Dataset Protocol 是 OnprsBench_Core 框架与外部数据集之间唯一的稳定接口。数据集只描述"测什么"；运行、调用模型、Judge、存储与展示全部属于框架职责。框架只依赖本协议，不依赖任何数据集的内部目录结构；数据集独立维护、独立发版。

协议本体由 [OnprsBench_Dataset](https://github.com/onprs/OnprsBench_Dataset) 仓库的 `PROTOCOL.md` 定义；本文件是框架侧副本，并补充框架实现约定（第 8~11 节）。两侧语义必须保持一致，协议变更必须版本化。

`protocol_version: "1"`

## 1. 版本模型

| 层级 | 标识 | 规则 |
| --- | --- | --- |
| 协议 | `protocol_version` | 不兼容变更时递增 |
| 数据集 | `dataset.version`（语义化版本）+ git commit + manifest hash | 每次发布递增并冻结 |
| 任务 | `task.revision`（整数，从 1 开始） | 已发布任务的任何内容变更必须 +1 |

一次 Run 的完整引用为：`dataset.id + dataset.version + dataset commit + manifest hash`。禁止使用 `latest` 作为历史 Run 的唯一标识。

## 2. 数据集描述（manifest）

发布产物的 `manifest.yaml` 是框架了解数据集的唯一入口（构建产物，发布时冻结）：

```yaml
protocol_version: "1"
dataset:
  id: onprsbench-dataset
  name: OnprsBench Dataset
  version: 0.3.0            # 语义化版本
  release_date: "2026-10-06"
  revision: <git commit sha>
  description: ...
  license: ...
suites:
  - id: ...
    name: ...
    description: ...
    layer: external | fresh | derived
    adapter: ...            # external suite 的接入适配器 id，无则 null
    tasks:
      - id: ...
        revision: 1
        title: ...
        type: ...
        tags: [...]
        status: draft | review | active | deprecated | superseded
        path: <相对 manifest 所在目录的 task bundle 路径>
        solver_visible: [ ...相对 task 根的路径... ]
        judge_visible: [ ... ]
        metadata: { difficulty, freshness, contamination, flagship }
        hashes:
          bundle_sha256: ...
          files: { "<path>": "<sha256>" }
```

框架只能信任 manifest 中列出的内容；目录结构属于数据集仓库内部实现，可随时调整（调整不产生协议变更）。`layer: external` 且 `tasks` 为空的 suite 是 metadata-only 坐标系（内容由用户按数据集说明本地导入），框架应展示但不可直接运行。

## 3. Task Bundle

逻辑结构：

```
solver_visible:
  problem            # 题面（problem.md）
  assets             # solver 可用的附件（assets/**）
judge_visible:
  reference          # 参考解答包（reference/）
  rubric             # 评分量规（rubric.yaml）
  anchors            # judge 校准答案（anchors/，flagship/canary 任务）
  judge_assets       # 程序判定资产（judge_assets/，如测试补丁、生成器、参考解代码）
meta:
  meta.yaml          # 生命周期、来源、污染风险、许可等（框架读取，不给 solver/judge）
```

可见性取值：`solver` / `judge` / `meta`。

- 框架必须把 `solver_visible` 以外的内容对 solver 完全隔离。
- `judge_visible` 仅在评分阶段提供给 judge。
- `meta` 用于任务筛选、统计与溯源，不进入 solver 或 judge 的提示词。

任务可以覆盖默认可见性映射：在 `meta.yaml` 的 `visibility_overrides` 中逐路径声明。

## 4. 标识与命名

- `task.id`：全数据集唯一，匹配 `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`，推荐 `<suite前缀>-<语义slug>`。首次进入任一发布后永久不变。
- `suite.id`：同一命名规则。
- 目录名与 id 可以不同，以 manifest 的 `path` 为准。

## 5. Hash 规则

- 文件 hash：对文件原始字节取 SHA-256。
- bundle hash：将 task 内所有文件按 POSIX 风格相对路径（`/` 分隔、UTF-8）字典序排序，逐行拼接 `<relpath>  <sha256>\n`（两个空格分隔），对拼接结果的 UTF-8 字节再取 SHA-256。
- manifest hash：发布产物中 `manifest.yaml` 文件字节的 SHA-256。

框架在安装数据集与创建 Run 时记录以上全部标识，历史 Run 可精确追溯到具体数据集内容。

## 6. 评分 Rubric

每道任务自带 `rubric.yaml`，不同任务类型使用不同维度。rubric 必须给出：维度 id、权重（总和 1.0）、维度说明、评分锚点（至少 0 / 0.5 / 1.0 三档）。Judge 按维度输出 0~1 的小数分；总分 = Σ(权重 × 维度分) × 100，为框架侧派生数据，数据集不存储"总分"字段。

```yaml
rubric_version: 1
dimensions:
  - id: correctness
    weight: 0.30
    description: ...
    anchors:
      - { score: 0.0, description: ... }
      - { score: 0.5, description: ... }
      - { score: 1.0, description: ... }
```

## 7. Anchor Answer

flagship / canary 任务必须提供 anchor answers（建议 0 / 25 / 50 / 75 / 100 五档），存放于 `anchors/`（judge 可见），用于 judge 校准、judge 一致性测试与 rubric 验证。普通任务可选。框架在评分时将 anchors 作为校准参考注入 judge 提示词。

## 8. 程序判定契约（verify.yaml）

需要程序判定的任务在 `judge_assets/verify.yaml` 中声明机器可执行的判定契约。框架据此自动准备环境并执行判定，产出"判定事实"（Verifier Facts）供 judge 使用。当前定义两类契约：

### 8.1 工程修复任务（issue_resolution）

```yaml
repo: pallets/click                 # GitHub owner/repo
repo_url: https://github.com/pallets/click
base_commit: <sha>                  # 修复前仓库状态
environment:
  python: "3.14"                    # 判定环境 Python 版本
  setup:                            # 仓库内依赖安装步骤（框架在自带解释器的 venv 中执行）
    - pip install -e . pytest
evaluation:
  apply: [judge_assets/test.patch]  # 判定时由框架应用的测试补丁
  fail_to_pass: [tests/test_utils.py::test_xxx]   # 修复后必须通过
  pass_to_pass: [tests/test_utils.py]             # 不得回归
  reference_fix: judge_assets/fix.patch           # 参考修复（数据集自验用）
```

判定事实：`patch_applied`（solver 补丁能否应用）、`fail_to_pass` 逐项结果、`pass_to_pass` 汇总、日志摘要。凡 rubric 维度声明"程序 verifier 判定"的，judge 必须以判定事实为唯一依据打分。

### 8.2 竞赛代码任务（code_generation）

```yaml
source:
  limits: { time: "2s", memory: "256MB" }   # 题目时限（框架按语言放宽解释型语言）
evaluation:
  reference_solution: judge_assets/reference_solution.cpp   # 期望输出来源
  harness:
    generator: judge_assets/generator.py    # 用法：python generator.py <seed> <用例数>
samples: judge_assets/samples.json          # 官方样例 [{input, output}]
```

判定流程：编译/解释运行 solver 代码 → 官方样例回归 → 生成器应力测试（期望输出由参考解计算）→ 记录通过与失败用例。

## 9. Solver 输出契约（框架实现约定）

协议不约束 solver 的内部思考过程，但程序判定类任务要求 solver 的最终回答包含可提取的产物。框架按任务类型在 solver prompt 中声明输出格式：

| 任务类型 | 约定产物 |
| --- | --- |
| `issue_resolution` | 单个 ```diff 围栏内的 unified diff 补丁（git apply 风格，`a/`、`b/` 前缀），只改源码、不改测试 |
| `code_generation` / `implementation` | 单个 ```cpp 或 ```python 围栏内的完整程序（标准输入读入、标准输出写出） |
| 其他 | 直接文本回答 |

提取失败时判定事实记录 `patch_applied=false` / `compiled=false`，judge 按 anchors 对相应维度打 0 档。

## 10. 框架环境义务（框架实现约定）

程序判定所需环境由框架自动供给，不要求用户预装：

- Python 解释器与依赖：框架自动下载独立 Python 构建并创建虚拟环境（经 uv），按 `environment.python` 供给。
- 仓库快照：框架按 `repo_url` + `base_commit` 下载源码归档（GitHub tarball），本地缓存，不依赖用户安装 git。
- C/C++ 编译器：优先使用系统编译器；Windows 上缺失时自动下载便携 MinGW；无法供给时该任务判定降级为"不可用"，judge 仅依据文本证据评分并在结果中标注。
- 补丁应用：框架内置 unified diff 应用能力。

全部判定产物（补丁应用结果、测试输出、耗时、工具链版本）作为 immutable raw facts 落库。

## 11. 元数据（meta.yaml）

`meta.yaml` 的必填字段由数据集仓库 `schemas/task.schema.json` 定义，框架读取其中 `lifecycle` / `freshness` / `difficulty` / `contamination` / `source` / `flagship` 用于筛选、统计与溯源，不进入 solver 或 judge 的提示词。

## 12. 校验

使用 `protocol/tools/validate_dataset.py` 校验数据集目录，执行 JSON Schema 校验、文件 hash 与 bundle hash 校验、rubric 权重检查与可见性约束检查。框架安装数据集时执行同等校验。

`protocol/examples/mock-protocol-sample/` 是协议测试样例，仅用于验证协议与端到端流程，不构成真实评测数据。

## 13. 稳定性承诺与远程 registry

- 已发布（进入任一 release）的任务内容不可原地修改；修正错误通过 errata 记录 + `revision + 1` + 新数据集版本发布。
- 协议新增可选字段不递增 `protocol_version`；删除或改变既有字段语义才递增。
- v1 仅支持本地目录数据集（离线优先）。未来远程 registry 只需提供能解析为同样 manifest 的下载接口，协议本身不变。
