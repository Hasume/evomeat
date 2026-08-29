"""
Многошаговые диалоги (conversations) для бота
"""
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import select
from database.models import User, SubscriptionType
from database.database import AsyncSessionLocal
from utils.telegram_ui import esc, human_goal, human_activity, human_gender
import logging

logger = logging.getLogger(__name__)

# Состояния для диалога регистрации
AGE, GENDER, WEIGHT, HEIGHT, GOAL, ACTIVITY, ALLERGIES, DIET, CONFIRM = range(9)

# Ниже этого возраста нормы питания рассчитываются иначе (формула
# Миффлина — Сан Жеора выведена для взрослых), а дефицит калорий требует
# наблюдения врача, поэтому регистрацию не продолжаем.
MIN_AGE = 18
MAX_AGE = 100

TOTAL_STEPS = 8

# Варианты ответа, которые означают «ничего нет», а не название ограничения.
EMPTY_ANSWERS = {
    "нет", "нету", "не", "-", "—", "ничего", "никаких", "отсутствуют",
    "нет аллергий", "нет предпочтений", "нет ограничений", "без ограничений",
    "все ем", "всё ем", "любая", "обычное", "no", "none", "пропустить", "скип",
}

SKIP_ALLERGIES = "Нет аллергий ✅"
SKIP_DIET = "Нет ограничений ✅"


def _is_empty_answer(text: str) -> bool:
    """Понять, что пользователь ответил «ничего нет», а не назвал ограничение."""
    cleaned = (text or "").strip().lower().strip(".!")
    cleaned = cleaned.replace("✅", "").replace("⏭", "").strip()
    return cleaned in EMPTY_ANSWERS


def _parse_list(text: str) -> list:
    """Разобрать перечисление через запятую, отбросив «нет» и мусор."""
    if _is_empty_answer(text):
        return []
    items = [part.strip().lower() for part in text.replace(";", ",").split(",")]
    return [item for item in items if item and not _is_empty_answer(item)][:15]


def _step(number: int, title: str, required: bool = True) -> str:
    mark = "обязательно" if required else "можно пропустить"
    return f"Шаг {number} из {TOTAL_STEPS} · {title} _({mark})_"


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
        f"👋 Привет, {esc(user.first_name)}!\n\n"
        "Я помогу считать калории без ручного ввода: пишите, что съели, "
        "обычными словами — остальное сделаю сам.\n\n"
        "Сначала пара вопросов, чтобы рассчитать вашу норму. Это займёт минуту.\n\n"
        f"📝 {_step(1, 'Возраст')}\n\n"
        "Сколько вам полных лет?\n"
        "_Например: 28_\n\n"
        "Передумали — отправьте /cancel",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )

    return AGE


async def registration_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение возраста, запрос пола"""
    raw = update.message.text.strip()

    try:
        age = int(raw)
    except ValueError:
        await update.message.reply_text(
            "Не понял возраст. Напишите числом, например: *28*",
            parse_mode="Markdown"
        )
        return AGE

    if age < MIN_AGE:
        # Нормы для детей и подростков считаются по другим формулам и требуют
        # участия врача — здесь честно останавливаемся.
        logger.info(f"Registration stopped: user {update.effective_user.id} is under {MIN_AGE}")
        await update.message.reply_text(
            "Спасибо за честность 🙏\n\n"
            f"Я умею считать нормы только для взрослых — с {MIN_AGE} лет. "
            "В вашем возрасте потребность в калориях и белке зависит от роста и развития, "
            "и рассчитывать её «по формуле» может быть вредно.\n\n"
            "Лучше обсудить питание с педиатром или взрослыми в семье. "
            "Здоровья вам! 💚",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.clear()
        return ConversationHandler.END

    if age > MAX_AGE:
        await update.message.reply_text(
            f"Проверьте, пожалуйста, возраст — я ожидаю значение до {MAX_AGE} лет."
        )
        return AGE

    context.user_data['age'] = age

    keyboard = [[KeyboardButton("👨 Мужской"), KeyboardButton("👩 Женский")]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=True,
        input_field_placeholder="Выберите кнопкой ниже"
    )

    await update.message.reply_text(
        f"Принято ✅\n\n"
        f"📝 {_step(2, 'Пол')}\n\n"
        "Нужен для формулы расчёта — у мужчин и женщин она различается.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

    return GENDER


async def registration_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение пола, запрос веса"""
    gender_text = update.message.text

    if "Муж" in gender_text:
        context.user_data['gender'] = "male"
    elif "Жен" in gender_text:
        context.user_data['gender'] = "female"
    else:
        await update.message.reply_text(
            "Выберите вариант кнопкой ниже — «Мужской» или «Женский»."
        )
        return GENDER

    await update.message.reply_text(
        f"Принято ✅\n\n"
        f"📝 {_step(3, 'Вес')}\n\n"
        "Сколько вы весите сейчас, в килограммах?\n"
        "_Например: 72 или 72,5_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )

    return WEIGHT


async def registration_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение веса, запрос роста"""
    try:
        weight = float(update.message.text.replace(',', '.').strip())
    except ValueError:
        await update.message.reply_text(
            "Не понял вес. Напишите числом, например: *72* или *72,5*",
            parse_mode="Markdown"
        )
        return WEIGHT

    if weight < 30 or weight > 300:
        await update.message.reply_text(
            "Проверьте, пожалуйста, вес — я ожидаю значение от 30 до 300 кг."
        )
        return WEIGHT

    context.user_data['weight'] = weight

    await update.message.reply_text(
        f"Принято ✅\n\n"
        f"📝 {_step(4, 'Рост')}\n\n"
        "Ваш рост в сантиметрах?\n"
        "_Например: 175_",
        parse_mode="Markdown"
    )

    return HEIGHT


async def registration_height(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение роста, запрос цели"""
    try:
        height = float(update.message.text.replace(',', '.').strip())
    except ValueError:
        await update.message.reply_text(
            "Не понял рост. Напишите числом в сантиметрах, например: *175*",
            parse_mode="Markdown"
        )
        return HEIGHT

    if height < 100 or height > 250:
        await update.message.reply_text(
            "Проверьте, пожалуйста, рост — я ожидаю значение от 100 до 250 см."
        )
        return HEIGHT

    context.user_data['height'] = height

    keyboard = [
        [KeyboardButton("🔽 Похудение")],
        [KeyboardButton("⚖️ Поддержание веса")],
        [KeyboardButton("🔼 Набор массы")]
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=True,
        input_field_placeholder="Выберите цель кнопкой"
    )

    await update.message.reply_text(
        f"Принято ✅\n\n"
        f"📝 {_step(5, 'Цель')}\n\n"
        "К чему идём?",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

    return GOAL


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
        await update.message.reply_text("Выберите цель кнопкой ниже.")
        return GOAL

    keyboard = [
        [KeyboardButton("🛋 Сидячий образ жизни")],
        [KeyboardButton("🚶 Лёгкая активность (1-3 раза в неделю)")],
        [KeyboardButton("🏃 Средняя активность (3-5 раз в неделю)")],
        [KeyboardButton("💪 Высокая активность (6-7 раз в неделю)")],
        [KeyboardButton("🏋️ Очень высокая (2 раза в день)")]
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=True,
        input_field_placeholder="Выберите уровень кнопкой"
    )

    await update.message.reply_text(
        f"Принято ✅\n\n"
        f"📝 {_step(6, 'Активность')}\n\n"
        "Сколько двигаетесь за неделю? Считайте тренировки и активную работу.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

    return ACTIVITY


async def registration_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение уровня активности, запрос аллергий"""
    activity_text = update.message.text

    if "Сидячий" in activity_text:
        context.user_data['activity_level'] = "sedentary"
    elif "Очень высокая" in activity_text:
        context.user_data['activity_level'] = "very_active"
    elif "Лёгкая" in activity_text or "Легкая" in activity_text:
        context.user_data['activity_level'] = "light"
    elif "Средняя" in activity_text:
        context.user_data['activity_level'] = "moderate"
    elif "Высокая" in activity_text:
        context.user_data['activity_level'] = "active"
    else:
        await update.message.reply_text("Выберите уровень активности кнопкой ниже.")
        return ACTIVITY

    keyboard = [[KeyboardButton(SKIP_ALLERGIES)]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=True,
        input_field_placeholder="Перечислите через запятую или нажмите кнопку"
    )

    await update.message.reply_text(
        f"Принято ✅\n\n"
        f"📝 {_step(7, 'Аллергии', required=False)}\n\n"
        "Есть продукты, которые вам нельзя?\n\n"
        "Перечислите через запятую — _молоко, орехи_ — и я никогда не предложу их "
        "ни в рецептах, ни в перекусах.\n\n"
        f"Если аллергий нет, нажмите «{SKIP_ALLERGIES}»",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

    return ALLERGIES


async def registration_allergies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение аллергий, запрос диетических предпочтений"""
    context.user_data['allergies'] = _parse_list(update.message.text)

    keyboard = [[KeyboardButton(SKIP_DIET)]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=True,
        input_field_placeholder="Например: вегетарианство"
    )

    saved = context.user_data['allergies']
    confirm = f"Запомнил: *{esc(', '.join(saved))}* ✅\n\n" if saved else "Принято ✅\n\n"

    await update.message.reply_text(
        confirm +
        f"📝 {_step(8, 'Питание', required=False)}\n\n"
        "Придерживаетесь особого режима питания?\n\n"
        "_Например: вегетарианство, веганство, без глютена, халяль_\n\n"
        f"Если ограничений нет, нажмите «{SKIP_DIET}»",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

    return DIET


async def registration_diet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение диеты, показ сводки и подтверждение"""
    context.user_data['dietary_preferences'] = _parse_list(update.message.text)

    data = context.user_data
    allergies_text = ', '.join(data['allergies']) if data['allergies'] else "нет"
    diet_text = ', '.join(data['dietary_preferences']) if data['dietary_preferences'] else "нет"

    summary = f"""📋 *Проверьте данные*

👤 Возраст: {data['age']} лет
⚧️ Пол: {human_gender(data['gender'])}
⚖️ Вес: {esc(data['weight'])} кг
📏 Рост: {esc(data['height'])} см
🎯 Цель: {human_goal(data['goal'])}
💪 Активность: {human_activity(data['activity_level'])}
🚫 Аллергии: {esc(allergies_text)}
🥗 Питание: {esc(diet_text)}

Всё верно?"""

    keyboard = [
        [KeyboardButton("✅ Да, всё верно")],
        [KeyboardButton("✏️ Заполнить заново")]
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=True,
        input_field_placeholder="Подтвердите кнопкой"
    )

    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=reply_markup)

    return CONFIRM


async def registration_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение и сохранение данных в БД"""
    confirmation = update.message.text

    if "заново" in confirmation.lower():
        await update.message.reply_text(
            "Хорошо, начнём сначала.\n\n"
            f"📝 {_step(1, 'Возраст')}\n\n"
            "Сколько вам полных лет?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.clear()
        return AGE

    if "верно" not in confirmation.lower() and "да" not in confirmation.lower():
        await update.message.reply_text("Подтвердите кнопкой ниже — «Да, всё верно» или «Заполнить заново».")
        return CONFIRM

    user = update.effective_user
    data = context.user_data

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()

            if not db_user:
                await update.message.reply_text(
                    "Не нашёл ваш профиль. Отправьте /start, чтобы начать заново.",
                    reply_markup=ReplyKeyboardRemove()
                )
                context.user_data.clear()
                return ConversationHandler.END

            db_user.age = data['age']
            db_user.gender = data['gender']
            db_user.weight = data['weight']
            db_user.height = data['height']
            db_user.goal = data['goal']
            db_user.activity_level = data['activity_level']
            db_user.allergies = data['allergies'] or None
            db_user.dietary_preferences = data['dietary_preferences'] or None

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

        from bot.handlers import get_main_keyboard, format_target_warnings

        warnings_text = format_target_warnings(calorie_data.get("warnings", []))

        await update.message.reply_text(
            f"🎉 *Готово, профиль настроен!*\n\n"
            f"Ваша норма на день:\n"
            f"🔥 {calorie_data['calories']} ккал\n"
            f"🥩 Белки: {calorie_data['protein']} г\n"
            f"🧈 Жиры: {calorie_data['fats']} г\n"
            f"🍞 Углеводы: {calorie_data['carbs']} г"
            + warnings_text,
            parse_mode="Markdown"
        )

        await update.message.reply_text(
            "Попробуйте прямо сейчас — просто напишите, что вы ели сегодня:\n\n"
            "💬 _«Съел овсянку на молоке и кофе с сахаром»_\n\n"
            "Я посчитаю калории и запишу в дневник. Если чего-то не хватит для точности — переспрошу.\n\n"
            "Ещё умею:\n"
            "🍳 подобрать рецепт из того, что есть дома\n"
            "📸 оценить блюдо по фотографии\n"
            "📈 показать, как прошёл день и чем добрать норму\n\n"
            "Всё это — в кнопках ниже 👇",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )

        context.user_data.clear()
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error saving registration data: {e}", exc_info=True)
        await update.message.reply_text(
            "😔 Не получилось сохранить профиль — это сбой на моей стороне.\n\n"
            "Отправьте /start через минуту, данные не потеряются.",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.clear()
        return ConversationHandler.END


async def registration_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена регистрации"""
    from bot.handlers import get_main_keyboard

    await update.message.reply_text(
        "Хорошо, остановились.\n\n"
        "Без профиля я не смогу считать вашу норму, но калорийность блюд подскажу и так — "
        "просто напишите, что съели.\n\n"
        "Вернуться к настройке: /start",
        reply_markup=get_main_keyboard()
    )

    context.user_data.clear()
    return ConversationHandler.END
