"""
Валидаторы для проверки данных
"""
from typing import Optional
import re


def validate_weight(weight: float) -> bool:
    """Проверить корректность веса (кг)"""
    return 20 <= weight <= 300


def validate_height(height: float) -> bool:
    """Проверить корректность роста (см)"""
    return 100 <= height <= 250


def validate_age(age: int) -> bool:
    """Проверить корректность возраста"""
    return 10 <= age <= 120


def validate_gender(gender: str) -> bool:
    """Проверить корректность пола"""
    return gender.lower() in ["male", "female", "мужской", "женский"]


def validate_activity_level(level: str) -> bool:
    """Проверить корректность уровня активности"""
    valid_levels = [
        "sedentary", "light", "moderate", "active", "very_active",
        "сидячий", "лёгкий", "средний", "активный", "очень_активный"
    ]
    return level.lower() in valid_levels


def validate_goal(goal: str) -> bool:
    """Проверить корректность цели"""
    valid_goals = [
        "lose_weight", "gain_weight", "maintain",
        "похудение", "набор_массы", "поддержание"
    ]
    return goal.lower() in valid_goals


def validate_portion_size(size: float) -> bool:
    """Проверить корректность размера порции (граммы)"""
    return 0 < size <= 5000


def validate_ingredients(ingredients_text: str) -> Optional[list]:
    """
    Проверить и распарсить список ингредиентов
    Возвращает список ингредиентов или None если некорректно
    """
    if not ingredients_text or len(ingredients_text.strip()) < 2:
        return None
    
    # Разделяем по запятой или точке с запятой
    ingredients = re.split(r'[,;]', ingredients_text)
    ingredients = [ing.strip() for ing in ingredients if ing.strip()]
    
    if len(ingredients) < 1 or len(ingredients) > 20:
        return None
    
    return ingredients


def validate_meal_type(meal_type: str) -> bool:
    """Проверить корректность типа приёма пищи"""
    valid_types = [
        "breakfast", "lunch", "dinner", "snack",
        "завтрак", "обед", "ужин", "перекус"
    ]
    return meal_type.lower() in valid_types


def validate_calories(calories: float) -> bool:
    """Проверить корректность калорий"""
    return 0 <= calories <= 10000


def validate_macros(protein: float, fats: float, carbs: float) -> bool:
    """Проверить корректность БЖУ"""
    return all([
        0 <= protein <= 1000,
        0 <= fats <= 1000,
        0 <= carbs <= 1000
    ])


def normalize_gender(gender: str) -> str:
    """Нормализовать пол к стандартному формату"""
    male_variants = ["male", "m", "мужской", "м"]
    female_variants = ["female", "f", "женский", "ж"]
    
    gender_lower = gender.lower()
    
    if gender_lower in male_variants:
        return "male"
    elif gender_lower in female_variants:
        return "female"
    
    return "male"  # По умолчанию


def normalize_activity_level(level: str) -> str:
    """Нормализовать уровень активности к стандартному формату"""
    mapping = {
        "сидячий": "sedentary",
        "лёгкий": "light",
        "средний": "moderate",
        "активный": "active",
        "очень_активный": "very_active"
    }
    
    return mapping.get(level.lower(), level.lower())


def normalize_goal(goal: str) -> str:
    """Нормализовать цель к стандартному формату"""
    mapping = {
        "похудение": "lose_weight",
        "набор_массы": "gain_weight",
        "поддержание": "maintain"
    }
    
    return mapping.get(goal.lower(), goal.lower())

