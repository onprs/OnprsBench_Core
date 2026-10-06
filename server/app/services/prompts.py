"""Solver / Judge prompt 构建。

信息隔离约束（不得破坏）：
- Solver prompt 只包含 task.solver_visible 内容与输出契约。
- Judge prompt 可包含 problem / reference / rubric / anchors / 程序判定事实与候选回答，
  但绝不包含 Solver 的模型品牌、Provider、Deployment 名称或价格；
  候选回答以 "Candidate Response #A" 匿名引用。
- meta.yaml 内容（来源、污染风险、许可等）不进入任何提示词。
"""

from __future__ import annotations

import json

JUDGE_PROMPT_VERSION = "judge-v3"
SOLVER_PROMPT_VERSION = "solver-agent-v3"

_SOLVER_SYSTEM = (
    "你是一名严谨的解题者。请直接、完整地回答用户给出的问题，展示关键推理过程。"
)

_JUDGE_SYSTEM = (
    "你是一名严格、公正的评审。你需要根据题目、参考解答与评分量规，对一份匿名候选回答进行结构化评分。"
    "只输出一个 JSON 对象，不要输出任何其他文字。"
)

# 协议第 9 节 Solver 输出契约：程序判定类任务要求回答包含可提取的产物
_PATCH_CONTRACT = """

【输出格式要求】
你的最终回答必须包含一个且只有一个 ```diff 代码围栏，其中是可直接应用的 unified diff 补丁
（git 风格，路径带 a/、b/ 前缀）。只修改源码以修复问题；不要修改测试文件。
补丁之外可以附加简要说明。"""

_CODE_CONTRACT = """

【输出格式要求】
你的最终回答必须包含一个且只有一个 ```cpp 或 ```python 代码围栏，其中是完整的单文件程序
（从标准输入读入、向标准输出写出，符合题目输入输出格式）。
代码之外可以附加简要说明。"""

_TASK_TYPE_CONTRACTS = {
    "issue_resolution": _PATCH_CONTRACT,
    "code_generation": _CODE_CONTRACT,
    "implementation": _CODE_CONTRACT,
}


def build_solver_messages(task_payload: dict) -> list[dict[str, str]]:
    """Solver 输入：仅 solver_visible + 输出契约。"""
    problem = task_payload["solver_visible"]["problem"]
    contract = _TASK_TYPE_CONTRACTS.get(task_payload.get("type"), "")
    return [
        {"role": "system", "content": _SOLVER_SYSTEM},
        {"role": "user", "content": problem + contract},
    ]


def _format_rubric(rubric: dict) -> tuple[str, str]:
    """渲染 rubric 维度（含权重与评分锚点），返回 (文本, 维度 id 列表)。"""
    lines: list[str] = []
    for dim in rubric["dimensions"]:
        lines.append(f"- {dim['id']}（权重 {dim['weight']:.2f}）：{dim['description']}")
        for anchor in dim.get("anchors") or []:
            lines.append(f"  - {anchor['score']} 分：{anchor['description']}")
    dim_ids = ", ".join(f'"{d["id"]}"' for d in rubric["dimensions"])
    return "\n".join(lines), dim_ids


def _format_anchors(anchors: list[dict]) -> str:
    parts = []
    for anchor in anchors:
        parts.append(f"【校准回答 · {anchor['score']} 分档】\n{anchor['text']}")
    return "\n\n".join(parts)


def _format_verifier_facts(facts: dict | None) -> str:
    if not facts:
        return ""
    text = f"""
【程序判定事实】
以下事实由框架在隔离环境中实际执行判定契约得到（应用补丁、运行测试/对拍），
不是候选回答的自称。凡评分量规维度描述中声明"程序 verifier 判定"的维度，
必须严格以这些事实为唯一依据打分；其余维度仍由你依据文本证据评审。

```json
{json.dumps(facts, ensure_ascii=False, indent=2)}
```
"""
    solver = facts.get("solver") or {}
    if solver.get("truncated"):
        text += (
            "\n注意：候选回答曾因输出预算耗尽被截断（判定事实 solver.truncated 为 true）。"
            "评分只能依据回答中实际出现的产物：截断本身不额外扣分，"
            "但不得假设未呈现的修复或代码。\n"
        )
    return text


def build_judge_messages(
    task_payload: dict,
    candidate_response: str,
    verifier_facts: dict | None = None,
) -> list[dict[str, str]]:
    """Judge 输入：problem + rubric + reference pack + anchors + 判定事实 + 匿名候选回答。"""
    judge_visible = task_payload["judge_visible"]
    rubric = judge_visible["rubric"]

    rubric_text, dim_ids = _format_rubric(rubric)
    anchors = judge_visible.get("anchors") or []
    anchors_text = _format_anchors(anchors) if anchors else "（本题未提供校准回答）"
    facts_text = _format_verifier_facts(verifier_facts)

    user = f"""请评审以下匿名候选回答。

【题目】
{task_payload["solver_visible"]["problem"]}

【参考解答】
{judge_visible["reference"]}

【评分量规】（rubric 版本 {rubric["version"]}；维度分均为 0~1 的小数，参照各档锚点）
{rubric_text}
若候选回答存在致命错误（与题目要求完全背离、核心结论错误、或程序判定事实表明完全未通过），
将 fatal_error 置为 true，此时全部维度记 0 分。

【校准回答】（用于对齐评分尺度）
{anchors_text}
{facts_text}
【Candidate Response #A】
{candidate_response}

【输出格式】
只输出如下 JSON 对象（dimensions 必须恰好包含这些键：{dim_ids}；每个维度分为 0~1 的小数）：
{{
  "dimensions": {{"<dimension_id>": <number>, ...}},
  "fatal_error": <true|false>,
  "summary": "<一句话总评>",
  "key_errors": ["<关键错误>", ...]
}}"""

    return [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": user},
    ]
