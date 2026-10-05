# AGENTS.md — OnprsBench_Core 开发规范

本文件是 AI 代理与开发者在本仓库工作时必须遵守的项目级规范，优先级高于通用习惯。

## 一、项目定位

OnprsBench_Core 是一个开源、本地优先的 LLM Benchmark Lab，用于长期评测、对比和追踪大语言模型的真实推理能力。

真正被评测的对象是 Evaluation Target：

```
Evaluation Target = Model × Provider × Deployment × Reasoning Profile × Inference Config
```

项目目标是支持从 Quality × Cost × Latency × Time 四个维度比较不同 Evaluation Target，支持长期能力漂移追踪与 Run 级可复现。

## 二、不可妥协的架构原则

以下原则不因实现便利性而让步：

1. **评测框架与测评数据集完全解耦**。二者只通过版本化的 Dataset Protocol 通信，数据集仓库不得 import 框架内部代码。
2. **本仓库不得内置真实 benchmark 题目**。mock dataset 仅允许作为 Dataset Protocol 的测试样例存在，并明确标注用途。
3. **原始事实不可变（Immutable raw facts）**：task snapshot、solver prompt、solver response、raw API usage、raw judge output、timestamps、pricing snapshot、config snapshot 一经写入不得覆盖。
4. **统计结果均为派生数据（Derived metrics）**：总分、排名、Elo、Judge agreement、Pareto、drift 等指标必须可以在不重跑 Solver 的情况下重算。修改权重、聚合算法或校准逻辑时，历史原始数据保持有效。
5. **历史 Run 完整可追溯**：每次 Run 必须能精确追溯 framework version、framework git commit、dataset id、dataset version、dataset revision/hash、task revision。
6. **历史 Cost 不按未来价格重算**。每次实际 Run 必须保存 pricing snapshot。
7. **优先复用成熟开源基础设施**，不重新实现已有能力（见第四节技术栈）。

## 三、核心领域模型约束

### Model / Provider / Deployment 三分离

- `model_name` 不得作为任何实体的唯一身份。
- Model：canonical id、family、generation。
- Provider：id、name、type、endpoint metadata。
- Deployment：model_id、provider_id、API model name、endpoint、credential reference、supported reasoning profiles、custom options。
- 同一模型的不同 Provider 渠道是两个独立 Deployment（例如官方 API 与 OpenRouter 渠道）。
- 框架内部必须有自己的 Deployment 抽象，业务层不得直接绑定 LiteLLM 的内部数据结构。

### Reasoning Profile

- 同一 Deployment 的不同思考强度（如 Low / Medium / High）是独立评测配置。
- 记录 reasoning_effort、reasoning_budget、reasoning_tokens、max_output_tokens、temperature、top_p、seed，并允许 provider-specific 参数。
- Solver 与 Judge 各自拥有独立的 inference config。

### Solver / Judge 信息隔离

- Solver 只能看到 Dataset Protocol 定义的 solver-visible 内容。
- Judge 可以看到 problem、solver response、rubric、reference pack 及 judge-only assets。
- Judge 输入必须对 Solver 身份匿名化（如 `Candidate Response #A`），不得暴露 Solver 的模型品牌、Provider、Deployment 名称或价格。

### Multi-Judge

- 单个 Solver execution 支持任意多个 Judge，Judge 尽量并行运行。
- 必须保存每个 Judge 的原始输出，不能只存聚合分。
- 聚合至少计算 mean、median、stddev、min、max，并提供 Judge Agreement / Disagreement 分析；方差过大时标记 High Judge Disagreement。
- Judge 并发执行时，总等待时间按 wall time 记录，不得把各 Judge latency 简单相加。

## 四、技术栈

已确定选型，无充分理由不更换：

| 层 | 技术 |
|---|---|
| 桌面壳 | Tauri 2 |
| 前端 | React + TypeScript + Vite + shadcn/ui + TanStack Table + Apache ECharts |
| 后端 | Python + FastAPI（Tauri sidecar 方式运行） |
| 评测运行时 | Inspect AI（复用其 task execution、scorers、model-graded evaluation、并发、日志、重试、rescore、已保存响应） |
| 模型统一层 | LiteLLM |
| 价格来源 | 用户 Deployment 级 override → Provider 动态价格 → models.dev → LiteLLM cost map → Unknown |
| 数据库 | SQLite + SQLAlchemy 2.x |
| 可观测性 | Langfuse 仅作为 optional adapter，用户不安装时系统必须完整工作 |

自研范围：Multi-Judge 编排、Judge 聚合与分歧分析、Judge 校准、跨 Run 比较、Deployment 比较、reasoning scaling 分析、drift 分析。

不得做的事：重写 LiteLLM 已有 provider adapter、重写 Inspect AI 基本 eval runtime、自造图表库或 UI 组件库、引入 PostgreSQL、设计微服务、强制 Docker。

## 五、目标目录结构

以下为随 MVP 落地逐步建立的目标结构，实际调整时同步更新本节：

```
OnprsBench_Core/
├── apps/desktop/        # Tauri 2 + React 桌面应用（含 src-tauri/）
├── server/              # Python FastAPI sidecar：编排、分析、SQLite、数据集加载
├── protocol/            # Dataset Protocol：JSON Schema、版本化文档、校验工具
├── docs/                # architecture.md、数据库 schema 文档、Dataset Protocol 文档
├── tests/               # 跨层集成测试、protocol validation tests
├── AGENTS.md
└── README.md
```

## 六、Dataset Protocol

Dataset Protocol 是框架与外部数据集之间唯一的稳定接口：

- language-agnostic，有 JSON Schema 与显式 schema version。
- 支持向后兼容策略与 capability negotiation。
- 支持 task hash、asset hash、dataset manifest hash。
- 支持离线数据集与本地目录数据集，为未来远程 registry 保留接口。
- 框架代码只能依赖 Protocol 定义，不得依赖任何数据集的内部目录结构。
- 协议变更必须版本化，旧版本数据集在框架升级后保持可加载。

## 七、Run 可复现性

每次 Run 必须冻结并存档：run id、timestamps、framework version 与 commit、dataset id/version/revision、solver deployment、judge deployments、全部 inference configs、reasoning profiles、system prompt、judge prompt、pricing snapshot、task revisions。

任意两个历史 Run 之间必须能判定 Comparable 或 Not Directly Comparable，并输出判定原因。

## 八、数据记录要求

每次模型调用尽量记录：started_at、finished_at、TTFT、generation_time、total_latency、input_tokens、cached_input_tokens、output_tokens、reasoning_tokens（API 支持时）、pricing snapshot、calculated cost。

成本需区分 solver_cost、judge_cost、total_benchmark_cost；时间需区分 solver wall time、judge wall time、total run wall time。

时间是一级维度：Score / Cost / Latency / Judge disagreement / Token usage over time 均需支持多 Evaluation Target 同图对比。

## 九、工程规范

### 安全

- API key 等 secrets 不得以明文写入数据库或仓库；使用系统级安全存储或加密方案。
- 不得提交任何用户 credential；`.gitignore` 必须覆盖常见密钥与本地配置文件。

### 数据库

- 使用 SQLAlchemy 2.x + migrations（Alembic）。
- schema 至少覆盖：models、providers、deployments、reasoning_profiles、benchmark_suites、dataset_installations、tasks metadata cache、runs、solver_executions、judge_executions、usage_records、pricing_snapshots、config_snapshots；必要时增加 judge_calibration、aggregation_versions、derived_metrics。
- schema 变更必须配 migration 与文档更新。

### 测试

- Python 侧使用 pytest：单元测试、集成测试、protocol validation tests。
- 关键不变量必须有测试守护：raw data 不可变、Judge 匿名化、pricing snapshot 冻结、derived metrics 可重算。
- 前端关键逻辑（数据转换、比较计算）应有对应测试。

### 代码风格

- 全链路 type safety：Python 使用类型标注，前端严格 TypeScript。
- structured logging，graceful error handling。
- 简单、可靠、可测试、可扩展、本地优先，按此优先级取舍；避免过度抽象与为未来场景提前造复杂系统。
- Windows / Linux 优先支持。

### Git 工作流

- `main` 保持可运行状态；功能开发使用 `feature/*` 或 `fix/*` 分支，合并前通过测试。
- Commit message 使用 Conventional Commits 前缀（`feat:`、`fix:`、`docs:`、`refactor:`、`test:`、`chore:`），正文可用中文。
- 与用户 credential、本地路径、维护者私有配置相关的内容不得进入提交。

## 十、公开环境与维护环境

- README、公开安装命令与 Release 说明面向 GitHub 最终用户，只能依赖项目明确声明的系统要求与通用工具。
- 维护者本机的 SSH 别名、代理、绝对路径、测试数据、部署方式仅可出现在明确标注的开发文档或维护脚本中，不得成为公开默认配置或未声明前提。
- “一键安装”类命令必须能由目标用户在文档声明的起始环境中直接执行。

## 十一、文档与文案

- `README.md` 围绕项目用途、用户功能、安装、配置、使用展开，保持简洁，不写实现细节、内部架构、开发与测试过程。
- `docs/architecture.md` 记录架构与实现细节；`protocol/` 内维护 Dataset Protocol 文档；数据库 schema 有独立文档。
- GitHub Release 说明仅记录新增功能、功能改进和 Bug 修复。
- 文案直接陈述事实。
- 代码注释与提交说明使用中文。

## 十二、MVP 范围（第一阶段垂直切片）

1. Tauri Desktop 启动 + Python FastAPI sidecar + SQLite。
2. 添加 Provider、从 Provider 获取模型、创建 Deployment、设置 reasoning profile。
3. 加载符合 Dataset Protocol 的本地 mock dataset。
4. 选择 1~N 个 Solver 与 1~N 个 Judge，运行 benchmark，Judge 并行评分。
5. 保存 responses、scores、usage、price、latency、timestamps。
6. Results / Compare / History 页面，Score / Cost / Latency 图表。
7. 能重新打开历史 Run，能对历史 Solver response 重新 Judge。

MVP 完成前，不投入排行榜托管、远程 registry、多用户协作等超出范围的功能。

## 十三、后续方向（Roadmap 参考）

- rolling mean、std deviation、30d delta、回归检测。
- Reasoning Scaling 曲线、Pareto Frontier、Task × Model heatmap、Solver × Judge matrix。
- Judge calibration、aggregation 算法版本化。
- Langfuse optional adapter。
- 独立数据集仓库与 Dataset Protocol 远程 registry。
