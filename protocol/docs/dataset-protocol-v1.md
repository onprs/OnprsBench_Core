# Dataset Protocol v1

Dataset Protocol 是 OnprsBench_Core 框架与外部数据集之间唯一的稳定接口。框架只依赖本协议，不依赖任何数据集的内部目录结构；数据集独立维护、独立发版。

## 1. 基本形态

一个数据集是一个目录，根部包含 `manifest.json`（必需）与可选的 asset 文件：

```
my-dataset/
├── manifest.json      # 符合 schema/dataset-protocol-v1.schema.json
└── assets/...         # 可选，被 task 引用的文件
```

`manifest.json` 内联全部 suite 与 task 定义。协议是 language-agnostic 的：任何能生成符合 JSON Schema 的 `manifest.json` 的工具链都可以产出数据集。

## 2. 版本与兼容性

- `protocol_version` 是主版本号字符串（当前为 `"1"`）。
- 框架加载时进行 capability negotiation：不认识的 `protocol_version` 直接拒绝并提示支持的版本。
- 同一主版本内只允许新增可选字段（向后兼容）；破坏性变更必须升级主版本号并发布新 Schema。
- `dataset.capabilities` 声明可选能力（如 `tool_use`、`multimodal`），框架可据此提示哪些能力暂不支持。

## 3. 追溯与 hash

- `dataset.revision`：数据集内容修订标识（git commit 或内容 hash）。
- `task.revision`：单调递增整数，task 内容变更必须递增。
- task hash：对 task 对象的规范化 JSON（key 排序、紧凑分隔符、UTF-8）计算 sha256。
- manifest hash：对整个 manifest 对象以同样方式计算 sha256。
- asset hash：文件字节的 sha256，加载时校验。

框架在安装数据集与创建 Run 时记录以上全部标识，历史 Run 可精确追溯到具体数据集内容。

## 4. Solver / Judge 可见性

- `solver_visible`：Solver 唯一可见内容（`problem` + 可选 assets）。
- `judge_visible`：仅 Judge 可见（`reference`、`rubric`、`pitfalls`、`alternatives`、assets）。

框架必须保证 Solver 请求中不包含 `judge_visible` 的任何字段，Judge 请求中不包含 Solver 的身份信息（模型品牌、Provider、Deployment 名称、价格）。

## 5. Rubric

`rubric.dimensions` 定义评分维度及各自满分。Judge 输出按维度给 0~max_score 的分数，并可标记 `fatal_error`（此时全部维度记 0）。总分、加权分均为框架侧 derived data，不属于协议内容。

## 6. 校验

使用 `protocol/tools/validate_dataset.py` 校验数据集目录，执行 JSON Schema 校验、引用完整性检查与 asset hash 校验。框架安装数据集时执行同等校验。

`protocol/examples/mock-protocol-sample/` 是协议测试样例，仅用于验证协议与端到端流程，不构成真实评测数据。

## 7. 远程 registry（预留）

v1 仅支持本地目录数据集（离线优先）。未来远程 registry 只需提供能解析为同样 manifest 的下载接口，协议本身不变。
