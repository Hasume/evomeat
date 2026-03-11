"""
Сервис умных напоминаний о питании
"""
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database.models import User, DiaryEntry
from database.database import AsyncSessionLocal
from .statistics_service import (
    StatisticsService, 
    get_snack_recommendations,
    format_snack_recommendations,
    NutrientStatus
)
import logging
import pytz

logger = logging.getLogger(__name__)


class ReminderType:
    """Типы напоминаний"""
    MORNING_LOW_INTAKE = "morning_low"     # Утром мало съедено
    AFTERNOON_LOW_INTAKE = "afternoon_low"  # Днём мало съедено
    EVENING_OVEREAT = "evening_overeat"     # Вечером перебор
    PROTEIN_DEFICIT = "protein_deficit"     # Дефицит белка
    GENERAL_CHECK = "general_check"         # Общая проверка


class ReminderService:
    """Сервис для умных напоминаний"""
    
    def __init__(self):
        self.stats_service = StatisticsService()
    
    def _get_time_of_day(self, user_timezone: str = "Europe/Moscow") -> str:
        """
        Определяет время суток для пользователя.
        
        Returns:
            "morning" (6-12), "afternoon" (12-18), "evening" (18-23), "night" (23-6)
        """
        try:
            tz = pytz.timezone(user_timezone)
            local_time = datetime.now(tz)
            hour = local_time.hour
        except Exception:
            hour = datetime.now().hour
        
        if 6 <= hour < 12:
            return "morning"
        elif 12 <= hour < 18:
            return "afternoon"
        elif 18 <= hour < 23:
            return "evening"
        else:
            return "night"
    
    async def check_reminder_allowed(self, telegram_id: int) -> Tuple[bool, Optional[str]]:
        """
        Проверяет, можно ли отправить напоминание пользователю.
        
        Returns:
            (allowed, reason) - разрешено ли и причина отказа
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            
            if not user:
                return False, "user_not_found"
            
            # Проверяем, включены ли напоминания
            if not user.reminders_enabled:
                return False, "reminders_disabled"
            
            # Проверяем лимит на день
            today = date.today()
            if user.last_reminder_date == today:
                if user.reminders_sent_today >= user.max_reminders_per_day:
                    return False, "daily_limit_reached"
            
            return True, None
    
    async def record_reminder_sent(
        self, 
        telegram_id: int, 
        reminder_type: str
    ) -> None:
        """Записывает факт отправки напоминания"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            
            if user:
                today = date.today()
                
                # Сбрасываем счётчик если новый день
                if user.last_reminder_date != today:
                    user.reminders_sent_today = 0
                
                user.last_reminder_date = today
                user.reminders_sent_today += 1
                user.last_reminder_type = reminder_type
                
                await session.commit()
                logger.info(f"Reminder recorded for user {telegram_id}: {reminder_type}")
    
    async def get_user_eating_pattern(
        self, 
        telegram_id: int, 
        days: int = 7
    ) -> Optional[Dict]:
        """
        Анализирует паттерн питания пользователя за последние N дней.
        
        Returns:
            Dict с информацией о среднем потреблении по времени суток
        """
        start_date = datetime.now() - timedelta(days=days)
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            
            if not user:
                return None
            
            result = await session.execute(
                select(DiaryEntry)
                .where(DiaryEntry.user_id == user.id)
                .where(DiaryEntry.date >= start_date)
                .order_by(DiaryEntry.date)
            )
            entries = result.scalars().all()
            
            if not entries:
                return None
            
            # Группируем по времени суток
            morning_entries = []  # 6-12
            afternoon_entries = []  # 12-18
            evening_entries = []  # 18-24
            
            days_with_data = set()
            
            for entry in entries:
                hour = entry.date.hour
                days_with_data.add(entry.date.date())
                
                if 6 <= hour < 12:
                    morning_entries.append(entry.calories or 0)
                elif 12 <= hour < 18:
                    afternoon_entries.append(entry.calories or 0)
                else:
                    evening_entries.append(entry.calories or 0)
            
            days_count = len(days_with_data) or 1
            
            return {
                "avg_morning_calories": sum(morning_entries) / days_count if morning_entries else 0,
                "avg_afternoon_calories": sum(afternoon_entries) / days_count if afternoon_entries else 0,
                "avg_evening_calories": sum(evening_entries) / days_count if evening_entries else 0,
                "days_analyzed": days_count,
                "total_entries": len(entries)
            }
    
    async def generate_smart_reminder(
        self, 
        telegram_id: int
    ) -> Optional[Dict]:
        """
        Генерирует умное напоминание на основе текущего состояния.
        
        Returns:
            Dict с типом напоминания и текстом, или None если не нужно
        """
        # Проверяем можно ли отправлять
        allowed, reason = await self.check_reminder_allowed(telegram_id)
        if not allowed:
            logger.debug(f"Reminder not allowed for {telegram_id}: {reason}")
            return None
        
        # Получаем статистику за сегодня
        stats = await self.stats_service.get_daily_stats(telegram_id)
        if not stats:
            return None
        
        # Получаем паттерн питания
        pattern = await self.get_user_eating_pattern(telegram_id)
        
        # Определяем время суток
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            timezone = user.timezone if user else "Europe/Moscow"
        
        time_of_day = self._get_time_of_day(timezone)
        
        # Не беспокоим ночью
        if time_of_day == "night":
            return None
        
        target_calories = stats["target_calories"]
        current_calories = stats["calories"]
        calories_percent = (current_calories / target_calories * 100) if target_calories else 0
        
        # Логика утро/день - недоедание
        if time_of_day in ["morning", "afternoon"]:
            # Проверяем есть ли существенное отставание
            expected_by_now = target_calories * (0.35 if time_of_day == "morning" else 0.65)
            
            if current_calories < expected_by_now * 0.5 and stats["entries_count"] > 0:
                # Есть записи, но мало калорий
                reminder_type = ReminderType.MORNING_LOW_INTAKE if time_of_day == "morning" else ReminderType.AFTERNOON_LOW_INTAKE
                
                # Проверяем дефицит белка
                if stats["protein_status"] == NutrientStatus.LOW:
                    snacks = get_snack_recommendations(
                        "protein",
                        stats["target_protein"] - stats["protein"],
                        stats["calories_remaining"],
                        stats.get("user_allergies", [])
                    )
                    
                    if snacks:
                        snack_text = format_snack_recommendations(snacks, "белок")
                        return {
                            "type": ReminderType.PROTEIN_DEFICIT,
                            "message": f"Похоже, сегодня пока мало набралось по белку. Хочешь, подкину пару идей?\n\n{snack_text}",
                            "is_question": True
                        }
                
                return {
                    "type": reminder_type,
                    "message": "Похоже, сегодня пока мало набралось. Хочешь, подкину пару идей, чем добрать калории/белок? 🤔",
                    "is_question": True
                }
        
        # Логика вечер - перебор
        elif time_of_day == "evening":
            if calories_percent > 115:  # Перебор более 15%
                overage = current_calories - target_calories
                
                return {
                    "type": ReminderType.EVENING_OVEREAT,
                    "message": f"Сегодня перебор на {overage} ккал — но это нормально, бывает! 🤗\n\n"
                              f"Завтра можем сделать день полегче. Хочешь, скину идеи лёгких блюд на завтра?",
                    "is_question": True
                }
            
            # Проверяем дефицит белка вечером (ещё можно добрать)
            if stats["protein_status"] == NutrientStatus.LOW and stats["calories_remaining"] > 150:
                snacks = get_snack_recommendations(
                    "protein",
                    stats["target_protein"] - stats["protein"],
                    stats["calories_remaining"],
                    stats.get("user_allergies", [])
                )
                
                if snacks:
                    snack_text = format_snack_recommendations(snacks, "белок")
                    return {
                        "type": ReminderType.PROTEIN_DEFICIT,
                        "message": f"Ещё есть запас по калориям, а белка маловато. Вот лёгкие варианты на вечер:\n\n{snack_text}",
                        "is_question": False
                    }
        
        return None
    
    async def toggle_reminders(
        self, 
        telegram_id: int, 
        enabled: bool
    ) -> bool:
        """Включает/выключает напоминания для пользователя"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            
            if user:
                user.reminders_enabled = enabled
                await session.commit()
                logger.info(f"Reminders {'enabled' if enabled else 'disabled'} for user {telegram_id}")
                return True
            return False


# Создаём глобальный экземпляр сервиса
reminder_service = ReminderService()

