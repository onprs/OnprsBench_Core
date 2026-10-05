# 数据库 Schema

SQLite + SQLAlchemy 2.x，迁移由 Alembic 管理（`server/alembic/`，应用启动时自动
`upgrade head`）。所有时间戳为 UTC；金额/成本为美元浮点；id 为 32 位十六进制 UUID。

## 身份层（Model / Provider / Deployment 三分离）

### models
| 列 | 类型 | 说明 |
|---|---|---|
| id | str PK | |
| canonical_id | str unique | 规范化模型身份（与供应商无关） |
| display_name | str | |
| family / generation / notes | str? | |

### providers
| 列 | 类型 | 说明 |
|---|---|---|
| id | str PK | |
| name / type | str | type: openai/anthropic/gemini/openrouter/deepseek/openai_compatible/ollama/mock |
| base_url | str? | |
| credential_ref | str? | 指向系统 keyring，**不明文入库** |
| created_at | datetime | |

### deployments
| 列 | 类型 | 说明 |
|---|---|---|
| id | str PK | |
| name | str | 用户赋予的 Evaluation Target 身份，如「Kimi K3 · Official」 |
| model_id / provider_id | FK | |
| api_model_name | str | 发给 provider 的模型名 |
| endpoint_override | str? | |
| custom_options | JSON | provider 专属参数 |
| price_input_per_mtok / price_output_per_mtok | float? | 用户价格 override（最高优先级） |
| created_at | datetime | |

### reasoning_profiles
| 列 | 类型 | 说明 |
|---|---|---|
| id | str PK | |
| deployment_id | FK | |
| name | str | 如 low / medium / high |
| reasoning_effort / reasoning_budget / max_output_tokens | ? | |
| temperature / top_p / seed | ? | |
| provider_params | JSON | |

## 数据集层

### dataset_installations
一次安装记录；`manifest_hash`（manifest.yaml 文件字节 SHA-256）unique（同内容重复安装复用）。
保存 dataset_id / dataset_name / dataset_version / dataset_revision /
protocol_version / manifest_hash / source_path（数据目录下的托管副本）/
suites(JSON：id/name/description/layer/adapter/task_ids) / capabilities / installed_at。

### tasks（task 元数据缓存）
installation_id FK、task_id、revision、task_hash（协议 bundle_sha256）、
title、suite_id、type、status、tags、difficulty、flagship、contamination、
freshness、task_path（相对数据集根的 bundle 路径）、
payload（按 bundle 组装的完整 task 快照 JSON：solver_visible / judge_visible
（含 weight+anchors rubric 与校准回答）/ verify 契约 / metadata 含 meta.yaml 全文）。
`(installation_id, task_id, revision)` 唯一。

## Run 层（immutable raw facts）

### config_snapshots
Run 创建时冻结的全部配置 payload：solvers/judges 目标、suite、task revisions、
judge prompt 版本、聚合算法版本。

### runs
- 追溯：framework_version、framework_commit、dataset_id/version/revision、
  manifest_hash、suite_id、installation_id FK、config_snapshot_id FK。
- 生命周期：status(pending/running/completed/failed)、created_at/started_at/finished_at、error。
- 派生缓存（可由 usage_records 重算）：solver_wall_time_s、verifier_wall_time_s、
  judge_wall_time_s、total_wall_time_s、solver_cost、judge_cost、total_cost（价格未知记 NULL）。

### solver_executions
run_id FK、task_cache_id FK + 冗余 task_id/task_revision/task_hash、
deployment_id FK、reasoning_profile_id FK?、deployment_label/profile_name（冻结展示名）、
status、started_at/finished_at、ttft_s（非流式为 NULL）、generation_time_s、
total_latency_s、prompt_json、response_text、raw_response_json、error。

### judge_executions
run_id FK、solver_execution_id FK、deployment_id FK、reasoning_profile_id FK?、
deployment_label/profile_name、status、started_at/finished_at、total_latency_s、
rubric_version、prompt_json（匿名化输入）、raw_output_text、raw_response_json、
parse_ok、fatal_error、dimension_scores(JSON)、judge_summary、key_errors、
aggregation_version、weighted_total（derived cache）、error。

### verifier_executions
一次程序判定记录（对某个 solver execution 执行 verify 契约）：
run_id FK、solver_execution_id FK、verifier_kind（swe_issue/algorithm）、
status（running/completed/failed/unavailable）、started_at/finished_at、wall_time_s、
facts_json（判定事实：补丁应用结果、FAIL_TO_PASS/PASS_TO_PASS 结果或样例/应力对拍结果）、
environment_json（工具链版本）、log_tail、error。
**immutable raw facts**；rejudge 复用本表事实，不重新执行判定。
工具链无法供给时 status=unavailable，Judge 仅依据文本证据评分。

### usage_records
run_id FK、owner_type(solver/judge)、owner_id（execution id）、created_at、
input_tokens、cached_input_tokens、output_tokens、reasoning_tokens、
pricing_snapshot_id FK?、cost（价格未知记 NULL）。

### pricing_snapshots
deployment_id FK?、source(manual_override/models_dev/litellm_cost_map/unknown)、
price_input_per_mtok / price_output_per_mtok / price_cached_input_per_mtok、
currency、raw_json、captured_at。**历史 Run 的成本只引用本表快照，不按未来价格重算。**

### aggregation_versions
派生指标聚合算法版本注册表（name + version unique + definition JSON）。
修改权重/算法时新增版本，历史 weighted_total 可按任意版本重算。

## 不变量（由 server/tests 守护）

1. solver/judge 的 prompt、response、raw output、usage、pricing snapshot 写入后不更新。
2. Judge prompt 不含 Solver 品牌/Provider/Deployment 名/价格。
3. rejudge 只新增 judge_executions，不新增 verifier_executions。
4. 价格未知时成本为 NULL 而非 0。
5. verifier 判定事实（facts/environment/log）一经写入不覆盖。
