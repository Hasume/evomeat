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
    
    await query.message.reply_text(
        "👤 **Редактирование возраста**\n\n"
        "Введите ваш возраст (от 10 до 100 лет):\n\n"
        "💡 Или /cancel для отмены",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
    
    return EDIT_AGE


async def save_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранить новый возраст"""
    user = update.effective_user
    
    try:
        age = int(update.message.text)
        if age < 10 or age > 100:
            await update.message.reply_text("❌ Возраст должен быть от 10 до 100 лет")
            return EDIT_AGE
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()
            
            if db_user:
                db_user.age = age
                await session.commit()
                await update.message.reply_text(f"✅ Возраст обновлен: {age} лет")
                
                # Пересчитываем целевые показатели
                await recalculate_targets(db_user, session)
        
        return await show_edit_menu(update, context)
        
    except ValueError:
        await update.message.reply_text("❌ Введите число (например: 25)")
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
        await update.message.reply_text("❌ Используйте кнопки")
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
            await update.message.reply_text(f"✅ Пол обновлен: {gender_name}")
            
            # Пересчитываем целевые показатели
            await recalculate_targets(db_user, session)
    
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
            await update.message.reply_text("❌ Вес должен быть от 30 до 300 кг")
            return EDIT_WEIGHT
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()
            
            if db_user:
                db_user.weight = weight
                await session.commit()
                await update.message.reply_text(f"✅ Вес обновлен: {weight} кг")
                
                # Пересчитываем целевые показатели
                await recalculate_targets(db_user, session)
        
        return await show_edit_menu(update, context)
        
    except ValueError:
        await update.message.reply_text("❌ Введите число (например: 70 или 70.5)")
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
            await update.message.reply_text("❌ Рост должен быть от 100 до 250 см")
            return EDIT_HEIGHT
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()
            
            if db_user:
                db_user.height = height
                await session.commit()
                await update.message.reply_text(f"✅ Рост обновлен: {height} см")
                
                # Пересчитываем целевые показатели
                await recalculate_targets(db_user, session)
        
        return await show_edit_menu(update, context)
        
    except ValueError:
        await update.message.reply_text("❌ Введите число (например: 175)")
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
        await update.message.reply_text("❌ Используйте кнопки")
        return EDIT_GOAL
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.goal = goal
            await session.commit()
            await update.message.reply_text(f"✅ Цель обновлена: {goal_name}")
            
            # Пересчитываем целевые показатели
            await recalculate_targets(db_user, session)
    
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
        await update.message.reply_text("❌ Используйте кнопки")
        return EDIT_ACTIVITY
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.activity_level = activity
            await session.commit()
            await update.message.reply_text(f"✅ Уровень активности обновлен: {activity_name}")
            
            # Пересчитываем целевые показатели
            await recalculate_targets(db_user, session)
    
    return await show_edit_menu(update, context)


async def recalculate_targets(db_user, session):
    """Пересчитать целевые показатели после изменения данных"""
    if all([db_user.age, db_user.weight, db_user.height, db_user.gender, db_user.activity_level, db_user.goal]):
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
            "Редактирование отменено",
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "Редактирование отменено",
            reply_markup=get_main_keyboard()
        )
    
    return ConversationHandler.END

