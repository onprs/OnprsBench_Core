"""Dataset Protocol v1 校验工具。

独立脚本，不依赖框架内部代码。数据集仓库可用它自检，框架加载时也执行相同校验。

校验项：
- manifest.yaml 符合 JSON Schema（protocol/schema/dataset-protocol-v1.schema.json）
- protocol_version 受支持
- task id 全局唯一；path 目录存在
- 文件 hash、bundle hash 与 manifest 声明一致；无未登记文件
- solver_visible / judge_visible 不重叠，且分别含 problem.md / rubric.yaml
- rubric.yaml 可解析，维度权重之和为 1.0

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

import yaml
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "dataset-protocol-v1.schema.json"

SUPPORTED_PROTOCOL_VERSIONS = {"1"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundle_sha256(file_hashes: dict[str, str]) -> str:
    """协议定义的 bundle hash：路径字典序拼接 "<path>  <sha256>" 后再取 SHA-256。"""
    lines = "".join(f"{rel}  {digest}\n" for rel, digest in sorted(file_hashes.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def manifest_hash(manifest_path: Path) -> str:
    """manifest.yaml 文件字节的 SHA-256。"""
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _effective_visibility(task_dir: Path, task: dict, errors: list[str]) -> tuple[list[str], list[str]]:
    """按 manifest 声明 + meta.yaml.visibility_overrides 计算有效可见性。

    override 只能引用已登记文件；`meta` 可见的文件既不进 solver 也不进 judge。
    """
    declared = set(task["solver_visible"]) | set(task["judge_visible"])
    solver = set(task["solver_visible"])
    judge = set(task["judge_visible"])
    meta_path = task_dir / "meta.yaml"
    overrides: dict = {}
    if meta_path.is_file():
        try:
            raw = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            raw = None
        if isinstance(raw, dict) and isinstance(raw.get("visibility_overrides"), dict):
            overrides = raw["visibility_overrides"]
    for rel, visibility in overrides.items():
        rel = str(rel)
        if rel not in declared:
            errors.append(f"task {task['id']}: visibility_overrides 引用了未登记文件: {rel}")
            continue
        if visibility == "solver":
            solver.add(rel)
            judge.discard(rel)
        elif visibility == "judge":
            judge.add(rel)
            solver.discard(rel)
        elif visibility == "meta":
            solver.discard(rel)
            judge.discard(rel)
        else:
            errors.append(f"task {task['id']}: visibility_overrides 取值非法: {rel} -> {visibility}")
    return sorted(solver), sorted(judge)


def _check_rubric(task_dir: Path, judge_visible: list[str], task_id: str, errors: list[str]) -> None:
    if "rubric.yaml" not in judge_visible:
        errors.append(f"task {task_id}: judge_visible 缺少 rubric.yaml")
        return
    rubric_path = task_dir / "rubric.yaml"
    if not rubric_path.is_file():
        return  # 文件缺失已在 hash 校验中报错
    try:
        rubric = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"task {task_id}: rubric.yaml 不是合法 YAML: {exc}")
        return
    dimensions = (rubric or {}).get("dimensions") or []
    if not dimensions:
        errors.append(f"task {task_id}: rubric.yaml 缺少 dimensions")
        return
    total = sum(float(d.get("weight", 0)) for d in dimensions)
    if abs(total - 1.0) > 1e-6:
        errors.append(f"task {task_id}: rubric 维度权重之和为 {total}，应为 1.0")


def validate_dataset(dataset_dir: Path) -> list[str]:
    """校验数据集目录，返回错误信息列表（空列表表示通过）。"""
    errors: list[str] = []

    manifest_path = dataset_dir / "manifest.yaml"
    if not manifest_path.is_file():
        return [f"缺少 manifest.yaml: {manifest_path}"]

    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"manifest.yaml 不是合法 YAML: {exc}"]
    if not isinstance(manifest, dict):
        return ["manifest.yaml 顶层必须是对象"]

    # 1. JSON Schema 校验
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path)):
        location = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"schema: {location}: {err.message}")
    if errors:
        return errors

    # 2. 协议版本协商
    if manifest["protocol_version"] not in SUPPORTED_PROTOCOL_VERSIONS:
        errors.append(f"不支持的 protocol_version: {manifest['protocol_version']}")

    # 3. 逐 task 校验
    seen_ids: set[str] = set()
    for suite in manifest["suites"]:
        for task in suite["tasks"]:
            task_id = task["id"]
            if task_id in seen_ids:
                errors.append(f"task id 重复: {task_id}")
                continue
            seen_ids.add(task_id)

            task_dir = dataset_dir / task["path"]
            if not task_dir.is_dir():
                errors.append(f"task {task_id}: bundle 目录不存在: {task['path']}")
                continue

            declared = task["hashes"]["files"]
            actual = {
                p.relative_to(task_dir).as_posix(): sha256_file(p)
                for p in sorted(task_dir.rglob("*"))
                if p.is_file()
            }
            for rel, digest in declared.items():
                if rel not in actual:
                    errors.append(f"task {task_id}: 声明的文件不存在: {rel}")
                elif actual[rel] != digest:
                    errors.append(f"task {task_id}: 文件 hash 不匹配: {rel}")
            for rel in actual:
                if rel not in declared:
                    errors.append(f"task {task_id}: 存在未登记 hash 的文件: {rel}")
            if declared and bundle_sha256({k: v for k, v in actual.items() if k in declared}) != task["hashes"]["bundle_sha256"]:
                errors.append(f"task {task_id}: bundle_sha256 不匹配")

            solver_visible, judge_visible = _effective_visibility(task_dir, task, errors)
            overlap = set(solver_visible) & set(judge_visible)
            if overlap:
                errors.append(f"task {task_id}: solver/judge 可见性重叠: {sorted(overlap)}")
            for rel in solver_visible + judge_visible:
                if rel not in declared:
                    errors.append(f"task {task_id}: 可见性引用了未登记文件: {rel}")
            if not any(rel == "problem.md" or rel.startswith("assets/") for rel in solver_visible):
                errors.append(f"task {task_id}: solver_visible 缺少 problem.md")
            _check_rubric(task_dir, judge_visible, task_id, errors)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验数据集目录是否符合 Dataset Protocol v1")
    parser.add_argument("dataset_dir", type=Path)
    args = parser.parse_args()

    errors = validate_dataset(args.dataset_dir)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    task_count = sum(len(s["tasks"]) for s in yaml.safe_load((args.dataset_dir / "manifest.yaml").read_text(encoding="utf-8"))["suites"])
    print(f"OK: {args.dataset_dir}（{task_count} 个 task，manifest hash {manifest_hash(args.dataset_dir / 'manifest.yaml')}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
