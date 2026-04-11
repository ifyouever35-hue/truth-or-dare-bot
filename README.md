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
- 🔒 Анонимная игра — имена не раскрываются

## 🛠 Стек

- Python 3.11+ / 3.14
- aiogram 3.x
- PostgreSQL + Redis
- FastAPI (admin панель)
- SQLAlchemy async + Alembic
- Docker Compose

## 🚀 Запуск

### Требования
- Python 3.11+
- Docker Desktop

### Быстрый старт

```bash
# 1. Клонируй репозиторий
git clone https://github.com/ВАШ_НИК/truth-or-dare-bot.git
cd truth-or-dare-bot

# 2. Скопируй и заполни конфиг
copy .env.example .env      # Windows
cp .env.example .env        # Linux/Mac

# Обязательно: вставь BOT_TOKEN от @BotFather
# Придумай POSTGRES_PASSWORD и ADMIN_PASSWORD

# 3. Запускай
python bot.py
```

### Переменные окружения

| Переменная | Описание |
|-----------|----------|
| `BOT_TOKEN` | Токен от @BotFather |
| `POSTGRES_PASSWORD` | Пароль БД (придумай любой) |
| `ADMIN_USERNAME` | Логин веб-панели |
| `ADMIN_PASSWORD` | Пароль веб-панели |
| `WAYFORPAY_*` | Опционально для UAH оплаты |

## 📊 Веб-панель

После запуска: **http://localhost:8000/admin/**

## 👑 Перманентный Premium

Добавь свой Telegram ID в `app/config_premium.py`:

```python
PREMIUM_USER_IDS: set[int] = {
    123456789,  # твой ID (@userinfobot)
}
```

## 📁 Структура

```
bot.py                  ← Точка входа
app/
  bot/handlers/         ← Telegram хендлеры
  bot/keyboards/        ← Inline клавиатуры
  services/             ← Бизнес-логика
  database/models.py    ← ORM модели
  utils/                ← Redis, медиа, планировщик
  admin/                ← Веб-панель
  config_premium.py     ← Перманентные Premium юзеры
scripts/seed_tasks.py   ← 245 заданий
```
