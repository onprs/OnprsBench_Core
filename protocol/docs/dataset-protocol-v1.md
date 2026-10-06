# Dataset Protocol v1

本文档定义数据集仓库与 Benchmark Framework 之间的唯一约定。数据集只描述"测什么"；运行、调用模型、Judge、存储与展示全部属于框架职责。框架通过发布产物中的 `manifest.yaml` 了解数据，不依赖目录结构。

协议本体由 [OnprsBench_Dataset](https://github.com/onprs/OnprsBench_Dataset) 仓库的 `PROTOCOL.md` 定义；本文件是框架侧副本，正文与本体保持同步。第 9、10 节的"框架实现约定"与第 14 节的校验工具说明标注框架对协议的执行方式（新增框架约定不改变协议语义，两侧语义必须一致）。

`protocol_version: "1"`

## 1. 版本模型

| 层级 | 标识 | 规则 |
| --- | --- | --- |
| 协议 | `protocol_version` | 不兼容变更时递增 |
| 数据集 | `dataset.version`（语义化版本）+ git commit + manifest hash | 每次发布递增并冻结 |
| 任务 | `task.revision`（整数，从 1 开始） | 已发布任务的任何内容变更必须 +1 |

一次 Run 的完整引用为：`dataset.id + dataset.version + dataset commit + manifest hash`。禁止使用 `latest` 作为历史 Run 的唯一标识。

## 2. 数据集描述（manifest）

`manifest.yaml` 是构建产物（由数据集仓库 `scripts/build.py` 生成），发布时冻结：

```yaml
protocol_version: "1"
distribution: standard | full    # 分发形态，缺省 standard
dataset:
  id: onprsbench-dataset
  name: OnprsBench Dataset
  version: 0.1.0            # 语义化版本
  release_date: "2026-10-05"
  revision: <git commit sha>
  description: ...
  license: ...              # 汇总说明，细则见 LICENSING.md
resources:                  # distribution: full 时附带的判定资源，否则省略
  - id: repo-snapshot:pallets/click@<commit>
    kind: repo_snapshot
    path: resources/repos/pallets-click-<commit>.tar.gz
    sha256: ...
    bytes: 123456
    source: { repo, url, commit, license, attribution, license_file }
suites:
  - id: ...
    name: ...
    description: ...
    layer: external | fresh | derived
    adapter: ...            # external suite 的接入适配器 id，无则省略
    tasks:
      - id: ...
        revision: 1
        title: ...
        type: ...
        tags: [...]
        status: active
        path: tasks/<suite>/<slug>/     # 相对 manifest 的路径
        solver_visible: [ ...相对 task 根的路径... ]
        judge_visible:  [ ... ]
        metadata: { difficulty, freshness, contamination 摘要 }
        hashes:
          bundle_sha256: ...
          files: { "<path>": "<sha256>" }
```

框架只能信任 manifest 中列出的内容；目录结构属于数据集仓库内部实现，可随时调整（调整不产生协议变更）。`layer: external` 且 `tasks` 为空的 suite 是 metadata-only 坐标系（内容由用户按数据集说明本地导入），框架应展示但不可直接运行。

### 2.1 分发形态（distribution）

同一版本号可以发布两套产物，任务内容一致，分发形态不同：

| 形态 | 产物目录 | 判定资源 | 框架行为 |
| --- | --- | --- | --- |
| `standard` | `onprsbench-dataset-<version>/` | 不附带 | 安装时尽力预取，判定时缓存未命中重试下载 |
| `full` | `onprsbench-dataset-<version>-full/` | 附带 `resources/` | 安装时校验并注册本地资源，判定不联网 |

两套产物使用相同的 `dataset.id` / `dataset.version` / `revision`，由 `manifest hash` 区分；框架以 manifest hash 追溯历史 Run。`distribution` 缺省为 `standard`（兼容早期产物）。

### 2.2 附带资源（resources）

`distribution: full` 时，manifest 声明随产物分发的判定资源（含许可与署名）。规则：

- 只有 `redistribution: redistributable` 的来源允许随产物分发（见数据集仓库 LICENSING.md），必须附许可文本与署名。
- `path` 相对 manifest 所在目录，不得使用绝对路径或 `..`；每种资源必须给出 `bytes` 与 `sha256`。
- 框架安装 `full` 产物时必须校验每个资源的 `path` / `bytes` / `sha256`，校验失败即拒绝安装；校验通过后把资源注册到本地缓存，注册失败同样中止安装；判定时命中缓存、不联网。
- 框架安装 `standard` 产物时必须预取全部资源；任一项失败即中止安装（不创建安装记录），返回失败项并提示用户改用 `full` 产物。判定阶段缓存未命中时仍会重试下载。
- 资源不改变 task bundle 的 hash 规则（第 5 节）；资源自身的完整性由 `sha256` 校验，属于 manifest 冻结内容。

## 3. Task Bundle

逻辑结构：

```
solver_visible:
  problem            # 题面
  assets             # solver 可用的附件
judge_visible:
  canonical_reference
  alternatives
  proof
  pitfalls
  rubric
  judge_assets
  anchors            # judge 校准答案（flagship/canary 任务）
meta:
  meta.yaml          # 生命周期、来源、污染风险、许可等（框架读取，不给 solver）
```

可见性取值：`solver` / `judge` / `meta`。

- 框架必须把 `solver_visible` 以外的内容对 solver 完全隔离。
- `judge_visible` 仅在评分阶段提供给 judge。
- `meta` 用于任务筛选、统计与溯源，不进入 solver 或 judge 的提示词。

数据集仓库采用的默认布局（构建工具据此推导可见性；仅为数据集仓库内部惯例）：

| 路径 | 可见性 |
| --- | --- |
| `problem.md`、`assets/**` | solver |
| `rubric.yaml`、`reference/**`、`anchors/**`、`judge_assets/**` | judge |
| `meta.yaml` | meta |

任务可以覆盖默认映射：在 `meta.yaml` 的 `visibility_overrides` 中逐路径声明（`相对路径 -> solver | judge | meta`），只能引用 manifest 已登记的文件。

## 4. 标识与命名

- `task.id`：全数据集唯一，匹配 `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`，推荐 `<suite前缀>-<语义slug>`。首次进入任一发布后永久不变。
- `suite.id`：同一命名规则。
- 目录名与 id 可以不同，以 manifest 的 `path` 为准。

## 5. Hash 规则

- 文件 hash：对文件原始字节取 SHA-256。
- bundle hash：将 task 内所有文件按 POSIX 风格相对路径（`/` 分隔、UTF-8）字典序排序，逐行拼接 `<relpath>  <sha256>\n`（两个空格分隔），对拼接结果的 UTF-8 字节再取 SHA-256。
- manifest hash：发布产物中 `manifest.yaml` 文件字节的 SHA-256，记录于同目录 `SHA256SUMS` 与 Release 说明。

框架在安装数据集与创建 Run 时记录以上全部标识，历史 Run 可精确追溯到具体数据集内容。

## 6. 评分 Rubric

每道任务自带 `rubric.yaml`，不同任务类型使用不同维度。rubric 必须给出：维度 id、权重（总和 1.0）、维度说明、评分锚点（至少 0 / 0.5 / 1.0 三档）。总分为各维度加权后的派生结果，数据集不存储"总分"字段。

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

常用维度建议（各任务可增删）：算法题 `correctness / core_insight / complexity / proof / completeness`；论文任务 `understanding / reasoning / transfer / criticism / counterexample`；Debug 任务 `bug_identification / root_cause / patch_correctness / minimality / regression_risk`。

## 7. Anchor Answer

flagship / canary 任务必须提供 anchor answers（建议 0 / 25 / 50 / 75 / 100 五档），存放于 `anchors/`（judge 可见），用于 judge 校准、judge 一致性测试与 rubric 验证。普通任务可选。

## 8. 程序判定契约（verify.yaml）

需要程序判定的任务在 `judge_assets/verify.yaml` 中声明机器可执行的判定契约（schema 见数据集仓库 `schemas/verify.schema.json`）。框架据此自动准备环境并执行判定，产出判定事实（Verifier Facts）供 judge 使用。当前定义两类契约：

### 8.1 工程修复任务（issue_resolution）

```yaml
repo: pallets/click                 # GitHub owner/repo
repo_url: https://github.com/pallets/click
base_commit: <sha>                  # 修复前仓库状态
environment:
  python: "3.14"                    # 判定环境 Python 版本
  setup:                            # 仓库内依赖安装步骤（框架在自带解释器的 venv 中执行；
    - pip install -e . pytest       #   git clone/checkout 步骤由框架快照供给替代）
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
  mode: 程序判题
  reference_solution: judge_assets/reference_solution.cpp   # 期望输出来源（相对任务根路径）
  samples: judge_assets/samples.json          # 官方样例 [{input, output}]
  harness:
    generator: judge_assets/generator.py      # 用法：python generator.py <seed> <用例数>
    brute_force: judge_assets/brute_force.py  # 数据集自验对拍用，框架判定不依赖
```

判定流程：编译/解释运行 solver 代码 → 官方样例回归 → 生成器应力测试（期望输出由参考解计算）→ 记录通过与失败用例。`verify.yaml` 中的 `source` / `local_import` / `archived_content` / `verification` 段为溯源与复现记录，框架不执行。

## 9. Solver 输出契约与框架实现约定

协议不约束 solver 的内部思考过程，但程序判定类任务要求 solver 的最终回答包含可提取的产物。框架按任务类型在 solver prompt 中声明输出格式：

| 任务类型 | 约定产物 |
| --- | --- |
| `issue_resolution` | 单个 ```diff 围栏内的 unified diff 补丁（git 风格，`a/`、`b/` 前缀），只改源码、不改测试 |
| `code_generation` / `implementation` | 单个 ```cpp 或 ```python 围栏内的完整程序（标准输入读入、标准输出写出） |
| 其他 | 直接文本回答 |

提取失败时判定事实记录 `patch_applied=false` / `code_extracted=false`，judge 按 anchors 对相应维度打 0 档。数据集作者在 problem.md 中写明任务要求即可，输出格式由框架 prompt 统一声明。

框架实现约定（不改变协议语义）：

- **统一 agent 执行**：框架为每个任务准备隔离工作区，Solver 多轮调用工具（读题、写文件、执行命令）完成作答。`issue_resolution` 在仓库快照工作副本中修复，改动自动生成为补丁围栏；`code_generation` / `implementation` 在含题面与 solver 附件的自由工作区中编写解答文件（优先 `solution.cpp` / `solution.py`），框架在结束时把解答作为代码围栏并入回答；其余任务保留模型的最终文本。
- **轮次与推理配置**：最大工具循环轮次来自 Reasoning Profile（可设为不限制），推理强度、思考预算、输出上限、采样参数逐轮透传并冻结进 Run 配置快照。
- **输出预算耗尽（截断）**：某轮模型输出因长度上限结束时，框架提示模型改用动作表达并继续；`turns / finish_reason / truncated` 作为判定事实的一部分提供给 judge，截断与答错在数据上可区分。
- **评分派生**：总分 = Σ(维度权重 × 0~1 维度分) × 100，为框架侧派生数据，可随聚合算法版本重算。
- **安装输入**：框架接受数据集目录或发布产物归档（`.tar.gz` / `.tgz` / `.tar`）；归档安全解压后按同一校验流程安装，归档删除不影响历史（托管副本保留）。
- **可见性执行**：框架按 manifest 声明与 `meta.yaml.visibility_overrides` 计算有效可见性；`problem.md` 必须对 solver 可见、`rubric.yaml` 必须对 judge 可见，否则拒绝安装。被覆盖为 `meta` 的文件不进入任何提示词。

## 10. 框架环境义务

程序判定所需环境由框架自动供给，不要求用户预装：

- Python 解释器与依赖：框架自动下载独立 Python 构建并创建虚拟环境，按 `environment.python` 供给。
- 仓库快照：框架按 `repo_url` + `base_commit` 下载源码归档（GitHub tarball），本地缓存，不依赖用户安装 git。安装数据集时对带判定契约的任务预取快照，Run 判定时缓存未命中再重试。
- C/C++ 编译器：优先使用系统编译器；Windows 上缺失时自动下载便携 MinGW；无法供给时该任务判定降级为"不可用"，judge 仅依据文本证据评分并在结果中标注。
- 补丁应用：框架内置 unified diff 应用能力。

全部判定产物（补丁应用结果、测试输出、耗时、工具链版本）属于 Run 的原始事实，框架必须落库保存。框架实现约定：判定整体受框架侧超时约束，超时记为 failed；判定环境记录解释器版本、uv 版本与依赖清单，供历史核对；判定失败或工具链不可用时降级为纯文本评审，不阻塞 Run；历史 Run 的判定缺失可在重新评分时补齐，历史判定记录不被覆盖。`distribution: full` 产物安装时把附带的仓库快照注册到本地缓存，判定阶段命中缓存、不联网；`standard` 产物安装时尽力预取，缓存未命中时重试下载。

## 11. 元数据（meta.yaml）

必填字段由数据集仓库 `schemas/task.schema.json` 定义，核心包括：

- `lifecycle.status`：`draft | review | active | deprecated | superseded`
- `lifecycle.stage`：`fresh | mature | legacy`（映射规则见数据集仓库 DATASET_POLICY.md）
- `freshness`：`created_at` / `source_published_at` / `dataset_release_at` / `class`（F0/F1/F2/legacy）
- `contamination`：`risk`、`source_publication_date`、`exact_problem_public`、`reference_public`、`transformation[]`、`notes`
- `source`：`kind`、`name`、`url`、`published_at`、`license`、`redistribution`、`attribution`、`upstream_version`、`upstream_commit`、`adapter`
- `difficulty.author`：人工预测难度（`easy | medium | hard | very_hard | frontier`）
- `license`：该任务内容本身的许可

经验难度与区分度由框架根据真实运行结果计算，作为独立 analysis artifact 回流；数据集仓库接收此类产物时单独存放，不覆盖 `meta.yaml`。

## 12. 外部任务接入

外部基准通过数据集仓库 `adapters/` 接入，adapter 负责把上游格式转换为本协议。adapter 元数据记录 `upstream`（名称、主页、许可、再分发策略、版本/commit 固定方式）与 `adapter.version`。上游更新时升级 adapter 并重放转换，禁止 fork 整个外部数据集进数据集仓库。再分发策略分级见数据集仓库 LICENSING.md。

## 13. 稳定性承诺

- 已发布（进入任一 release）的任务内容不可原地修改；修正错误通过 errata 记录 + `revision + 1` + 新数据集版本发布。
- 被替代的任务标记 `status: superseded` 并填写 `superseded_by`。
- 协议新增可选字段不递增 `protocol_version`；删除或改变既有字段语义才递增。

## 14. 校验（框架侧工具）

框架侧使用 `protocol/tools/validate_dataset.py` 校验数据集目录，执行 JSON Schema 校验、文件 hash 与 bundle hash 校验、rubric 权重检查与可见性约束检查；框架安装数据集时执行同等校验。数据集仓库侧的构建与评审工具见其自身文档。
