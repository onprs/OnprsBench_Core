# OnprsBench_Core

开源、本地优先的 LLM Benchmark Lab：长期评测、对比和追踪大语言模型的真实推理能力。

被评测的对象不是裸模型，而是 **Evaluation Target = Model × Provider × Deployment × Reasoning Profile × Inference Config**。同一个模型的不同渠道（官方 API / OpenRouter）、不同思考强度（low / medium / high），都是相互独立的评测对象。

## 功能

- **多 Solver × 多 Judge**：任意多个被评模型与评分模型，Judge 并行运行，评分输入对 Solver 身份匿名化
- **程序判题**：对带判定契约的任务（工程修复、竞赛代码）在本地真实执行判定——应用补丁跑 FAIL_TO_PASS/PASS_TO_PASS 回归测试、编译代码跑官方样例与生成器应力对拍；判定事实注入评分依据
- **环境自带**：程序判定所需的 Python 解释器、依赖环境、仓库快照、C++ 编译器均由应用自动供给与缓存（首次使用需联网），不要求用户预装；无法供给时自动降级为纯文本评审并标注
- **结构化评分**：按 rubric 维度打分（权重 × 0~1 维度分），保留每个 Judge 的原始输出，聚合 mean / median / stddev / min / max，分歧过大自动标记
- **完整追溯**：每次 Run 冻结 framework 版本与 git commit、数据集 id/version/revision/manifest hash、task revision、全部推理配置与价格快照
- **成本与延迟**：逐次调用记录 input/cached/output/reasoning tokens、成本、延迟；区分 solver / judge / 总计；历史成本永远按当时的定价快照计算
- **时间维度**：Score / Cost / Latency over time，观察模型是否"只在今天聪明"
- **对比**：多 Run 对比前自动判定可比性（数据集版本、修订、框架版本），Score vs Cost 散点图含 Pareto 前沿
- **历史重评**：不重跑 Solver，用新 Judge 或新聚合算法重新评分历史回答
- **数据集解耦**：评测题目来自符合 [Dataset Protocol](protocol/docs/dataset-protocol-v1.md) 的独立数据集，数据集独立发版、独立更新；本仓库不含真实 benchmark 题目

## 界面

桌面应用（Tauri 2 + React），包含：设置（Provider / Deployment / Reasoning Profile）、数据集、新建 Run、历史（时间序列图表）、对比、Run 详情（逐题明细与 Judge 分歧）。

## 快速开始

环境要求：Python ≥ 3.11、Node.js ≥ 20 与 pnpm、Rust 工具链（[Tauri 2 前置要求](https://tauri.app/start/prerequisites/)）。

```bash
git clone <repo-url> && cd OnprsBench_Core

# 1. 后端依赖
python -m venv server/.venv
server/.venv/Scripts/pip install -r server/requirements.txt   # Windows
# server/.venv/bin/pip install -r server/requirements.txt     # Linux/macOS

# 2. 前端依赖
cd apps/desktop && pnpm install

# 3. 启动桌面应用（自动拉起本地后端与前端）
pnpm tauri dev
```

也可以只用浏览器：先运行 `server/.venv/Scripts/python -m app.main`（Windows）
启动后端，再在 `apps/desktop` 运行 `pnpm dev`，打开 <http://localhost:14200>。

首次体验无需任何 API key：在「设置」中创建一个 **Mock** 类型的 Provider，
拉取并添加 mock 模型为 Deployment，然后在「数据集」中安装
`protocol/examples/mock-protocol-sample`（协议测试样例目录），即可离线跑通
完整评测流程。

程序判题说明：仅当任务带判定契约（如 SWE 修复、竞赛代码题）时触发。
安装数据集时会预取判定所需的仓库快照（失败不阻断安装，可在 Run 时重试）；
首次运行会自动下载所需工具链（uv / Python 独立构建；Windows 缺编译器时
自动下载便携 MinGW），之后离线可用；无网络时自动降级为纯文本评审，不影响其余功能。

接入真实模型：创建对应类型的 Provider（OpenAI / Anthropic / Gemini / OpenRouter /
DeepSeek / OpenAI 兼容 / Ollama），填写 API Key。Key 只保存在系统安全存储中
（Windows Credential Manager / macOS Keychain / Secret Service），不会写入数据库或磁盘文件。

## 数据集

本仓库不内置真实 benchmark 题目。配套数据集是独立项目
[OnprsBench_Dataset](https://github.com/onprs/OnprsBench_Dataset)：下载其 Release 产物
（含冻结的 manifest.yaml 与完整 hash 清单），在「数据集」页安装解压后的目录即可。
数据集独立发版，每次 Run 冻结 dataset id/version/commit/manifest hash，历史可追溯。

任何符合 [Dataset Protocol v1](protocol/docs/dataset-protocol-v1.md) 的本地目录都可安装；
数据集作者可用 `protocol/tools/validate_dataset.py` 校验数据集目录。

## 数据存放

全部数据保存在本机用户数据目录（Windows：`%APPDATA%\OnprsBench`；Linux/macOS：
`~/.local/share/onprsbench`），包含 SQLite 数据库、已安装数据集的托管副本、
价格目录缓存与程序判定工具链缓存。

## 文档

- [架构说明](docs/architecture.md)
- [数据库 Schema](docs/database-schema.md)
- [Dataset Protocol v1](protocol/docs/dataset-protocol-v1.md)

## License

MIT
