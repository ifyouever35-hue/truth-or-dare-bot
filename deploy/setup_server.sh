#!/bin/bash
# =============================================================================
# deploy/setup_server.sh — Полная настройка сервера с нуля
#
# Что делает скрипт:
#   1. Устанавливает Docker, Docker Compose, nginx, certbot
#   2. Получает бесплатный SSL-сертификат от Let's Encrypt
#   3. Настраивает nginx как reverse proxy перед нашим приложением
#   4. Настраивает автообновление сертификата (раз в 90 дней)
#   5. Запускает приложение через docker compose
#   6. Применяет миграции Alembic
#   7. Заполняет пул заданий
#
# Использование:
#   chmod +x deploy/setup_server.sh
#   sudo ./deploy/setup_server.sh yourdomain.com your@email.com
#
# Требования:
#   - Ubuntu 22.04 или 24.04
#   - Пользователь с sudo
#   - DNS A-запись yourdomain.com → IP этого сервера уже настроена
#   - .env файл лежит рядом со скриптом
# =============================================================================

set -euo pipefail   # Останавливаемся при любой ошибке

# ── Аргументы ─────────────────────────────────────────────────────────────────
DOMAIN="${1:?Укажи домен: ./setup_server.sh yourdomain.com your@email.com}"
EMAIL="${2:?Укажи email для Let's Encrypt}"
APP_DIR="/opt/truth_or_dare"

echo "=============================================="
echo " Truth or Dare Bot — Server Setup"
echo " Domain : $DOMAIN"
echo " Email  : $EMAIL"
echo " App dir: $APP_DIR"
echo "=============================================="
echo ""

# ── 1. Обновляем систему ──────────────────────────────────────────────────────
echo "→ [1/8] Обновление пакетов..."
apt-get update -qq
apt-get upgrade -y -qq

# ── 2. Docker ─────────────────────────────────────────────────────────────────
echo "→ [2/8] Установка Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker "$SUDO_USER" 2>/dev/null || true
    echo "✓ Docker установлен"
else
    echo "✓ Docker уже установлен ($(docker --version))"
fi

# Docker Compose plugin
if ! docker compose version &>/dev/null; then
    apt-get install -y docker-compose-plugin
fi
echo "✓ Docker Compose: $(docker compose version --short)"

# ── 3. nginx + certbot ────────────────────────────────────────────────────────
echo "→ [3/8] Установка nginx и certbot..."
apt-get install -y -qq nginx certbot python3-certbot-nginx

# ── 4. Временный nginx конфиг для прохождения ACME-проверки ──────────────────
echo "→ [4/8] Настройка временного nginx для получения SSL..."
cat > /etc/nginx/sites-available/tod_temp <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    # Let's Encrypt ACME challenge
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}
EOF

ln -sf /etc/nginx/sites-available/tod_temp /etc/nginx/sites-enabled/tod_temp
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
echo "✓ Временный nginx запущен"

# ── 5. Получаем SSL-сертификат ────────────────────────────────────────────────
echo "→ [5/8] Получение SSL сертификата от Let's Encrypt..."
certbot certonly \
    --nginx \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    --domains "$DOMAIN"

echo "✓ Сертификат получен: /etc/letsencrypt/live/$DOMAIN/"

# Автообновление через systemd timer (лучше, чем cron)
systemctl enable certbot.timer
systemctl start certbot.timer
echo "✓ Автообновление сертификата включено"

# ── 6. Финальный nginx конфиг ─────────────────────────────────────────────────
echo "→ [6/8] Настройка production nginx конфига..."
cat > /etc/nginx/sites-available/truth_or_dare <<NGINX_EOF
# Редирект HTTP → HTTPS
server {
    listen 80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

# Основной HTTPS сервер
server {
    listen 443 ssl http2;
    server_name $DOMAIN;

    # SSL сертификат от Let's Encrypt
    ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    # Современные SSL настройки
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    # Защита от некоторых атак
    add_header Strict-Transport-Security "max-age=63072000" always;
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;

    # Макс размер тела запроса — для медиафайлов до 50 МБ
    client_max_body_size 60M;

    # Логи
    access_log /var/log/nginx/tod_access.log;
    error_log  /var/log/nginx/tod_error.log;

    # ── Telegram Webhook ─────────────────────────────────────────────────────
    # Telegram шлёт сюда все апдейты от пользователей
    location /webhook {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
    }

    # ── Платёжные webhook'и (WayForPay) ──────────────────────────────────────
    location /api/ {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_read_timeout 30s;
    }

    # ── Админ панель ──────────────────────────────────────────────────────────
    # ВАЖНО: замените 0.0.0.0/0 на ваш реальный IP чтобы закрыть доступ снаружи
    location /admin {
        # Раскомментируйте и впишите свой IP:
        # allow 1.2.3.4;
        # deny all;

        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_read_timeout 60s;
    }

    # ── Health check (публичный) ──────────────────────────────────────────────
    location /health {
        proxy_pass http://127.0.0.1:8000;
        access_log off;
    }

    # ── Prometheus метрики — ТОЛЬКО локально, наружу не пропускаем ───────────
    location /metrics {
        deny all;
        return 403;
    }
}
NGINX_EOF

# Удаляем временный конфиг, включаем финальный
rm -f /etc/nginx/sites-enabled/tod_temp
ln -sf /etc/nginx/sites-available/truth_or_dare /etc/nginx/sites-enabled/truth_or_dare

nginx -t
systemctl reload nginx
echo "✓ Production nginx конфиг применён"

# ── 7. Копируем приложение и запускаем ───────────────────────────────────────
echo "→ [7/8] Запуск приложения..."
mkdir -p "$APP_DIR"

# Если запускаем не из директории проекта — копируем
if [ "$(pwd)" != "$APP_DIR" ]; then
    cp -r . "$APP_DIR/"
fi

cd "$APP_DIR"

# Проверяем .env
if [ ! -f .env ]; then
    echo "❌ Файл .env не найден в $APP_DIR"
    echo "   Скопируйте .env.example в .env и заполните все значения"
    exit 1
fi

# Обновляем WEBHOOK_HOST в .env
if grep -q "^WEBHOOK_HOST=" .env; then
    sed -i "s|^WEBHOOK_HOST=.*|WEBHOOK_HOST=https://$DOMAIN|" .env
else
    echo "WEBHOOK_HOST=https://$DOMAIN" >> .env
fi

docker compose pull
docker compose up -d --build
echo "✓ Контейнеры запущены"

# Ждём пока PostgreSQL будет готов
echo "   Ожидание PostgreSQL..."
sleep 5
for i in {1..30}; do
    if docker compose exec -T db pg_isready -q; then
        echo "✓ PostgreSQL готов"
        break
    fi
    sleep 2
done

# ── 8. Миграции и seed ────────────────────────────────────────────────────────
echo "→ [8/8] Применение миграций и заполнение данных..."

# Alembic upgrade head
docker compose exec -T app alembic upgrade head
echo "✓ Миграции применены"

# Seed начальных заданий (пропустит если задания уже есть)
docker compose exec -T app python scripts/seed_tasks.py
echo "✓ Задания заполнены"

# ── Финальная проверка ────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo " ✅ Установка завершена!"
echo "=============================================="
echo ""
echo " Бот запущен и доступен по адресу:"
echo "   https://$DOMAIN"
echo ""
echo " Проверить работу:"
echo "   curl https://$DOMAIN/health"
echo ""
echo " Админ панель:"
echo "   https://$DOMAIN/admin/"
echo "   Логин/пароль: из ADMIN_USERNAME / ADMIN_PASSWORD в .env"
echo ""
echo " Grafana (мониторинг):"
echo "   http://$(hostname -I | awk '{print $1}'):3000"
echo ""
echo " Просмотр логов бота:"
echo "   docker compose -f $APP_DIR/docker-compose.yml logs -f app"
echo ""
echo " Следующие шаги:"
echo "   1. Откройте @BotFather → /setwebhook (делается автоматически при старте)"
echo "   2. Откройте Admin панель и проверьте задания"
echo "   3. Укажите свой IP в nginx конфиге для защиты /admin"
echo "   4. Настройте Grafana дашборд"
echo ""
