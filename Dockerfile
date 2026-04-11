FROM python:3.11-slim

# Системные зависимости: ffmpeg для видео, curl для healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала только requirements — кэшируем слой зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Остальной код
COPY . .

# Директория для медиафайлов и логов
RUN mkdir -p /app/media_storage /app/logs

# Делаем entrypoint исполняемым
RUN chmod +x /app/deploy/entrypoint.sh

# Непривилегированный пользователь
RUN useradd --no-create-home --shell /bin/false appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# entrypoint: ждёт БД → alembic upgrade head → uvicorn
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
