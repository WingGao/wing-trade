FROM python:3.12-slim
ARG PIP_INDEX_URL=https://mirrors.ustc.edu.cn/pypi/simple
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN PIP_INDEX_URL=${PIP_INDEX_URL} pip install --no-cache-dir . uvicorn
ENV DATABASE_URL=sqlite:////app/data/ashare_journal.db
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
