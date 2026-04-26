# syntax=docker/dockerfile:1.6
# =============================================================================
# Dockerfile — production-образ Truth or Dare Bot
# Образ запускает FastAPI + aiogram (через main.py), а не локальный bot.py.
# Никакого "Docker внутри Docker" — только сам Python-процесс.
# =============================================================================

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Системные зависимости: ffmpeg для видео, curl для healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала только requirements — кэшируем слой зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения. Что НЕ копировать — указано в .dockerignore.
COPY . .

# Директории под рантайм-данные (volumes монтируются поверх)
RUN mkdir -p /app/media_storage /app/logs /app/backups

# entrypoint исполняемый
RUN chmod +x /app/deploy/entrypoint.sh

# Непривилегированный пользователь
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Healthcheck — FastAPI отвечает на /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# entrypoint: ждёт БД → alembic upgrade head → seed → uvicorn main:app
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
