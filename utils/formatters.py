"""
Утилиты для форматирования данных
"""
from datetime import datetime, date
from typing import List, Dict


def format_date(dt: datetime) -> str:
    """Форматировать дату в читаемый вид"""
    return dt.strftime("%d.%m.%Y")


def format_datetime(dt: datetime) -> str:
    """Форматировать дату и время"""
    return dt.strftime("%d.%m.%Y %H:%M")


def format_calories(calories: float) -> str:
    """Форматировать калории"""
    return f"{int(calories)} ккал"


def format_macros(protein: float, fats: float, carbs: float) -> str:
    """Форматировать БЖУ"""
    return f"Б: {protein}г | Ж: {fats}г | У: {carbs}г"


def format_nutrition_info(
    calories: float,
    protein: float,
    fats: float,
    carbs: float
) -> str:
    """Форматировать полную информацию о пищевой ценности"""
    return f"""
🔥 Калории: {int(calories)} ккал
🥩 Белки: {protein}г
🧈 Жиры: {fats}г
🍞 Углеводы: {carbs}г
"""


def format_recipe_list(recipes: List[Dict]) -> str:
    """Форматировать список рецептов"""
    if not recipes:
        return "Рецепты не найдены"
    
    text = "📖 Найденные рецепты:\n\n"
    for i, recipe in enumerate(recipes, 1):
        text += f"{i}. {recipe.get('name', 'Без названия')}\n"
        text += f"   ⏱ {recipe.get('cooking_time', '?')} мин | "
        text += f"🔥 {recipe.get('nutrition', {}).get('calories', '?')} ккал\n\n"
    
    return text


def format_weekly_plan_day(day_name: str, meals: Dict) -> str:
    """Форматировать план на день"""
    text = f"\n📅 **{day_name}**\n\n"
    
    meal_types = {
        "breakfast": "🌅 Завтрак",
        "lunch": "🍽 Обед",
        "dinner": "🌙 Ужин",
        "snack": "🍎 Перекус"
    }
    
    for meal_type, meal_label in meal_types.items():
        if meal_type in meals:
            meal = meals[meal_type]
            text += f"{meal_label}: {meal.get('name', 'Не указано')}\n"
            text += f"   {meal.get('calories', 0)} ккал\n\n"
    
    return text


def format_subscription_status(subscription_type: str, expires_at: datetime = None) -> str:
    """Форматировать статус подписки"""
    if subscription_type == "free":
        return "🆓 Бесплатный тариф"
    
    status_names = {
        "weekly_plan": "🌟 Недельные планы",
        "unlimited": "🚀 Безлимит",
        "personalization": "👤 Персонализация"
    }
    
    name = status_names.get(subscription_type, "Неизвестная подписка")
    
    if expires_at:
        days_left = (expires_at - datetime.utcnow()).days
        if days_left > 0:
            return f"{name} (осталось {days_left} дн.)"
        else:
            return f"{name} (истекла)"
    
    return name


def format_progress_bar(current: float, target: float, length: int = 10) -> str:
    """Создать прогресс-бар"""
    if target == 0:
        return "▱" * length
    
    filled = int((current / target) * length)
    filled = min(filled, length)
    
    bar = "▰" * filled + "▱" * (length - filled)
    percentage = int((current / target) * 100)
    
    return f"{bar} {percentage}%"


def format_diary_summary(entries: List[Dict]) -> str:
    """Форматировать сводку дневника"""
    if not entries:
        return "Дневник пуст"
    
    total_calories = sum(e.get('calories', 0) for e in entries)
    total_protein = sum(e.get('protein', 0) for e in entries)
    total_fats = sum(e.get('fats', 0) for e in entries)
    total_carbs = sum(e.get('carbs', 0) for e in entries)
    
    return f"""
📊 **Сводка за день**

Всего приёмов пищи: {len(entries)}

{format_nutrition_info(total_calories, total_protein, total_fats, total_carbs)}
"""

