"""外部通知推送服务包：提供可扩展通道抽象、企业微信 Webhook 发送、配置管理与实时止盈评估引擎。"""

from .channels import (
    WECOM_CHANNEL_TYPE,
    PushChannel,
    WeComWebhookChannel,
    mask_wecom_webhook_url,
    validate_wecom_webhook_url,
)
from .formatter import (
    TakeProfitAlertItem,
    format_take_profit_markdown_batches,
    format_test_markdown,
)

__all__ = [
    "PushChannel",
    "TakeProfitAlertItem",
    "WECOM_CHANNEL_TYPE",
    "WeComWebhookChannel",
    "format_take_profit_markdown_batches",
    "format_test_markdown",
    "mask_wecom_webhook_url",
    "validate_wecom_webhook_url",
]
