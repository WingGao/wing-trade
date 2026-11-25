"""通知渠道配置与测试推送 API 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WeComNotificationUpdate(BaseModel):
    """保存或更新企业微信 Webhook 配置请求体。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    enabled: bool = Field(default=False)
    webhook_url: str | None = Field(default=None, alias="webhookUrl")


class WeComNotificationTestRequest(BaseModel):
    """发送企业微信测试消息请求体（可选传入未保存的临时 Webhook 地址）。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    webhook_url: str | None = Field(default=None, alias="webhookUrl")
