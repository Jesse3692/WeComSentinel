FROM node:24-bookworm-slim AS web
WORKDIR /web
RUN npm install -g pnpm@11.25.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache && useradd -m appuser
COPY app/ app/
COPY migrations/ migrations/
COPY alembic.ini ./
COPY --from=web /web/dist/ frontend/dist/
COPY deploy/start.py deploy/start.py
RUN mkdir -p /app/data/media && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["python", "deploy/start.py"]
