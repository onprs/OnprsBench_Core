"""数据集分发形态（standard / full）测试。

- full 形态：安装时校验并注册附带的仓库归档，判定阶段命中本地缓存、不联网；
- 资源被篡改：安装被拒绝（bytes / sha256 校验）；
- standard 形态：不带资源，登记为 standard。
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.services.datasets import bundle_sha256

_COMMIT = "c" * 40
_REPO_URL = "https://github.com/local/demo"
_REPO_SLUG = "local-demo"
_RESOURCE_REL = f"resources/repos/{_REPO_SLUG}-{_COMMIT}.tar.gz"
_LICENSE_REL = f"resources/licenses/{_REPO_SLUG}-LICENSE.txt"


def _tarball(path: Path, prefix: str, files: dict[str, str]) -> None:
    """生成与 GitHub 归档同构的 tar.gz（顶层目录前缀 + 仓库内容）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for rel, content in sorted(files.items()):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(f"{prefix}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _build_full_dataset(tmp_path: Path, commit: str = _COMMIT) -> Path:
    """构造含一个 SWE 契约任务与附带仓库快照的 full 数据集。"""
    root = tmp_path / "full-ds"
    task_dir = root / "tasks" / "swe-suite" / "swe-demo-bug"
    (task_dir / "judge_assets").mkdir(parents=True)
    (task_dir / "reference").mkdir()
    (task_dir / "problem.md").write_text(
        "# 修复 add 函数\n\npkg/calc.py 的 add 实现成了减法，请修复。", encoding="utf-8"
    )
    (task_dir / "reference" / "canonical.md").write_text("# 参考：把减法改回加法", encoding="utf-8")
    (task_dir / "rubric.yaml").write_text(
        yaml.safe_dump(
            {
                "rubric_version": 1,
                "dimensions": [
                    {
                        "id": "patch_correctness",
                        "weight": 1.0,
                        "description": "程序 verifier 判定：FAIL_TO_PASS 通过。",
                        "anchors": [
                            {"score": 0.0, "description": "未通过"},
                            {"score": 0.5, "description": "部分通过"},
                            {"score": 1.0, "description": "全部通过"},
                        ],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (task_dir / "meta.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "swe-demo-bug",
                "revision": 1,
                "title": "演示修复任务",
                "suite": "swe-suite",
                "type": "issue_resolution",
                "tags": ["demo"],
                "lifecycle": {"status": "active", "stage": "fresh"},
                "freshness": {"class": "F0", "created_at": "2026-10-07"},
                "difficulty": {"author": "easy"},
                "source": {
                    "kind": "synthetic",
                    "license": "MIT",
                    "attribution": "local/demo",
                },
                "contamination": {"risk": "none"},
                "license": "MIT",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (task_dir / "judge_assets" / "verify.yaml").write_text(
        yaml.safe_dump(
            {
                "repo": "local/demo",
                "repo_url": _REPO_URL,
                "base_commit": commit,
                "environment": {"python": "3.12", "setup": []},
                "evaluation": {
                    "apply": [],
                    "fail_to_pass": ["tests/test_calc.py::test_add"],
                    "pass_to_pass": [],
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    files = {
        p.relative_to(task_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(task_dir.rglob("*"))
        if p.is_file()
    }
    manifest = {
        "protocol_version": "1",
        "distribution": "full",
        "dataset": {
            "id": "mock-full-ds",
            "name": "Mock Full Dataset",
            "version": "0.1.0",
            "release_date": "2026-10-07",
            "revision": "mock-full-rev-1",
            "description": "完整形态测试样例",
            "license": "MIT",
        },
        "resources": [],
        "suites": [
            {
                "id": "swe-suite",
                "name": "SWE Demo Suite",
                "description": "分发形态测试",
                "layer": "derived",
                "adapter": None,
                "tasks": [
                    {
                        "id": "swe-demo-bug",
                        "revision": 1,
                        "title": "演示修复任务",
                        "type": "issue_resolution",
                        "tags": ["demo"],
                        "status": "active",
                        "path": "tasks/swe-suite/swe-demo-bug",
                        "solver_visible": ["problem.md"],
                        "judge_visible": [
                            "judge_assets/verify.yaml",
                            "rubric.yaml",
                            "reference/canonical.md",
                        ],
                        "metadata": {
                            "difficulty": "easy",
                            "freshness": "F0",
                            "contamination": "none",
                            "flagship": False,
                        },
                        "hashes": {"bundle_sha256": bundle_sha256(files), "files": files},
                    }
                ],
            }
        ],
    }

    # 附带资源：仓库快照（顶层前缀与 GitHub 归档一致）+ 上游许可
    resource_rel = f"resources/repos/{_REPO_SLUG}-{commit}.tar.gz"
    license_rel = f"resources/licenses/{_REPO_SLUG}-LICENSE.txt"
    _tarball(
        root / resource_rel,
        f"demo-{commit}",
        {
            "pkg/calc.py": "def add(a, b):\n    return a - b  # BUG\n",
            "tests/test_calc.py": (
                "from pkg.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
            ),
        },
    )
    (root / license_rel).parent.mkdir(parents=True, exist_ok=True)
    (root / license_rel).write_text("MIT License (test fixture)\n", encoding="utf-8")
    resource_path = root / resource_rel
    manifest["resources"] = [
        {
            "id": f"repo-snapshot:local/demo@{commit}",
            "kind": "repo_snapshot",
            "path": resource_rel,
            "sha256": hashlib.sha256(resource_path.read_bytes()).hexdigest(),
            "bytes": resource_path.stat().st_size,
            "source": {
                "repo": "local/demo",
                "url": _REPO_URL,
                "commit": commit,
                "license": "MIT",
                "attribution": "local/demo",
                "license_file": license_rel,
            },
        }
    ]
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return root


def test_full_dataset_registers_bundled_archive(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    from app.toolchain import repos as repo_toolchain

    ds = _build_full_dataset(tmp_path)
    resp = client.post("/api/datasets/installations", json={"path": str(ds)})
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["installation"]["distribution"] == "full"
    assert any(item["status"] == "bundled" for item in payload["prefetch"])

    # 判定阶段不再联网：下载入口被禁用仍能取到快照（命中安装时注册的归档）
    def _no_download(*args, **kwargs):
        raise AssertionError("不应联网下载仓库归档")

    monkeypatch.setattr(repo_toolchain, "download_file", _no_download)
    snapshot = repo_toolchain.fetch_repo_snapshot(_REPO_URL, _COMMIT)
    assert (snapshot / "pkg" / "calc.py").is_file()
    assert "return a - b" in (snapshot / "pkg" / "calc.py").read_text(encoding="utf-8")


def test_tampered_resource_rejected(client: TestClient, tmp_path: Path) -> None:
    ds = _build_full_dataset(tmp_path)
    (ds / _RESOURCE_REL).write_bytes(b"tampered-content")
    resp = client.post("/api/datasets/installations", json={"path": str(ds)})
    assert resp.status_code == 422, resp.text
    assert "资源" in resp.text or "sha256" in resp.text.lower()


def test_full_dataset_registration_failure_aborts(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """完整形态的资源注册失败同样中止安装并提示。"""
    from app.toolchain import ToolchainUnavailable
    from app.toolchain import repos as repo_toolchain

    ds = _build_full_dataset(tmp_path, commit="d" * 40)

    def fail(**kwargs):
        raise ToolchainUnavailable("缓存目录不可写")

    monkeypatch.setattr(repo_toolchain, "register_repo_archive", fail)
    resp = client.post("/api/datasets/installations", json={"path": str(ds)})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert any("缓存目录不可写" in error for error in detail["errors"])
    assert "完整数据集" in detail["hint"]


def test_standard_dataset_is_registered_as_standard(client: TestClient, mock_setup: dict) -> None:
    rows = client.get("/api/datasets/installations").json()
    assert rows
    mock_rows = [r for r in rows if r["dataset_id"] == "onprs-mock-protocol-sample"]
    assert mock_rows and mock_rows[0]["distribution"] == "standard"
