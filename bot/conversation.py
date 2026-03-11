"""
Многошаговые диалоги (conversations) для бота
"""
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import select
from database.models import User, SubscriptionType
from database.database import AsyncSessionLocal
import logging

logger = logging.getLogger(__name__)

# Состояния для диалога регистрации
AGE, GENDER, WEIGHT, HEIGHT, GOAL, ACTIVITY, ALLERGIES, DIET, CONFIRM = range(9)


async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало регистрации - запрос возраста"""
    user = update.effective_user
    
    # Проверяем, не зарегистрирован ли уже
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        # Если пользователь новый - создаем базовую запись
        if not db_user:
            db_user = User(
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                subscription_type=SubscriptionType.FREE
            )
            session.add(db_user)
            await session.commit()
            logger.info(f"Created basic user record for {user.id}")
    
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "Давайте настроим ваш профиль для персональных рекомендаций.\n\n"
        "📝 **Шаг 1/8: Возраст** _(обязательно)_\n\n"
        "Сколько вам полных лет?\n"
        "Например: 25\n\n"
        "💡 _Для отмены регистрации введите /cancel_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    
    return AGE


async def registration_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение возраста, запрос пола"""
    try:
        age = int(update.message.text)
        if age < 10 or age > 100:
            await update.message.reply_text(
                "❌ Пожалуйста, укажите реальный возраст (от 10 до 100 лет)"
            )
            return AGE
        
        context.user_data['age'] = age
        
        # Клавиатура для выбора пола
        keyboard = [
            [KeyboardButton("👨 Мужской"), KeyboardButton("👩 Женский")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        
        await update.message.reply_text(
            "✅ Отлично!\n\n"
            "📝 **Шаг 2/8: Пол** _(обязательно)_\n\n"
            "Выберите ваш пол:",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        
        return GENDER
        
    except ValueError:
        await update.message.reply_text(
            "❌ Пожалуйста, введите число (например: 25)"
        )
        return AGE


async def registration_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение пола, запрос веса"""
    gender_text = update.message.text
    
    if "Муж" in gender_text:
        context.user_data['gender'] = "male"
    elif "Жен" in gender_text:
        context.user_data['gender'] = "female"
    else:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите пол с помощью кнопок"
        )
        return GENDER
    
    await update.message.reply_text(
        "✅ Отлично!\n\n"
        "📝 **Шаг 3/8: Вес** _(обязательно)_\n\n"
        "Укажите ваш текущий вес в килограммах\n"
        "Например: 75 или 70.5",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    
    return WEIGHT


async def registration_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение веса, запрос роста"""
    try:
        weight = float(update.message.text.replace(',', '.'))
        if weight < 30 or weight > 300:
            await update.message.reply_text(
                "❌ Пожалуйста, укажите реальный вес (от 30 до 300 кг)"
            )
            return WEIGHT
        
        context.user_data['weight'] = weight
        
        await update.message.reply_text(
            "✅ Отлично!\n\n"
            "📝 **Шаг 4/8: Рост** _(обязательно)_\n\n"
            "Укажите ваш рост в сантиметрах\n"
            "Например: 175",
            parse_mode="Markdown"
        )
        
        return HEIGHT
        
    except ValueError:
        await update.message.reply_text(
            "❌ Пожалуйста, введите число (например: 70 или 70.5)"
        )
        return WEIGHT


async def registration_height(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение роста, запрос цели"""
    try:
        height = float(update.message.text.replace(',', '.'))
        if height < 100 or height > 250:
            await update.message.reply_text(
                "❌ Пожалуйста, укажите реальный рост (от 100 до 250 см)"
            )
            return HEIGHT
        
        context.user_data['height'] = height
        
        # Клавиатура для выбора цели
        keyboard = [
            [KeyboardButton("🔽 Похудение")],
            [KeyboardButton("⚖️ Поддержание веса")],
            [KeyboardButton("🔼 Набор массы")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        
        await update.message.reply_text(
            "✅ Отлично!\n\n"
            "📝 **Шаг 5/8: Цель** _(обязательно)_\n\n"
            "Какая у вас цель?",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        
        return GOAL
        
    except ValueError:
        await update.message.reply_text(
            "❌ Пожалуйста, введите число (например: 175)"
        )
        return HEIGHT


async def registration_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение цели, запрос уровня активности"""
    goal_text = update.message.text
    
    if "Похудение" in goal_text:
        context.user_data['goal'] = "lose_weight"
    elif "Поддержание" in goal_text:
        context.user_data['goal'] = "maintain"
    elif "Набор" in goal_text:
        context.user_data['goal'] = "gain_weight"
    else:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите цель с помощью кнопок"
        )
        return GOAL
    
    # Клавиатура для выбора активности
    keyboard = [
        [KeyboardButton("🛋 Сидячий образ жизни")],
        [KeyboardButton("🚶 Легкая активность (1-3 раза/неделю)")],
        [KeyboardButton("🏃 Средняя активность (3-5 раз/неделю)")],
        [KeyboardButton("💪 Высокая активность (6-7 раз/неделю)")],
        [KeyboardButton("🏋️ Очень высокая (2 раза в день)")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "✅ Отлично!\n\n"
        "📝 **Шаг 6/8: Уровень активности** _(обязательно)_\n\n"
        "Как часто вы занимаетесь спортом?",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    
    return ACTIVITY


async def registration_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение уровня активности, запрос аллергий"""
    activity_text = update.message.text
    
    if "Сидячий" in activity_text:
        context.user_data['activity_level'] = "sedentary"
    elif "Легкая" in activity_text:
        context.user_data['activity_level'] = "light"
    elif "Средняя" in activity_text:
        context.user_data['activity_level'] = "moderate"
    elif "Высокая" in activity_text:
        context.user_data['activity_level'] = "active"
    elif "Очень высокая" in activity_text:
        context.user_data['activity_level'] = "very_active"
    else:
        await update.message.reply_text(
            "❌ Пожалуйста, выберите уровень активности с помощью кнопок"
        )
        return ACTIVITY
    
    keyboard = [
        [KeyboardButton("Нет аллергий ✅")],
        [KeyboardButton("⏭ Пропустить")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "✅ Отлично!\n\n"
        "📝 **Шаг 7/8: Аллергии** _(необязательно)_\n\n"
        "Есть ли у вас аллергии на продукты?\n\n"
        "• Если есть - перечислите через запятую (например: молоко, орехи)\n"
        "• Если нет - нажмите \"Нет аллергий ✅\"\n"
        "• Можно пропустить и заполнить позже",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    
    return ALLERGIES


async def registration_allergies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение аллергий, запрос диетических предпочтений"""
    allergies_text = update.message.text
    
    if allergies_text == "Нет аллергий ✅" or allergies_text == "⏭ Пропустить":
        context.user_data['allergies'] = []
    else:
        # Парсим список аллергий
        allergies = [a.strip() for a in allergies_text.split(',') if a.strip()]
        context.user_data['allergies'] = allergies
    
    keyboard = [
        [KeyboardButton("Нет предпочтений ✅")],
        [KeyboardButton("⏭ Пропустить")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "✅ Отлично!\n\n"
        "📝 **Шаг 8/8: Диетические предпочтения** _(необязательно)_\n\n"
        "Есть ли у вас диетические предпочтения?\n\n"
        "• Например: вегетарианство, веганство, без глютена\n"
        "• Если нет - нажмите \"Нет предпочтений ✅\"\n"
        "• Можно пропустить и заполнить позже",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    
    return DIET


async def registration_diet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение диеты, показ сводки и подтверждение"""
    diet_text = update.message.text
    
    if diet_text == "Нет предпочтений ✅" or diet_text == "⏭ Пропустить":
        context.user_data['dietary_preferences'] = []
    else:
        # Парсим список предпочтений
        prefs = [p.strip() for p in diet_text.split(',') if p.strip()]
        context.user_data['dietary_preferences'] = prefs
    
    # Формируем сводку
    data = context.user_data
    
    gender_text = "Мужской" if data['gender'] == "male" else "Женский"
    
    goal_map = {
        "lose_weight": "Похудение",
        "maintain": "Поддержание веса",
        "gain_weight": "Набор массы"
    }
    goal_text = goal_map.get(data['goal'], data['goal'])
    
    activity_map = {
        "sedentary": "Сидячий",
        "light": "Легкая",
        "moderate": "Средняя",
        "active": "Высокая",
        "very_active": "Очень высокая"
    }
    activity_text = activity_map.get(data['activity_level'], data['activity_level'])
    
    allergies_text = ', '.join(data['allergies']) if data['allergies'] else "Нет"
    diet_preferences_text = ', '.join(data['dietary_preferences']) if data['dietary_preferences'] else "Нет"
    
    summary = f"""
📋 **Проверьте ваши данные:**

👤 Возраст: {data['age']} лет
⚧️ Пол: {gender_text}
⚖️ Вес: {data['weight']} кг
📏 Рост: {data['height']} см
🎯 Цель: {goal_text}
💪 Активность: {activity_text}
🚫 Аллергии: {allergies_text}
🥗 Диета: {diet_preferences_text}

Все верно?
"""
    
    keyboard = [
        [KeyboardButton("✅ Да, все верно"), KeyboardButton("❌ Начать заново")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        summary,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    
    return CONFIRM


async def registration_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение и сохранение данных в БД"""
    confirmation = update.message.text
    
    if "Начать заново" in confirmation:
        await update.message.reply_text(
            "Хорошо, давайте начнем сначала!\n\n"
            "📝 **Шаг 1/8: Возраст**\n\n"
            "Сколько вам полных лет?",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.clear()
        return AGE
    
    if "Да, все верно" not in confirmation:
        await update.message.reply_text(
            "Пожалуйста, используйте кнопки для ответа"
        )
        return CONFIRM
    
    # Сохраняем данные в БД
    user = update.effective_user
    data = context.user_data
    
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()
            
            if db_user:
                # Обновляем данные
                db_user.age = data['age']
                db_user.gender = data['gender']
                db_user.weight = data['weight']
                db_user.height = data['height']
                db_user.goal = data['goal']
                db_user.activity_level = data['activity_level']
                db_user.allergies = data['allergies'] if data['allergies'] else None
                db_user.dietary_preferences = data['dietary_preferences'] if data['dietary_preferences'] else None
                
                # Рассчитываем целевые показатели
                from services import AIService
                ai_service = AIService()
                
                calorie_data = await ai_service.calculate_daily_calories(
                    weight=data['weight'],
                    height=data['height'],
                    age=data['age'],
                    gender=data['gender'],
                    activity_level=data['activity_level'],
                    goal=data['goal']
                )
                
                db_user.target_calories = calorie_data['calories']
                db_user.target_protein = calorie_data['protein']
                db_user.target_fats = calorie_data['fats']
                db_user.target_carbs = calorie_data['carbs']
                
                await session.commit()
                
                logger.info(f"User {user.id} completed registration")
                
                # Клавиатура
                from bot.handlers import get_main_keyboard
                
                await update.message.reply_text(
                    f"🎉 **Регистрация завершена!**\n\n"
                    f"Ваши целевые показатели:\n"
                    f"🔥 Калории: {calorie_data['calories']} ккал/день\n"
                    f"🥩 Белки: {calorie_data['protein']} г\n"
                    f"🧈 Жиры: {calorie_data['fats']} г\n"
                    f"🍞 Углеводы: {calorie_data['carbs']} г",
                    parse_mode="Markdown"
                )
                
                # Отправляем информацию о возможностях бота
                await update.message.reply_text(
                    "🤖 **Что я умею:**\n\n"
                    
                    "🍳 **Поиск рецептов**\n"
                    "Напишите список ингредиентов (например: \"курица, рис, морковь\") "
                    "и я подберу вкусные рецепты. Учитываю ваши аллергии и диету!\n\n"
                    
                    "📊 **Подсчет калорий и БЖУ**\n"
                    "Опишите что съели (например: \"200г курицы вареной, 100г гречки\") "
                    "и я точно посчитаю калории, белки, жиры и углеводы.\n\n"
                    
                    "📝 **Дневник питания**\n"
                    "Добавляйте приемы пищи в дневник и отслеживайте прогресс к вашим целям.\n\n"
                    
                    "💬 **Советы по питанию**\n"
                    "Задавайте любые вопросы о питании и получайте экспертные ответы.\n\n"
                    
                    "💡 _Вы всегда можете изменить данные:_\n"
                    "• Профиль: /profile\n"
                    "• Аллергии/диета: просто напишите \"У меня аллергия на...\"\n\n"
                    "Используйте кнопки ниже для навигации 👇",
                    parse_mode="Markdown",
                    reply_markup=get_main_keyboard()
                )
                
                context.user_data.clear()
                return ConversationHandler.END
                
    except Exception as e:
        logger.error(f"Error saving registration data: {e}", exc_info=True)
        await update.message.reply_text(
            "😔 Произошла ошибка при сохранении данных. Попробуйте позже.",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.clear()
        return ConversationHandler.END


async def registration_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена регистрации"""
    from bot.handlers import get_main_keyboard
    
    await update.message.reply_text(
        "❌ Регистрация отменена.\n\n"
        "Вы можете начать заново нажав на кнопки ниже или используя /start",
        reply_markup=get_main_keyboard()
    )
    
    context.user_data.clear()
    return ConversationHandler.END

