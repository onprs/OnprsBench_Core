"""数据集归档安装测试。

覆盖：
- 发布产物形态（顶层目录 + manifest.yaml）可直接安装
- 目录内容的扁平打包同样可安装，且与目录安装得到同一数据集
- 完整形态归档安装后资源照常注册
- 拒绝路径穿越、链接条目、非 tar 文件、缺少 manifest 与不存在的路径
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

from fastapi.testclient import TestClient

from .conftest import MOCK_DATASET_DIR


def _archive(src: Path, dest: Path, *, top_dir: str | None) -> Path:
    with tarfile.open(dest, "w:gz") as tar:
        if top_dir:
            tar.add(src, arcname=top_dir)
        else:
            for path in sorted(src.rglob("*")):
                tar.add(path, arcname=path.relative_to(src).as_posix())
    return dest


def _install(client: TestClient, path: Path):
    return client.post("/api/datasets/installations", json={"path": str(path)})


def test_install_from_release_archive(client: TestClient, tmp_path: Path) -> None:
    """发布产物形态（顶层目录内含 manifest.yaml）可直接安装。"""
    archive = _archive(
        MOCK_DATASET_DIR,
        tmp_path / "onprsbench-dataset-0.0.0.tar.gz",
        top_dir="onprsbench-dataset-0.0.0",
    )
    resp = _install(client, archive)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["installation"]["dataset_id"] == "onprs-mock-protocol-sample"
    assert body["installation"]["distribution"] == "standard"

    tasks = client.get(f"/api/datasets/installations/{body['installation']['id']}/tasks").json()
    assert len(tasks) == 2


def test_install_from_flat_archive(client: TestClient, tmp_path: Path) -> None:
    """扁平打包（无顶层目录）与目录安装等价。"""
    archive = _archive(MOCK_DATASET_DIR, tmp_path / "flat.tgz", top_dir=None)
    resp = _install(client, archive)
    assert resp.status_code == 201, resp.text
    assert resp.json()["installation"]["dataset_id"] == "onprs-mock-protocol-sample"


def test_install_full_dataset_from_archive(client: TestClient, tmp_path: Path) -> None:
    """完整形态归档：安装时注册附带的仓库资源。"""
    from .test_dataset_distribution import _build_full_dataset

    source = _build_full_dataset(tmp_path / "src", commit="e" * 40)
    archive = _archive(source, tmp_path / "full.tar.gz", top_dir="onprsbench-dataset-0.1.0-full")
    resp = _install(client, archive)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["installation"]["distribution"] == "full"
    assert any(item["status"] == "bundled" for item in body["prefetch"])


def test_archive_path_traversal_rejected(client: TestClient, tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"x"
        info = tarfile.TarInfo("../evil.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    resp = _install(client, archive)
    assert resp.status_code == 422, resp.text
    assert "非法路径" in resp.text


def test_archive_symlink_rejected(client: TestClient, tmp_path: Path) -> None:
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        tar.addfile(info)
    resp = _install(client, archive)
    assert resp.status_code == 422, resp.text
    assert "链接" in resp.text


def test_archive_without_manifest_rejected(client: TestClient, tmp_path: Path) -> None:
    archive = tmp_path / "nomanifest.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = b"hello"
        info = tarfile.TarInfo("readme.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    resp = _install(client, archive)
    assert resp.status_code == 422, resp.text
    assert "manifest.yaml" in resp.text


def test_non_archive_file_rejected(client: TestClient, tmp_path: Path) -> None:
    junk = tmp_path / "dataset.tar.gz"
    junk.write_text("not a tar archive", encoding="utf-8")
    resp = _install(client, junk)
    assert resp.status_code == 422, resp.text
    assert "tar" in resp.text


def test_missing_path_rejected(client: TestClient, tmp_path: Path) -> None:
    resp = _install(client, tmp_path / "not-exists")
    assert resp.status_code == 400, resp.text
