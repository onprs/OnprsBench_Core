"""生成 mock-protocol-sample 的 manifest.yaml（含全部 hash）。

修改任务文件后必须重新运行：
    python build_manifest.py
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

SAMPLE_ROOT = Path(__file__).resolve().parent
TASKS_ROOT = SAMPLE_ROOT / "tasks"

VIS_SOLVER = "solver"
VIS_JUDGE = "judge"
VIS_META = "meta"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundle_sha256(file_hashes: dict[str, str]) -> str:
    lines = "".join(f"{rel}  {digest}\n" for rel, digest in sorted(file_hashes.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def default_visibility(relpath: str) -> str | None:
    """与数据集仓库一致的默认可见性惯例。"""
    if relpath == "problem.md" or relpath.startswith("assets/"):
        return VIS_SOLVER
    if (
        relpath == "rubric.yaml"
        or relpath.startswith("reference/")
        or relpath.startswith("anchors/")
        or relpath.startswith("judge_assets/")
    ):
        return VIS_JUDGE
    if relpath == "meta.yaml":
        return VIS_META
    return None


def build() -> dict:
    task_entries: dict[str, dict] = {}
    for suite_dir in sorted(TASKS_ROOT.iterdir()):
        if not suite_dir.is_dir():
            continue
        for task_dir in sorted(suite_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            meta = yaml.safe_load((task_dir / "meta.yaml").read_text(encoding="utf-8"))
            files = {
                p.relative_to(task_dir).as_posix(): sha256_file(p)
                for p in sorted(task_dir.rglob("*"))
                if p.is_file()
            }
            visibility = {rel: default_visibility(rel) for rel in files}
            unknown = [rel for rel, vis in visibility.items() if vis is None]
            if unknown:
                raise SystemExit(f"task {meta['id']}: 无法推导可见性: {unknown}")
            task_entries[meta["id"]] = {
                "id": meta["id"],
                "revision": meta["revision"],
                "title": meta["title"],
                "type": meta["type"],
                "tags": meta["tags"],
                "status": meta["lifecycle"]["status"],
                "path": task_dir.relative_to(SAMPLE_ROOT).as_posix(),
                "solver_visible": sorted(rel for rel, v in visibility.items() if v == VIS_SOLVER),
                "judge_visible": sorted(rel for rel, v in visibility.items() if v == VIS_JUDGE),
                "metadata": {
                    "difficulty": meta["difficulty"]["author"],
                    "freshness": meta["freshness"]["class"],
                    "contamination": meta["contamination"]["risk"],
                    "flagship": bool(meta.get("flagship", False)),
                },
                "hashes": {"bundle_sha256": bundle_sha256(files), "files": files},
            }

    return {
        "protocol_version": "1",
        "dataset": {
            "id": "onprs-mock-protocol-sample",
            "name": "Mock Protocol Sample",
            "version": "0.1.0",
            "release_date": "2025-01-01",
            "revision": "mock-rev-1",
            "description": "Dataset Protocol v1 的测试样例，仅用于验证协议加载与端到端流程，不构成任何真实评测结论。",
            "license": "MIT",
        },
        "suites": [
            {
                "id": "mock-core",
                "name": "Mock Core Suite",
                "description": "协议测试样例套件",
                "layer": "derived",
                "adapter": None,
                "tasks": [task_entries["mock-sum-001"], task_entries["mock-logic-001"]],
            }
        ],
    }


def main() -> None:
    manifest = build()
    out = SAMPLE_ROOT / "manifest.yaml"
    out.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    print(f"已生成 {out}（{sum(len(s['tasks']) for s in manifest['suites'])} 个 task）")


if __name__ == "__main__":
    main()
