from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError

from app.api.errors import (
    domain_error_handler,
    generic_error_handler,
    integrity_error_handler,
    validation_error_handler,
)
from app.api.v1.router import router
from app.db.migrations import prepare_database_schema
from app.db.session import engine, healthcheck
from app.domain.exceptions import DomainError

logger = logging.getLogger("ashare_ledger.api")


def create_app(*, create_tables: bool = True) -> FastAPI:
    application = FastAPI(title="A-Share Portfolio Journal", version="0.1.0")

    @application.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed", extra={"request_id": request_id, "path": request.url.path}
            )
            raise
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["x-request-id"] = request_id
        logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": elapsed_ms,
            },
        )
        return response

    @application.on_event("startup")
    def startup() -> None:
        if create_tables:
            prepare_database_schema(engine)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/ready")
    def ready() -> dict[str, str]:
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            if not healthcheck(session):
                raise RuntimeError("database is unavailable")
        return {"status": "ready"}

    application.include_router(router)
    application.add_exception_handler(DomainError, domain_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(IntegrityError, integrity_error_handler)
    application.add_exception_handler(Exception, generic_error_handler)
    return application


app = create_app()
