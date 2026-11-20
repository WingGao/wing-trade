"""前端静态资源与 SPA 路由回退托管集成测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


def _make_dist_dir(tmp_path: Path) -> Path:
    """构造最小前端静态构建目录用于测试。"""
    dist_dir = tmp_path / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text(
        "<!doctype html><html><body>SPA</body></html>",
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("console.log('wing-trade');", encoding="utf-8")
    return dist_dir


def test_frontend_static_and_spa_fallback(tmp_path: Path) -> None:
    """验证根路径、静态资源、SPA 回退、健康检查与未知 API 404 行为。"""
    dist_dir = _make_dist_dir(tmp_path)
    application = create_app(create_tables=False, frontend_dist_dir=str(dist_dir))

    with TestClient(application) as client:
        root_resp = client.get("/")
        assert root_resp.status_code == 200
        assert "SPA" in root_resp.text

        asset_resp = client.get("/assets/app.js")
        assert asset_resp.status_code == 200
        assert "wing-trade" in asset_resp.text

        spa_resp = client.get("/trades/review/123")
        assert spa_resp.status_code == 200
        assert "SPA" in spa_resp.text

        health_resp = client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "ok"}

        api_missing = client.get("/api/v1/not-a-real-endpoint")
        assert api_missing.status_code == 404
        assert "SPA" not in api_missing.text

        api_root_missing = client.get("/api")
        assert api_root_missing.status_code == 404


def test_frontend_not_mounted_when_dist_dir_empty() -> None:
    """未配置前端构建目录时保持纯 API 行为。"""
    application = create_app(create_tables=False, frontend_dist_dir="")
    with TestClient(application) as client:
        assert client.get("/").status_code == 404
        assert client.get("/health").status_code == 200
