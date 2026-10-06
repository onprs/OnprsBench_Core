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

### 2.1 模型调用层：LiteLLM + 自有 runtime 抽象（含 agent 循环；不嵌入 Inspect AI）

AGENTS.md 默认选型是 Inspect AI 作为 eval runtime。两次评估后决定不直接嵌入：

1. Inspect 的执行模型（`eval()` / `eval_set()`、`.eval` 日志文件、自有 retry/resume）
   与本项目要求的「SQLite 中保存 immutable raw facts、逐条 solver/judge execution 落库、
   多 Judge 并行编排、Judge 匿名化、不重跑 Solver 的重评分」高度重叠且冲突。
2. Inspect 的日志格式与我们的关系型存储需要双向转换，增加出错面。
3. （0.3.276 实测）Inspect 的 react agent 无法脱离其 eval sample 上下文独立运行——
   `inspect_ai.agent.run(react(...))` 在无 active sample 时抛
   `RuntimeError: checkpointer() must be called inside an active sample`，
   其上下文依赖未公开为稳定 API。因此 agent 零件同样无法复用。

当前方案：`app/runtime/base.py` 定义 `ModelClient`（单轮）与 `ToolsCapableClient`
（function calling）协议；`litellm_client.py` 是 LiteLLM driver；`mock_client.py`
是确定性离线 driver（支持 `agent_script` 脚本化动作）。
`app/agents/loop.py` 基于 ToolsCapableClient 实现多轮工具循环（约百行，不引入框架依赖）。
后续如需引入 Inspect AI，可在该抽象层新增 adapter，数据模型与协议不变。

### 2.1.1 Agent 统一执行形态

所有 Solver 调用统一走 agent 形态（`app/agents/loop.py`）：runner 为任务准备工作区，
多轮循环驱动模型在其中执行 list_files / read_file / write_file / search_text /
run_command。带仓库契约的任务（`verify.yaml` 含 `base_commit`，如 issue_resolution）
以安装时预取的仓库快照为工作区，结束后把工作副本与纯净快照的差异生成为 unified diff
（纯 Python，不依赖 git），拼接进 `response_text` 的 ```diff 围栏；其余任务以含题面、
solver 可见附件的自由工作区运行，竞赛代码任务在结束时从工作区收集约定解答文件
（`solution.cpp` / `solution.py` 等）作为回答主体。两类任务的判定与 Judge 流程一致。

轮次与推理参数均来自 Reasoning Profile（`agent_max_turns`：空 = 框架默认 40，
0 = 不限制，>0 = 上限；reasoning_effort / max_output_tokens / provider_params 等同榜传递），
每轮调用复用同一 `ModelRequest` 模板。逐轮 usage、耗时、`finish_reason` 与工具调用
记入 `solver_executions.raw_response_json.turn_records`，每一轮单独写一条 `usage_records`。

输出预算保护：某轮 `finish_reason == length`（思考/回答耗尽输出预算）时框架追加
续行提示，要求模型停止长篇推演并直接写文件，最多连续提示 2 次；最终把
`turns`、`finish_reason`、`truncated` 落库，并把 solver 元信息注入判定事实，
让 Judge 把“被截断”与“答错”区分开。工具链无法供给工作区或框架开关关闭时，
降级为单轮问答并记录 `solver_mode: oneshot_fallback`。

工作区隔离（`app/agents/sandbox.py`）：

- **命令允许名单**：只允许解释器/编译器/只读查看类命令（python、pytest、g++、
  ls、grep…）；下载工具（curl/wget/git/pip/uv/ssh）与 shell（sh/bash/cmd/
  powershell）不在名单内；工作区内自编译的二进制可执行，脚本后缀不直接执行。
- **参数校验**：拒绝绝对路径、父目录穿越、URL，以及 `python -c` 与 `-S/-E/-I`
  等绕过解释器环境的参数；命令以 argv 直接执行（不经过 shell），不支持管道与重定向。
- **环境清理**：子进程不继承代理与疑似凭据变量（KEY/SECRET/TOKEN/PASSWORD 等标记），
  TEMP/TMP 指向工作区，PYTHONNOUSERSITE=1。
- **Python 网络限制**：向子进程注入 sitecustomize，把 socket 解析与连接限制在
  回环地址（pytest 的本地服务可用）；Linux 且权限允许时额外使用 `unshare -n`。
- **可追溯**：被拒绝与可疑命令写入 `command_audit`（reasons + blocked 标记）；
  `agent_sandbox_mode` 控制 enforce（默认，拒绝违规命令）与 audit（仅记录）。

工作区产物过滤：`app/agents/diff.py` 在生成补丁时剔除缓存/依赖/归档/锁文件/超大文件
并记录原因（`excluded_files`），同时把可疑产物（下载目录、临时转储等）记入
`suspicious_files`。

能力边界：工作区内新编译的二进制无法在进程内限制其网络/文件系统行为；
真正的强隔离需要平台级沙箱（见第 6 节 Roadmap）。

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
- 截断与轮次是一等事实：`solver_executions.turns / finish_reason / truncated` 可直接查询，
  `GET /api/runs/{id}/results` 返回逐题 turns / truncated / solver_mode，
  Target 汇总给出 `turns_mean / turns_total / truncated_count`。

### 2.4 Solver / Judge 信息隔离

- Solver prompt 只含 `solver_visible`（`app/services/prompts.py`）。
- Judge prompt 含 problem/reference/rubric/pitfalls/alternatives 与候选回答，
  候选回答固定以 `Candidate Response #A` 匿名引用，prompt 中不出现 Solver 的
  品牌、Provider、Deployment 名称与价格。该不变量由 `test_judge_anonymization` 守护。

### 2.5 Run 可复现与可比性

Run 创建时冻结：framework version、git commit、dataset id/version/revision、
manifest hash、suite、task revisions、solver/judge 目标、judge prompt 模板版本、
solver prompt 模板版本、聚合算法版本、每个 Reasoning Profile 的完整参数快照
（`config_snapshots.payload`）。

`GET /api/runs/compare?ids=...` 逐字段比对，任一不一致即判
`Not Directly Comparable` 并列出原因。比对项包括数据集标识、suite、框架版本、
task revision 集合与评分口径（judge prompt 模板版本、聚合算法版本）；
Solver / Judge 目标不同属于正常跨 target 对比场景，不作为不可比原因。

### 2.6 密钥

API key 只进系统 keyring（Windows Credential Manager / macOS Keychain /
Secret Service），数据库仅存 `credential_ref`（随机 UUID）。keyring 不可用时
报错而不是退化为明文。

### 2.7 后台执行模型

uvicorn 单进程；`RunManager` 在 FastAPI lifespan 捕获主事件循环，路由（threadpool）
通过 `asyncio.run_coroutine_threadsafe` 提交 Run/Rejudge 任务，任务完成后从任务表清理。
Solver 并发受 `solver_concurrency` 信号量限制，Judge 以 2 倍并发并行，程序判定以
`verifier_concurrency`（默认 2）单独限流。SQLite 写入为毫秒级，
直接在事件循环线程执行（本地优先场景下的取舍，避免 asyncio driver 复杂度）。

单次模型调用有墙钟上限（`llm_call_timeout_s`，默认 1800s，0 = 不限制）：
超时视为确定性失败、不重试，避免长思考或挂起连接让 Run 永久停留。
服务启动时 `recover_interrupted_runs()` 把上次进程遗留的 pending/running Run
与子执行记录置为终态（可追溯的失败原因），并清理崩溃时未回收的判定工作区。
取消一个 Run 时会把 Solver / Judge / Verifier 三类进行中的执行记录一并置为 cancelled。

### 2.8 程序判定（Verifier）

带 `judge_assets/verify.yaml` 契约的任务在 Solver 完成后、Judge 评分前执行程序判定
（`app/verifier/`）：

- **工程修复（issue_resolution）**：按 `repo_url + base_commit` 供给仓库快照 →
  独立 venv 安装依赖 → 应用 judge 侧测试补丁 → 提取并应用 solver 的 unified diff 补丁 →
  pytest 运行 FAIL_TO_PASS / PASS_TO_PASS（JUnit XML 结构化采集）。F2P/P2P 以契约声明为准：
  声明文件/类级 nodeid 时按前缀聚合该范围下全部用例（取最差结果），未采集到的声明项
  记为 `not_found` 并视为不通过，避免漏跑被静默忽略。
- **竞赛代码（code_generation）**：提取 solver 代码（cpp/python）→ 编译（C++）→
  官方样例回归 → 生成器应力测试（期望输出由参考解计算，参考解按内容 hash 缓存编译产物）。
  输出比对为 token 级归一化；解释型语言时限按题目时限 ×3 放宽（框架约定）。

判定整体受 `verify_timeout_s`（默认 1800s）约束：安装、编译、测试每一步都从剩余预算中扣除，
超时或超出预算时抛出并记为 failed，不会让单个任务无限占用判定线程。
判定产物（`verifier_executions` 表：facts、工具链环境、日志尾部、耗时）是 immutable
raw facts，注入 Judge prompt（"程序判定事实"段落）；rubric 中声明"程序 verifier 判定"
的维度由 Judge 以事实为依据打分。判定事实还携带 solver 的 `turns / truncated /
finish_reason`，Judge 据此区分“答错”与“输出被截断”（prompt 显式说明截断不额外扣分）。
同一 Run 内已完成的判定不重复执行；rejudge 遇到判定缺失（failed/unavailable）时
先补齐判定再评分，不改写历史判定记录。
判定失败/工具链不可用只影响该任务的 facts（status=failed/unavailable），不阻塞 Run。

### 2.9 工具链自带供给

程序判定所需环境由框架自动供给（`app/toolchain/`），不要求用户预装：

- **uv**：优先复用数据目录内的自带副本，其次系统 PATH，最后从 GitHub releases 下载
  静态二进制。
- **Python 解释器/venv/依赖**：全部经 uv（`uv python install` 下载独立构建、
  `uv venv`、`uv pip install`），按任务契约的 `environment.python` 供给。
- **仓库快照**：GitHub codeload tarball（按 commit），本地缓存，不依赖 git。
  standard 数据集安装时预取全部快照，任一项失败即中止安装（返回失败项与改用完整
  数据集的提示）；full 数据集安装时注册附带归档到 `cache/repo_archives/`，
  判定阶段优先命中本地归档、不联网，缓存未命中时重试下载。
- **C/C++ 编译器**：优先系统 g++/clang++；Windows 缺失时自动下载便携 MinGW（winlibs）；
  无法供给时抛 `ToolchainUnavailable`，该任务判定降级为 unavailable。
- **补丁应用**：patch-ng（纯 Python，自动剥离 git 风格 a//b 前缀）。
- **判定环境记录**：`verifier_executions.environment_json` 记录解释器路径与版本、
  uv 版本、`uv pip freeze` 的依赖清单与编译器版本，便于历史判定核对复现。

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
reference 拼接、anchors、verify 契约、meta.yaml 元数据）。安装时按 manifest 声明
与 `meta.yaml.visibility_overrides` 计算有效可见性：override 只能引用已登记文件，
`meta` 可见的文件既不进 solver 也不进 judge；`problem.md` 必须对 solver 可见、
`rubric.yaml` 必须对 judge 可见，否则拒绝安装。

分发形态（协议 2.1/2.2）：同一版本号可发布 standard 与 full 两套产物。standard 安装时
预取全部仓库快照，任一项失败即中止安装（不创建记录）并提示改用完整数据集；full 安装时
校验附带的 `resources[]`（path / bytes / sha256 / 许可文件）并注册到 `cache/repo_archives/`，
注册失败同样中止安装。判定阶段 `fetch_repo_snapshot` 优先命中本地归档、不联网，
缓存未命中时才会重试下载。两套产物共用 dataset id、版本号与 revision，以 manifest hash 区分与追溯。

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

- `server/tests/`（pytest）：协议校验（bundle/hash/rubric/可见性覆盖）、聚合解析、价格优先级、
  端到端 Run（mock provider）、Judge 匿名化、重评分不改写原始事实、Run 可比性、
  时间序列；agent 形态测试（Profile 参数透传、轮次上限与不限制、截断续行、
  工作区解答收集、算法任务 agent 端到端与逐轮 usage）；verifier 单测（补丁/代码提取、
  算法对拍、SWE 补丁判定，本机工具链夹具）与编排集成测试（facts 落库、prompt 注入、
  rejudge 补齐判定、降级、启动恢复）。
- `tests/`（仓库根，pytest 子进程黑盒）：独立协议校验工具行为。

## 6. Roadmap

- 打包分发：PyInstaller sidecar + Tauri installer（externalBin）。
- 平台级执行沙箱：已实现命令允许名单、参数校验、环境清理、Python 子进程网络限制
  （回环 + Linux `unshare -n` 尝试）；工作区内自编译二进制与 Windows 平台仍需
  OS 级沙箱（Job Object / 容器）才能完全隔离。
- Inspect AI adapter（如收益大于适配成本，见 2.1）。
- metadata_only 坐标系套件（HLE / LiveCodeBench / SWE-bench）的用户本地导入流程。
- Judge calibration 自动化（anchors 一致性报告）与 aggregation 算法版本化扩展。
- rolling mean / stddev / 30d delta / 回归检测；Reasoning Scaling 曲线；
  Task × Model heatmap；Solver × Judge matrix（数据已在库中，属于展示层工作）。
- Langfuse optional adapter（不装不影响任何功能）。
- 数据集远程 registry。
