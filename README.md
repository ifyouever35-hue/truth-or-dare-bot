# 🎲 Правда или Действие — Telegram Bot

Многопользовательский бот для игры «Правда или Действие» с монетизацией через Telegram Stars.

## ✨ Возможности

- 🎮 Создание комнат и вход по коду
- 🔍 Быстрый матчмейкинг (обычный и 18+)
- 🗣 Правда — ответ текстом, голосом или видео-кружком
- ⚡ Действие — подтверждение фото/видео + голосование
- 💎 Verified 18+ подписка через Telegram Stars
- ⭐ Система Stars и откупов
- 📊 Веб-дашборд администратора

## 🛠 Стек

Python 3.11 · aiogram 3 · FastAPI · PostgreSQL · Redis · SQLAlchemy + Alembic · Docker Compose.

---

## 🚀 Деплой на VM (production)

Один контейнер — один процесс, никакого «Docker внутри Docker».

### Шаги

```bash
# 1. Подключись к серверу (Ubuntu 22.04 / 24.04)
ssh user@your-server

# 2. Залей код проекта (любым способом — git clone, scp, rsync...)
git clone https://github.com/your/repo.git truth_or_dare
cd truth_or_dare

# 3. Заполни .env
cp .env.example .env
nano .env
# обязательно: BOT_TOKEN, POSTGRES_PASSWORD, ADMIN_PASSWORD, ADMIN_SECRET_KEY

# 4. Запусти автоустановщик (поставит Docker, соберёт образ, поднимет стек)
sudo bash deploy/setup_server.sh

# 5. (опционально) Настрой публичный HTTPS-домен и переключи на webhook
sudo bash deploy/setup_nginx_ssl.sh yourdomain.com you@example.com
```

После шага 4 бот уже работает в polling-режиме — нужен только токен от
@BotFather и больше ничего. Шаг 5 нужен, только если хочется публичный
HTTPS-эндпоинт (для webhook'а Telegram, доступа к /admin/ снаружи и т.п.).

### Полезные команды

```bash
docker compose ps                # статус
docker compose logs -f app       # логи бота
docker compose restart app       # рестарт после правок .env
docker compose pull && docker compose up -d --build   # обновление
docker compose down              # остановить (данные сохранятся в volumes)
```

### Что куда монтируется

| Путь в контейнере     | Источник на хосте | Зачем                        |
|-----------------------|-------------------|------------------------------|
| `/app/media_storage`  | volume `media_storage` | загруженные пользователями медиа |
| `/app/logs`           | `./logs`          | логи приложения              |
| `/app/backups`        | `./backups`       | бэкапы БД                    |
| `/var/lib/postgresql` | volume `postgres_data` | данные PostgreSQL       |
| `/data` (redis)       | volume `redis_data` | дамп Redis                 |

Volumes пережидают `docker compose down`. Чтобы стереть всё — `docker compose down -v`.

### Порты

По умолчанию `app` слушает только `127.0.0.1:8000` — снаружи доступа нет.
Это специально, чтобы наружу торчал только nginx (после `setup_nginx_ssl.sh`).
Если nginx не нужен и ты хочешь открыть порт наружу — поправь в `docker-compose.yml`:

```yaml
ports:
  - "8000:8000"   # вместо 127.0.0.1:8000:8000
```

---

## 🧪 Локальная разработка

На своём компе (Windows/Mac/Linux) — без публичного домена, бот в polling.

```bash
# Поднять только PostgreSQL и Redis в контейнерах
docker compose -f docker-compose.local.yml up -d

# Запустить бота локально (вне Docker, для удобной отладки)
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# впиши BOT_TOKEN, поменяй POSTGRES_HOST=localhost и REDIS_HOST=localhost
python bot.py
```

`bot.py` — это helper для локалки: он сам поднимает контейнеры с БД/Redis,
проверяет окружение и запускает polling. В Docker-образ он не попадает
(см. `.dockerignore`).

---

## 🔧 Переменные окружения

| Переменная | Обязательно | Описание |
|-----------|:-----------:|----------|
| `BOT_TOKEN` | ✅ | Токен от @BotFather |
| `POSTGRES_PASSWORD` | ✅ | Пароль БД |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | ✅ | Доступ к /admin/ |
| `ADMIN_SECRET_KEY` | ✅ | Случайная строка для cookie-сессий |
| `WEBHOOK_HOST` | — | `https://yourdomain.com`. Пусто = polling-режим |
| `WEBHOOK_SECRET` | — | Секрет, которым Telegram подписывает webhook |
| `STARS_PROVIDER_TOKEN` | — | Для платежей через Stars |

Полный список — в `.env.example`.

---

## 📊 Веб-панель

После деплоя:
- В polling-режиме (без домена): `http://SERVER_IP:8000/admin/` (если открыл порт)
- С доменом: `https://yourdomain.com/admin/`

---

## 👑 Перманентный Premium

Свой Telegram ID → `app/config_premium.py`:

```python
PREMIUM_USER_IDS: set[int] = {
    123456789,  # узнать у @userinfobot
}
```

---

## 📁 Структура

```
main.py                 ← prod-точка входа (FastAPI + aiogram)
bot.py                  ← локальная dev-точка входа (polling + автоконтейнеры)
Dockerfile              ← prod-образ
docker-compose.yml      ← prod-стек (db + redis + app)
docker-compose.local.yml ← только db+redis для локальной разработки
.env.example            ← шаблон конфига

deploy/
  entrypoint.sh         ← старт контейнера: wait-for-db → migrate → seed → uvicorn
  setup_server.sh       ← поставить Docker + поднять стек на чистой VM
  setup_nginx_ssl.sh    ← (опционально) nginx + Let's Encrypt + переключение на webhook

app/
  bot/handlers/         ← Telegram-хендлеры
  bot/keyboards/        ← inline-клавиатуры
  services/             ← бизнес-логика
  database/models.py    ← ORM-модели
  database/migrations/  ← Alembic
  utils/                ← Redis, медиа, планировщик
  admin/                ← FastAPI admin
  config.py             ← Pydantic Settings
  config_premium.py     ← белый список Premium юзеров
scripts/seed_tasks.py   ← пул заданий
```
