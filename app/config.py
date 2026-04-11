"""
app/config.py — Централизованная конфигурация.
Все параметры берутся из .env или переменных окружения.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram ──────────────────────────────────────────
    bot_token: str
    webhook_host: str = ""
    webhook_path: str = "/webhook"
    webhook_secret: str = "change_me"

    # ── Database ──────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "truth_or_dare"
    postgres_user: str = "tod_user"
    postgres_password: str = "localpassword123"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Redis ─────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ── Media ─────────────────────────────────────────────
    media_dir: str = "./media_storage"
    max_photo_size_mb: int = 10
    max_video_size_mb: int = 50
    media_retention_days: int = 7

    # ── Admin Panel ───────────────────────────────────────
    admin_secret_key: str = "change_me"
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # ── Payments ──────────────────────────────────────────
    stars_provider_token: str = ""
    wayforpay_merchant_account: str = ""
    wayforpay_secret_key: str = ""
    wayforpay_merchant_domain: str = ""

    # ── Game settings ─────────────────────────────────────
    task_timer_seconds: int = 120
    default_lives: int = 3
    max_lobby_size: int = 10
    max_rounds: int = 10          # 0 = бесконечная игра

    # ── Монетизация (Stars) ───────────────────────────────
    # 1 Star ≈ $0.013 USD
    #
    # СТРАТЕГИЯ: сделать Verified настолько дешёвым, чтобы
    # не было смысла "пиратить" 18+ контент в обычных чатах.
    # Цена = барьер от случайных, но не от желающих заплатить.
    #
    # Откуп: 5 Stars ≈ $0.065 — символично, не жалко
    buyout_cost_stars: int = 5

    # Stars пакеты
    stars_pack_small_price: int = 50      # 50 Stars ≈ $0.65  — стартовый пакет
    stars_pack_medium_price: int = 150    # 150 Stars ≈ $1.95 — популярный
    stars_pack_large_price: int = 350     # 350 Stars ≈ $4.55 — максимум

    # Баланс который зачисляется (с бонусом)
    stars_pack_small_bonus: int = 50
    stars_pack_medium_bonus: int = 170    # +13% бонус
    stars_pack_large_bonus: int = 420     # +20% бонус

    # ── Verified 18+ ──────────────────────────────────────
    # 99 Stars ≈ $1.29/мес — дешевле чашки кофе.
    # Смысл "пиратить" исчезает: проще заплатить.
    verified_stars_price: int = 99
    verified_price_uah: int = 49          # ~$1.29 по курсу
    verified_duration_days: int = 30

    # Скидка при продлении
    verified_renewal_discount: int = 20  # -20 Stars при продлении = 79 Stars


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
