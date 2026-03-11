"""
Сервис для работы с Redis (кэширование)
"""
import redis.asyncio as aioredis
from config import settings
import json
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)


class RedisService:
    """Сервис кэширования с использованием Redis"""
    
    def __init__(self):
        self.redis = None
    
    async def connect(self):
        """Подключение к Redis"""
        try:
            self.redis = await aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True
            )
            logger.info("Connected to Redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.redis = None
    
    async def disconnect(self):
        """Отключение от Redis"""
        if self.redis:
            await self.redis.close()
            logger.info("Disconnected from Redis")
    
    async def get(self, key: str) -> Optional[Any]:
        """Получить значение из кэша"""
        if not self.redis:
            return None
        
        try:
            value = await self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Error getting from Redis: {e}")
            return None
    
    async def set(self, key: str, value: Any, ttl: int = 3600):
        """Сохранить значение в кэш с TTL (время жизни в секундах)"""
        if not self.redis:
            return False
        
        try:
            serialized = json.dumps(value)
            await self.redis.set(key, serialized, ex=ttl)
            return True
        except Exception as e:
            logger.error(f"Error setting to Redis: {e}")
            return False
    
    async def delete(self, key: str):
        """Удалить значение из кэша"""
        if not self.redis:
            return False
        
        try:
            await self.redis.delete(key)
            return True
        except Exception as e:
            logger.error(f"Error deleting from Redis: {e}")
            return False
    
    async def cache_recipes(self, user_id: int, ingredients: list, recipes: list, ttl: int = 3600):
        """Кэшировать результаты поиска рецептов"""
        key = f"recipes:{user_id}:{':'.join(sorted(ingredients))}"
        await self.set(key, recipes, ttl)
    
    async def get_cached_recipes(self, user_id: int, ingredients: list) -> Optional[list]:
        """Получить закэшированные рецепты"""
        key = f"recipes:{user_id}:{':'.join(sorted(ingredients))}"
        return await self.get(key)
    
    async def cache_user_stats(self, user_id: int, stats: dict, ttl: int = 300):
        """Кэшировать статистику пользователя"""
        key = f"user_stats:{user_id}"
        await self.set(key, stats, ttl)
    
    async def get_cached_user_stats(self, user_id: int) -> Optional[dict]:
        """Получить закэшированную статистику"""
        key = f"user_stats:{user_id}"
        return await self.get(key)
    
    async def increment_request_count(self, user_id: int, limit: int = 10) -> bool:
        """Инкрементировать счётчик запросов (rate limiting)"""
        if not self.redis:
            return True  # Пропускаем если Redis недоступен
        
        try:
            key = f"rate_limit:{user_id}"
            count = await self.redis.incr(key)
            
            if count == 1:
                # Устанавливаем TTL при первом запросе
                await self.redis.expire(key, 3600)  # 1 час
            
            return count <= limit
        except Exception as e:
            logger.error(f"Error incrementing request count: {e}")
            return True  # В случае ошибки пропускаем


# Глобальный экземпляр
redis_service = RedisService()

