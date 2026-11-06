# Backend Directory Structure

## Scope

The backend is a single Python package under backend/app. Keep transport,
application services, domain rules, persistence, and market data in their
existing layers.

## Layout

    app/main.py                 FastAPI app, middleware, health endpoints
    app/api/                    Dependencies, schemas, errors, v1 routes
    app/domain/                 Decimal rules, value objects, enums, exceptions
    app/services/               Ledger, matching, accounting, cycle, target
    app/models/ledger.py        SQLAlchemy tables and relationships
    app/db/                     Engine, sessions, metadata, migrations support
    app/market_data/             Quote providers, calendar, cache, collector
    tests/unit/                  Pure accounting and invariant tests
    tests/integration/           Database and HTTP boundary tests
    alembic/                     Versioned schema migrations

## Module Rules

Put HTTP request/response shapes in app/api/schemas and route composition in
app/api/v1/router.py. Keep business calculations in domain or services so they
can run without FastAPI. Put external quote SDK calls behind app/market_data
providers. Add schema changes as a new Alembic revision.

## Naming

Use lowercase snake_case filenames and singular model classes with plural table
names. Keep route response keys in the public camelCase form while Python
attributes remain snake_case.

## Examples

TradeLedgerService is in app/services/ledger.py, ORM entities are in
app/models/ledger.py, and the quote boundary is split across
app/market_data/provider.py, service.py, and collector.py.
