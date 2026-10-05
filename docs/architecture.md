# 架构说明

本文档记录 OnprsBench_Core 的架构与关键实现决策。面向开发者；用户向说明见 README.md。

## 1. 总览

```
apps/desktop/   Tauri 2 + React + TypeScript + Vite（桌面壳与 UI）
server/         Python + FastAPI sidecar（编排、分析、SQLite、数据集加载）
protocol/       Dataset Protocol：JSON Schema、版本化文档、校验工具、mock 样例
docs/           本文档、数据库 schema 文档
tests/          跨层集成测试（经子进程黑盒测试协议工具）
```

运行形态：Tauri 桌面壳内嵌 React 前端；前端通过 HTTP 访问本机 `127.0.0.1:8765` 上的
FastAPI sidecar；sidecar 读写 SQLite（默认位于用户数据目录，可用 `ONPRSBENCH_DATA_DIR`
覆盖）。所有数据留在本机。

被评测对象不是裸模型，而是 Evaluation Target：

```
Evaluation Target = Model × Provider × Deployment × Reasoning Profile × Inference Config
```

因此 `model_name` 不作为任何实体的唯一身份；同一模型的不同 Provider 渠道、不同
reasoning effort 都是独立的 Deployment / Profile。

## 2. 关键决策

### 2.1 模型调用层：LiteLLM + 自有 runtime 抽象（MVP 未直接嵌入 Inspect AI）

AGENTS.md 默认选型是 Inspect AI 作为 eval runtime。实现前评估后决定 MVP 不直接嵌入，
理由：

1. Inspect 的执行模型（`eval()` / `eval_set()`、`.eval` 日志文件、自有 retry/resume）
   与本项目要求的「SQLite 中保存 immutable raw facts、逐条 solver/judge execution 落库、
   多 Judge 并行编排、Judge 匿名化、不重跑 Solver 的重评分」高度重叠且冲突：
   自研部分（Multi-Judge 编排、聚合、分歧分析）恰好位于 Inspect scorer 所在层，
   真正可复用的只剩并发与重试，而它们只是 asyncio 的薄封装。
2. Inspect 的日志格式与我们的关系型存储需要双向转换，增加出错面。

当前方案：`app/runtime/base.py` 定义 `ModelClient` 协议（输入 `ModelRequest`，输出
含 usage/时间戳/延迟的 `ModelResult`），业务层只依赖该协议；`litellm_client.py` 是
LiteLLM driver；`mock_client.py` 是确定性离线 driver。后续如需引入 Inspect AI，可在
该抽象层新增 adapter，数据模型与协议不变。

### 2.2 价格解析

优先级：Deployment 手动 override → models.dev（内存 + 磁盘缓存，离线自动降级）→
LiteLLM cost map → Unknown。

每次 Run 启动时为每个用到的 Deployment 固化一条 `pricing_snapshots` 记录，
`usage_records` 引用快照计算成本。历史成本绝不按未来价格重算。
价格未知时成本记 `NULL`（未知），不静默记 0。

### 2.3 数据不可变与派生指标

- Immutable raw facts：task snapshot（`tasks.payload`）、solver prompt/response、
  raw API usage、raw judge output、timestamps、pricing snapshot、config snapshot。
  这些行一经写入不更新（唯一例外：执行中行的 status/finished_at 从 running 置为终态）。
- Derived metrics：weighted_total、mean/median/stddev、Judge disagreement、
  run 级成本聚合等，全部由原始事实实时计算；聚合算法以 `aggregation_versions`
  版本化（当前 `weighted-mean/v2`：维度分 0~1 × 权重 × 100，对齐协议 weight/anchors 模型），
  改算法只新增版本，可重算历史。
- Judge 并发执行的耗时按 wall time 记录（阶段计时），不把各 Judge latency 相加。

### 2.4 Solver / Judge 信息隔离

- Solver prompt 只含 `solver_visible`（`app/services/prompts.py`）。
- Judge prompt 含 problem/reference/rubric/pitfalls/alternatives 与候选回答，
  候选回答固定以 `Candidate Response #A` 匿名引用，prompt 中不出现 Solver 的
  品牌、Provider、Deployment 名称与价格。该不变量由 `test_judge_anonymization` 守护。

### 2.5 Run 可复现与可比性

Run 创建时冻结：framework version、git commit、dataset id/version/revision、
manifest hash、suite、task revisions、solver/judge 目标、judge prompt 模板版本、
聚合算法版本（`config_snapshots.payload`）。

`GET /api/runs/compare?ids=...` 逐字段比对，任一不一致即判
`Not Directly Comparable` 并列出原因。

### 2.6 密钥

API key 只进系统 keyring（Windows Credential Manager / macOS Keychain /
Secret Service），数据库仅存 `credential_ref`（随机 UUID）。keyring 不可用时
报错而不是退化为明文。

### 2.7 后台执行模型

uvicorn 单进程；`RunManager` 在 FastAPI lifespan 捕获主事件循环，路由（threadpool）
通过 `asyncio.run_coroutine_threadsafe` 提交 Run/Rejudge 任务。Solver 并发受
`solver_concurrency` 信号量限制，Judge 以 2 倍并发并行，程序判定以
`verifier_concurrency`（默认 2）单独限流。SQLite 写入为毫秒级，
直接在事件循环线程执行（本地优先场景下的取舍，避免 asyncio driver 复杂度）。

### 2.8 程序判定（Verifier）

带 `judge_assets/verify.yaml` 契约的任务在 Solver 完成后、Judge 评分前执行程序判定
（`app/verifier/`）：

- **工程修复（issue_resolution）**：按 `repo_url + base_commit` 供给仓库快照 →
  独立 venv 安装依赖 → 应用 judge 侧测试补丁 → 提取并应用 solver 的 unified diff 补丁 →
  pytest 运行 FAIL_TO_PASS / PASS_TO_PASS（JUnit XML 结构化采集）。
- **竞赛代码（code_generation）**：提取 solver 代码（cpp/python）→ 编译（C++）→
  官方样例回归 → 生成器应力测试（期望输出由参考解计算，参考解按内容 hash 缓存编译产物）。
  输出比对为 token 级归一化；解释型语言时限按题目时限 ×3 放宽（框架约定）。

判定产物（`verifier_executions` 表：facts、工具链环境、日志尾部、耗时）是 immutable
raw facts，注入 Judge prompt（"程序判定事实"段落）；rubric 中声明"程序 verifier 判定"
的维度由 Judge 以事实为依据打分。rejudge 复用历史判定事实，不重新执行。
判定失败/工具链不可用只影响该任务的 facts（status=failed/unavailable），不阻塞 Run。

### 2.9 工具链自带供给

程序判定所需环境由框架自动供给（`app/toolchain/`），不要求用户预装：

- **uv**：优先复用数据目录内的自带副本，其次系统 PATH，最后从 GitHub releases 下载
  静态二进制。
- **Python 解释器/venv/依赖**：全部经 uv（`uv python install` 下载独立构建、
  `uv venv`、`uv pip install`），按任务契约的 `environment.python` 供给。
- **仓库快照**：GitHub codeload tarball（按 commit），本地缓存，不依赖 git。
- **C/C++ 编译器**：优先系统 g++/clang++；Windows 缺失时自动下载便携 MinGW（winlibs）；
  无法供给时抛 `ToolchainUnavailable`，该任务判定降级为 unavailable。
- **补丁应用**：patch-ng（纯 Python，自动剥离 git 风格 a//b 前缀）。

所有供给产物与缓存均在数据目录下（`toolchains/`、`cache/`、`verify_workspaces/`）。

### 2.10 Tauri sidecar 生命周期

当前为开发模式：`tauri dev` 的 `beforeDevCommand` 运行 `scripts/dev.mjs`，
同时拉起 Python sidecar（优先 `server/.venv`）与 Vite dev server。
若 8765 端口已有健康 sidecar 则直接复用，避免多实例写同一 SQLite。
前端冷启动有 `BackendGate` 轮询 `/api/meta`，避免 sidecar 未就绪导致的竞态报错。

正式发布打包（PyInstaller 单文件 sidecar + Tauri bundle externalBin）属于 Roadmap，
当前版本以开发者从源码运行为主。

## 3. Dataset Protocol

框架与数据集之间唯一的稳定接口，见 `protocol/docs/dataset-protocol-v1.md`
（协议本体由 OnprsBench_Dataset 仓库 `PROTOCOL.md` 定义，两侧语义一致）。
要点：发布产物 `manifest.yaml` + task bundle 文件目录、JSON Schema 校验、
协议版本协商、文件/bundle/manifest 三级 sha256、Solver/Judge/meta 三级可见性、
weight + anchors rubric、anchors 校准回答、verify.yaml 程序判定契约、
离线本地目录优先、为远程 registry 预留。

安装时框架把数据集目录复制为数据目录下的托管副本（`datasets/<manifest_hash 前 16 位>/`），
与原始目录解耦；bundle 内容组装为 `tasks.payload` 快照（problem 文本、rubric、
reference 拼接、anchors、verify 契约、meta.yaml 元数据）。

`protocol/examples/mock-protocol-sample/` 仅是协议测试样例（bundle 形态，
`build_manifest.py` 重新生成 manifest），不构成真实评测数据；
真实 benchmark 题目永不进入本仓库。

## 4. API 概览

| 路由 | 说明 |
|---|---|
| `GET /api/meta` | 框架版本、commit、支持的协议版本 |
| `GET/POST/PATCH/DELETE /api/providers` | Provider 管理（api_key write-only） |
| `GET/POST/PATCH/DELETE /api/deployments` | Deployment；被历史 Run 引用时禁止删除（409） |
| `GET/POST/DELETE /api/reasoning-profiles` | Reasoning Profile；被引用时禁止删除（409） |
| `DELETE /api/datasets/installations/{id}` | 卸载数据集；被 Run 引用时禁止（409） |
| `GET /api/provider-types` | 内置 provider 类型与默认值 |
| `GET /api/providers/{id}/models` | 从 provider 拉取模型列表（尽力而为） |
| `GET/POST /api/models` | canonical Model |
| `GET/POST/PATCH/DELETE /api/deployments` | Deployment |
| `GET/POST/DELETE /api/reasoning-profiles` | Reasoning Profile |
| `POST /api/datasets/installations` | 校验并安装本地数据集目录 |
| `GET /api/datasets/installations[/...]` | 浏览安装与题目 |
| `POST /api/runs` | 创建并启动 Run（冻结 config snapshot） |
| `GET /api/runs[/{id}]` | Run 列表/详情（含成本、耗时、计数） |
| `POST /api/runs/{id}/cancel` | 取消运行中的 Run（进行中的执行记录置为 cancelled） |
| `GET /api/runs/{id}/results` | 结果视图：target 汇总 + 逐题明细 + judge 分歧 |
| `GET /api/runs/{id}/usage` | 逐次调用 usage + pricing snapshot |
| `GET /api/runs/{id}/config-snapshot` | 冻结配置 |
| `POST /api/runs/{id}/rejudge` | 对历史回答重新评分（只新增 judge 记录） |
| `GET /api/runs/compare?ids=` | Run 可比性判定 |
| `GET /api/analytics/timeseries` | Score/Cost/Latency over time 数据点 |

## 5. 测试

- `server/tests/`（pytest）：协议校验（bundle/hash/rubric）、聚合解析、价格优先级、
  端到端 Run（mock provider）、Judge 匿名化、重评分不改写原始事实、Run 可比性、
  时间序列；verifier 单测（补丁/代码提取、算法对拍、SWE 补丁判定，
  本机工具链夹具）与编排集成测试（facts 落库、prompt 注入、rejudge 复用、降级）。
- `tests/`（仓库根，pytest 子进程黑盒）：独立协议校验工具行为。

## 6. Roadmap

- 打包分发：PyInstaller sidecar + Tauri installer（externalBin）。
- TTFT/generation time：流式调用采集（当前非流式，记 NULL）。
- Inspect AI adapter（如收益大于适配成本，见 2.1）。
- Agent 形态 solver（SWE 任务的 workspace 内多轮工具循环；当前为单轮补丁输出）。
- metadata_only 坐标系套件（HLE / LiveCodeBench / SWE-bench）的用户本地导入流程。
- Judge calibration 自动化（anchors 一致性报告）与 aggregation 算法版本化扩展。
- rolling mean / stddev / 30d delta / 回归检测；Reasoning Scaling 曲线；
  Task × Model heatmap；Solver × Judge matrix（数据已在库中，属于展示层工作）。
- Langfuse optional adapter（不装不影响任何功能）。
- 数据集远程 registry。
