"""
Конфигурация приложения
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Настройки приложения"""
    
    # Telegram
    TELEGRAM_BOT_TOKEN: str
    
    # OpenAI
    OPENAI_API_KEY: str
    
    # Database
    DATABASE_URL: str
    DATABASE_URL_SYNC: str
    
    # Redis
    REDIS_URL: str
    
    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    
    # App
    DEBUG: bool = False
    APP_NAME: str = "NutritionBot"
    APP_VERSION: str = "1.0.0"
    SECRET_KEY: str
    
    # Prices (в копейках)
    PRICE_WEEKLY_PLAN: int = 499
    PRICE_UNLIMITED: int = 999
    PRICE_PERSONALIZATION: int = 1499
    
    # Sentry
    SENTRY_DSN: str = ""
    
    # OpenFoodFacts
    OPENFOODFACTS_USER_AGENT: str = "NutritionBot/1.0"
    
    # Limits для бесплатных пользователей
    FREE_RECIPES_LIMIT: int = 3
    FREE_DIARY_DAYS: int = 7
    
    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Получить настройки приложения (с кэшированием)"""
    return Settings()


settings = get_settings()

