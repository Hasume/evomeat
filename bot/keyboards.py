"""
Клавиатуры для Telegram бота
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню"""
    keyboard = [
        [InlineKeyboardButton("🍳 Найти рецепты", callback_data="find_recipes")],
        [InlineKeyboardButton("📊 Рассчитать калории", callback_data="calc_calories")],
        [InlineKeyboardButton("📝 Дневник питания", callback_data="diary")],
        [InlineKeyboardButton("💎 Премиум функции", callback_data="premium")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_diary_keyboard() -> InlineKeyboardMarkup:
    """Меню дневника питания"""
    keyboard = [
        [InlineKeyboardButton("➕ Добавить приём пищи", callback_data="add_meal")],
        [InlineKeyboardButton("📊 Статистика за день", callback_data="stats_today")],
        [InlineKeyboardButton("📅 Статистика за неделю", callback_data="stats_week")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_meal_type_keyboard() -> InlineKeyboardMarkup:
    """Выбор типа приёма пищи"""
    keyboard = [
        [InlineKeyboardButton("🌅 Завтрак", callback_data="meal_breakfast")],
        [InlineKeyboardButton("🍽 Обед", callback_data="meal_lunch")],
        [InlineKeyboardButton("🌙 Ужин", callback_data="meal_dinner")],
        [InlineKeyboardButton("🍎 Перекус", callback_data="meal_snack")],
        [InlineKeyboardButton("◀️ Отмена", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_premium_keyboard() -> InlineKeyboardMarkup:
    """Меню премиум подписок"""
    keyboard = [
        [InlineKeyboardButton("🌟 Недельные планы - 499₽", callback_data="buy_weekly")],
        [InlineKeyboardButton("🚀 Безлимит - 999₽", callback_data="buy_unlimited")],
        [InlineKeyboardButton("👤 Персонализация - 1499₽", callback_data="buy_personalization")],
        [InlineKeyboardButton("ℹ️ Сравнить тарифы", callback_data="compare_plans")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_settings_keyboard() -> InlineKeyboardMarkup:
    """Меню настроек"""
    keyboard = [
        [InlineKeyboardButton("👤 Профиль", callback_data="edit_profile")],
        [InlineKeyboardButton("🥗 Диета и аллергии", callback_data="edit_diet")],
        [InlineKeyboardButton("🎯 Цели", callback_data="edit_goals")],
        [InlineKeyboardButton("📱 Мои подписки", callback_data="my_subscriptions")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Кнопка отмены"""
    keyboard = [[InlineKeyboardButton("❌ Отменить", callback_data="cancel")]]
    return InlineKeyboardMarkup(keyboard)


def get_confirm_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение действия"""
    keyboard = [
        [InlineKeyboardButton("✅ Да", callback_data="confirm_yes")],
        [InlineKeyboardButton("❌ Нет", callback_data="confirm_no")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_recipe_actions_keyboard(recipe_id: int) -> InlineKeyboardMarkup:
    """Действия с рецептом"""
    keyboard = [
        [InlineKeyboardButton("➕ Добавить в дневник", callback_data=f"add_recipe_{recipe_id}")],
        [InlineKeyboardButton("💾 Сохранить рецепт", callback_data=f"save_recipe_{recipe_id}")],
        [InlineKeyboardButton("📤 Поделиться", callback_data=f"share_recipe_{recipe_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_activity_level_keyboard() -> InlineKeyboardMarkup:
    """Выбор уровня активности"""
    keyboard = [
        [InlineKeyboardButton("🛋 Сидячий", callback_data="activity_sedentary")],
        [InlineKeyboardButton("🚶 Лёгкая активность", callback_data="activity_light")],
        [InlineKeyboardButton("🏃 Средняя активность", callback_data="activity_moderate")],
        [InlineKeyboardButton("💪 Активный", callback_data="activity_active")],
        [InlineKeyboardButton("🏋️ Очень активный", callback_data="activity_very_active")],
        [InlineKeyboardButton("◀️ Отмена", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_goal_keyboard() -> InlineKeyboardMarkup:
    """Выбор цели"""
    keyboard = [
        [InlineKeyboardButton("📉 Похудение", callback_data="goal_lose")],
        [InlineKeyboardButton("📈 Набор массы", callback_data="goal_gain")],
        [InlineKeyboardButton("➡️ Поддержание", callback_data="goal_maintain")],
        [InlineKeyboardButton("◀️ Отмена", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_gender_keyboard() -> InlineKeyboardMarkup:
    """Выбор пола"""
    keyboard = [
        [InlineKeyboardButton("👨 Мужской", callback_data="gender_male")],
        [InlineKeyboardButton("👩 Женский", callback_data="gender_female")],
        [InlineKeyboardButton("◀️ Отмена", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

