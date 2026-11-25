from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
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
from app.api.v1.router import quote_hub, router
from app.config import normalize_log_level, settings
from app.db.migrations import prepare_database_schema
from app.db.session import SessionLocal, engine, healthcheck
from app.domain.exceptions import DomainError
from app.domain.time import BEIJING_TZ
from app.services.auth_bootstrap import ensure_default_admin_and_backfill

logger = logging.getLogger("ashare_ledger.api")


class BeijingFormatter(logging.Formatter):
    """统一以北京时间（Asia/Shanghai）输出日志时间戳的格式器。"""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """将日志记录时间戳格式化为北京时间字符串。"""
        dt = datetime.fromtimestamp(record.created, tz=BEIJING_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="milliseconds")


def configure_logging(log_level: str) -> None:
    """按配置等级统一设置应用与 Uvicorn 命名日志记录器，并默认使用北京时间。"""
    level_name = normalize_log_level(log_level)
    level = getattr(logging, level_name)
    formatter = BeijingFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)
    root_logger.setLevel(level)
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
    """创建并配置 FastAPI 应用实例，并通过 lifespan 管理数据库启动初始化与行情中心生命周期。"""

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        """启动时完成数据库迁移、默认管理员初始化与行情中心启动；停机时优雅刷盘并关闭推流。"""
        if create_tables:
            prepare_database_schema(engine)
            with SessionLocal() as session:
                ensure_default_admin_and_backfill(session, settings.default_admin_email)
                session.commit()
            await quote_hub.start()
        try:
            yield
        finally:
            if create_tables:
                await quote_hub.stop()

    application = FastAPI(title="A-Share Portfolio Journal", version="0.1.0", lifespan=lifespan)

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


def run_server(argv: list[str] | None = None) -> None:
    """使用应用配置中的端口启动 Uvicorn 服务，支持通过命令行参数覆盖监听地址、端口与热重载。"""
    import argparse
    import importlib

    from app.config import _parse_port

    parser = argparse.ArgumentParser(description="启动 Wing Trade 后端服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听主机地址，默认 127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=settings.server_port,
        help="监听端口，默认读取 server.port / SERVER_PORT（回退 12011）",
    )
    parser.add_argument("--reload", action="store_true", help="开启开发热重载模式")
    args = parser.parse_args(argv)
    port = _parse_port(args.port, env_name="SERVER_PORT")

    uvicorn = importlib.import_module("uvicorn")
    reload_dirs = [str(Path(__file__).resolve().parents[1])] if args.reload else None
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=port,
        reload=args.reload,
        reload_dirs=reload_dirs,
    )


if __name__ == "__main__":
    run_server()
