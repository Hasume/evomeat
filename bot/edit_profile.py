"""
Диалоги для редактирования профиля
"""
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import select
from database.models import User
from database.database import AsyncSessionLocal
import logging

logger = logging.getLogger(__name__)

# Состояния для редактирования
EDIT_MENU, EDIT_AGE, EDIT_GENDER, EDIT_WEIGHT, EDIT_HEIGHT, EDIT_GOAL, EDIT_ACTIVITY = range(7)


async def show_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню редактирования"""
    query = update.callback_query
    user = query.from_user if query else update.effective_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if not db_user:
            from bot.handlers import get_main_keyboard
            if query:
                await query.message.reply_text("Сначала используйте /start", reply_markup=get_main_keyboard())
            else:
                await update.message.reply_text("Сначала используйте /start", reply_markup=get_main_keyboard())
            return ConversationHandler.END
        
        gender_text = "Мужской" if db_user.gender == "male" else "Женский" if db_user.gender else "Не указан"
        
        goal_map = {
            "lose_weight": "Похудение",
            "maintain": "Поддержание",
            "gain_weight": "Набор массы"
        }
        goal_text = goal_map.get(db_user.goal, "Не указана")
        
        activity_map = {
            "sedentary": "Сидячий",
            "light": "Легкая",
            "moderate": "Средняя",
            "active": "Высокая",
            "very_active": "Очень высокая"
        }
        activity_text = activity_map.get(db_user.activity_level, "Не указана")
        
        text = f"""
⚙️ **Редактирование профиля**

Текущие данные:
👤 Возраст: {db_user.age or '?'} лет
⚧️ Пол: {gender_text}
⚖️ Вес: {db_user.weight or '?'} кг
📏 Рост: {db_user.height or '?'} см
🎯 Цель: {goal_text}
💪 Активность: {activity_text}

Выберите что хотите изменить:
"""
        
        keyboard = [
            [InlineKeyboardButton("👤 Возраст", callback_data="edit_age"),
             InlineKeyboardButton("⚧️ Пол", callback_data="edit_gender")],
            [InlineKeyboardButton("⚖️ Вес", callback_data="edit_weight"),
             InlineKeyboardButton("📏 Рост", callback_data="edit_height")],
            [InlineKeyboardButton("🎯 Цель", callback_data="edit_goal"),
             InlineKeyboardButton("💪 Активность", callback_data="edit_activity")],
            [InlineKeyboardButton("◀️ Назад в настройки", callback_data="back_to_settings")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    
    return EDIT_MENU


async def start_edit_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать редактирование возраста"""
    query = update.callback_query
    await query.answer()
    
    from bot.handlers import get_main_keyboard
    
    from bot.conversation import MIN_AGE, MAX_AGE

    await query.message.reply_text(
        "👤 *Возраст*\n\n"
        f"Сколько вам полных лет? (от {MIN_AGE} до {MAX_AGE})\n\n"
        "_Отменить: /cancel_",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

    return EDIT_AGE


async def save_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранить новый возраст"""
    user = update.effective_user
    from bot.conversation import MIN_AGE, MAX_AGE

    try:
        age = int(update.message.text)
        if age < MIN_AGE:
            # Тот же порог, что и при регистрации: нормы для подростков
            # считаются иначе и требуют участия врача.
            await update.message.reply_text(
                f"Я рассчитываю нормы только для взрослых — с {MIN_AGE} лет.\n\n"
                "Профиль оставил без изменений."
            )
            return EDIT_AGE
        if age > MAX_AGE:
            await update.message.reply_text(f"Проверьте возраст — я ожидаю значение до {MAX_AGE} лет.")
            return EDIT_AGE

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()
            
            if db_user:
                db_user.age = age
                await session.commit()
                await update.message.reply_text(f"✅ Возраст: {age} лет")
                
                # Пересчитываем целевые показатели
                await recalculate_targets(db_user, session, update.message)
        
        return await show_edit_menu(update, context)
        
    except ValueError:
        await update.message.reply_text("Нужно число — например: 28")
        return EDIT_AGE


async def start_edit_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать редактирование пола"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [KeyboardButton("👨 Мужской"), KeyboardButton("👩 Женский")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await query.message.reply_text(
        "⚧️ **Редактирование пола**\n\n"
        "Выберите ваш пол:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    
    return EDIT_GENDER


async def save_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранить новый пол"""
    user = update.effective_user
    gender_text = update.message.text
    
    if "Муж" in gender_text:
        gender = "male"
    elif "Жен" in gender_text:
        gender = "female"
    else:
        await update.message.reply_text("Выберите вариант кнопкой ниже.")
        return EDIT_GENDER
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.gender = gender
            await session.commit()
            gender_name = "Мужской" if gender == "male" else "Женский"
            await update.message.reply_text(f"✅ Пол: {gender_name}")
            
            # Пересчитываем целевые показатели
            await recalculate_targets(db_user, session, update.message)
    
    return await show_edit_menu(update, context)


async def start_edit_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать редактирование веса"""
    query = update.callback_query
    await query.answer()
    
    from bot.handlers import get_main_keyboard
    
    await query.message.reply_text(
        "⚖️ **Редактирование веса**\n\n"
        "Введите ваш текущий вес в килограммах (например: 75 или 70.5):\n\n"
        "💡 Или /cancel для отмены",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
    
    return EDIT_WEIGHT


async def save_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранить новый вес"""
    user = update.effective_user
    
    try:
        weight = float(update.message.text.replace(',', '.'))
        if weight < 30 or weight > 300:
            await update.message.reply_text("Проверьте вес — я ожидаю значение от 30 до 300 кг.")
            return EDIT_WEIGHT
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()
            
            if db_user:
                db_user.weight = weight
                await session.commit()
                await update.message.reply_text(f"✅ Вес: {weight} кг")
                
                # Пересчитываем целевые показатели
                await recalculate_targets(db_user, session, update.message)
        
        return await show_edit_menu(update, context)
        
    except ValueError:
        await update.message.reply_text("Нужно число — например: 72 или 72,5")
        return EDIT_WEIGHT


async def start_edit_height(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать редактирование роста"""
    query = update.callback_query
    await query.answer()
    
    from bot.handlers import get_main_keyboard
    
    await query.message.reply_text(
        "📏 **Редактирование роста**\n\n"
        "Введите ваш рост в сантиметрах (например: 175):\n\n"
        "💡 Или /cancel для отмены",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
    
    return EDIT_HEIGHT


async def save_height(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранить новый рост"""
    user = update.effective_user
    
    try:
        height = float(update.message.text.replace(',', '.'))
        if height < 100 or height > 250:
            await update.message.reply_text("Проверьте рост — я ожидаю значение от 100 до 250 см.")
            return EDIT_HEIGHT
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()
            
            if db_user:
                db_user.height = height
                await session.commit()
                await update.message.reply_text(f"✅ Рост: {height} см")
                
                # Пересчитываем целевые показатели
                await recalculate_targets(db_user, session, update.message)
        
        return await show_edit_menu(update, context)
        
    except ValueError:
        await update.message.reply_text("Нужно число в сантиметрах — например: 175")
        return EDIT_HEIGHT


async def start_edit_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать редактирование цели"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [KeyboardButton("🔽 Похудение")],
        [KeyboardButton("⚖️ Поддержание веса")],
        [KeyboardButton("🔼 Набор массы")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await query.message.reply_text(
        "🎯 **Редактирование цели**\n\n"
        "Какая у вас цель?",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    
    return EDIT_GOAL


async def save_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранить новую цель"""
    user = update.effective_user
    goal_text = update.message.text
    
    if "Похудение" in goal_text:
        goal = "lose_weight"
        goal_name = "Похудение"
    elif "Поддержание" in goal_text:
        goal = "maintain"
        goal_name = "Поддержание веса"
    elif "Набор" in goal_text:
        goal = "gain_weight"
        goal_name = "Набор массы"
    else:
        await update.message.reply_text("Выберите вариант кнопкой ниже.")
        return EDIT_GOAL
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.goal = goal
            await session.commit()
            await update.message.reply_text(f"✅ Цель: {goal_name}")
            
            # Пересчитываем целевые показатели
            await recalculate_targets(db_user, session, update.message)
    
    return await show_edit_menu(update, context)


async def start_edit_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать редактирование уровня активности"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [KeyboardButton("🛋 Сидячий образ жизни")],
        [KeyboardButton("🚶 Легкая активность (1-3 раза/неделю)")],
        [KeyboardButton("🏃 Средняя активность (3-5 раз/неделю)")],
        [KeyboardButton("💪 Высокая активность (6-7 раз/неделю)")],
        [KeyboardButton("🏋️ Очень высокая (2 раза в день)")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await query.message.reply_text(
        "💪 **Редактирование уровня активности**\n\n"
        "Как часто вы занимаетесь спортом?",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    
    return EDIT_ACTIVITY


async def save_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранить новый уровень активности"""
    user = update.effective_user
    activity_text = update.message.text
    
    if "Сидячий" in activity_text:
        activity = "sedentary"
        activity_name = "Сидячий"
    elif "Легкая" in activity_text:
        activity = "light"
        activity_name = "Легкая"
    elif "Средняя" in activity_text:
        activity = "moderate"
        activity_name = "Средняя"
    elif "Высокая" in activity_text:
        activity = "active"
        activity_name = "Высокая"
    elif "Очень высокая" in activity_text:
        activity = "very_active"
        activity_name = "Очень высокая"
    else:
        await update.message.reply_text("Выберите вариант кнопкой ниже.")
        return EDIT_ACTIVITY
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.activity_level = activity
            await session.commit()
            await update.message.reply_text(f"✅ Активность: {activity_name}")
            
            # Пересчитываем целевые показатели
            await recalculate_targets(db_user, session, update.message)
    
    return await show_edit_menu(update, context)


async def recalculate_targets(db_user, session, message=None):
    """
    Пересчитать целевые показатели после изменения данных.

    Если расчёт упёрся в безопасный минимум, сообщаем об этом пользователю —
    иначе новая цифра выглядит как ошибка бота.
    """
    if not all([db_user.age, db_user.weight, db_user.height, db_user.gender,
                db_user.activity_level, db_user.goal]):
        return

    from services import AIService
    ai_service = AIService()

    calorie_data = await ai_service.calculate_daily_calories(
        weight=db_user.weight,
        height=db_user.height,
        age=db_user.age,
        gender=db_user.gender,
        activity_level=db_user.activity_level,
        goal=db_user.goal
    )

    db_user.target_calories = calorie_data['calories']
    db_user.target_protein = calorie_data['protein']
    db_user.target_fats = calorie_data['fats']
    db_user.target_carbs = calorie_data['carbs']

    await session.commit()
    logger.info(f"Recalculated targets for user {db_user.telegram_id}")

    if message is not None:
        from bot.handlers import format_target_warnings, safe_reply
        warnings = format_target_warnings(calorie_data.get("warnings", []))
        await safe_reply(
            message,
            f"🎯 Новая норма: *{calorie_data['calories']}* ккал в день\n"
            f"Б {calorie_data['protein']} · Ж {calorie_data['fats']} · У {calorie_data['carbs']}"
            + warnings,
            parse_mode="Markdown"
        )


async def finish_editing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершить редактирование - вернуться в настройки"""
    query = update.callback_query
    await query.answer()
    
    # Возвращаемся в меню настроек
    from bot.handlers import show_settings
    await show_settings(query)
    
    return ConversationHandler.END


async def cancel_editing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменить редактирование"""
    from bot.handlers import get_main_keyboard
    
    if update.callback_query:
        await update.callback_query.message.reply_text(
            "Хорошо, оставил всё как было.",
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "Хорошо, оставил всё как было.",
            reply_markup=get_main_keyboard()
        )
    
    return ConversationHandler.END

