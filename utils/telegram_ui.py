"""
Утилиты представления: экранирование разметки, человекочитаемые значения,
единые тексты ошибок и определение приёма пищи по времени.
"""
from datetime import datetime
from typing import Optional


# Символы, которые Telegram трактует как разметку в режиме Markdown (legacy).
_MD_SPECIAL = ("_", "*", "`", "[")


def esc(value) -> str:
    """
    Экранировать значение для вставки в сообщение с parse_mode="Markdown".

    Любые данные, пришедшие от пользователя, из БД или от модели, обязаны
    проходить через эту функцию: непарное подчёркивание в имени пользователя
    ломает разметку всего сообщения, а непарная звёздочка приводит к отказу
    Telegram отправить его целиком.
    """
    if value is None:
        return ""
    text = str(value)
    for char in _MD_SPECIAL:
        text = text.replace(char, "\\" + char)
    return text


def join_esc(values, separator: str = ", ") -> str:
    """Склеить список значений, экранировав каждое."""
    return separator.join(esc(v) for v in values or [])


# --- человекочитаемые значения профиля ---

GENDER_NAMES = {"male": "Мужской", "female": "Женский"}

GOAL_NAMES = {
    "lose_weight": "Похудение",
    "maintain": "Поддержание веса",
    "gain_weight": "Набор массы",
}

ACTIVITY_NAMES = {
    "sedentary": "Сидячий образ жизни",
    "light": "Лёгкая активность",
    "moderate": "Средняя активность",
    "active": "Высокая активность",
    "very_active": "Очень высокая активность",
}

SUBSCRIPTION_NAMES = {
    "free": "Бесплатный",
    "weekly_plan": "Недельные планы",
    "unlimited": "Безлимит",
    "personalization": "Персонализация",
}


def human_gender(value: Optional[str]) -> str:
    return GENDER_NAMES.get(value, "не указан")


def human_goal(value: Optional[str]) -> str:
    return GOAL_NAMES.get(value, "не указана")


def human_activity(value: Optional[str]) -> str:
    return ACTIVITY_NAMES.get(value, "не указана")


def human_subscription(value: Optional[str]) -> str:
    return SUBSCRIPTION_NAMES.get(value, "Бесплатный")


# --- приёмы пищи ---

MEAL_BREAKFAST = "завтрак"
MEAL_LUNCH = "обед"
MEAL_DINNER = "ужин"
MEAL_SNACK = "перекус"

MEAL_EMOJI = {
    MEAL_BREAKFAST: "🌅",
    MEAL_LUNCH: "🌞",
    MEAL_DINNER: "🌙",
    MEAL_SNACK: "🍎",
}


def meal_type_by_time(moment: Optional[datetime] = None) -> str:
    """
    Определить приём пищи по времени записи.

    Нужен, чтобы дневник и планы могли раскладывать калории по приёмам:
    раньше в каждую запись писалась строка «приём пищи», и разбивка была невозможна.
    """
    hour = (moment or datetime.now()).hour
    if 5 <= hour < 11:
        return MEAL_BREAKFAST
    if 11 <= hour < 16:
        return MEAL_LUNCH
    if 16 <= hour < 23:
        return MEAL_DINNER
    return MEAL_SNACK


def meal_label(meal_type: Optional[str]) -> str:
    """«🌞 Обед» — для заголовков в дневнике."""
    key = (meal_type or "").lower()
    emoji = MEAL_EMOJI.get(key, "🍽")
    return f"{emoji} {key.capitalize()}" if key else "🍽 Приём пищи"


# --- единые тексты ошибок ---

SERVICE_UNAVAILABLE = (
    "⚠️ Сейчас не получается связаться с сервисом расчёта — это на моей стороне, "
    "с вашим сообщением всё в порядке.\n\n"
    "Попробуйте ещё раз через пару минут."
)

NOT_UNDERSTOOD_FOOD = (
    "Не удалось разобрать, что именно вы съели.\n\n"
    "Напишите продукт и количество, например:\n"
    "• _200г варёной курицы и 100г риса_\n"
    "• _омлет из 2 яиц_"
)


def format_amount(value: float, unit: str = "") -> str:
    """Округлить число до целого, если дробная часть незначима."""
    if value is None:
        return "0"
    rounded = round(float(value), 1)
    text = str(int(rounded)) if rounded == int(rounded) else str(rounded)
    return f"{text}{unit}"
