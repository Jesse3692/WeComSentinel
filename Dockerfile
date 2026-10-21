FROM node:24-bookworm-slim AS web
WORKDIR /web
RUN npm install -g pnpm@11.25.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && useradd -m appuser
COPY app/ app/
COPY migrations/ migrations/
COPY alembic.ini ./
COPY --from=web /web/dist/ frontend/dist/
COPY deploy/start.py deploy/start.py
RUN mkdir -p /app/data/media && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["python", "deploy/start.py"]
