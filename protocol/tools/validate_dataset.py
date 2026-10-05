"""Dataset Protocol v1 校验工具。

独立脚本，不依赖框架内部代码。数据集仓库可用它自检，框架加载时也执行相同校验。

用法：
    python validate_dataset.py <dataset_dir>

退出码：0 = 校验通过；1 = 校验失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "dataset-protocol-v1.schema.json"

SUPPORTED_PROTOCOL_VERSIONS = {"1"}


def canonical_json(data: object) -> bytes:
    """生成规范化 JSON 字节串，用于 hash 计算。"""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def task_hash(task: dict) -> str:
    """task 内容 hash（sha256，基于规范化 JSON）。"""
    return hashlib.sha256(canonical_json(task)).hexdigest()


def manifest_hash(manifest: dict) -> str:
    """dataset manifest hash（sha256，基于规范化 JSON）。"""
    return hashlib.sha256(canonical_json(manifest)).hexdigest()


def validate_dataset(dataset_dir: Path) -> list[str]:
    """校验数据集目录，返回错误信息列表（空列表表示通过）。"""
    errors: list[str] = []

    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.is_file():
        return [f"缺少 manifest.json: {manifest_path}"]

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"manifest.json 不是合法 JSON: {exc}"]

    # 1. JSON Schema 校验
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path)):
        location = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"schema: {location}: {err.message}")
    if errors:
        return errors

    # 2. 协议版本协商
    version = manifest["protocol_version"]
    if version not in SUPPORTED_PROTOCOL_VERSIONS:
        errors.append(f"不支持的 protocol_version: {version}（支持: {sorted(SUPPORTED_PROTOCOL_VERSIONS)}）")

    # 3. 引用完整性
    tasks = manifest["tasks"]
    task_ids = [t["id"] for t in tasks]
    if len(task_ids) != len(set(task_ids)):
        errors.append("tasks 中存在重复 task id")

    all_suite_task_ids: list[str] = []
    for suite in manifest["suites"]:
        for tid in suite["task_ids"]:
            if tid not in set(task_ids):
                errors.append(f"suite {suite['id']} 引用了不存在的 task: {tid}")
            all_suite_task_ids.append(tid)

    # 4. asset hash 校验
    for task in tasks:
        for section in ("solver_visible", "judge_visible"):
            for asset in task.get(section, {}).get("assets", []) or []:
                asset_path = dataset_dir / asset["path"]
                if not asset_path.is_file():
                    errors.append(f"task {task['id']}: asset 不存在: {asset['path']}")
                    continue
                digest = hashlib.sha256(asset_path.read_bytes()).hexdigest()
                if digest != asset["sha256"]:
                    errors.append(f"task {task['id']}: asset hash 不匹配: {asset['path']}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 Dataset Protocol v1 数据集目录")
    parser.add_argument("dataset_dir", type=Path)
    args = parser.parse_args()

    errors = validate_dataset(args.dataset_dir)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"OK: {args.dataset_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
