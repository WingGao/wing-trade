"""个股策略表 REST API 与多用户隔离、增量迁移集成测试。"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.api.deps import get_auth_service, get_db
from app.config import Settings
from app.db.migrations import prepare_database_schema
from app.db.session import create_engine_for_url
from app.main import create_app
from app.services.auth import AuthService
from app.services.auth_bootstrap import ensure_default_admin_and_backfill
from tests.unit.test_auth_config_and_service import FakeEmailSender


def _build_strategy_test_app(
    tmp_path: Path,
    *,
    admin_email: str = "admin@example.com",
) -> tuple[TestClient, sessionmaker[Session], FakeEmailSender]:
    """构造带独立临时 SQLite 库的测试客户端，绝不触碰本地 ledger.db。"""
    db_path = tmp_path / "strategy_plan_test.db"
    engine = create_engine_for_url(f"sqlite:///{db_path.as_posix()}")
    prepare_database_schema(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with factory() as setup_session:
        ensure_default_admin_and_backfill(setup_session, admin_email)
        setup_session.commit()

    sender = FakeEmailSender()
    auth_service = AuthService(
        app_settings=Settings(
            default_admin_email=admin_email,
            auth_session_days=30,
            auth_secret_key="strategy-test-secret",
        ),
        email_sender=sender,
    )
    application = create_app(create_tables=False)

    def override_db():
        """每个 HTTP 请求使用独立数据库会话。"""
        with factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_auth_service] = lambda: auth_service
    return TestClient(application), factory, sender


def _login(client: TestClient, sender: FakeEmailSender, email: str) -> None:
    """完成邮箱验证码登录流程并写入客户端 Cookie。"""
    req = client.post("/api/v1/auth/request-code", json={"email": email})
    assert req.status_code == 200, req.text
    code = sender.codes[-1][1]
    verify = client.post("/api/v1/auth/verify-code", json={"email": email, "code": code})
    assert verify.status_code == 200, verify.text


def _create_security(client: TestClient, symbol: str = "600519", name: str = "贵州茅台") -> str:
    """创建测试用证券并返回其 ID。"""
    resp = client.post(
        "/api/v1/securities",
        json={"market": "SH", "symbol": symbol, "name": name, "securityType": "STOCK"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_strategy_rows_full_crud_and_import_modes(tmp_path: Path) -> None:
    """验证个股策略表的查询、覆盖/追加导入、单行增删改、状态切换与清空闭环。"""
    client, _factory, sender = _build_strategy_test_app(tmp_path)
    _login(client, sender, "admin@example.com")
    sec_id = _create_security(client)

    # 1. 初始为空。
    res = client.get(f"/api/v1/securities/{sec_id}/strategy-rows")
    assert res.status_code == 200
    assert res.json() == []

    # 2. 批量导入（replace 模式，含单边留空与备注）。
    imported = client.post(
        f"/api/v1/securities/{sec_id}/strategy-rows/import",
        json={
            "mode": "replace",
            "rows": [
                {
                    "buyPrice": "1500.5",
                    "buyQty": "100",
                    "sellPrice": "1650",
                    "sellQty": "100",
                    "note": "第一档支撑位",
                },
                {
                    "buyPrice": None,
                    "buyQty": None,
                    "sellPrice": "1800",
                    "sellQty": "50",
                    "note": "高抛止盈",
                    "status": "BOUGHT",
                },
            ],
        },
    )
    assert imported.status_code == 200, imported.text
    rows = imported.json()
    assert len(rows) == 2
    assert rows[0]["buyPrice"] == "1500.500000"
    assert rows[0]["buyQty"] == "100.0000"
    assert rows[0]["sellPrice"] == "1650.000000"
    assert rows[0]["note"] == "第一档支撑位"
    assert rows[0]["status"] == "PENDING"
    assert rows[1]["buyPrice"] is None
    assert rows[1]["status"] == "BOUGHT"

    # 3. 追加导入（append 模式）。
    appended = client.post(
        f"/api/v1/securities/{sec_id}/strategy-rows/import",
        json={
            "mode": "append",
            "rows": [{"buyPrice": "1400", "buyQty": "200", "note": "深跌加仓"}],
        },
    )
    assert appended.status_code == 200, appended.text
    assert len(appended.json()) == 3
    assert [r["sortOrder"] for r in appended.json()] == [0, 1, 2]

    # 4. 单行新增。
    created = client.post(
        f"/api/v1/securities/{sec_id}/strategy-rows",
        json={"buyPrice": "1350", "buyQty": "100", "note": "极端档"},
    )
    assert created.status_code == 201, created.text
    created_row = created.json()
    assert created_row["sortOrder"] == 3

    # 5. 局部更新（切换状态、清空卖出侧、更新备注）。
    row_id = rows[0]["id"]
    patched = client.patch(
        f"/api/v1/securities/{sec_id}/strategy-rows/{row_id}",
        json={"status": "COMPLETED", "sellPrice": None, "sellQty": None, "note": "已完成波段"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "COMPLETED"
    assert patched.json()["sellPrice"] is None
    assert patched.json()["buyPrice"] == "1500.500000"
    assert patched.json()["note"] == "已完成波段"

    # 6. 删除单行。
    deleted = client.delete(f"/api/v1/securities/{sec_id}/strategy-rows/{created_row['id']}")
    assert deleted.status_code == 204
    assert len(client.get(f"/api/v1/securities/{sec_id}/strategy-rows").json()) == 3

    # 7. 清空全部。
    cleared = client.delete(f"/api/v1/securities/{sec_id}/strategy-rows")
    assert cleared.status_code == 204
    assert client.get(f"/api/v1/securities/{sec_id}/strategy-rows").json() == []


def test_strategy_rows_validation_and_user_isolation(tmp_path: Path) -> None:
    """验证参数校验拦截及不同用户间策略表严格隔离。"""
    client, _factory, sender = _build_strategy_test_app(tmp_path)
    _login(client, sender, "admin@example.com")
    sec_id = _create_security(client, symbol="000001", name="平安银行")

    # 校验拦截：全空行、非正数、不存在的标的。
    empty_row = client.post(
        f"/api/v1/securities/{sec_id}/strategy-rows",
        json={"note": "只有备注没有数值"},
    )
    assert empty_row.status_code == 422

    non_positive = client.post(
        f"/api/v1/securities/{sec_id}/strategy-rows",
        json={"buyPrice": "-10", "buyQty": "100"},
    )
    assert non_positive.status_code == 422

    missing_sec = client.get("/api/v1/securities/non-existent-id/strategy-rows")
    assert missing_sec.status_code == 404

    # 管理员写入一行策略。
    created = client.post(
        f"/api/v1/securities/{sec_id}/strategy-rows",
        json={"buyPrice": "10.5", "buyQty": "500", "note": "管理员计划"},
    )
    assert created.status_code == 201
    admin_row_id = created.json()["id"]

    # 邀请并切换到普通用户 B，验证完全隔离。
    invite = client.post(
        "/api/v1/admin/users/invite",
        json={"email": "userb@example.com", "displayName": "用户B"},
    )
    assert invite.status_code == 201, invite.text
    client.post("/api/v1/auth/logout")
    _login(client, sender, "userb@example.com")

    user_b_rows = client.get(f"/api/v1/securities/{sec_id}/strategy-rows")
    assert user_b_rows.status_code == 200
    assert user_b_rows.json() == []

    # 用户 B 无法越权更新或删除管理员的策略行。
    assert (
        client.patch(
            f"/api/v1/securities/{sec_id}/strategy-rows/{admin_row_id}",
            json={"status": "BOUGHT"},
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/securities/{sec_id}/strategy-rows/{admin_row_id}").status_code
        == 404
    )


def test_alembic_upgrade_from_0008_to_0009(tmp_path: Path) -> None:
    """验证 Alembic 从 0008 升级到 0009 无损创建 security_strategy_rows 表。"""
    db_path = tmp_path / "migration_0009.db"
    engine = create_engine_for_url(f"sqlite:///{db_path.as_posix()}")
    backend_root = Path(__file__).resolve().parents[2]

    with engine.connect() as conn:
        cfg = Config(str(backend_root / "alembic.ini"))
        cfg.set_main_option("script_location", str(backend_root / "alembic"))
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0008_watchlist_buyback_alerts")
        assert "security_strategy_rows" not in inspect(conn).get_table_names()
        command.upgrade(cfg, "head")
        assert "security_strategy_rows" in inspect(conn).get_table_names()
