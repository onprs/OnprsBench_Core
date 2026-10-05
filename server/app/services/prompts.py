"""Solver / Judge prompt 构建。

信息隔离约束（不得破坏）：
- Solver prompt 只包含 task.solver_visible 内容。
- Judge prompt 可包含 problem / reference / rubric / pitfalls / alternatives 与候选回答，
  但绝不包含 Solver 的模型品牌、Provider、Deployment 名称或价格；
  候选回答以 "Candidate Response #A" 匿名引用。
"""

from __future__ import annotations

JUDGE_PROMPT_VERSION = "judge-v1"

_SOLVER_SYSTEM = (
    "你是一名严谨的解题者。请直接、完整地回答用户给出的问题，展示关键推理过程。"
)

_JUDGE_SYSTEM = (
    "你是一名严格、公正的评审。你需要根据题目、参考解答与评分量规，对一份匿名候选回答进行结构化评分。"
    "只输出一个 JSON 对象，不要输出任何其他文字。"
)


def build_solver_messages(task_payload: dict) -> list[dict[str, str]]:
    """Solver 输入：仅 solver_visible。"""
    problem = task_payload["solver_visible"]["problem"]
    return [
        {"role": "system", "content": _SOLVER_SYSTEM},
        {"role": "user", "content": problem},
    ]


def build_judge_messages(task_payload: dict, candidate_response: str) -> list[dict[str, str]]:
    """Judge 输入：problem + rubric + reference pack + 匿名候选回答。"""
    judge_visible = task_payload["judge_visible"]
    rubric = judge_visible["rubric"]

    dim_lines = "\n".join(
        f"- {d['id']}（满分 {d['max_score']}）：{d['description']}" for d in rubric["dimensions"]
    )
    pitfalls = judge_visible.get("pitfalls") or []
    alternatives = judge_visible.get("alternatives") or []
    pitfalls_text = "\n".join(f"- {p}" for p in pitfalls) if pitfalls else "（无）"
    alternatives_text = "\n".join(f"- {a}" for a in alternatives) if alternatives else "（无）"
    fatal_policy = rubric.get("fatal_error_policy") or "存在致命错误时 fatal_error=true。"
    dim_ids = ", ".join(f'"{d["id"]}"' for d in rubric["dimensions"])

    user = f"""请评审以下匿名候选回答。

【题目】
{task_payload["solver_visible"]["problem"]}

【参考解答】
{judge_visible["reference"]}

【评分量规】（量规版本 {rubric["version"]}）
{dim_lines}
致命错误判定：{fatal_policy}

【常见错误】
{pitfalls_text}

【可接受的替代解法】
{alternatives_text}

【Candidate Response #A】
{candidate_response}

【输出格式】
只输出如下 JSON 对象（dimensions 必须恰好包含这些键：{dim_ids}；分数范围 0 到该维度满分）：
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
