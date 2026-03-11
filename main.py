"""
Главный файл запуска приложения
"""
import asyncio
import logging
from bot.bot import start_bot
from database import init_db
from services.redis_service import redis_service
import sentry_sdk
from config import settings

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO if not settings.DEBUG else logging.DEBUG
)
logger = logging.getLogger(__name__)

# Инициализация Sentry
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=1.0,
    )
    logger.info("Sentry initialized")


async def main():
    """Главная функция запуска приложения"""
    try:
        # Инициализация базы данных
        logger.info("Initializing database...")
        await init_db()
        logger.info("Database initialized successfully")
        
        # Подключение к Redis
        logger.info("Connecting to Redis...")
        await redis_service.connect()
        
        # Запуск бота
        logger.info("Starting Telegram bot...")
        await start_bot()
        
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise
    finally:
        # Очистка ресурсов
        await redis_service.disconnect()
        logger.info("Application stopped")


if __name__ == "__main__":
    asyncio.run(main())

