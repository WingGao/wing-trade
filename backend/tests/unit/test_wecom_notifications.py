"""企业微信 Webhook 通道校验、脱敏、发送与 Markdown 格式化单元测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.exceptions import NotificationDeliveryError, ValidationError
from app.services.notifications.channels import (
    HttpPostResponse,
    WeComWebhookChannel,
    mask_wecom_webhook_url,
    validate_wecom_webhook_url,
)
from app.services.notifications.formatter import (
    TakeProfitAlertItem,
    build_take_profit_markdown_batches,
    format_take_profit_markdown_batches,
    format_test_markdown,
)


def _sample_item(index: int = 1, *, is_estimated: bool = False) -> TakeProfitAlertItem:
    """构造用于格式化测试的止盈命中样例。"""
    return TakeProfitAlertItem(
        user_id="user-1",
        account_name="A股主账户",
        security_name=f"测试标的{index}",
        canonical_symbol=f"SH:60000{index}" if not is_estimated else f"OF:00000{index}",
        market="OF" if is_estimated else "SH",
        currency="CNY",
        lot_id=f"lot-{index}",
        explicit_target_id=f"target-{index}" if not is_estimated else None,
        target_key=f"LOT:target-{index}" if not is_estimated else "INHERITED",
        target_signature="LOT|NET|0.100000|11.000000|100.0000",
        target_source="LOT" if not is_estimated else "GLOBAL",
        calculation_basis="NET",
        unit_cost=Decimal("10.000000"),
        planned_qty=Decimal("100.0000"),
        last_price=Decimal("11.200000"),
        target_price=Decimal("11.000000"),
        current_return_pct=Decimal("0.120000"),
        target_return_pct=Decimal("0.100000"),
        estimated_pnl=Decimal("120.0000"),
        is_estimated=is_estimated,
    )


def test_validate_and_mask_wecom_webhook_url() -> None:
    """合法企微 Webhook 应规范化通过并正确脱敏；非法或潜在 SSRF 地址必须拒绝。"""
    valid = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdef1234567890"
    assert validate_wecom_webhook_url(f"  {valid}  ") == valid
    masked = mask_wecom_webhook_url(valid)
    assert masked == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcd****7890"
    assert "abcdef1234567890" not in masked

    assert (
        mask_wecom_webhook_url("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=short12")
        == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=****"
    )
    assert mask_wecom_webhook_url(None) is None

    invalid_urls = [
        "",
        "http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdef1234567890",
        "https://127.0.0.1/cgi-bin/webhook/send?key=abcdef1234567890",
        "https://qyapi.weixin.qq.com:8443/cgi-bin/webhook/send?key=abcdef1234567890",
        "https://qyapi.weixin.qq.com/other/path?key=abcdef1234567890",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdef1234567890#frag",
    ]
    for bad in invalid_urls:
        with pytest.raises(ValidationError):
            validate_wecom_webhook_url(bad)


def test_wecom_channel_send_success_and_error_handling() -> None:
    """企微通道应校验 errcode==0，非零返回码与网络异常均抛出带重试标记的领域异常。"""
    captured: list[tuple[str, dict[str, object]]] = []
    webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secretkey12345678"

    def ok_transport(
        url: str, body: bytes, headers: dict[str, str], timeout: float
    ) -> HttpPostResponse:
        assert headers["Content-Type"].startswith("application/json")
        assert timeout >= 1.0
        captured.append((url, json.loads(body.decode("utf-8"))))
        return HttpPostResponse(status_code=200, body='{"errcode": 0, "errmsg": "ok"}')

    channel = WeComWebhookChannel(timeout_seconds=3.0, transport=ok_transport)
    channel.send_markdown(webhook, "### 测试")
    assert len(captured) == 1
    assert captured[0][0] == webhook
    assert captured[0][1] == {"msgtype": "markdown", "markdown": {"content": "### 测试"}}

    # 企微限频错误码 45009 应标记为可重试。
    rate_limited = WeComWebhookChannel(
        transport=lambda *_args: HttpPostResponse(
            status_code=200,
            body='{"errcode": 45009, "errmsg": "api freq out of limit"}',
        )
    )
    with pytest.raises(NotificationDeliveryError) as rate_exc:
        rate_limited.send_markdown(webhook, "### 测试")
    assert rate_exc.value.details.get("retryable") is True
    assert rate_exc.value.details.get("errcode") == 45009

    # 无效 key 错误码 93000 不应无限重试。
    invalid_key = WeComWebhookChannel(
        transport=lambda *_args: HttpPostResponse(
            status_code=200,
            body='{"errcode": 93000, "errmsg": "invalid webhook url"}',
        )
    )
    with pytest.raises(NotificationDeliveryError) as invalid_exc:
        invalid_key.send_markdown(webhook, "### 测试")
    assert invalid_exc.value.details.get("retryable") is False
    assert "secretkey12345678" not in invalid_exc.value.message

    # 网络异常标记为可重试。
    def failing_transport(*_args: object) -> HttpPostResponse:
        raise TimeoutError("timed out")

    net_fail = WeComWebhookChannel(transport=failing_transport)
    with pytest.raises(NotificationDeliveryError) as net_exc:
        net_fail.send_markdown(webhook, "### 测试")
    assert net_exc.value.details.get("retryable") is True


def test_formatter_marks_estimated_and_splits_large_batches() -> None:
    """格式化器应标注场外基金盘中估算，并在超出字节上限时自动拆批且不丢项。"""
    triggered_at = datetime(2026, 11, 25, 2, 30, 0, tzinfo=UTC)
    single = format_take_profit_markdown_batches(
        [_sample_item(1, is_estimated=True)],
        triggered_at=triggered_at,
    )
    assert len(single) == 1
    assert "盘中估算" in single[0]
    assert "估算净值" in single[0]
    assert "2026-11-25 10:30:00" in single[0]

    many = [_sample_item(idx) for idx in range(1, 15)]
    batches = build_take_profit_markdown_batches(many, triggered_at=triggered_at, max_bytes=900)
    assert len(batches) > 1
    recovered_ids = [item.lot_id for batch_items, _ in batches for item in batch_items]
    assert recovered_ids == [item.lot_id for item in many]
    for _, content in batches:
        assert len(content.encode("utf-8")) <= 900

    test_md = format_test_markdown(user_label="wing@example.com", sent_at=triggered_at)
    assert "止盈推送通道测试成功" in test_md
    assert "wing@example.com" in test_md
