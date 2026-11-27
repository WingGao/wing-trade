# 企业微信通知渲染契约

## 1. 适用范围

适用于 `backend/app/services/notifications/formatter.py` 中的企业微信群机器人 Markdown 消息渲染逻辑，包括止盈提醒（`TakeProfitAlertItem`）与买回价提醒（`BuybackAlertItem`）。

## 2. 签名

- `_colorize_trend(text: str, value: Decimal | None) -> str`
- `build_take_profit_markdown_batches(items, *, triggered_at, max_bytes=_WECOM_SAFE_MAX_BYTES)`
- `build_buyback_markdown_batches(items, *, triggered_at, max_bytes=_WECOM_SAFE_MAX_BYTES)`

## 3. 契约

- **企业微信字色映射（红涨绿跌）**：企业微信 Markdown `<font color="...">` 仅支持三种色值，本项目统一约定：
  - `warning`（橙红）：上涨、正收益率、正浮盈、正偏离（`value > 0`）
  - `info`（绿）：下跌、负收益率、负浮盈、负偏离（`value < 0`）
  - `comment`（灰）：持平（`value == 0`）、缺失（`value is None`）以及 `[盘中估算]` 辅助标签
- **止盈提醒着色**：`最新价 / 估算净值`（按当前收益率或现价相对成本差判断方向）、`收益`（`current_return_pct`）与 `预计浮盈`（`estimated_pnl`）均经 `_colorize_trend` 着色。
- **买回价提醒着色**：`最新价 / 估算净值`（按较目标买回价偏离或现价相对目标价差判断方向）、目标买回价括号内偏离（`vs_target_pct`）与 `较上卖`（`vs_last_sell_pct`）均经 `_colorize_trend` 着色。

## 4. 校验与状态矩阵

| 数值条件 | 企微 `<font color>` | 展示语义 |
|---|---|---|
| `value > 0` | `warning` | 橙红（涨 / 盈利 / 高于参考价） |
| `value < 0` | `info` | 绿（跌 / 亏损 / 低于参考价） |
| `value == 0` 或 `None` | `comment` | 灰（持平 / 缺失） |
| `[盘中估算]` 标签 | `comment` | 灰（辅助状态标识，不与红色涨跌混淆） |

## 5. 正常、边界与错误示例

- 正常：止盈命中 `+12.00%`、`+¥120.00`、现价高于成本，三项均渲染为 `<font color="warning">...</font>`。
- 正常：买回价命中 `-1.46%`、较上卖 `-5.40%`，均渲染为 `<font color="info">...</font>`。
- 边界：现价恰好等于目标买回价（`0.00%`）时渲染为 `<font color="comment">...</font>`；较上卖为正（`+2.94%`）时渲染为 `<font color="warning">...</font>`。
- 错误：在止盈提醒中写死 `<font color="info">`，导致上涨盈利在企微中显示为绿色。

## 6. 必须测试

- `backend/tests/unit/test_wecom_notifications.py::test_formatter_applies_red_up_green_down_colors` 断言止盈上涨项为 `warning`、买回价下跌项为 `info`、持平项为 `comment`、买回价正偏离为 `warning`。
- 运行 `.venv/bin/ruff check` 与 `.venv/bin/pytest tests/unit/test_wecom_notifications.py tests/integration/test_profit_alert_notifications.py tests/integration/test_watchlist_api.py`。

## 7. 错误与正确写法

```python
# 错误：企微 Markdown 的 info 是绿色，止盈上涨写死 info 会变成绿涨。
f'> 收益：<font color="info">{curr_pct_text}</font>'
```

```python
# 正确：统一通过 _colorize_trend 按数值正负映射红涨绿跌灰平。
f"> 收益：{_colorize_trend(curr_pct_text, item.current_return_pct)}"
```
