"""Judge 输出解析与派生分数计算。

AGGREGATION_VERSION 标识当前聚合算法版本；修改算法必须新增版本号，
历史 JudgeExecution.weighted_total 可在不改动原始输出的情况下按任意版本重算。

weighted-mean/v2（对齐 Dataset Protocol v1 的 weight + anchors rubric 模型）：
- 维度分为 0~1 小数（参照 rubric anchors）
- 总分 = Σ(权重 × 维度分) × 100；fatal_error 记 0
- v1（已废弃）为 max_score 归一化模型，仅存在于旧协议样例
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

AGGREGATION_VERSION = "weighted-mean/v2"


@dataclass
class ParsedJudgeOutput:
    parse_ok: bool
    dimensions: dict[str, float] = field(default_factory=dict)  # 仅保留 rubric 声明的维度
    fatal_error: bool | None = None
    summary: str | None = None
    key_errors: list[str] = field(default_factory=list)
    error: str | None = None


def extract_json_object(text: str) -> dict | None:
    """从模型输出中提取第一个 JSON 对象。容忍 markdown 代码块包裹与前后杂质。"""
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidates.insert(0, fence.group(1))
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_judge_output(raw_text: str, rubric: dict) -> ParsedJudgeOutput:
    """解析 Judge 原始输出为结构化评分。维度分按 rubric 过滤并截断到 [0, 1]。"""
    parsed = extract_json_object(raw_text)
    if parsed is None:
        return ParsedJudgeOutput(parse_ok=False, error="无法从输出中解析 JSON")

    raw_dims = parsed.get("dimensions")
    if not isinstance(raw_dims, dict):
        return ParsedJudgeOutput(parse_ok=False, error="缺少 dimensions 对象")

    rubric_dims = {d["id"]: d for d in rubric.get("dimensions", [])}
    dimensions: dict[str, float] = {}
    for dim_id in rubric_dims:
        value = raw_dims.get(dim_id)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            dimensions[dim_id] = float(max(0.0, min(1.0, float(value))))

    if not dimensions:
        return ParsedJudgeOutput(parse_ok=False, error="dimensions 中没有可用的 rubric 维度得分")

    key_errors_raw = parsed.get("key_errors")
    key_errors = [str(e) for e in key_errors_raw] if isinstance(key_errors_raw, list) else []

    return ParsedJudgeOutput(
        parse_ok=True,
        dimensions=dimensions,
        fatal_error=bool(parsed.get("fatal_error")) if parsed.get("fatal_error") is not None else None,
        summary=str(parsed["summary"]) if parsed.get("summary") is not None else None,
        key_errors=key_errors,
    )


def weighted_total(parsed: ParsedJudgeOutput, rubric: dict) -> float | None:
    """weighted-mean/v2：fatal_error 记 0；否则按 rubric 权重加权并放大到 0~100。"""
    if not parsed.parse_ok:
        return None
    if parsed.fatal_error:
        return 0.0
    rubric_dims = {d["id"]: d for d in rubric.get("dimensions", [])}
    total = 0.0
    weight_sum = 0.0
    for dim_id, score in parsed.dimensions.items():
        dim = rubric_dims.get(dim_id)
        if dim is None:
            continue
        weight = float(dim["weight"])
        total += weight * score
        weight_sum += weight
    if weight_sum <= 0:
        return None
    # 权重和不为 1 时按实际权重归一化（协议要求 1.0，此处容错）
    return round(total / weight_sum * 100.0, 4)
