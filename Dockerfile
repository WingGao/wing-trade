FROM node:22-alpine AS frontend-build
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV COREPACK_NPM_REGISTRY=${NPM_REGISTRY}
ENV npm_config_registry=${NPM_REGISTRY}
WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile --registry=${NPM_REGISTRY}
COPY frontend/ ./
RUN pnpm run build

FROM python:3.12-slim
ARG PIP_INDEX_URL=https://mirrors.ustc.edu.cn/pypi/simple
ARG PIP_INDEX_URL=https://mirrors.ustc.edu.cn/pypi/simple
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
RUN PIP_INDEX_URL=${PIP_INDEX_URL} pip install --no-cache-dir . uvicorn
COPY --from=frontend-build /frontend/dist /app/static
ENV DATABASE_SQLITE_PATH=/app/data/ashare_journal.db
ENV FRONTEND_DIST_DIR=/app/static
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
