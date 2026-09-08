"""
Настройка подключения к базе данных
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, text
from config import settings
from .models import Base


# Async движок для основной работы
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
)

# Sync движок для миграций Alembic
sync_engine = create_engine(
    settings.DATABASE_URL_SYNC,
    echo=settings.DEBUG,
)

# Фабрика сессий
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """Dependency для получения сессии БД"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Колонки, добавленные после первого релиза. create_all не меняет существующие
# таблицы, поэтому на старте добавляем их явно — это делает деплой безопасным
# и без запуска alembic. Тот же список продублирован в миграции 002.
SCHEMA_UPGRADES = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_mode_date DATE",
]


async def init_db():
    """Инициализация базы данных (создание таблиц и догоняющие правки схемы)"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in SCHEMA_UPGRADES:
            try:
                await conn.execute(text(statement))
            except Exception as e:  # noqa: BLE001 — не валим старт из-за косметики схемы
                import logging
                logging.getLogger(__name__).warning(f"Schema upgrade skipped: {statement} ({e})")

