"""企业微信止盈提醒与测试消息 Markdown 渲染器。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.time import BEIJING_TZ

_WECOM_SAFE_MAX_BYTES = 3800


@dataclass(frozen=True, slots=True)
class TakeProfitAlertItem:
    """单笔命中止盈目标的完整通知上下文。"""

    user_id: str
    account_name: str
    security_name: str
    canonical_symbol: str
    market: str
    currency: str
    lot_id: str
    explicit_target_id: str | None
    target_key: str
    target_signature: str
    target_source: str
    calculation_basis: str
    unit_cost: Decimal
    planned_qty: Decimal
    last_price: Decimal
    target_price: Decimal
    current_return_pct: Decimal | None
    target_return_pct: Decimal
    estimated_pnl: Decimal
    is_estimated: bool


@dataclass(frozen=True, slots=True)
class BuybackAlertItem:
    """单条命中目标买回价的关注提醒上下文。"""

    user_id: str
    account_id: str
    account_name: str
    security_id: str
    watchlist_item_id: str
    security_name: str
    canonical_symbol: str
    market: str
    currency: str
    target_signature: str
    last_price: Decimal
    target_price: Decimal
    vs_target_pct: Decimal | None
    last_sell_price: Decimal | None
    vs_last_sell_pct: Decimal | None
    has_open_position: bool
    note: str | None
    is_estimated: bool


def _currency_symbol(currency: str) -> str:
    """返回币种展示符号。"""
    return "$" if currency.upper() == "USD" else "¥"


def _fmt_price(value: Decimal, *, market: str) -> str:
    """按市场习惯格式化价格或基金净值展示精度。"""
    digits = 4 if market.upper() == "OF" else 3
    text = format(value, f".{digits}f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
        if "." not in text:
            return format(value, ".2f")
        decimals = len(text.split(".", 1)[1])
        if decimals < 2:
            return format(value, ".2f")
    return text


def _fmt_qty(value: Decimal, *, market: str) -> str:
    """格式化股数或基金份额数量。"""
    if market.upper() == "OF":
        text = format(value, ".2f")
        return f"{text} 份"
    text = format(value, ".4f").rstrip("0").rstrip(".")
    return f"{text} 股"


def _fmt_rate_pct(rate: Decimal | None) -> str:
    """将小数收益率（如 0.125）格式化为带符号百分数（+12.50%）。"""
    if rate is None:
        return "--"
    pct = rate * Decimal("100")
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.2f}%"


def _fmt_percent_value(pct: Decimal | None) -> str:
    """将已乘以 100 的百分数（如 -1.4085）格式化为带符号百分数（-1.41%）。"""
    if pct is None:
        return "--"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.2f}%"


def _fmt_money(amount: Decimal, currency: str) -> str:
    """格式化带币种与正负号的金额字符串。"""
    sign = "+" if amount > 0 else ""
    return f"{sign}{_currency_symbol(currency)}{amount:,.2f}"


def _source_label(source: str, basis: str) -> str:
    """返回止盈来源与计算口径的简明中文说明。"""
    source_map = {"LOT": "批次目标", "SECURITY": "个股默认", "GLOBAL": "全局默认"}
    basis_label = "净收益" if basis.upper() == "NET" else "毛收益"
    return f"{source_map.get(source.upper(), source)} · {basis_label}"


def _colorize_trend(text: str, value: Decimal | None) -> str:
    """按红涨绿跌着色（正数 warning 橙红，负数 info 绿，零或空 comment 灰）。"""
    if value is None or value == 0:
        return f'<font color="comment">{text}</font>'
    color = "warning" if value > 0 else "info"
    return f'<font color="{color}">{text}</font>'


def _render_item_block(index: int, item: TakeProfitAlertItem) -> str:
    """将单笔止盈命中项渲染为紧凑易读的企微 Markdown 段落。"""
    est_badge = ' <font color="comment">[盘中估算]</font>' if item.is_estimated else ""
    price_label = "估算净值" if item.is_estimated else "最新价"
    unit_cost_text = _fmt_price(item.unit_cost, market=item.market)
    last_price_text = _fmt_price(item.last_price, market=item.market)
    target_price_text = _fmt_price(item.target_price, market=item.market)
    qty_text = _fmt_qty(item.planned_qty, market=item.market)
    curr_pct_text = _fmt_rate_pct(item.current_return_pct)
    target_pct_text = _fmt_rate_pct(item.target_return_pct)
    pnl_text = _fmt_money(item.estimated_pnl, item.currency)
    source_text = _source_label(item.target_source, item.calculation_basis)
    price_trend = (
        item.current_return_pct
        if item.current_return_pct is not None
        else item.last_price - item.unit_cost
    )
    colored_last_price = _colorize_trend(last_price_text, price_trend)
    colored_curr_pct = _colorize_trend(curr_pct_text, item.current_return_pct)
    colored_pnl = _colorize_trend(pnl_text, item.estimated_pnl)
    return (
        f"**{index}. {item.security_name}（{item.canonical_symbol}）**{est_badge}\n"
        f"> 账户：{item.account_name} | 数量：{qty_text}\n"
        f"> 成本：{unit_cost_text} | {price_label}："
        f"{colored_last_price} | 目标：{target_price_text}\n"
        f"> 收益：{colored_curr_pct}"
        f"（目标 {target_pct_text}）| 预计浮盈：{colored_pnl}\n"
        f"> 规则：{source_text}"
    )


def build_take_profit_markdown_batches(
    items: Sequence[TakeProfitAlertItem],
    *,
    triggered_at: datetime,
    max_bytes: int = _WECOM_SAFE_MAX_BYTES,
) -> list[tuple[list[TakeProfitAlertItem], str]]:
    """将同一用户的多笔止盈命中分批渲染为企微 Markdown，并保留每批对应的命中项列表。"""
    if not items:
        return []
    ts = triggered_at.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    total = len(items)
    rendered_pairs = [(item, _render_item_block(idx + 1, item)) for idx, item in enumerate(items)]

    def _build_header(part: int | None = None, total_parts: int | None = None) -> str:
        """构造卡片标题头部。"""
        suffix = f"（{part}/{total_parts}）" if part is not None and total_parts is not None else ""
        return f"### 止盈提醒 · 命中 {total} 笔{suffix}\n> 时间：{ts}（北京时间）\n\n"

    # 先按单批贪心分桶，保证每条消息 UTF-8 字节数不超过安全阈值。
    raw_groups: list[list[tuple[TakeProfitAlertItem, str]]] = []
    current_group: list[tuple[TakeProfitAlertItem, str]] = []
    header_reserve = len(_build_header(99, 99).encode("utf-8"))
    current_bytes = header_reserve

    for pair in rendered_pairs:
        block_bytes = len((pair[1] + "\n\n").encode("utf-8"))
        if current_group and current_bytes + block_bytes > max_bytes:
            raw_groups.append(current_group)
            current_group = [pair]
            current_bytes = header_reserve + block_bytes
        else:
            current_group.append(pair)
            current_bytes += block_bytes
    if current_group:
        raw_groups.append(current_group)

    total_parts = len(raw_groups)
    batches: list[tuple[list[TakeProfitAlertItem], str]] = []
    for part_idx, group in enumerate(raw_groups, start=1):
        header = (
            _build_header(part_idx, total_parts)
            if total_parts > 1
            else _build_header()
        )
        batch_items = [item for item, _ in group]
        batch_text = header + "\n\n".join(block for _, block in group)
        batches.append((batch_items, batch_text))
    return batches


def format_take_profit_markdown_batches(
    items: Sequence[TakeProfitAlertItem],
    *,
    triggered_at: datetime,
    max_bytes: int = _WECOM_SAFE_MAX_BYTES,
) -> list[str]:
    """将同一用户的多笔止盈命中聚合为一条或多条不超过企微字节上限的 Markdown 卡片。"""
    return [
        content
        for _, content in build_take_profit_markdown_batches(
            items, triggered_at=triggered_at, max_bytes=max_bytes
        )
    ]


def _render_buyback_block(index: int, item: BuybackAlertItem) -> str:
    """将单条买回价命中项渲染为紧凑易读的企微 Markdown 段落。"""
    est_badge = ' <font color="comment">[盘中估算]</font>' if item.is_estimated else ""
    price_label = "估算净值" if item.is_estimated else "最新价"
    status_label = "持仓中（低位加仓参考）" if item.has_open_position else "空仓待回接"
    last_price_text = _fmt_price(item.last_price, market=item.market)
    target_price_text = _fmt_price(item.target_price, market=item.market)
    vs_target_text = _fmt_percent_value(item.vs_target_pct)
    price_trend = (
        item.vs_target_pct
        if item.vs_target_pct is not None
        else item.last_price - item.target_price
    )
    colored_last_price = _colorize_trend(last_price_text, price_trend)
    colored_vs_target = _colorize_trend(vs_target_text, item.vs_target_pct)
    lines = [
        f"**{index}. {item.security_name}（{item.canonical_symbol}）**{est_badge}",
        f"> 账户：{item.account_name} | 状态：{status_label}",
        f"> {price_label}：{colored_last_price}"
        f" | 目标买回价：{target_price_text}（{colored_vs_target}）",
    ]
    if item.last_sell_price is not None:
        last_sell_text = _fmt_price(item.last_sell_price, market=item.market)
        vs_sell_text = _colorize_trend(
            _fmt_percent_value(item.vs_last_sell_pct),
            item.vs_last_sell_pct,
        )
        lines.append(f"> 上次卖出：{last_sell_text} | 较上卖：{vs_sell_text}")
    if item.note:
        lines.append(f"> 备注：{item.note}")
    return "\n".join(lines)


def build_buyback_markdown_batches(
    items: Sequence[BuybackAlertItem],
    *,
    triggered_at: datetime,
    max_bytes: int = _WECOM_SAFE_MAX_BYTES,
) -> list[tuple[list[BuybackAlertItem], str]]:
    """将同一用户的多条买回价命中分批渲染为不超过企微上限的 Markdown 卡片。"""
    if not items:
        return []
    ts = triggered_at.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    total = len(items)
    rendered_pairs = [
        (item, _render_buyback_block(idx + 1, item)) for idx, item in enumerate(items)
    ]

    def _build_header(part: int | None = None, total_parts: int | None = None) -> str:
        """构造买回价提醒卡片标题头部。"""
        suffix = f"（{part}/{total_parts}）" if part is not None and total_parts is not None else ""
        return f"### 买回价提醒 · 命中 {total} 项{suffix}\n> 时间：{ts}（北京时间）\n\n"

    raw_groups: list[list[tuple[BuybackAlertItem, str]]] = []
    current_group: list[tuple[BuybackAlertItem, str]] = []
    header_reserve = len(_build_header(99, 99).encode("utf-8"))
    current_bytes = header_reserve

    for pair in rendered_pairs:
        block_bytes = len((pair[1] + "\n\n").encode("utf-8"))
        if current_group and current_bytes + block_bytes > max_bytes:
            raw_groups.append(current_group)
            current_group = [pair]
            current_bytes = header_reserve + block_bytes
        else:
            current_group.append(pair)
            current_bytes += block_bytes
    if current_group:
        raw_groups.append(current_group)

    total_parts = len(raw_groups)
    batches: list[tuple[list[BuybackAlertItem], str]] = []
    for part_idx, group in enumerate(raw_groups, start=1):
        header = (
            _build_header(part_idx, total_parts)
            if total_parts > 1
            else _build_header()
        )
        batch_items = [item for item, _ in group]
        batch_text = header + "\n\n".join(block for _, block in group)
        batches.append((batch_items, batch_text))
    return batches


def format_test_markdown(*, user_label: str, sent_at: datetime) -> str:
    """构造企业微信配置测试消息 Markdown 内容。"""
    ts = sent_at.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return (
        "### 止盈推送通道测试成功\n"
        f"> 账号：{user_label}\n"
        f"> 时间：{ts}（北京时间）\n"
        "> 说明：企业微信群机器人 Webhook 配置正常，盘中达到止盈目标时将通过此通道提醒。"
    )
