"""Create the initial ledger schema."""

from alembic import op
import sqlalchemy as sa

from app.db.base import ExactDecimal

revision = "0001_initial_ledger_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("broker", sa.String(128)),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "securities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128)),
        sa.Column("security_type", sa.String(32), nullable=False, server_default="STOCK"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("market", "symbol", name="uq_securities_market_symbol"),
    )
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("security_id", sa.String(36), sa.ForeignKey("securities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "security_id", name="uq_watchlist_account_security"),
    )
    op.create_table(
        "strategies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "name", name="uq_strategies_account_name"),
    )
    op.create_table(
        "cycles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("security_id", sa.String(36), sa.ForeignKey("securities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_id", sa.String(36), sa.ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
    )
    op.create_index("ix_cycles_scope_status", "cycles", ["account_id", "security_id", "strategy_id", "status"])
    op.create_table(
        "trades",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("security_id", sa.String(36), sa.ForeignKey("securities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_id", sa.String(36), sa.ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cycle_id", sa.String(36), sa.ForeignKey("cycles.id", ondelete="SET NULL")),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qty", ExactDecimal(4), nullable=False),
        sa.Column("price", ExactDecimal(6), nullable=False),
        sa.Column("gross_amount", ExactDecimal(4), nullable=False),
        sa.Column("commission", ExactDecimal(4), nullable=False),
        sa.Column("stamp_duty", ExactDecimal(4), nullable=False),
        sa.Column("transfer_fee", ExactDecimal(4), nullable=False),
        sa.Column("other_fee", ExactDecimal(4), nullable=False),
        sa.Column("fees_total", ExactDecimal(4), nullable=False),
        sa.Column("external_trade_id", sa.String(128)),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("import_batch_id", sa.String(128)),
        sa.Column("memo", sa.Text),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("client_request_id", sa.String(128)),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "client_request_id", name="uq_trades_account_client_request"),
    )
    op.create_index("ix_trades_scope_executed", "trades", ["account_id", "security_id", "strategy_id", "executed_at"])
    op.create_table(
        "lots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("buy_trade_id", sa.String(36), sa.ForeignKey("trades.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("security_id", sa.String(36), sa.ForeignKey("securities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_id", sa.String(36), sa.ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cycle_id", sa.String(36), sa.ForeignKey("cycles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_qty", ExactDecimal(4), nullable=False),
        sa.Column("original_cost", ExactDecimal(4), nullable=False),
        sa.Column("remaining_qty", ExactDecimal(4), nullable=False),
        sa.Column("remaining_cost", ExactDecimal(4), nullable=False),
        sa.Column("unit_cost", ExactDecimal(6), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("buy_trade_id", name="uq_lots_buy_trade"),
    )
    op.create_index("ix_lots_scope_status", "lots", ["account_id", "security_id", "strategy_id", "status"])
    op.create_table(
        "trade_matches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sell_trade_id", sa.String(36), sa.ForeignKey("trades.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lot_id", sa.String(36), sa.ForeignKey("lots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("matched_qty", ExactDecimal(4), nullable=False),
        sa.Column("allocated_buy_cost", ExactDecimal(4), nullable=False),
        sa.Column("allocated_sell_net", ExactDecimal(4), nullable=False),
        sa.Column("realized_pnl", ExactDecimal(4), nullable=False),
        sa.Column("matching_method", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sell_trade_id", "lot_id", name="uq_trade_matches_sell_lot"),
    )
    op.create_index("ix_trade_matches_sell", "trade_matches", ["sell_trade_id"])
    op.create_index("ix_trade_matches_lot", "trade_matches", ["lot_id"])
    op.create_table(
        "profit_targets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lot_id", sa.String(36), sa.ForeignKey("lots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_return_pct", ExactDecimal(6), nullable=False),
        sa.Column("planned_sell_qty", ExactDecimal(4), nullable=False),
        sa.Column("calculation_basis", sa.String(8), nullable=False),
        sa.Column("target_price_estimate", ExactDecimal(6)),
        sa.Column("alert_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_profit_targets_lot_status", "profit_targets", ["lot_id", "status"])
    op.create_table(
        "quote_cache",
        sa.Column("security_id", sa.String(36), sa.ForeignKey("securities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("last_price", ExactDecimal(6), nullable=False),
        sa.Column("prev_close", ExactDecimal(6)),
        sa.Column("change_pct", ExactDecimal(6)),
        sa.Column("source_time", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("is_stale", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("before_json", sa.Text),
        sa.Column("after_json", sa.Text),
        sa.Column("actor", sa.String(128)),
        sa.Column("reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_entity", "audit_events", ["entity_type", "entity_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("quote_cache")
    op.drop_index("ix_profit_targets_lot_status", table_name="profit_targets")
    op.drop_table("profit_targets")
    op.drop_index("ix_trade_matches_lot", table_name="trade_matches")
    op.drop_index("ix_trade_matches_sell", table_name="trade_matches")
    op.drop_table("trade_matches")
    op.drop_index("ix_lots_scope_status", table_name="lots")
    op.drop_table("lots")
    op.drop_index("ix_trades_scope_executed", table_name="trades")
    op.drop_table("trades")
    op.drop_index("ix_cycles_scope_status", table_name="cycles")
    op.drop_table("cycles")
    op.drop_table("strategies")
    op.drop_table("watchlist_items")
    op.drop_table("securities")
    op.drop_table("accounts")
