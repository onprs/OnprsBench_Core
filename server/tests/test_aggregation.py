"""Judge 输出解析与派生分数测试。"""

from __future__ import annotations

import json

from app.services.aggregation import extract_json_object, parse_judge_output, weighted_total

RUBRIC = {
    "version": "1.0",
    "dimensions": [
        {"id": "correctness", "description": "...", "max_score": 100},
        {"id": "proof", "description": "...", "max_score": 50},
    ],
}


def test_parse_clean_json() -> None:
    raw = json.dumps({"dimensions": {"correctness": 90, "proof": 40}, "fatal_error": False, "summary": "好", "key_errors": []})
    parsed = parse_judge_output(raw, RUBRIC)
    assert parsed.parse_ok
    assert parsed.dimensions == {"correctness": 90.0, "proof": 40.0}
    assert weighted_total(parsed, RUBRIC) == round(130 / 150 * 100, 4)


def test_parse_with_markdown_fence() -> None:
    raw = '思考过程...\n```json\n{"dimensions": {"correctness": 100, "proof": 50}, "fatal_error": false}\n```'
    parsed = parse_judge_output(raw, RUBRIC)
    assert parsed.parse_ok
    assert weighted_total(parsed, RUBRIC) == 100.0


def test_unparseable_output() -> None:
    parsed = parse_judge_output("这不是 JSON", RUBRIC)
    assert not parsed.parse_ok
    assert weighted_total(parsed, RUBRIC) is None


def test_fatal_error_zeroes_total() -> None:
    raw = json.dumps({"dimensions": {"correctness": 90, "proof": 40}, "fatal_error": True})
    parsed = parse_judge_output(raw, RUBRIC)
    assert parsed.parse_ok
    assert weighted_total(parsed, RUBRIC) == 0.0


def test_scores_clamped_and_unknown_dims_dropped() -> None:
    raw = json.dumps({"dimensions": {"correctness": 150, "proof": -5, "style": 99}, "fatal_error": False})
    parsed = parse_judge_output(raw, RUBRIC)
    assert parsed.dimensions == {"correctness": 100.0, "proof": 0.0}


def test_missing_dimensions_rejected() -> None:
    parsed = parse_judge_output(json.dumps({"fatal_error": False}), RUBRIC)
    assert not parsed.parse_ok


def test_extract_json_picks_first_object() -> None:
    assert extract_json_object('前缀 {"a": 1} 后缀') == {"a": 1}
    assert extract_json_object("没有对象") is None
