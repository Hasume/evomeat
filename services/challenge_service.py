"""
Сервис для управления мини-челленджами
"""
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.models import User, Challenge, DiaryEntry
from database.database import AsyncSessionLocal
from utils.timeutil import get_zone, local_today, local_day_bounds, to_local, utc_now
import logging
import random

logger = logging.getLogger(__name__)


# Доступные типы челленджей
CHALLENGE_TYPES = {
    "no_sweets_evening": {
        "name": "3 дня без сладкого вечером",
        "description": "Не есть сладкое после 18:00",
        "duration": 3,
        "check_keywords": ["сладкое", "конфет", "шоколад", "торт", "печенье", "пирожн", "десерт", "мороженое"],
        "check_after_hour": 18,
    },
    "protein_week": {
        "name": "Неделя с нормой по белку",
        "description": "Каждый день набирать минимум 80% от нормы белка",
        "duration": 7,
        "check_type": "protein_goal",
        "threshold": 0.8,
    },
    "no_snacks_late": {
        "name": "5 дней без перекусов после 21:00",
        "description": "Не есть после 21:00",
        "duration": 5,
        "check_after_hour": 21,
    },
    "water_week": {
        "name": "Неделя осознанного питания",
        "description": "Записывать все приёмы пищи каждый день",
        "duration": 7,
        "check_type": "min_entries",
        "min_entries": 3,
    },
    "no_fastfood": {
        "name": "3 дня без фастфуда",
        "description": "Избегаем бургеров, пиццы и фастфуда",
        "duration": 3,
        "check_keywords": ["бургер", "пицца", "фастфуд", "макдональдс", "kfc", "бургер кинг", "шаурма"],
    },
}


class ChallengeService:
    """Сервис для управления челленджами"""

    def get_available_challenges(self) -> List[Dict]:
        """Возвращает список доступных челленджей"""
        return [
            {"type": k, **v}
            for k, v in CHALLENGE_TYPES.items()
        ]

    def get_random_challenge_suggestion(self) -> Dict:
        """Возвращает случайное предложение челленджа"""
        challenge_type = random.choice(list(CHALLENGE_TYPES.keys()))
        return {
            "type": challenge_type,
            **CHALLENGE_TYPES[challenge_type]
        }

    async def start_challenge(
        self,
        telegram_id: int,
        challenge_type: str
    ) -> Optional[Challenge]:
        """Начинает новый челлендж для пользователя"""
        if challenge_type not in CHALLENGE_TYPES:
            return None

        challenge_info = CHALLENGE_TYPES[challenge_type]

        async with AsyncSessionLocal() as session:
            # Получаем пользователя
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if not user:
                return None

            today = local_today(get_zone(user.timezone))
            end_date = today + timedelta(days=challenge_info["duration"])

            # Проверяем нет ли активного челленджа
            result = await session.execute(
                select(Challenge)
                .where(Challenge.user_id == user.id)
                .where(Challenge.status == "active")
            )
            existing = result.scalar_one_or_none()

            if existing:
                logger.info(f"User {telegram_id} already has active challenge")
                return None

            # Создаём челлендж
            challenge = Challenge(
                user_id=user.id,
                challenge_type=challenge_type,
                name=challenge_info["name"],
                description=challenge_info["description"],
                start_date=today,
                end_date=end_date,
                duration_days=challenge_info["duration"],
                days_completed=0,
                days_failed=0,
                status="active"
            )

            session.add(challenge)

            # Сохраняем в user.active_challenge для быстрого доступа
            user.active_challenge = {
                "id": None,  # Будет обновлено после commit
                "type": challenge_type,
                "name": challenge_info["name"],
                "start_date": str(today),
                "end_date": str(end_date),
                "days_completed": 0,
                "days_failed": 0,
            }

            await session.flush()

            # Присваиваем новый словарь целиком: изменения внутри JSON-значения
            # SQLAlchemy не отслеживает, и id челленджа оставался None — из-за
            # этого отменённый челлендж не закрывался, а новый было не начать.
            user.active_challenge = {**user.active_challenge, "id": challenge.id}
            await session.commit()

            logger.info(f"User {telegram_id} started challenge: {challenge_type}")
            return challenge

    async def cancel_challenge(self, telegram_id: int) -> bool:
        """Отменяет активный челлендж"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if not user or not user.active_challenge:
                return False

            # Получаем челлендж
            challenge_id = user.active_challenge.get("id")
            if challenge_id:
                result = await session.execute(
                    select(Challenge).where(Challenge.id == challenge_id)
                )
                challenge = result.scalar_one_or_none()
                if challenge:
                    challenge.status = "cancelled"
                    challenge.completed_at = utc_now()

            user.active_challenge = None
            await session.commit()

            logger.info(f"User {telegram_id} cancelled challenge")
            return True

    async def check_daily_progress(
        self,
        telegram_id: int
    ) -> Optional[Dict]:
        """
        Проверяет прогресс за текущий день.

        Returns:
            Dict с результатом проверки или None
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if not user or not user.active_challenge:
                return None

            challenge_data = user.active_challenge
            challenge_type = challenge_data.get("type")

            if challenge_type not in CHALLENGE_TYPES:
                return None

            challenge_info = CHALLENGE_TYPES[challenge_type]
            zone = get_zone(user.timezone)
            today = local_today(zone)

            start_dt, end_dt = local_day_bounds(zone, today)
            result = await session.execute(
                select(DiaryEntry)
                .where(DiaryEntry.user_id == user.id)
                .where(DiaryEntry.date >= start_dt)
                .where(DiaryEntry.date < end_dt)
            )
            # Вода — не еда: стакан воды после 21:00 не должен проваливать челлендж.
            entries = [e for e in result.scalars().all() if e.meal_type != "вода"]

            # Проверяем в зависимости от типа челленджа
            passed = True
            reason = ""

            if "check_keywords" in challenge_info:
                # Проверка на запрещённые продукты
                keywords = challenge_info["check_keywords"]
                after_hour = challenge_info.get("check_after_hour", 0)

                for entry in entries:
                    if to_local(entry.date, zone).hour >= after_hour:
                        food_lower = entry.food_name.lower()
                        for keyword in keywords:
                            if keyword in food_lower:
                                passed = False
                                reason = f"Обнаружено: {entry.food_name}"
                                break
                    if not passed:
                        break

            elif challenge_info.get("check_type") == "protein_goal":
                # Проверка на норму белка
                total_protein = sum(e.protein or 0 for e in entries)
                target = (user.target_protein or 100) * challenge_info["threshold"]
                if total_protein < target:
                    passed = False
                    reason = f"Белок: {total_protein:.0f}г из {target:.0f}г нужных"

            elif challenge_info.get("check_type") == "min_entries":
                # Проверка на минимум записей
                if len(entries) < challenge_info["min_entries"]:
                    passed = False
                    reason = f"Записей: {len(entries)} из {challenge_info['min_entries']} нужных"

            elif "check_after_hour" in challenge_info:
                # Проверка на время
                after_hour = challenge_info["check_after_hour"]
                for entry in entries:
                    if to_local(entry.date, zone).hour >= after_hour:
                        passed = False
                        reason = f"Еда после {after_hour}:00: {entry.food_name}"
                        break

            return {
                "passed": passed,
                "reason": reason,
                "challenge_name": challenge_data["name"],
                "date": today.isoformat(),
                "days_completed": challenge_data["days_completed"],
                "days_failed": challenge_data["days_failed"],
                "total_days": challenge_info["duration"]
            }

    async def update_challenge_progress(
        self,
        telegram_id: int,
        day_passed: bool,
        checked_date: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Обновляет прогресс челленджа после проверки дня.

        Returns:
            Dict с обновлённым статусом
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if not user or not user.active_challenge:
                return None

            # Работаем с копией: изменения внутри исходного словаря SQLAlchemy
            # не отслеживает, и прогресс не доезжал бы до базы.
            challenge_data = dict(user.active_challenge)
            challenge_id = challenge_data.get("id")
            if checked_date:
                challenge_data["last_checked_date"] = checked_date

            # Обновляем прогресс
            if day_passed:
                challenge_data["days_completed"] = challenge_data.get("days_completed", 0) + 1
            else:
                challenge_data["days_failed"] = challenge_data.get("days_failed", 0) + 1

            # Проверяем завершение
            total = challenge_data["days_completed"] + challenge_data["days_failed"]
            challenge_type = challenge_data.get("type")
            duration = CHALLENGE_TYPES.get(challenge_type, {}).get("duration", 7)

            status = "active"
            result_text = None

            if total >= duration:
                status = "completed"
                # Определяем результат
                if challenge_data["days_failed"] == 0:
                    result_text = "success"
                elif challenge_data["days_completed"] >= challenge_data["days_failed"]:
                    result_text = "partial"
                else:
                    result_text = "failed"

            user.active_challenge = challenge_data if status == "active" else None

            # Обновляем запись в таблице challenges
            if challenge_id:
                result = await session.execute(
                    select(Challenge).where(Challenge.id == challenge_id)
                )
                challenge = result.scalar_one_or_none()
                if challenge:
                    challenge.days_completed = challenge_data["days_completed"]
                    challenge.days_failed = challenge_data["days_failed"]
                    if status == "completed":
                        challenge.status = "completed"
                        challenge.result = result_text
                        challenge.completed_at = utc_now()

            await session.commit()

            return {
                "status": status,
                "days_completed": challenge_data["days_completed"],
                "days_failed": challenge_data["days_failed"],
                "result": result_text
            }

    def format_challenge_result(self, result: str, name: str) -> str:
        """Форматирует результат челленджа"""
        if result == "success":
            return f"🎉 Челлендж «{name}» пройден!\n\nОтлично! Вы справились на 100%. Это показывает, что вы можете придерживаться плана."
        elif result == "partial":
            return f"👍 Челлендж «{name}» почти пройден!\n\nНе идеально, но вы старались — и это главное. Хотите попробовать ещё раз?"
        else:
            return f"😌 Челлендж «{name}» не зашёл — и это ок.\n\nНе все эксперименты удаются, это нормально. Может, попробуем что-то другое?"

    def format_challenge_proposal(self, challenge: Dict) -> str:
        """Форматирует предложение челленджа"""
        return f"""🎯 **Мини-челлендж**

Хотите попробовать: **{challenge['name']}**?

{challenge['description']}
Длительность: {challenge['duration']} дней

Я буду просто напоминать и отмечать прогресс. Можно отказаться в любой момент."""


# Глобальный экземпляр
challenge_service = ChallengeService()

