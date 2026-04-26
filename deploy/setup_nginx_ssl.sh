#!/bin/bash
# =============================================================================
# deploy/setup_nginx_ssl.sh — опциональная настройка nginx + Let's Encrypt.
#
# Запускать ТОЛЬКО если нужен публичный HTTPS-домен (для webhook'а Telegram
# или внешнего доступа к админке). Без этого бот прекрасно работает
# в polling-режиме (см. WEBHOOK_HOST в .env).
#
# Использование:
#   sudo bash deploy/setup_nginx_ssl.sh yourdomain.com you@example.com
#
# Что делает:
#   1. apt install nginx + certbot
#   2. Получает SSL-сертификат
#   3. Кладёт production nginx-конфиг (proxy_pass на 127.0.0.1:8000)
#   4. Прописывает WEBHOOK_HOST=https://yourdomain.com в .env
#   5. Перезапускает контейнер app, чтобы он зарегистрировал webhook
#
# Требования:
#   - Контейнеры уже запущены (deploy/setup_server.sh выполнен)
#   - DNS A-запись yourdomain.com → IP сервера
#   - Порты 80 и 443 открыты в firewall
# =============================================================================

set -euo pipefail

DOMAIN="${1:?Укажи домен: sudo bash deploy/setup_nginx_ssl.sh yourdomain.com you@example.com}"
EMAIL="${2:?Укажи email для Lets Encrypt}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Запусти через sudo."
    exit 1
fi

if [ ! -f .env ]; then
    echo ".env не найден — сначала выполни deploy/setup_server.sh."
    exit 1
fi

echo "==> Установка nginx и certbot..."
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx

echo "==> Временный nginx-конфиг для ACME-проверки..."
cat > /etc/nginx/sites-available/tod_temp <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}
EOF
ln -sf /etc/nginx/sites-available/tod_temp /etc/nginx/sites-enabled/tod_temp
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> Получение SSL-сертификата..."
certbot certonly --nginx --non-interactive --agree-tos --email "$EMAIL" --domains "$DOMAIN"
systemctl enable certbot.timer
systemctl start certbot.timer

echo "==> Production nginx-конфиг..."
cat > /etc/nginx/sites-available/truth_or_dare <<NGINX_EOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    add_header Strict-Transport-Security "max-age=63072000" always;
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;

    client_max_body_size 60M;

    access_log /var/log/nginx/tod_access.log;
    error_log  /var/log/nginx/tod_error.log;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }

    # Метрики Prometheus наружу не отдаём
    location /metrics { deny all; return 403; }
}
NGINX_EOF

rm -f /etc/nginx/sites-enabled/tod_temp
ln -sf /etc/nginx/sites-available/truth_or_dare /etc/nginx/sites-enabled/truth_or_dare
nginx -t
systemctl reload nginx

echo "==> Прописываю WEBHOOK_HOST в .env..."
if grep -q "^WEBHOOK_HOST=" .env; then
    sed -i "s|^WEBHOOK_HOST=.*|WEBHOOK_HOST=https://$DOMAIN|" .env
else
    echo "WEBHOOK_HOST=https://$DOMAIN" >> .env
fi

echo "==> Перезапускаю контейнер app, чтобы зарегистрировать webhook..."
docker compose up -d --no-deps --force-recreate app

echo ""
echo "✅ Готово. Бот доступен на https://$DOMAIN"
echo "   Проверка:  curl https://$DOMAIN/health"
echo "   Админка:   https://$DOMAIN/admin/"
