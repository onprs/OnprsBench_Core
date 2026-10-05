"""Judge 输出解析与派生分数测试（weighted-mean/v2：weight + anchors 模型）。"""

from __future__ import annotations

import json

from app.services.aggregation import extract_json_object, parse_judge_output, weighted_total

RUBRIC = {
    "version": "1",
    "dimensions": [
        {"id": "correctness", "weight": 0.75, "description": "...", "anchors": []},
        {"id": "proof", "weight": 0.25, "description": "...", "anchors": []},
    ],
}


def test_parse_clean_json() -> None:
    raw = json.dumps({"dimensions": {"correctness": 1.0, "proof": 0.5}, "fatal_error": False, "summary": "好", "key_errors": []})
    parsed = parse_judge_output(raw, RUBRIC)
    assert parsed.parse_ok
    assert parsed.dimensions == {"correctness": 1.0, "proof": 0.5}
    # 0.75×1.0 + 0.25×0.5 = 0.875 → 87.5
    assert weighted_total(parsed, RUBRIC) == 87.5


def test_parse_with_markdown_fence() -> None:
    raw = '思考过程...\n```json\n{"dimensions": {"correctness": 1.0, "proof": 1.0}, "fatal_error": false}\n```'
    parsed = parse_judge_output(raw, RUBRIC)
    assert parsed.parse_ok
    assert weighted_total(parsed, RUBRIC) == 100.0


def test_unparseable_output() -> None:
    parsed = parse_judge_output("这不是 JSON", RUBRIC)
    assert not parsed.parse_ok
    assert weighted_total(parsed, RUBRIC) is None


def test_fatal_error_zeroes_total() -> None:
    raw = json.dumps({"dimensions": {"correctness": 0.9, "proof": 0.8}, "fatal_error": True})
    parsed = parse_judge_output(raw, RUBRIC)
    assert parsed.parse_ok
    assert weighted_total(parsed, RUBRIC) == 0.0


def test_scores_clamped_and_unknown_dims_dropped() -> None:
    raw = json.dumps({"dimensions": {"correctness": 1.5, "proof": -0.5, "style": 0.9}, "fatal_error": False})
    parsed = parse_judge_output(raw, RUBRIC)
    assert parsed.dimensions == {"correctness": 1.0, "proof": 0.0}


def test_missing_dimensions_rejected() -> None:
    parsed = parse_judge_output(json.dumps({"fatal_error": False}), RUBRIC)
    assert not parsed.parse_ok


def test_weighted_total_tolerates_weight_drift() -> None:
    """协议要求权重和为 1.0；若不满足则按实际权重归一化。"""
    rubric = {"version": "1", "dimensions": [
        {"id": "a", "weight": 2.0, "description": "", "anchors": []},
        {"id": "b", "weight": 1.0, "description": "", "anchors": []},
    ]}
    parsed = parse_judge_output(json.dumps({"dimensions": {"a": 1.0, "b": 0.0}}), rubric)
    assert weighted_total(parsed, rubric) == round(2.0 / 3.0 * 100, 4)


def test_extract_json_picks_first_object() -> None:
    assert extract_json_object('前缀 {"a": 1} 后缀') == {"a": 1}
    assert extract_json_object("没有对象") is None
