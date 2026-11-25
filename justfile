set dotenv-load

default: dev

# Start the backend and frontend development servers together.
[parallel]
dev: backend frontend

# FastAPI development server (port from config.yaml server.port or SERVER_PORT, default 12011).
backend:
    uv run --cache-dir .uv-cache --project backend --with "uvicorn[standard]>=0.30,<1.0" python -m app.main --reload

# Vite development server on http://localhost:5173.
frontend:
    pnpm --dir frontend run dev

# Run the main local checks.
check:
    uv run --cache-dir .uv-cache --project backend python -m compileall -q backend/app backend/tests
    pnpm --dir frontend run type-check
