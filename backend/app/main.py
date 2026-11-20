from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError

from app.api.errors import (
    domain_error_handler,
    generic_error_handler,
    integrity_error_handler,
    validation_error_handler,
)
from app.api.v1.router import router
from app.config import normalize_log_level, settings
from app.db.migrations import prepare_database_schema
from app.db.session import SessionLocal, engine, healthcheck
from app.domain.exceptions import DomainError
from app.services.auth_bootstrap import ensure_default_admin_and_backfill

logger = logging.getLogger("ashare_ledger.api")


def configure_logging(log_level: str) -> None:
    """按配置等级统一设置应用与 Uvicorn 命名日志记录器。"""
    level_name = normalize_log_level(log_level)
    level = getattr(logging, level_name)
    logging.basicConfig(level=level)
    logging.getLogger().setLevel(level)
    for logger_name in ("ashare_ledger", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(logger_name).setLevel(level)


configure_logging(settings.log_level)


def _mount_frontend(application: FastAPI, dist_dir: str) -> None:
    """在存在前端构建产物时，由 FastAPI 直接托管静态资源并提供 SPA 路由回退。"""
    normalized = dist_dir.strip()
    if not normalized:
        return
    root = Path(normalized).resolve()
    index_file = root / "index.html"
    if not root.is_dir() or not index_file.is_file():
        logger.warning("frontend dist dir is missing index.html, skipping static mount: %s", root)
        return

    @application.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def serve_frontend(full_path: str) -> FileResponse:
        """优先返回前端静态文件，非 API 路径则回退到 index.html 支持前端 History 路由。"""
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index_file)


def create_app(*, create_tables: bool = True, frontend_dist_dir: str | None = None) -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
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
        """启动时完成数据库迁移、默认管理员初始化及存量数据无损回填。"""
        if create_tables:
            prepare_database_schema(engine)
            with SessionLocal() as session:
                ensure_default_admin_and_backfill(session, settings.default_admin_email)
                session.commit()

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
    resolved_dist_dir = (
        settings.frontend_dist_dir if frontend_dist_dir is None else frontend_dist_dir
    )
    _mount_frontend(application, resolved_dist_dir)
    return application


app = create_app()
