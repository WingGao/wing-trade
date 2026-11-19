"""用户认证、管理员邀请、跨用户严格隔离与存量 SQLite 无损迁移集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.api.deps import get_auth_service, get_db
from app.config import Settings
from app.db.migrations import prepare_database_schema
from app.db.session import create_engine_for_url
from app.main import create_app
from app.models import Account, Lot, ProfitSetting, Trade
from app.services.auth import AuthService
from app.services.auth_bootstrap import ensure_default_admin_and_backfill
from app.services.ledger import TradeLedgerService
from tests.unit.test_auth_config_and_service import FakeEmailSender


def _build_test_app(
    tmp_path: Path,
    *,
    admin_email: str = "admin@example.com",
) -> tuple[TestClient, sessionmaker[Session], FakeEmailSender]:
    """构造带独立 SQLite 库与内存发信器的真实认证应用客户端。"""
    db_path = tmp_path / "auth_integration.db"
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
            auth_secret_key="integration-secret",
        ),
        email_sender=sender,
    )
    application = create_app(create_tables=False)

    def override_db():
        """每个 HTTP 请求独立打开和关闭测试数据库会话。"""
        with factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_db
    application.dependency_overrides[get_auth_service] = lambda: auth_service
    return TestClient(application), factory, sender


def _login_via_email(client: TestClient, sender: FakeEmailSender, email: str) -> dict:
    """走真实邮箱发码与验证码登录流程，返回登录响应体。"""
    req = client.post("/api/v1/auth/request-code", json={"email": email})
    assert req.status_code == 200, req.text
    code = sender.codes[-1][1]
    verify = client.post("/api/v1/auth/verify-code", json={"email": email, "code": code})
    assert verify.status_code == 200, verify.text
    return verify.json()


def test_unauthenticated_access_blocked_and_login_logout_flow(tmp_path: Path) -> None:
    """验证未登录拦截 401、30 天 Cookie 登录态及登出即刻失效。"""
    client, _factory, sender = _build_test_app(tmp_path)

    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/dashboard").status_code == 401
    assert client.get("/api/v1/trades").status_code == 401
    assert client.get("/api/v1/accounts").status_code == 401

    # 未受邀邮箱不可获取验证码。
    uninvited = client.post("/api/v1/auth/request-code", json={"email": "nobody@example.com"})
    assert uninvited.status_code == 403

    # 默认管理员通过验证码登录。
    data = _login_via_email(client, sender, "admin@example.com")
    assert data["user"]["role"] == "ADMIN"
    assert data["user"]["status"] == "ACTIVE"
    assert "wing_session" in client.cookies

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "admin@example.com"

    # 退出登录后原 Cookie 立即失效。
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_admin_invite_disable_and_strict_multi_user_isolation(tmp_path: Path) -> None:
    """验证管理员定向邀请、普通用户 403、严格账本/止盈隔离及禁用即时踢下线。"""
    admin_client, _factory, sender = _build_test_app(tmp_path)
    _login_via_email(admin_client, sender, "admin@example.com")

    # 管理员录入一笔交易与止盈设置。
    sec = admin_client.post(
        "/api/v1/securities", json={"market": "SH", "symbol": "600519", "name": "贵州茅台"}
    ).json()
    buy = admin_client.post(
        "/api/v1/trades/buy",
        json={
            "securityId": sec["id"],
            "executedAt": datetime(2026, 11, 10, 10, 0, tzinfo=UTC).isoformat(),
            "qty": "100",
            "price": "1500",
            "clientRequestId": "admin-buy-1",
        },
    )
    assert buy.status_code == 201, buy.text
    buy_data = buy.json()
    admin_account_id = buy_data["accountId"]

    lots_resp = admin_client.get(f"/api/v1/securities/{sec['id']}/lots").json()
    admin_lot_id = lots_resp["lots"][0]["id"]
    admin_target = admin_client.post(
        f"/api/v1/lots/{admin_lot_id}/targets",
        json={"targetReturnPct": "0.15", "plannedSellQty": "100", "calculationBasis": "NET"},
    ).json()

    admin_client.put(
        "/api/v1/profit-settings",
        json={"targetReturnPct": "0.12", "calculationBasis": "NET", "alertEnabled": True},
    )

    # 管理员邀请普通用户与第二个待撤销邀请。
    inv = admin_client.post(
        "/api/v1/admin/users/invite",
        json={"email": "alice@example.com", "displayName": "Alice"},
    )
    assert inv.status_code == 201
    alice_id = inv.json()["user"]["id"]
    assert inv.json()["user"]["status"] == "INVITED"

    # 重复邀请返回 409。
    dup = admin_client.post("/api/v1/admin/users/invite", json={"email": "alice@example.com"})
    assert dup.status_code == 409

    # 邀请并撤销一个未激活用户。
    temp_inv = admin_client.post("/api/v1/admin/users/invite", json={"email": "temp@example.com"})
    assert temp_inv.status_code == 201
    temp_id = temp_inv.json()["user"]["id"]
    assert admin_client.delete(f"/api/v1/admin/users/{temp_id}/invite").status_code == 204

    # 普通用户使用独立客户端通过邮箱验证码首登激活。
    user_client = TestClient(admin_client.app)
    alice_login = _login_via_email(user_client, sender, "alice@example.com")
    assert alice_login["user"]["role"] == "USER"
    assert alice_login["user"]["status"] == "ACTIVE"

    # 普通用户访问管理员接口返回 403。
    assert user_client.get("/api/v1/admin/users").status_code == 403
    forbidden_invite = user_client.post(
        "/api/v1/admin/users/invite", json={"email": "other@example.com"}
    )
    assert forbidden_invite.status_code == 403

    # 严格账本隔离：普通用户看不到管理员的流水、持仓、止盈设置。
    assert user_client.get("/api/v1/trades").json() == []
    assert user_client.get("/api/v1/positions").json() == []
    assert user_client.get("/api/v1/profit-settings").json() is None

    # 跨用户直查他人资源 ID 一律返回 404。
    assert user_client.get(f"/api/v1/dashboard?accountId={admin_account_id}").status_code == 404
    assert user_client.get(f"/api/v1/trades?accountId={admin_account_id}").status_code == 404
    void_other = user_client.post(
        f"/api/v1/trades/{buy_data['id']}/void", json={"reason": "x"}
    )
    assert void_other.status_code == 404
    assert user_client.get(f"/api/v1/lots/{admin_lot_id}/targets").status_code == 404
    assert user_client.delete(f"/api/v1/targets/{admin_target['id']}").status_code == 404

    # 普通用户保存自己的全局止盈设置，不影响管理员的止盈设置。
    user_client.put(
        "/api/v1/profit-settings",
        json={"targetReturnPct": "0.25", "calculationBasis": "GROSS", "alertEnabled": False},
    )
    assert admin_client.get("/api/v1/profit-settings").json()["targetReturnPct"] == "0.120000"
    assert user_client.get("/api/v1/profit-settings").json()["targetReturnPct"] == "0.250000"

    # 管理员不可禁用自己。
    admin_id = admin_client.get("/api/v1/auth/me").json()["user"]["id"]
    assert (
        admin_client.patch(
            f"/api/v1/admin/users/{admin_id}/status", json={"status": "DISABLED"}
        ).status_code
        == 422
    )

    # 管理员禁用普通用户后，普通用户现有会话立即返回 401，且不能再请求验证码。
    disable = admin_client.patch(
        f"/api/v1/admin/users/{alice_id}/status", json={"status": "DISABLED"}
    )
    assert disable.status_code == 200
    assert disable.json()["user"]["status"] == "DISABLED"
    assert user_client.get("/api/v1/auth/me").status_code == 401
    assert user_client.get("/api/v1/trades").status_code == 401
    disabled_code_req = user_client.post(
        "/api/v1/auth/request-code", json={"email": "alice@example.com"}
    )
    assert disabled_code_req.status_code == 403


def test_legacy_sqlite_database_upgrades_without_data_loss(tmp_path: Path) -> None:
    """验证停留在 0003 的旧版 SQLite 数据库无损升级后，存量数据全部归属默认管理员。"""
    db_path = tmp_path / "legacy_0003.db"
    engine = create_engine_for_url(f"sqlite:///{db_path.as_posix()}")

    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0003_position_refresh_states")

    # 写入存量账户、证券、交易、批次与旧格式全局止盈配置。
    now_iso = datetime(2026, 11, 1, 9, 30, tzinfo=UTC).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO accounts (id, name, broker, currency, created_at) "
                "VALUES ('acc-legacy', '老账本', '华泰', 'CNY', :now)"
            ),
            {"now": now_iso},
        )
        conn.execute(
            text(
                "INSERT INTO strategies (id, account_id, name, created_at) "
                "VALUES ('str-legacy', 'acc-legacy', 'DEFAULT', :now)"
            ),
            {"now": now_iso},
        )
        conn.execute(
            text(
                "INSERT INTO securities "
                "(id, market, symbol, name, security_type, status, created_at) "
                "VALUES ('sec-legacy', 'SH', '600519', '贵州茅台', 'STOCK', 'ACTIVE', :now)"
            ),
            {"now": now_iso},
        )
        conn.execute(
            text(
                "INSERT INTO profit_settings "
                "(scope_key, security_id, target_return_pct, calculation_basis, "
                "alert_enabled, created_at) "
                "VALUES ('GLOBAL', NULL, '0.100000', 'NET', 1, :now)"
            ),
            {"now": now_iso},
        )
        conn.execute(
            text(
                "INSERT INTO profit_settings "
                "(scope_key, security_id, target_return_pct, calculation_basis, "
                "alert_enabled, created_at) "
                "VALUES ('SECURITY:sec-legacy', 'sec-legacy', '0.180000', 'GROSS', 1, :now)"
            ),
            {"now": now_iso},
        )

    # 执行新版启动迁移与默认管理员回填。
    prepare_database_schema(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with factory() as session:
        admin = ensure_default_admin_and_backfill(session, "owner@example.com")
        session.commit()

        # 再写入一笔交易确认升级后外键与账务写入完全正常。
        TradeLedgerService(session, actor=admin.email).buy(
            account_id="acc-legacy",
            security_id="sec-legacy",
            strategy_id="str-legacy",
            executed_at=datetime(2026, 11, 2, 10, 0, tzinfo=UTC),
            qty=Decimal("100"),
            price=Decimal("1500"),
            client_request_id="post-upgrade-buy",
        )

        account = session.get(Account, "acc-legacy")
        assert account is not None
        assert account.user_id == admin.id
        assert session.query(Trade).count() == 1
        assert session.query(Lot).count() == 1

        global_setting = session.get(ProfitSetting, f"USER:{admin.id}:GLOBAL")
        sec_setting = session.get(ProfitSetting, f"USER:{admin.id}:SECURITY:sec-legacy")
        assert global_setting is not None
        assert global_setting.user_id == admin.id
        assert global_setting.target_return_pct == Decimal("0.100000")
        assert sec_setting is not None
        assert sec_setting.user_id == admin.id
        assert sec_setting.target_return_pct == Decimal("0.180000")

        # 确认 SQLite 完整性检查通过。
        integrity = session.execute(text("PRAGMA integrity_check")).scalar()
        assert integrity == "ok"
    engine.dispose()
