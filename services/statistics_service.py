"""
Сервис статистики и рекомендаций по питанию
"""
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database.models import User, DiaryEntry
from database.database import AsyncSessionLocal
from utils.allergens import product_conflicts
from utils.telegram_ui import meal_label
from utils.timeutil import get_zone, local_today, local_day_bounds, local_range_bounds, to_local
from utils.water import WATER_MEAL_TYPE, water_target_ml, format_litres
import logging

logger = logging.getLogger(__name__)


class NutrientStatus:
    """Статусы для оценки нутриентов"""
    LOW = "мало"
    NORMAL = "норм"
    HIGH = "много"


def evaluate_nutrient(current: float, target: float, nutrient_type: str = "default") -> str:
    """
    Оценивает статус нутриента относительно цели.

    Args:
        current: Текущее потребление
        target: Целевое значение
        nutrient_type: Тип нутриента (protein, carbs, fats, calories)

    Returns:
        Строка оценки: "мало", "норм", "много"
    """
    if target <= 0:
        return NutrientStatus.NORMAL

    ratio = current / target

    # Для белка строже - важно набрать норму
    if nutrient_type == "protein":
        if ratio < 0.7:
            return NutrientStatus.LOW
        elif ratio > 1.2:
            return NutrientStatus.HIGH
        return NutrientStatus.NORMAL

    # Для углеводов средние границы
    elif nutrient_type == "carbs":
        if ratio < 0.6:
            return NutrientStatus.LOW
        elif ratio > 1.3:
            return NutrientStatus.HIGH
        return NutrientStatus.NORMAL

    # Для жиров - следим за перебором
    elif nutrient_type == "fats":
        if ratio < 0.5:
            return NutrientStatus.LOW
        elif ratio > 1.2:
            return NutrientStatus.HIGH
        return NutrientStatus.NORMAL

    # Общий случай
    else:
        if ratio < 0.7:
            return NutrientStatus.LOW
        elif ratio > 1.15:
            return NutrientStatus.HIGH
        return NutrientStatus.NORMAL


def get_status_emoji(status: str) -> str:
    """Возвращает эмодзи для статуса"""
    emoji_map = {
        NutrientStatus.LOW: "🔻",
        NutrientStatus.NORMAL: "✅",
        NutrientStatus.HIGH: "🔺"
    }
    return emoji_map.get(status, "❓")


class StatisticsService:
    """Сервис для работы со статистикой питания"""

    async def get_daily_stats(
        self,
        telegram_id: int,
        target_date: Optional[date] = None
    ) -> Optional[Dict]:
        """
        Получить статистику за день.

        Returns:
            Dict с полями:
            - calories, protein, fats, carbs (текущие)
            - target_calories, target_protein, target_fats, target_carbs (целевые)
            - protein_status, carbs_status, fats_status (оценки)
            - calories_remaining (сколько осталось)
            - entries_count (количество записей)
            - entries (список записей)
        """
        async with AsyncSessionLocal() as session:
            # Получаем пользователя
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if not user:
                return None

            # День считаем во времени пользователя, а не сервера: ужин в 01:00
            # по Москве раньше попадал во «вчера», потому что сервер живёт в UTC.
            zone = get_zone(user.timezone)
            if target_date is None:
                target_date = local_today(zone)
            start_datetime, end_datetime = local_day_bounds(zone, target_date)

            result = await session.execute(
                select(DiaryEntry)
                .where(DiaryEntry.user_id == user.id)
                .where(DiaryEntry.date >= start_datetime)
                .where(DiaryEntry.date < end_datetime)
                .order_by(DiaryEntry.date)
            )
            all_entries = result.scalars().all()

            # Вода учитывается отдельно и в еду не входит.
            water_ml = sum((e.portion_size or 0) for e in all_entries if e.meal_type == WATER_MEAL_TYPE)
            entries = [e for e in all_entries if e.meal_type != WATER_MEAL_TYPE]

            # Подсчитываем итоги
            total_calories = sum(e.calories or 0 for e in entries)
            total_protein = sum(e.protein or 0 for e in entries)
            total_fats = sum(e.fats or 0 for e in entries)
            total_carbs = sum(e.carbs or 0 for e in entries)

            # Целевые значения
            target_calories = user.target_calories or 2000
            target_protein = user.target_protein or 100
            target_fats = user.target_fats or 65
            target_carbs = user.target_carbs or 250

            # Оценки
            protein_status = evaluate_nutrient(total_protein, target_protein, "protein")
            carbs_status = evaluate_nutrient(total_carbs, target_carbs, "carbs")
            fats_status = evaluate_nutrient(total_fats, target_fats, "fats")

            # Остаток калорий
            calories_remaining = target_calories - total_calories

            # Разбивка по приёмам пищи — нужна, чтобы показать, где именно
            # набрана дневная калорийность.
            by_meal = {}
            for entry in entries:
                label = meal_label(entry.meal_type)
                by_meal[label] = round(by_meal.get(label, 0) + (entry.calories or 0))

            return {
                # Текущие показатели
                "calories": round(total_calories),
                "protein": round(total_protein, 1),
                "fats": round(total_fats, 1),
                "carbs": round(total_carbs, 1),

                # Целевые показатели
                "target_calories": target_calories,
                "target_protein": round(target_protein, 1),
                "target_fats": round(target_fats, 1),
                "target_carbs": round(target_carbs, 1),

                # Оценки
                "protein_status": protein_status,
                "carbs_status": carbs_status,
                "fats_status": fats_status,

                # Дополнительно
                "calories_remaining": round(calories_remaining),
                "entries_count": len(entries),
                "by_meal": by_meal,
                "water_ml": round(water_ml),
                "water_target_ml": water_target_ml(user.weight),
                "date": target_date.isoformat(),
                "timezone": user.timezone,
                "entries": [
                    {
                        "id": e.id,
                        "time": to_local(e.date, zone).strftime("%H:%M"),
                        "food_name": e.food_name,
                        "meal_type": e.meal_type,
                        "calories": e.calories
                    }
                    for e in entries
                ],

                # Данные пользователя для рекомендаций
                "user_allergies": user.allergies or [],
                "user_preferences": user.dietary_preferences or [],
                "user_goal": user.goal
            }

    async def get_weekly_stats(self, telegram_id: int) -> Optional[Dict]:
        """
        Получить статистику за неделю.
        """
        async with AsyncSessionLocal() as session:
            # Получаем пользователя
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if not user:
                return None

            zone = get_zone(user.timezone)
            today = local_today(zone)
            week_start = today - timedelta(days=6)  # семь дней вместе с сегодняшним
            start_datetime, end_datetime = local_range_bounds(zone, week_start, today)

            result = await session.execute(
                select(DiaryEntry)
                .where(DiaryEntry.user_id == user.id)
                .where(DiaryEntry.date >= start_datetime)
                .where(DiaryEntry.date < end_datetime)
                .order_by(DiaryEntry.date)
            )
            entries = [e for e in result.scalars().all() if e.meal_type != WATER_MEAL_TYPE]

            # Группируем по дням
            days_data = {}
            for entry in entries:
                day_key = to_local(entry.date, zone).date()
                if day_key not in days_data:
                    days_data[day_key] = {
                        "calories": 0,
                        "protein": 0,
                        "fats": 0,
                        "carbs": 0,
                        "entries_count": 0
                    }
                days_data[day_key]["calories"] += entry.calories or 0
                days_data[day_key]["protein"] += entry.protein or 0
                days_data[day_key]["fats"] += entry.fats or 0
                days_data[day_key]["carbs"] += entry.carbs or 0
                days_data[day_key]["entries_count"] += 1

            # Средние значения
            days_count = len(days_data) or 1
            avg_calories = sum(d["calories"] for d in days_data.values()) / days_count
            avg_protein = sum(d["protein"] for d in days_data.values()) / days_count
            avg_fats = sum(d["fats"] for d in days_data.values()) / days_count
            avg_carbs = sum(d["carbs"] for d in days_data.values()) / days_count

            return {
                "days_tracked": len(days_data),
                "avg_calories": round(avg_calories),
                "avg_protein": round(avg_protein, 1),
                "avg_fats": round(avg_fats, 1),
                "avg_carbs": round(avg_carbs, 1),
                "target_calories": user.target_calories or 2000,
                "days_data": {
                    str(k): v for k, v in days_data.items()
                }
            }

    @staticmethod
    def _progress_bar(current: float, target: float, width: int = 10) -> str:
        """Полоса прогресса: показывает наполненность нормы одним взглядом."""
        if not target or target <= 0:
            return "▫️" * width
        share = max(0.0, min(current / target, 1.0))
        filled = int(round(share * width))
        overflow = current > target * 1.05
        symbol = "🟧" if overflow else "🟩"
        return symbol * filled + "▫️" * (width - filled)

    def format_daily_dashboard(self, stats: Dict) -> str:
        """
        Форматирует дневной дашборд для отправки пользователю.
        """
        if not stats:
            return "📊 Пока нечего показать — добавьте первую запись в дневник."

        protein_emoji = get_status_emoji(stats["protein_status"])
        carbs_emoji = get_status_emoji(stats["carbs_status"])
        fats_emoji = get_status_emoji(stats["fats_status"])

        remaining = stats["calories_remaining"]
        if remaining > 100:
            calories_status = f"Осталось на сегодня: *{remaining}* ккал"
        elif remaining < -100:
            calories_status = f"⚠️ Перебор на *{abs(remaining)}* ккал — ничего страшного, бывает"
        else:
            calories_status = "🎯 Ровно в цель, отличный день!"

        calories_percent = round(stats["calories"] / stats["target_calories"] * 100) if stats["target_calories"] else 0
        bar = self._progress_bar(stats["calories"], stats["target_calories"])

        meals = stats.get("by_meal") or {}
        meals_line = ""
        if meals:
            parts = [f"{label} {value} ккал" for label, value in meals.items()]
            meals_line = "\n\n🍽 " + " · ".join(parts)

        entries_word = self._plural(stats["entries_count"], "запись", "записи", "записей")

        water_line = ""
        water_ml = stats.get("water_ml") or 0
        if water_ml:
            water_line = (f"\n💧 Вода: {format_litres(water_ml)} из "
                          f"{format_litres(stats.get('water_target_ml') or 2000)} л")

        dashboard = f"""📊 *Сегодня*

{bar}  {calories_percent}%
🔥 {stats["calories"]} из {stats["target_calories"]} ккал
{calories_status}

*Нутриенты*
{protein_emoji} Белок: {stats["protein"]} / {stats["target_protein"]} г — {stats["protein_status"]}
{carbs_emoji} Углеводы: {stats["carbs"]} / {stats["target_carbs"]} г — {stats["carbs_status"]}
{fats_emoji} Жиры: {stats["fats"]} / {stats["target_fats"]} г — {stats["fats_status"]}{meals_line}{water_line}

📝 {stats["entries_count"]} {entries_word} за день"""

        return dashboard

    @staticmethod
    def _plural(number: int, one: str, few: str, many: str) -> str:
        """Согласовать существительное с числом: 1 запись, 2 записи, 5 записей."""
        n = abs(number) % 100
        if 11 <= n <= 14:
            return many
        n %= 10
        if n == 1:
            return one
        if 2 <= n <= 4:
            return few
        return many

    def get_deficit_info(self, stats: Dict) -> Optional[Dict]:
        """
        Определяет дефициты нутриентов для рекомендаций.

        Returns:
            Dict с информацией о дефицитах или None если всё в норме
        """
        if not stats:
            return None

        deficits = {}

        # Проверяем белок
        if stats["protein_status"] == NutrientStatus.LOW:
            protein_deficit = stats["target_protein"] - stats["protein"]
            deficits["protein"] = {
                "current": stats["protein"],
                "target": stats["target_protein"],
                "deficit": round(protein_deficit, 1),
                "name": "белок"
            }

        # Проверяем углеводы
        if stats["carbs_status"] == NutrientStatus.LOW:
            carbs_deficit = stats["target_carbs"] - stats["carbs"]
            deficits["carbs"] = {
                "current": stats["carbs"],
                "target": stats["target_carbs"],
                "deficit": round(carbs_deficit, 1),
                "name": "углеводы"
            }

        # Проверяем жиры
        if stats["fats_status"] == NutrientStatus.LOW:
            fats_deficit = stats["target_fats"] - stats["fats"]
            deficits["fats"] = {
                "current": stats["fats"],
                "target": stats["target_fats"],
                "deficit": round(fats_deficit, 1),
                "name": "жиры"
            }

        # Остаток калорий
        calories_remaining = stats["calories_remaining"]

        if deficits and calories_remaining > 100:
            return {
                "deficits": deficits,
                "calories_remaining": calories_remaining,
                "has_room_for_snack": True
            }

        return None


# Готовые варианты перекусов для рекомендаций.
# Поле `contains` перечисляет состав для проверки на аллергены: по названию
# продукта его определить нельзя (протеиновый коктейль — молочный белок).
SNACK_OPTIONS = {
    "protein": [
        {"name": "Творог 5%", "portion": "150г", "protein": 25, "calories": 150, "carbs": 5, "fats": 7,
         "contains": ["молоко"]},
        {"name": "Куриная грудка варёная", "portion": "100г", "protein": 30, "calories": 165, "carbs": 0, "fats": 3,
         "contains": ["курица", "мясо"]},
        {"name": "Яйца варёные", "portion": "2 шт", "protein": 13, "calories": 155, "carbs": 1, "fats": 11,
         "contains": ["яйцо"]},
        {"name": "Греческий йогурт", "portion": "200г", "protein": 20, "calories": 130, "carbs": 8, "fats": 3,
         "contains": ["молоко"]},
        {"name": "Тунец консервированный", "portion": "100г", "protein": 26, "calories": 130, "carbs": 0, "fats": 1,
         "contains": ["рыба"]},
        {"name": "Протеиновый коктейль", "portion": "1 порция", "protein": 25, "calories": 120, "carbs": 3, "fats": 1,
         "contains": ["молоко", "сыворотка"]},
        {"name": "Сыр нежирный", "portion": "50г", "protein": 12, "calories": 100, "carbs": 1, "fats": 5,
         "contains": ["молоко"]},
        {"name": "Нут отварной", "portion": "150г", "protein": 13, "calories": 180, "carbs": 27, "fats": 3,
         "contains": []},
        {"name": "Тофу", "portion": "150г", "protein": 12, "calories": 115, "carbs": 3, "fats": 7,
         "contains": ["соя"]},
    ],
    "carbs": [
        {"name": "Банан", "portion": "1 средний", "protein": 1, "calories": 105, "carbs": 27, "fats": 0,
         "contains": []},
        {"name": "Овсянка на воде", "portion": "150г готовой", "protein": 4, "calories": 130, "carbs": 25, "fats": 2,
         "contains": ["овсянка", "глютен"]},
        {"name": "Хлебцы цельнозерновые", "portion": "4 шт", "protein": 3, "calories": 100, "carbs": 20, "fats": 1,
         "contains": ["глютен"]},
        {"name": "Рисовая каша", "portion": "150г", "protein": 3, "calories": 140, "carbs": 30, "fats": 1,
         "contains": []},
        {"name": "Яблоко с мёдом", "portion": "1 шт + 1 ч.л.", "protein": 0, "calories": 120, "carbs": 30, "fats": 0,
         "contains": ["мёд"]},
        {"name": "Печёный картофель", "portion": "150г", "protein": 3, "calories": 130, "carbs": 29, "fats": 0,
         "contains": []},
    ],
    "fats": [
        {"name": "Авокадо", "portion": "половинка", "protein": 2, "calories": 160, "carbs": 8, "fats": 15,
         "contains": []},
        {"name": "Орехи микс", "portion": "30г", "protein": 5, "calories": 180, "carbs": 5, "fats": 16,
         "contains": ["орехи"]},
        {"name": "Оливковое масло", "portion": "1 ст.л.", "protein": 0, "calories": 120, "carbs": 0, "fats": 14,
         "contains": []},
        {"name": "Лосось слабосолёный", "portion": "50г", "protein": 10, "calories": 100, "carbs": 0, "fats": 6,
         "contains": ["рыба"]},
        {"name": "Семена тыквы", "portion": "25г", "protein": 7, "calories": 145, "carbs": 4, "fats": 12,
         "contains": []},
    ]
}


def get_snack_recommendations(
    deficit_type: str,
    deficit_amount: float,
    calories_remaining: int,
    allergies: List[str] = None,
    count: int = 3,
    dietary_preferences: List[str] = None
) -> List[Dict]:
    """
    Подбирает варианты перекусов для закрытия дефицита.

    Отбор идёт по составу продукта, а не по совпадению слов в названии:
    «Протеиновый коктейль» — сывороточный, то есть молочный белок, и человеку
    с аллергией на молоко он не подходит, хотя слова «молоко» в названии нет.
    """
    snacks = SNACK_OPTIONS.get(deficit_type, [])

    suitable = []
    for snack in snacks:
        if snack["calories"] > calories_remaining + 50:
            continue

        # Проверяем и явный состав, и название — на случай, если продукт
        # добавили в справочник без разметки состава.
        conflict = None
        for candidate in [snack["name"], *snack.get("contains", [])]:
            conflict = product_conflicts(candidate, allergies, dietary_preferences)
            if conflict:
                break

        if not conflict:
            suitable.append(snack)

    # Сортируем по эффективности (сколько нутриента на калорию)
    nutrient_key = deficit_type if deficit_type != "protein" else "protein"
    suitable.sort(key=lambda x: x.get(nutrient_key, 0) / max(x["calories"], 1), reverse=True)

    return suitable[:count]


def format_snack_recommendations(snacks: List[Dict], deficit_name: str) -> str:
    """
    Форматирует рекомендации перекусов для пользователя.
    """
    if not snacks:
        return (
            f"Подходящих вариантов для добора {deficit_name} не нашлось — "
            "всё из моего списка не подходит под ваши ограничения или не влезает "
            "в остаток калорий."
        )

    text = f"🍽 *Чем добрать {deficit_name}*\n\n"

    for snack in snacks:
        nutrient_info = []
        if snack.get("protein", 0) > 5:
            nutrient_info.append(f"🥩 {snack['protein']} г белка")
        if snack.get("carbs", 0) > 10:
            nutrient_info.append(f"🍞 {snack['carbs']} г углеводов")
        if snack.get("fats", 0) > 5:
            nutrient_info.append(f"🧈 {snack['fats']} г жиров")

        nutrients = " · ".join(nutrient_info)

        text += f"• *{snack['name']}* — {snack['portion']}\n"
        text += f"  🔥 {snack['calories']} ккал"
        if nutrients:
            text += f" · {nutrients}"
        text += "\n\n"

    text += "_Съели что-то из этого — просто напишите мне, я запишу._"
    return text


# Создаём глобальный экземпляр сервиса
statistics_service = StatisticsService()

