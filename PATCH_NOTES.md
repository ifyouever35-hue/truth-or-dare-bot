# PATCH NOTES — Truth or Dare bot

Дата патча: 2026-04-08
Применено автоматически по результатам статического анализа.

## Что изменилось

### 🚨 Критические исправления

#### #1 — Timezone-aware datetime (16 мест в 9 файлах)
**Проблема:** все DateTime-колонки моделей объявлены как `DateTime(timezone=True)`,
но повсюду использовался `datetime.utcnow()`, который возвращает naive datetime.
Это приводит к `TypeError: can't compare offset-naive and offset-aware datetimes`
при сравнениях, и DeprecationWarning в Python 3.12+ (в 3.13+ функция удалена).

**Фикс:** замена `datetime.utcnow()` → `datetime.now(timezone.utc)` + добавление
`timezone` в импорты `from datetime import …` в каждом затронутом файле.

**Файлы:**
- `app/utils/media.py` (2)
- `app/utils/scheduler.py` (3)
- `app/database/models.py` (1, метод `is_verification_active`)
- `app/bot/handlers/payment.py` (2)
- `app/bot/handlers/game.py` (1)
- `app/admin/routes/dashboard.py` (1)
- `app/admin/routes/webhooks.py` (1)
- `app/services/lobby_service.py` (1)
- `app/services/user_service.py` (3)

#### #2 — Двойной бонус в сообщении о покупке Stars-пакета
**Проблема:** `payment.py:successful_payment_handler` после вызова
`add_stars(db, user, bonus)` (который делает `user.stars_balance += amount`)
показывал пользователю `f"Текущий баланс: {user.stars_balance + bonus}"` —
с двойным учётом бонуса.

**Фикс:** убрано `+ bonus` из f-строки.

#### #3 — Двойное вычитание в сообщении об откупе
**Проблема:** `game.py:cb_buyout_confirm` после вызова `buyout_task`, который
вызывает `deduct_stars` (`user.stars_balance -= amount`), показывал
`f"Остаток: {user.stars_balance - settings.buyout_cost_stars}"` — с двойным
вычетом.

**Фикс:** убрано `- settings.buyout_cost_stars` из f-строки.

#### #4 — Idempotency check на платежах
**Проблема:** `successful_payment_handler` не проверял, был ли уже обработан
`telegram_payment_charge_id`. При повторном webhook от Telegram (что бывает)
пользователь получал Stars / Verified дважды.

**Фикс:** добавлен SELECT по `Payment.telegram_payment_charge_id` в самом начале
обработчика. Если запись уже существует — return без действий.

### 🔧 Стилистика

#### #7 — Хак `__import__("sqlalchemy").text(...)` в main.py
**Фикс:** добавлен нормальный `from sqlalchemy import text` в импорты,
вызов заменён на `text("SELECT 1")`.

## Что НЕ исправлено в этом патче (требует решения)

### #5 — Логика `_advance_turn` (game_service.py)
Индексирование по позиции в массиве активных игроков может сбоить при выбывании
игрока в середине партии. Требует рефакторинга на работу через user_id.
**Не критично для запуска**, но требует ручной проверки в smoke-тестах.

### #6 — `current_round` инкрементится на каждый ход
По смыслу это «номер хода», а не «номер раунда». UX-наименование, не баг.
Можно либо переименовать колонку в `current_turn`, либо считать круги
по формуле `(turns_count // active_players_count) + 1`.

### #8 — `.env.example` неполный
8 ключей из `config.py` отсутствуют в `.env.example`:
`STARS_PACK_SMALL_PRICE`, `STARS_PACK_MEDIUM_PRICE`, `STARS_PACK_LARGE_PRICE`,
`STARS_PACK_SMALL_BONUS`, `STARS_PACK_MEDIUM_BONUS`, `STARS_PACK_LARGE_BONUS`,
`VERIFIED_RENEWAL_DISCOUNT`, `VERIFIED_STARS_PRICE`.
Все имеют дефолты — не блокирует запуск, но `.env.example` нужно дополнить.

Также в `.env.example` устарело: `BUYOUT_COST_STARS=5`, в `config.py` дефолт `15`.

### #9 — Локальные импорты внутри функций
В нескольких местах (`game.py`, `scheduler.py`) импорты внутри тел функций.
Иногда нужно для разрыва циклических импортов, но в большинстве случаев —
просто стилистика. Можно вынести наверх при следующем рефакторинге.

### #10 — `.env` с реальными секретами в архиве
**Действие требуется от тебя:** убедись, что `.env` и `.env.local` в `.gitignore`,
и не закоммичены в `.git/`. Если коммитил — поменяй BOT_TOKEN, пароли БД и
admin_password.

### #11 — Дубль папки `truth_or_dare/`
Удалена в этом архиве. У тебя на машине удали тоже.

## Чек-лист после применения патча

- [x] Синтаксис всех 44 файлов чистый
- [x] Внутренние импорты резолвятся
- [x] FSM-состояния согласованы
- [x] Callback-кнопки имеют хендлеры
- [x] Дубликатов хендлеров нет
- [x] `datetime.utcnow()` нет нигде
- [x] `timezone` импортирован во всех файлах с datetime
- [ ] Прогнать `pytest` локально (не могу из-за отсутствия зависимостей в моей песочнице)
- [ ] Smoke-тест на твоей машине: создать лобби, выбрать задание, выполнить с медиа, откуп, покупка Verified
- [ ] Деплой
