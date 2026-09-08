"""
Сервис для генерации ежедневных и еженедельных отчётов
"""
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database.models import User, DiaryEntry, WeightLog
from database.database import AsyncSessionLocal
from services.statistics_service import statistics_service, NutrientStatus
from utils.timeutil import get_zone, local_today, local_range_bounds, to_local
import logging
import random

logger = logging.getLogger(__name__)


# Советы на завтра по категориям
TOMORROW_TIPS = {
    "protein_low": [
        "Сделайте упор на белковый завтрак — это поможет удержать калории днём.",
        "Попробуйте добавить яйца или творог на завтрак.",
        "Перекус с орехами или йогуртом поможет добрать белок.",
        "Курица или рыба на обед — отличный вариант.",
    ],
    "carbs_high": [
        "Попробуйте заменить один гарнир на овощи.",
        "Уменьшите порцию хлеба или макарон — это поможет.",
        "Больше белка, меньше углеводов на ужин.",
    ],
    "carbs_low": [
        "Не забудьте про крупы — они дают энергию на весь день.",
        "Фрукты на перекус — вкусно и полезно.",
    ],
    "calories_high": [
        "Завтра можно сделать день полегче — больше овощей, меньше калорийного.",
        "Попробуйте увеличить порции овощей, уменьшив калорийные продукты.",
        "Один лёгкий приём пищи поможет сбалансировать неделю.",
    ],
    "calories_low": [
        "Не забывайте про полноценные приёмы пищи — это важно для здоровья.",
        "Добавьте полезные жиры — авокадо, орехи, оливковое масло.",
    ],
    "good_day": [
        "Продолжайте в том же духе!",
        "Отличный баланс — держите планку.",
        "Всё идёт по плану, так держать!",
    ]
}


def get_goal_direction(current_calories: int, target_calories: int, goal: str) -> str:
    """
    Определяет движение к цели.

    Returns:
        "closer" / "same" / "further"
    """
    if not goal:
        return "same"

    diff = current_calories - target_calories

    if goal == "lose_weight":
        # Для похудения: меньше калорий = ближе к цели
        if diff < -100:
            return "closer"
        elif diff > 100:
            return "further"
        return "same"
    elif goal == "gain_weight":
        # Для набора: больше калорий = ближе
        if diff > 100:
            return "closer"
        elif diff < -100:
            return "further"
        return "same"
    else:  # maintain
        if abs(diff) < 150:
            return "same"
        return "further"


def get_goal_direction_text(direction: str) -> str:
    """Текст о движении к цели"""
    texts = {
        "closer": "Вы чуть ближе к цели.",
        "same": "Вы держите планку — так и нужно.",
        "further": "Немного дальше от цели — и это нормально, бывает."
    }
    return texts.get(direction, "")


def get_tomorrow_tip(stats: Dict, goal: str) -> str:
    """
    Выбирает совет на завтра на основе сегодняшних данных.
    """
    tips_pool = []

    # Анализируем проблемы
    if stats.get("protein_status") == NutrientStatus.LOW:
        tips_pool.extend(TOMORROW_TIPS["protein_low"])

    if stats.get("carbs_status") == NutrientStatus.HIGH:
        tips_pool.extend(TOMORROW_TIPS["carbs_high"])
    elif stats.get("carbs_status") == NutrientStatus.LOW:
        tips_pool.extend(TOMORROW_TIPS["carbs_low"])

    remaining = stats.get("calories_remaining", 0)
    if remaining < -200:  # Перебор
        tips_pool.extend(TOMORROW_TIPS["calories_high"])
    elif remaining > 500:  # Сильный недобор
        tips_pool.extend(TOMORROW_TIPS["calories_low"])

    # Если всё хорошо
    if not tips_pool:
        tips_pool = TOMORROW_TIPS["good_day"]

    return random.choice(tips_pool)


class ReportService:
    """Сервис для генерации отчётов"""

    async def generate_daily_report(self, telegram_id: int) -> Optional[str]:
        """
        Генерирует вечерний отчёт за день.
        Формат: 4-5 строк простым языком.
        """
        stats = await statistics_service.get_daily_stats(telegram_id)

        if not stats or stats["entries_count"] == 0:
            return None

        # Получаем цель пользователя
        goal = stats.get("user_goal", "maintain")

        # Определяем статусы
        calories = stats["calories"]
        target = stats["target_calories"]

        # Статус белка
        protein_text = "ок" if stats["protein_status"] != NutrientStatus.LOW else "маловато"

        # Статус углеводов
        carbs_status = stats["carbs_status"]
        if carbs_status == NutrientStatus.LOW:
            carbs_text = "недобор"
        elif carbs_status == NutrientStatus.HIGH:
            carbs_text = "перебор"
        else:
            carbs_text = "норма"

        # Направление к цели
        direction = get_goal_direction(calories, target, goal)
        direction_text = get_goal_direction_text(direction)

        # Совет на завтра
        tip = get_tomorrow_tip(stats, goal)

        # Формируем отчёт
        report = f"""📊 Итоги дня

Сегодня набрано: ~{calories} ккал
По белку — {protein_text}, по углеводам {carbs_text}.
{direction_text}

💡 Мой совет на завтра: {tip}"""

        return report

    async def generate_weekly_report(self, telegram_id: int) -> Optional[str]:
        """
        Генерирует еженедельный обзор прогресса.
        """
        async with AsyncSessionLocal() as session:
            # Получаем пользователя
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if not user or not user.target_calories:
                return None

            zone = get_zone(user.timezone)
            today = local_today(zone)
            week_start = today - timedelta(days=6)
            start_dt, end_dt = local_range_bounds(zone, week_start, today)

            result = await session.execute(
                select(DiaryEntry)
                .where(DiaryEntry.user_id == user.id)
                .where(DiaryEntry.date >= start_dt)
                .where(DiaryEntry.date < end_dt)
            )
            entries = [e for e in result.scalars().all() if e.meal_type != "вода"]

            if not entries:
                return None

            # Группируем по дням
            days_data = {}
            for entry in entries:
                day_key = to_local(entry.date, zone).date()
                if day_key not in days_data:
                    days_data[day_key] = 0
                days_data[day_key] += entry.calories or 0

            # Считаем дни в цели и дни с перебором
            target = user.target_calories
            days_on_target = 0
            days_over = 0

            for day_calories in days_data.values():
                if day_calories <= target * 1.1:  # +10% допуск
                    days_on_target += 1
                else:
                    days_over += 1

            total_days = len(days_data)

            # Получаем изменение веса
            weight_change_text = ""
            result = await session.execute(
                select(WeightLog)
                .where(WeightLog.user_id == user.id)
                .where(WeightLog.date >= week_start)
                .order_by(WeightLog.date)
            )
            weight_logs = result.scalars().all()

            if len(weight_logs) >= 2:
                weight_diff = weight_logs[-1].weight - weight_logs[0].weight
                sign = "+" if weight_diff > 0 else ""
                weight_change_text = f"Вес: {sign}{weight_diff:.1f} кг\n"
            elif user.weight:
                weight_change_text = f"Текущий вес: {user.weight} кг\n"

            # Прогноз (если есть цель похудения и изменение веса)
            forecast_text = ""
            if user.goal == "lose_weight" and len(weight_logs) >= 2:
                weekly_loss = weight_logs[0].weight - weight_logs[-1].weight
                if weekly_loss > 0 and user.weight:
                    # Примерный расчёт сколько недель до цели
                    target_weight = user.weight - 5  # Примерная цель -5 кг
                    weeks_needed = int(5 / weekly_loss) if weekly_loss > 0 else 0
                    if weeks_needed > 0:
                        forecast_text = f"\nЕсли продолжите в таком темпе, примерно через {weeks_needed} недель будете около {target_weight:.0f} кг."

            # Формируем отчёт
            report = f"""📈 Итоги за 7 дней

{weight_change_text}{days_on_target} из {total_days} дней вы держали цель{f", {days_over} дней выходили за лимит" if days_over else ""}.
{forecast_text if forecast_text else "Продолжайте в том же духе! 💪"}

Это марафон, не спринт — главное стабильность."""

            return report

    async def mark_daily_report_sent(self, telegram_id: int) -> None:
        """Отмечает, что дневной отчёт отправлен"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if user:
                user.last_daily_report_date = local_today(get_zone(user.timezone))
                await session.commit()

    async def mark_weekly_report_sent(self, telegram_id: int) -> None:
        """Отмечает, что еженедельный отчёт отправлен"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if user:
                user.last_weekly_report_date = local_today(get_zone(user.timezone))
                await session.commit()

    async def should_send_daily_report(self, user: User) -> bool:
        """Проверяет, нужно ли отправлять дневной отчёт"""
        if not user.daily_report_enabled:
            return False

        today = local_today(get_zone(user.timezone))
        if user.last_daily_report_date == today:
            return False

        return True

    async def should_send_weekly_report(self, user: User) -> bool:
        """Проверяет, нужно ли отправлять еженедельный отчёт"""
        if not user.weekly_report_enabled:
            return False

        today = local_today(get_zone(user.timezone))

        # Проверяем день недели
        if today.weekday() != user.weekly_report_day:
            return False

        # Проверяем не отправляли ли уже
        if user.last_weekly_report_date == today:
            return False

        return True


# Глобальный экземпляр
report_service = ReportService()

