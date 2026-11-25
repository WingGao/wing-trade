"""止盈推送迁移、用户隔离配置 API 与实时止盈引擎集成测试（仅使用隔离临时库）。"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.api.v1.notifications as notifications_api
from app.api.deps import get_current_user, get_db
from app.domain.enums import TargetStatus, UserRole, UserStatus
from app.domain.exceptions import NotificationDeliveryError
from app.main import create_app
from app.market_data.hub import MarketDataHub
from app.market_data.provider import MockQuoteProvider, QuoteSnapshot
from app.market_data.service import QuoteService
from app.models import (
    Account,
    Base,
    Lot,
    NotificationChannel,
    ProfitAlertDelivery,
    ProfitSetting,
    ProfitTarget,
    Security,
    Strategy,
    User,
)
from app.services.ledger import TradeLedgerService
from app.services.notifications.channels import WECOM_CHANNEL_TYPE
from app.services.notifications.engine import ProfitAlertEngine
from app.services.profit_settings import ProfitSettingsService
from app.services.target import TargetService

VALID_WEBHOOK_A = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=userAsecret123456"
VALID_WEBHOOK_B = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=userBsecret654321"


class FakeWeComChannel:
    """可控的企业微信推送替身，用于记录发送消息并模拟失败/恢复。"""

    channel_type = WECOM_CHANNEL_TYPE

    def __init__(self) -> None:
        """初始化记录列表与预设失败次数。"""
        self.sent: list[tuple[str, str]] = []
        self.fail_remaining: int = 0
        self.retryable: bool = True

    def send_markdown(self, target: str, content: str) -> None:
        """按配置模拟失败或记录成功发送的企微消息。"""
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise NotificationDeliveryError(
                "模拟企微推送失败",
                details={
                    "retryable": self.retryable,
                    "errcode": 45009 if self.retryable else 93000,
                },
            )
        self.sent.append((target, content))


def test_notification_migration_0006_upgrade_and_downgrade(tmp_path: Path) -> None:
    """在隔离临时库验证 0006 迁移升级、保留存量数据及可逆回退。"""
    database = tmp_path / "notification-migration.sqlite"
    backend = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    def migrate(target: str, *, downgrade: bool = False) -> None:
        """在指定临时库执行 Alembic 升降级，不触碰本地 ledger.db。"""
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade" if downgrade else "upgrade", target],
            cwd=backend,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("0005_security_proxy_fields")
    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO securities (id, market, symbol, name, security_type, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("sec-keep", "SH", "600519", "贵州茅台", "STOCK", "ACTIVE", "2026-11-25"),
        )
    migrate("head")
    with sqlite3.connect(database) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "notification_channels" in tables
        assert "profit_alert_deliveries" in tables
        assert conn.execute("SELECT name FROM securities").fetchall() == [("贵州茅台",)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    migrate("0005_security_proxy_fields", downgrade=True)
    with sqlite3.connect(database) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "notification_channels" not in tables
        assert "profit_alert_deliveries" not in tables
        assert conn.execute("SELECT name FROM securities").fetchall() == [("贵州茅台",)]


def test_wecom_config_api_isolation_masking_and_test_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证企微配置读写脱敏、保留密钥切换开关、清空、多用户隔离及一键测试接口。"""
    db_file = tmp_path / "notif-api.sqlite"
    engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with session_factory() as session:
        user_a = User(
            email="a@example.com",
            display_name="用户A",
            role=UserRole.ADMIN.value,
            status=UserStatus.ACTIVE.value,
            is_default_admin=True,
        )
        user_b = User(
            email="b@example.com",
            display_name="用户B",
            role=UserRole.USER.value,
            status=UserStatus.ACTIVE.value,
            is_default_admin=False,
        )
        session.add_all([user_a, user_b])
        session.commit()

    active_user: dict[str, User] = {"current": user_a}
    fake_channel = FakeWeComChannel()
    monkeypatch.setattr(notifications_api, "_wecom_channel", fake_channel)

    app = create_app(create_tables=False, frontend_dist_dir="")

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: active_user["current"]

    client = TestClient(app)

    # 初始未配置
    resp = client.get("/api/v1/notifications/wecom")
    assert resp.status_code == 200
    assert resp.json()["configured"] is False
    assert resp.json()["enabled"] is False
    assert resp.json()["maskedWebhookUrl"] is None

    # 未填 URL 直接开启必须返回 422
    resp = client.put("/api/v1/notifications/wecom", json={"enabled": True})
    assert resp.status_code == 422

    # 非法域名必须拒绝
    resp = client.put(
        "/api/v1/notifications/wecom",
        json={"enabled": True, "webhookUrl": "https://evil.example.com/hook?key=123456789"},
    )
    assert resp.status_code == 422

    # 保存合法 Webhook 并检查脱敏返回
    resp = client.put(
        "/api/v1/notifications/wecom",
        json={"enabled": True, "webhookUrl": VALID_WEBHOOK_A},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["enabled"] is True
    assert "userAsecret123456" not in (data["maskedWebhookUrl"] or "")
    assert "user****3456" in (data["maskedWebhookUrl"] or "")

    # 省略 webhookUrl 仅关闭开关，保留已保存地址
    resp = client.put("/api/v1/notifications/wecom", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["configured"] is True
    assert resp.json()["enabled"] is False

    # 一键测试发送成功
    resp = client.post("/api/v1/notifications/wecom/test", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(fake_channel.sent) == 1
    assert fake_channel.sent[0][0] == VALID_WEBHOOK_A

    # 企微接口失败时返回 502
    fake_channel.fail_remaining = 1
    resp = client.post("/api/v1/notifications/wecom/test", json={})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "NOTIFICATION_DELIVERY_FAILED"

    # 切换到用户 B：看不到用户 A 的配置
    active_user["current"] = user_b
    resp = client.get("/api/v1/notifications/wecom")
    assert resp.status_code == 200
    assert resp.json()["configured"] is False

    # 切回用户 A 并清空配置
    active_user["current"] = user_a
    resp = client.put("/api/v1/notifications/wecom", json={"enabled": True, "webhookUrl": None})
    assert resp.status_code == 200
    assert resp.json()["configured"] is False
    assert resp.json()["enabled"] is False
    engine.dispose()


def test_profit_alert_engine_inheritance_dedup_batching_and_retry(tmp_path: Path) -> None:
    """全面验证三层止盈触发、场外基金估算、交易窗口、自然日去重、改目标重触发、同轮合并与失败重试。"""
    db_file = tmp_path / "notif-engine.sqlite"
    engine_db = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine_db)
    session_factory = sessionmaker(bind=engine_db, autoflush=False, expire_on_commit=False)

    # 2026-11-25（周三）北京时间 10:30 = UTC 02:30，处于 A 股盘中交易时段
    day1_open = datetime(2026, 11, 25, 2, 30, 0, tzinfo=UTC)
    day1_closed = datetime(2026, 11, 25, 13, 0, 0, tzinfo=UTC)  # 北京时间 21:00，A 股已休市
    day2_open = datetime(2026, 11, 26, 2, 30, 0, tzinfo=UTC)

    with session_factory() as session:
        user_a = User(
            email="owner@example.com",
            display_name="交易员A",
            role=UserRole.ADMIN.value,
            status=UserStatus.ACTIVE.value,
            is_default_admin=True,
        )
        user_b = User(
            email="other@example.com",
            display_name="交易员B",
            role=UserRole.USER.value,
            status=UserStatus.ACTIVE.value,
            is_default_admin=False,
        )
        session.add_all([user_a, user_b])
        session.flush()

        acc_a = Account(name="A股主账户", currency="CNY", user_id=user_a.id)
        acc_b = Account(name="B账户", currency="CNY", user_id=user_b.id)
        session.add_all([acc_a, acc_b])
        session.flush()
        strat_a = Strategy(account_id=acc_a.id, name="默认策略")
        strat_b = Strategy(account_id=acc_b.id, name="默认策略")
        sec_lot = Security(market="SH", symbol="600519", name="贵州茅台", security_type="STOCK")
        sec_override = Security(
            market="SZ", symbol="000001", name="平安银行", security_type="STOCK"
        )
        sec_global = Security(market="SH", symbol="601318", name="中国平安", security_type="STOCK")
        sec_fund = Security(
            market="OF",
            symbol="000001",
            name="华夏成长混合",
            security_type="FUND",
            proxy_symbol="SH:510300",
            proxy_name="沪深300ETF",
            tracking_ratio=Decimal("1.000000"),
        )
        sec_proxy = Security(market="SH", symbol="510300", name="沪深300ETF", security_type="ETF")
        session.add_all(
            [strat_a, strat_b, sec_lot, sec_override, sec_global, sec_fund, sec_proxy]
        )
        session.flush()

        # 配置用户 A 与用户 B 的企微渠道
        session.add_all(
            [
                NotificationChannel(
                    user_id=user_a.id,
                    channel_type=WECOM_CHANNEL_TYPE,
                    enabled=True,
                    webhook_url=VALID_WEBHOOK_A,
                ),
                NotificationChannel(
                    user_id=user_b.id,
                    channel_type=WECOM_CHANNEL_TYPE,
                    enabled=True,
                    webhook_url=VALID_WEBHOOK_B,
                ),
            ]
        )

        # 三层止盈设置：用户 A 全局 10%，个股 000001 覆盖为 5%（且开启提醒）
        session.add_all(
            [
                ProfitSetting(
                    scope_key=ProfitSettingsService.scope_key(user_id=user_a.id),
                    user_id=user_a.id,
                    security_id=None,
                    target_return_pct=Decimal("0.100000"),
                    calculation_basis="GROSS",
                    alert_enabled=True,
                ),
                ProfitSetting(
                    scope_key=ProfitSettingsService.scope_key(sec_override.id, user_id=user_a.id),
                    user_id=user_a.id,
                    security_id=sec_override.id,
                    target_return_pct=Decimal("0.050000"),
                    calculation_basis="GROSS",
                    alert_enabled=True,
                ),
            ]
        )
        session.commit()

        ledger = TradeLedgerService(session)
        # 用户 A 买入 4 笔持仓
        buy_lot = ledger.buy(
            account_id=acc_a.id,
            security_id=sec_lot.id,
            strategy_id=strat_a.id,
            executed_at=datetime(2026, 11, 20, 2, 0, tzinfo=UTC),
            qty=Decimal("100"),
            price=Decimal("100"),
        )
        ledger.buy(
            account_id=acc_a.id,
            security_id=sec_override.id,
            strategy_id=strat_a.id,
            executed_at=datetime(2026, 11, 20, 2, 0, tzinfo=UTC),
            qty=Decimal("100"),
            price=Decimal("10"),
        )
        ledger.buy(
            account_id=acc_a.id,
            security_id=sec_global.id,
            strategy_id=strat_a.id,
            executed_at=datetime(2026, 11, 20, 2, 0, tzinfo=UTC),
            qty=Decimal("100"),
            price=Decimal("50"),
        )
        ledger.buy(
            account_id=acc_a.id,
            security_id=sec_fund.id,
            strategy_id=strat_a.id,
            executed_at=datetime(2026, 11, 20, 2, 0, tzinfo=UTC),
            qty=Decimal("1000"),
            price=Decimal("1.0000"),
        )
        # 用户 B 买入 600519，未设任何止盈，不应被推送
        ledger.buy(
            account_id=acc_b.id,
            security_id=sec_lot.id,
            strategy_id=strat_b.id,
            executed_at=datetime(2026, 11, 20, 2, 0, tzinfo=UTC),
            qty=Decimal("100"),
            price=Decimal("100"),
        )

        # 给 600519 的批次设置一条显式 8% 目标（目标价 108）
        lot_600519 = session.scalar(select(Lot).where(Lot.buy_trade_id == buy_lot.id))
        assert lot_600519 is not None
        explicit_target = ProfitTarget(
            lot_id=lot_600519.id,
            target_return_pct=Decimal("0.080000"),
            planned_sell_qty=Decimal("100.0000"),
            calculation_basis="GROSS",
            target_price_estimate=TargetService.solve_price(
                unit_cost=lot_600519.unit_cost,
                planned_sell_qty=Decimal("100.0000"),
                target_return_pct=Decimal("0.080000"),
                basis="GROSS",
            ),
            alert_enabled=True,
            status=TargetStatus.ACTIVE.value,
        )
        session.add(explicit_target)
        session.commit()
        explicit_target_id = explicit_target.id
        sec_override_id = sec_override.id
        user_a_id = user_a.id

    current_time = {"now": day1_open}
    quote_service = QuoteService()
    hub = MarketDataHub(
        quote_service=quote_service,
        provider=MockQuoteProvider(),
        session_factory=session_factory,
        broadcast_coalesce_seconds=0.0,
        now=lambda: current_time["now"],
    )
    hub.warm_from_db()

    fake_channel = FakeWeComChannel()
    alert_engine = ProfitAlertEngine(
        quote_hub=hub,
        session_factory=session_factory,
        channels={WECOM_CHANNEL_TYPE: fake_channel},
        debounce_seconds=0.0,
        max_retries=2,
        retry_backoff_seconds=(0.0,),
        failure_cooldown_seconds=60.0,
        now=lambda: current_time["now"],
    )

    # 1. 发布行情：600519 达到 109（显式 108）、000001 达到 10.6（个股 10.5）、
    #    601318 达到 56（全局 55）；场外基金估算净值 1.12（全局 1.10 达标）
    hub.publish(
        [
            QuoteSnapshot(
                symbol="SH:600519",
                last_price=Decimal("109.00"),
                prev_close=Decimal("100.00"),
                change_pct=Decimal("9.00"),
                source_time=day1_open,
                fetched_at=day1_open,
                provider="test",
                market_session="regular",
            ),
            QuoteSnapshot(
                symbol="SZ:000001",
                last_price=Decimal("10.60"),
                prev_close=Decimal("10.00"),
                change_pct=Decimal("6.00"),
                source_time=day1_open,
                fetched_at=day1_open,
                provider="test",
                market_session="regular",
            ),
            QuoteSnapshot(
                symbol="SH:601318",
                last_price=Decimal("56.00"),
                prev_close=Decimal("50.00"),
                change_pct=Decimal("12.00"),
                source_time=day1_open,
                fetched_at=day1_open,
                provider="test",
                market_session="regular",
            ),
            QuoteSnapshot(
                symbol="OF:000001",
                last_price=Decimal("1.0000"),
                prev_close=Decimal("1.0000"),
                change_pct=Decimal("0.00"),
                source_time=datetime(2026, 11, 24, 12, 0, tzinfo=UTC),
                fetched_at=day1_open,
                provider="test",
            ),
            QuoteSnapshot(
                symbol="SH:510300",
                last_price=Decimal("4.4800"),
                prev_close=Decimal("4.0000"),
                change_pct=Decimal("12.00"),
                source_time=day1_open,
                fetched_at=day1_open,
                provider="test",
                market_session="regular",
            ),
        ],
        mark_dirty=False,
    )

    # 先验证休市时间不触发任何推送
    current_time["now"] = day1_closed
    closed_sent = asyncio.run(
        alert_engine.evaluate_and_dispatch(
            ["SH:600519", "SZ:000001", "SH:601318", "OF:000001"], now=day1_closed
        )
    )
    assert closed_sent == 0
    assert len(fake_channel.sent) == 0

    # 切回盘中交易时段：同轮 4 笔达标应合并为 1 条卡片发给用户 A，用户 B 不受影响
    current_time["now"] = day1_open
    sent_count = asyncio.run(
        alert_engine.evaluate_and_dispatch(
            ["SH:600519", "SZ:000001", "SH:601318", "OF:000001"], now=day1_open
        )
    )
    assert sent_count == 4
    assert len(fake_channel.sent) == 1
    target_url, md_content = fake_channel.sent[0]
    assert target_url == VALID_WEBHOOK_A
    assert "命中 4 笔" in md_content
    assert "贵州茅台" in md_content
    assert "平安银行" in md_content
    assert "中国平安" in md_content
    assert "华夏成长混合" in md_content
    assert "盘中估算" in md_content

    # 验证显式目标同步标记为 TRIGGERED
    with session_factory() as session:
        t_row = session.get(ProfitTarget, explicit_target_id)
        assert t_row is not None
        assert t_row.status == TargetStatus.TRIGGERED.value
        assert t_row.triggered_at is not None

    # 2. 当日重复评估应被去重跳过
    repeat_sent = asyncio.run(
        alert_engine.evaluate_and_dispatch(
            ["SH:600519", "SZ:000001", "SH:601318", "OF:000001"], now=day1_open
        )
    )
    assert repeat_sent == 0
    assert len(fake_channel.sent) == 1

    # 3. 当日修改个股 000001 止盈率（5% -> 4%），指纹变化应重新触发 1 次
    with session_factory() as session:
        setting = session.get(
            ProfitSetting, ProfitSettingsService.scope_key(sec_override_id, user_id=user_a_id)
        )
        assert setting is not None
        setting.target_return_pct = Decimal("0.040000")
        session.commit()

    # 模拟发送失败 2 次（达到 max_retries=2），不应消耗去重名额
    fake_channel.fail_remaining = 2
    failed_sent = asyncio.run(
        alert_engine.evaluate_and_dispatch(["SZ:000001"], now=day1_open)
    )
    assert failed_sent == 0
    assert len(fake_channel.sent) == 1

    # 冷却期过后重试恢复成功
    after_cooldown = datetime(2026, 11, 25, 2, 32, 0, tzinfo=UTC)
    current_time["now"] = after_cooldown
    hub.publish(
        [
            QuoteSnapshot(
                symbol="SZ:000001",
                last_price=Decimal("10.60"),
                prev_close=Decimal("10.00"),
                change_pct=Decimal("6.00"),
                source_time=after_cooldown,
                fetched_at=after_cooldown,
                provider="test",
                market_session="regular",
            )
        ],
        mark_dirty=False,
    )
    recovered_sent = asyncio.run(
        alert_engine.evaluate_and_dispatch(["SZ:000001"], now=after_cooldown)
    )
    assert recovered_sent == 1
    assert len(fake_channel.sent) == 2
    assert "平安银行" in fake_channel.sent[1][1]

    # 4. 次日交易时段，即便显式目标已是 TRIGGERED，仍按自然日规则再次提醒
    current_time["now"] = day2_open
    hub.publish(
        [
            QuoteSnapshot(
                symbol="SH:600519",
                last_price=Decimal("109.50"),
                prev_close=Decimal("109.00"),
                change_pct=Decimal("0.46"),
                source_time=day2_open,
                fetched_at=day2_open,
                provider="test",
                market_session="regular",
            )
        ],
        mark_dirty=False,
    )
    next_day_sent = asyncio.run(
        alert_engine.evaluate_and_dispatch(["SH:600519"], now=day2_open)
    )
    assert next_day_sent == 1
    assert len(fake_channel.sent) == 3
    assert "贵州茅台" in fake_channel.sent[2][1]

    with session_factory() as session:
        deliveries = list(session.scalars(select(ProfitAlertDelivery)).all())
        assert len(deliveries) == 6
    engine_db.dispose()
