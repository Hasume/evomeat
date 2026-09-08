"""
Обработчики команд Telegram бота
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from sqlalchemy import select
from datetime import datetime, timedelta, date
from database.models import User, DiaryEntry, Recipe, SubscriptionType, DataSource, WeightLog, Challenge
from database.database import AsyncSessionLocal
from services import AIService, NutritionAPI
from services.ai_service import AIServiceUnavailable
from services.statistics_service import statistics_service, get_snack_recommendations, format_snack_recommendations, NutrientStatus
from services.reminder_service import reminder_service
from services.report_service import report_service
from services.challenge_service import challenge_service, CHALLENGE_TYPES
from utils.telegram_ui import (
    esc, human_gender, human_goal, human_activity, human_subscription,
    meal_type_by_time, meal_label, format_amount, MEAL_EMOJI,
    SERVICE_UNAVAILABLE, NOT_UNDERSTOOD_FOOD,
)
from utils.timeutil import (
    get_zone, utc_now, local_now, local_today, local_day_bounds, local_range_bounds, to_local,
    describe_zone, parse_reported_offset, zone_for_offset, zone_from_coordinates, DEFAULT_TZ,
)
from utils.meal_context import parse_meal_context, resolve_moment, describe_when
from utils.water import parse_water, WATER_MEAL_TYPE, water_target_ml, format_litres
from config import settings
import logging
import asyncio
import re

logger = logging.getLogger(__name__)

# Инициализация сервисов
ai_service = AIService()
nutrition_api = NutritionAPI()


# =====================
# ОБЩИЕ ХЕЛПЕРЫ
# =====================

async def keep_typing(update: Update, stop_event: asyncio.Event):
    """
    Поддерживает анимацию 'печатает...' для длительных операций
    """
    try:
        while not stop_event.is_set():
            await update.message.chat.send_action(action="typing")
            await asyncio.sleep(4)  # Обновляем каждые 4 секунды (typing длится 5 сек)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Error in keep_typing: {e}")


def get_main_keyboard():
    """Создает основную клавиатуру для быстрого доступа"""
    keyboard = [
        [KeyboardButton("📊 Мой день"), KeyboardButton("📝 Дневник")],
        [KeyboardButton("🔁 Повторить"), KeyboardButton("🍳 Рецепты")],
        [KeyboardButton("⚙️ Настройки"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        input_field_placeholder="Напишите или скажите, что вы съели"
    )


async def safe_reply(message, text: str, **kwargs):
    """
    Отправить сообщение, не потеряв его из-за разметки.

    Telegram отклоняет всё сообщение целиком, если в Markdown осталась непарная
    управляющая пара. Динамические данные экранируются на входе, но модель может
    вернуть что угодно, поэтому здесь есть запасной путь — отправить как есть.
    """
    try:
        return await message.reply_text(text, **kwargs)
    except BadRequest as e:
        if "parse" not in str(e).lower() and "entity" not in str(e).lower():
            raise
        logger.warning(f"Markdown parse failed, sending as plain text: {e}")
        kwargs.pop("parse_mode", None)
        return await message.reply_text(text, **kwargs)


async def safe_edit(message, text: str, **kwargs):
    """То же, что safe_reply, но для редактирования существующего сообщения."""
    try:
        return await message.edit_text(text, **kwargs)
    except BadRequest as e:
        error = str(e).lower()
        if "not modified" in error:
            return None
        if "parse" not in error and "entity" not in error:
            raise
        logger.warning(f"Markdown parse failed on edit, sending as plain text: {e}")
        kwargs.pop("parse_mode", None)
        return await message.edit_text(text, **kwargs)


def format_target_warnings(warnings) -> str:
    """
    Пояснения к рассчитанной норме.

    Расчёт может упереться в безопасный минимум — пользователь должен понимать,
    почему цифра именно такая, а не думать, что бот ошибся.
    """
    if not warnings:
        return ""

    blocks = []
    if "min_calories" in warnings:
        blocks.append(
            "ℹ️ Расчёт по вашим данным дал меньше, чем безопасный минимум, "
            "поэтому норма поднята до него. Более низкая калорийность возможна "
            "только под наблюдением врача."
        )
    if "deficit_not_advised" in warnings:
        blocks.append(
            "⚠️ При ваших параметрах снижать калорийность не стоит. "
            "Если цель — форма, а не вес, лучше добавить активность и обсудить план со специалистом."
        )
    if "macros_adjusted" in warnings:
        blocks.append("ℹ️ Соотношение Б/Ж/У немного скорректировано, чтобы рацион оставался сбалансированным.")

    return "\n\n" + "\n\n".join(blocks)


async def get_db_user(telegram_id: int):
    """Получить пользователя по telegram_id (или None)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()


def user_zone(db_user):
    """Часовой пояс пользователя (по умолчанию московский)."""
    return get_zone(db_user.timezone if db_user else None)


def render_nutrition_items(items, show_total: bool = True) -> str:
    """Единое представление списка продуктов с их КБЖУ."""
    lines = []
    for item in items:
        cooking = item.get("cooking_method", "")
        note = f", {esc(cooking)}" if cooking and cooking != "готовое" else ""
        lines.append(
            f"• *{esc(item.get('name', 'Продукт'))}* — {esc(item.get('amount', 'порция'))}{note}\n"
            f"  🔥 {format_amount(item.get('calories', 0))} ккал · "
            f"Б {format_amount(item.get('protein', 0))} · "
            f"Ж {format_amount(item.get('fats', 0))} · "
            f"У {format_amount(item.get('carbs', 0))}"
        )

    text = "\n".join(lines)

    if show_total and len(items) > 1:
        total_cal = sum(i.get("calories", 0) for i in items)
        total_p = sum(i.get("protein", 0) for i in items)
        total_f = sum(i.get("fats", 0) for i in items)
        total_c = sum(i.get("carbs", 0) for i in items)
        text += (
            f"\n\n━━━━━━━━━━━━━━━\n"
            f"*Итого:* 🔥 {format_amount(total_cal)} ккал · "
            f"Б {format_amount(total_p)} · Ж {format_amount(total_f)} · У {format_amount(total_c)}"
        )

    return text


async def save_meal_entries(telegram_id: int, items, notes: str = "", source=DataSource.AI_ESTIMATE,
                            moment_utc=None, meal_type: str = None):
    """
    Записать приём пищи в дневник — по одной строке на продукт.

    Раздельные записи нужны, чтобы можно было удалить один продукт, а не весь
    день. Момент записи задаётся снаружи: «вчера на ужин» ложится во вчерашний
    ужин, а не на текущую минуту.

    Returns:
        (entry_ids, meal_type) или (None, None), если пользователь не найден.
    """
    if not items:
        return None, None

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        db_user = result.scalar_one_or_none()
        if not db_user:
            return None, None

        zone = user_zone(db_user)
        moment = moment_utc or utc_now()
        if not meal_type:
            meal_type = meal_type_by_time(to_local(moment, zone))

        entries = []
        for item in items:
            cooking = item.get("cooking_method", "")
            name = item.get("name", "Продукт")
            amount = item.get("amount", "порция")
            title = f"{name} ({amount}{', ' + cooking if cooking and cooking != 'готовое' else ''})"

            entry = DiaryEntry(
                user_id=db_user.id,
                date=moment,
                meal_type=meal_type,
                food_name=title,
                portion_size=1.0,
                calories=item.get("calories", 0),
                protein=item.get("protein", 0),
                fats=item.get("fats", 0),
                carbs=item.get("carbs", 0),
                data_source=source,
                notes=notes or None,
            )
            session.add(entry)
            entries.append(entry)

        await session.commit()
        entry_ids = [e.id for e in entries]

    logger.info(f"User {telegram_id} logged {len(entry_ids)} items as {meal_type}")
    return entry_ids, meal_type


def meal_saved_keyboard(entry_ids) -> InlineKeyboardMarkup:
    """Кнопки под подтверждением записи: отменить ошибочную, посмотреть итог."""
    buttons = [[InlineKeyboardButton("📊 Мой день", callback_data="stats_today")]]
    if entry_ids:
        ids = ",".join(str(i) for i in entry_ids[:10])
        buttons.insert(0, [InlineKeyboardButton("↩️ Отменить запись", callback_data=f"undo_meal_{ids}")])
    return InlineKeyboardMarkup(buttons)


def short_food_name(food_name: str) -> str:
    """«рис (200г, варёное)» → «рис» — для компактных кнопок и списков."""
    return (food_name or "").split(" (")[0].strip() or food_name


async def day_stats_footer(telegram_id: int, day_offset: int = 0) -> str:
    """Строка «за день: X из Y ккал» для подтверждения записи."""
    db_user = await get_db_user(telegram_id)
    if not db_user:
        return ""
    target_date = local_today(user_zone(db_user)) + timedelta(days=day_offset)
    stats = await statistics_service.get_daily_stats(telegram_id, target_date=target_date)
    if not stats:
        return ""

    label = "За день" if day_offset == 0 else ("За вчера" if day_offset == -1 else f"За {target_date.strftime('%d.%m')}")
    remaining = stats["calories_remaining"]
    if remaining >= 0:
        return f"\n\n📊 {label}: *{stats['calories']}* из *{stats['target_calories']}* ккал · осталось *{remaining}*"
    return f"\n\n📊 {label}: *{stats['calories']}* из *{stats['target_calories']}* ккал · перебор на *{abs(remaining)}*"


# =====================
# КОМАНДЫ
# =====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    logger.info(f"🚀 START command from user {user.id} ({user.username or 'no username'})")

    try:
        async with AsyncSessionLocal() as session:
            try:
                result = await session.execute(
                    select(User).where(User.telegram_id == user.id)
                )
                db_user = result.scalar_one_or_none()

                # Профиль уже заполнен — сразу к делу
                if db_user and db_user.age is not None:
                    stats = await statistics_service.get_daily_stats(user.id)
                    eaten = stats["calories"] if stats else 0
                    target = stats["target_calories"] if stats else db_user.target_calories

                    await update.message.reply_text(
                        f"👋 С возвращением, {esc(user.first_name)}!\n\n"
                        f"Сегодня уже набрано *{eaten}* из *{target}* ккал.\n\n"
                        "Напишите или скажите голосом, что съели — запишу и посчитаю.",
                        parse_mode="Markdown",
                        reply_markup=get_main_keyboard()
                    )
                    logger.info(f"✅ START command completed for existing user {user.id}")
                    return

                if not db_user:
                    logger.info(f"Creating new user {user.id}")
                    db_user = User(
                        telegram_id=user.id,
                        username=user.username,
                        first_name=user.first_name,
                        last_name=user.last_name,
                        subscription_type=SubscriptionType.FREE
                    )
                    session.add(db_user)
                    await session.commit()
                    logger.info(f"✅ User {user.id} created, starting registration")

                from bot.conversation import start_registration
                return await start_registration(update, context)

            except Exception as db_error:
                logger.error(f"❌ Database error for user {user.id}: {db_error}", exc_info=True)
                await update.message.reply_text(
                    "Не получилось открыть ваш профиль — это сбой на моей стороне.\n"
                    "Попробуйте ещё раз через минуту.",
                    reply_markup=get_main_keyboard()
                )
                return

    except Exception as e:
        logger.error(f"❌ Critical error in start_command for user {user.id}: {e}", exc_info=True)
        await update.message.reply_text(
            "Что-то пошло не так на моей стороне. Попробуйте позже."
        )


async def send_onboarding_tips(message):
    """Первое объяснение возможностей — после регистрации или настройки пояса."""
    await safe_reply(
        message,
        "Попробуйте прямо сейчас — напишите или скажите голосом, что вы ели:\n\n"
        "💬 _«Съел овсянку на молоке и кофе с сахаром»_\n"
        "🎤 _голосовое сообщение тоже подойдёт_\n\n"
        "Я посчитаю калории и запишу в дневник. Можно записывать задним числом: "
        "_«вчера на ужин пицца»_ — положу во вчерашний ужин.\n\n"
        "Ещё умею:\n"
        "🔁 повторить привычный приём пищи одной кнопкой\n"
        "📸 оценить блюдо по фотографии\n"
        "💧 считать воду — _«выпил стакан воды»_\n"
        "🍳 подобрать рецепт из того, что есть дома\n"
        "📈 показать, как прошёл день и чем добрать норму\n\n"
        "Всё это — в кнопках ниже 👇",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """🤖 *Как со мной общаться*

Главное — просто пишите или говорите обычными словами, команды учить не нужно.

*Записать еду*
_«Съел 200 г риса и куриную грудку»_
_«На завтрак овсянка на молоке и кофе»_
_«Вчера на ужин пицца»_ — запишу во вчерашний ужин
Посчитаю КБЖУ и запишу в дневник. Если для точности не хватит деталей — переспрошу один раз.

*Голосом* 🎤
Запишите голосовое — разберу и посчитаю так же, как текст.

*Повторить* 🔁
Едите одно и то же? Кнопка «Повторить» покажет недавние приёмы пищи — один тап, и записано. Или напишите _«как вчера на завтрак»_.

*Вода* 💧
_«Выпил стакан воды»_, _«0,5 л воды»_ — учту в итогах дня.

*Узнать калорийность, не записывая*
_«Сколько калорий в 100 г творога?»_

*Фото еды* 📸
Пришлите снимок тарелки — оценю блюдо и калорийность. С подписью получится точнее.

*Рецепты*
_«Что приготовить из курицы, риса и моркови?»_
Учитываю ваши аллергии и режим питания.

*Итоги дня*
Кнопка «📊 Мой день» — сколько набрано, чего не хватает и чем добрать.

⚙️ В настройках — профиль, аллергии, часовой пояс, напоминания и цели.

Команды: /start /diary /stats /repeat /goals /timezone /profile /help"""

    await safe_reply(update.message, help_text, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def recipes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /recipes"""
    db_user = await get_db_user(update.effective_user.id)
    limits = ""
    if db_user and (db_user.allergies or db_user.dietary_preferences):
        parts = []
        if db_user.allergies:
            parts.append(f"без {', '.join(db_user.allergies)}")
        if db_user.dietary_preferences:
            parts.append(", ".join(db_user.dietary_preferences))
        limits = f"\n\n_Учту ваши ограничения: {esc('; '.join(parts))}._"

    await safe_reply(
        update.message,
        "🍳 *Что приготовить?*\n\n"
        "Напишите, какие продукты есть дома — подберу рецепты из них.\n\n"
        "_курица, рис, помидоры_\n"
        "_творог, яйца, мука_" + limits,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📖 Сохранённые рецепты", callback_data="saved_recipes")
        ]])
    )


async def calories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /calories — показывает цели по КБЖУ"""
    db_user = await get_db_user(update.effective_user.id)

    if db_user and db_user.target_calories:
        await safe_reply(
            update.message,
            f"🎯 *Ваша норма на день*\n\n"
            f"🔥 Калории: {db_user.target_calories} ккал\n"
            f"🥩 Белки: {format_amount(db_user.target_protein)} г\n"
            f"🧈 Жиры: {format_amount(db_user.target_fats)} г\n"
            f"🍞 Углеводы: {format_amount(db_user.target_carbs)} г\n\n"
            "Хотите узнать калорийность блюда — просто напишите его.\n"
            "Изменить норму: /goals",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "Чтобы рассчитать вашу норму, нужно заполнить профиль — это минута.\n\n"
            "Начать: /start",
            reply_markup=get_main_keyboard()
        )


async def goals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик команды /goals — просмотр и ручная установка целей.
    """
    user = update.effective_user
    args = context.args or []

    db_user = await get_db_user(user.id)
    if not db_user:
        await update.message.reply_text("Сначала заполните профиль: /start", reply_markup=get_main_keyboard())
        return

    if not args:
        current = (
            f"🎯 *Цели по КБЖУ*\n\n"
            f"🔥 Калории: {db_user.target_calories or '—'} ккал\n"
            f"🥩 Белки: {format_amount(db_user.target_protein) if db_user.target_protein else '—'} г\n"
            f"🧈 Жиры: {format_amount(db_user.target_fats) if db_user.target_fats else '—'} г\n"
            f"🍞 Углеводы: {format_amount(db_user.target_carbs) if db_user.target_carbs else '—'} г\n\n"
            "Обычно я считаю их сам по профилю. Задать вручную:\n"
            "`/goals 2000 150 60 200`\n"
            "_(калории, белки, жиры, углеводы)_\n\n"
            "Вернуть автоматический расчёт: `/goals авто`"
        )
        await safe_reply(update.message, current, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return

    if args[0].lower() in ("авто", "auto", "сброс", "reset"):
        if not all([db_user.age, db_user.weight, db_user.height, db_user.gender,
                    db_user.activity_level, db_user.goal]):
            await update.message.reply_text(
                "Для автоматического расчёта не хватает данных профиля. Заполните их в настройках.",
                reply_markup=get_main_keyboard()
            )
            return

        data = await ai_service.calculate_daily_calories(
            weight=db_user.weight, height=db_user.height, age=db_user.age,
            gender=db_user.gender, activity_level=db_user.activity_level, goal=db_user.goal
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.telegram_id == user.id))
            fresh = result.scalar_one_or_none()
            fresh.target_calories = data["calories"]
            fresh.target_protein = data["protein"]
            fresh.target_fats = data["fats"]
            fresh.target_carbs = data["carbs"]
            await session.commit()

        await safe_reply(
            update.message,
            f"✅ Вернул расчёт по профилю:\n\n"
            f"🔥 {data['calories']} ккал · Б {data['protein']} · Ж {data['fats']} · У {data['carbs']}"
            + format_target_warnings(data.get("warnings", [])),
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return

    if len(args) < 4:
        await safe_reply(
            update.message,
            "Нужно четыре числа: калории, белки, жиры, углеводы.\n\n"
            "Например: `/goals 2000 150 60 200`",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return

    try:
        calories = int(float(args[0].replace(",", ".")))
        protein = float(args[1].replace(",", "."))
        fats = float(args[2].replace(",", "."))
        carbs = float(args[3].replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "Не разобрал числа. Пример: /goals 2000 150 60 200",
            reply_markup=get_main_keyboard()
        )
        return

    # Ручные цели тоже проверяем на безопасность: команда не должна быть
    # обходным путём для установки голодной нормы.
    floor = AIService.MIN_CALORIES.get((db_user.gender or "female").lower(), 1200)
    if calories < floor:
        await update.message.reply_text(
            f"Не могу поставить цель ниже {floor} ккал — это небезопасно без наблюдения врача.\n\n"
            f"Если такая калорийность назначена специалистом, ориентируйтесь на его рекомендации, "
            f"а я буду просто вести дневник.",
            reply_markup=get_main_keyboard()
        )
        return

    if any(value < 0 for value in (protein, fats, carbs)) or calories > 8000:
        await update.message.reply_text("Проверьте значения — похоже, где-то опечатка.",
                                        reply_markup=get_main_keyboard())
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user.id))
        fresh = result.scalar_one_or_none()
        fresh.target_calories = calories
        fresh.target_protein = protein
        fresh.target_fats = fats
        fresh.target_carbs = carbs
        await session.commit()

    await safe_reply(
        update.message,
        f"✅ *Цели обновлены*\n\n"
        f"🔥 {calories} ккал · Б {format_amount(protein)} · "
        f"Ж {format_amount(fats)} · У {format_amount(carbs)}\n\n"
        "_Вернуть автоматический расчёт: /goals авто_",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /profile"""
    user = update.effective_user
    db_user = await get_db_user(user.id)

    if not db_user:
        await update.message.reply_text(
            "Профиль ещё не создан. Начать: /start",
            reply_markup=get_main_keyboard()
        )
        return

    allergies = ", ".join(db_user.allergies) if db_user.allergies else "нет"
    diet = ", ".join(db_user.dietary_preferences) if db_user.dietary_preferences else "нет"

    profile_text = f"""👤 *Профиль*

*О вас*
• Имя: {esc(user.first_name)}
• Возраст: {db_user.age or '—'} лет
• Пол: {human_gender(db_user.gender)}
• Вес: {esc(db_user.weight) if db_user.weight else '—'} кг
• Рост: {esc(db_user.height) if db_user.height else '—'} см

*Цель и режим*
• Цель: {human_goal(db_user.goal)}
• Активность: {human_activity(db_user.activity_level)}
• Часовой пояс: {esc(describe_zone(db_user.timezone))}
• Тариф: {human_subscription(db_user.subscription_type.value if db_user.subscription_type else None)}

*Ограничения*
• Аллергии: {esc(allergies)}
• Питание: {esc(diet)}

*Норма на день*
🔥 {db_user.target_calories or '—'} ккал · Б {format_amount(db_user.target_protein) if db_user.target_protein else '—'} · Ж {format_amount(db_user.target_fats) if db_user.target_fats else '—'} · У {format_amount(db_user.target_carbs) if db_user.target_carbs else '—'}"""

    keyboard = [
        [InlineKeyboardButton("✏️ Изменить данные", callback_data="edit_profile")],
        [InlineKeyboardButton("🥗 Аллергии и питание", callback_data="edit_diet")],
        [InlineKeyboardButton("🎯 Цели по КБЖУ", callback_data="edit_goals")],
        [InlineKeyboardButton("🕒 Часовой пояс", callback_data="set_timezone")],
    ]

    await safe_reply(update.message, profile_text, parse_mode="Markdown",
                     reply_markup=InlineKeyboardMarkup(keyboard))


async def diary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /diary - показывает дневник за сегодня"""
    await show_diary(update, context, period="today")


def stats_keyboard(deficit_info) -> InlineKeyboardMarkup:
    keyboard = []
    if deficit_info:
        keyboard.append([InlineKeyboardButton("💡 Чем добрать норму", callback_data="suggest_snack")])
    keyboard.append([
        InlineKeyboardButton("📝 Дневник", callback_data="diary_today"),
        InlineKeyboardButton("📅 Неделя", callback_data="stats_week"),
    ])
    keyboard.append([InlineKeyboardButton("🔁 Повторить блюдо", callback_data="repeat_menu")])
    return InlineKeyboardMarkup(keyboard)


async def statistics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Дневной дашборд: сколько набрано и чем добрать."""
    user = update.effective_user

    await update.message.chat.send_action(action="typing")

    stats = await statistics_service.get_daily_stats(user.id)

    if not stats or (stats["entries_count"] == 0 and not stats.get("water_ml")):
        await safe_reply(
            update.message,
            "📊 *Мой день*\n\n"
            "Пока пусто. Напишите или скажите, что вы сегодня ели, — и здесь появится картина дня.\n\n"
            "_Например: «Съел омлет из 2 яиц и кофе с молоком»_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔁 Повторить недавнее", callback_data="repeat_menu")
            ]])
        )
        return

    dashboard = statistics_service.format_daily_dashboard(stats)
    deficit_info = statistics_service.get_deficit_info(stats)

    await safe_reply(update.message, dashboard, parse_mode="Markdown",
                     reply_markup=stats_keyboard(deficit_info))


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /timezone"""
    await ask_timezone(update.message, context)


async def repeat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /repeat — недавние приёмы пищи одной кнопкой"""
    await show_repeat_menu(update.effective_user.id, context, message=update.message)


# =====================
# ЧАСОВОЙ ПОЯС
# =====================

TZ_MOSCOW_BUTTON = "🕒 Как в Москве"
TZ_LOCATION_BUTTON = "📍 Определить по геопозиции"


def timezone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(TZ_MOSCOW_BUTTON)],
            [KeyboardButton(TZ_LOCATION_BUTTON, request_location=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Например: 14:35",
    )


async def ask_timezone(message, context: ContextTypes.DEFAULT_TYPE, onboarding: bool = False):
    """
    Спросить часовой пояс.

    Telegram не сообщает пояс пользователя, поэтому просим назвать текущее
    время (по разнице с UTC пояс восстанавливается) или поделиться геопозицией.
    """
    context.user_data['awaiting_timezone'] = True
    context.user_data['tz_onboarding'] = onboarding

    intro = "Последний штрих. " if onboarding else ""
    await safe_reply(
        message,
        f"🕒 *Часовой пояс*\n\n{intro}Чтобы напоминания и итоги дня приходили вовремя, "
        "подскажите, сколько у вас сейчас времени — например *14:35*.\n\n"
        "Или нажмите кнопку ниже.",
        parse_mode="Markdown",
        reply_markup=timezone_keyboard()
    )


async def apply_timezone(telegram_id: int, zone_name: str) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        db_user = result.scalar_one_or_none()
        if not db_user:
            return False
        db_user.timezone = zone_name
        await session.commit()
    logger.info(f"User {telegram_id} timezone set to {zone_name}")
    return True


async def confirm_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE, zone_name: str):
    """Сообщить о новом поясе и, если это онбординг, показать подсказки."""
    zone = get_zone(zone_name)
    onboarding = context.user_data.pop('tz_onboarding', False)
    context.user_data.pop('awaiting_timezone', None)

    await safe_reply(
        update.message,
        f"✅ Часовой пояс: *{esc(describe_zone(zone_name))}*\n"
        f"У вас сейчас {local_now(zone).strftime('%H:%M')}. "
        "Напоминания и итоги дня буду присылать по этому времени.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

    if onboarding:
        await send_onboarding_tips(update.message)


async def handle_timezone_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """
    Обработать ответ на вопрос о поясе.

    Returns True, если сообщение было ответом на вопрос. Если человек написал
    что-то другое (например, сразу про еду), не держим его в ловушке: оставляем
    московское время и пропускаем сообщение дальше.
    """
    lowered = (text or "").lower()

    if TZ_MOSCOW_BUTTON in text or "москв" in lowered:
        await apply_timezone(update.effective_user.id, DEFAULT_TZ)
        await confirm_timezone(update, context, DEFAULT_TZ)
        return True

    offset = parse_reported_offset(text)
    if offset is not None:
        zone_name = zone_for_offset(offset)
        await apply_timezone(update.effective_user.id, zone_name)
        await confirm_timezone(update, context, zone_name)
        return True

    onboarding = context.user_data.pop('tz_onboarding', False)
    context.user_data.pop('awaiting_timezone', None)
    if onboarding:
        await update.message.reply_text(
            "Оставил московское время — поменять можно в настройках или командой /timezone.",
            reply_markup=get_main_keyboard()
        )
    return False


async def handle_location_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Геопозиция → часовой пояс."""
    location = update.message.location
    if not location:
        return

    zone_name = zone_from_coordinates(location.latitude, location.longitude)
    await apply_timezone(update.effective_user.id, zone_name)
    await confirm_timezone(update, context, zone_name)


# =====================
# ТЕКСТОВЫЙ РОУТЕР
# =====================

# Точные фразы попытки вытащить системную инструкцию.
PROMPT_EXTRACTION_PATTERNS = [
    "системный промпт", "system prompt", "покажи промпт", "твой промпт",
    "твои инструкции", "свои инструкции", "your instructions", "system message",
    "ignore previous", "ignore all previous", "disregard previous",
    "забудь инструкции", "забудь свои инструкции",
    "повтори инструкции", "повтори свои инструкции", "процитируй инструкции",
    "что написано выше", "текст выше", "твоя роль", "твои правила",
]


def looks_like_prompt_extraction(message: str) -> bool:
    """Проверка на попытку вытащить системную инструкцию — по фразам, не подстрокам."""
    lowered = message.lower()
    return any(pattern in lowered for pattern in PROMPT_EXTRACTION_PATTERNS)


MEAL_KEYWORDS = [
    'съел', 'съела', 'поел', 'поела', 'ел ', 'ела ', 'кушал', 'кушала',
    'на завтрак', 'на обед', 'на ужин', 'перекусил', 'перекусила',
    'позавтракал', 'пообедал', 'поужинал', 'выпил', 'выпила', 'запиши',
]

FOOD_INDICATORS = ['грамм', 'гр', ' г ', 'порци', 'штук', 'кусок', 'тарелк', 'чашк', 'стакан', 'мл', 'шт']

DIETARY_KEYWORDS = ['аллергия', 'аллергию', 'не ем', 'не переношу', 'непереносимость',
                    'вегетариан', 'веган', 'без глютена', 'без лактозы']

KNOWN_ALLERGENS = ['молоко', 'орех', 'глютен', 'яйц', 'рыб', 'морепродукт',
                   'соя', 'арахис', 'лактоз', 'цитрус', 'мёд']

_REPEAT_RE = re.compile(
    r"^\s*(повтори(?:ть|м)?|как вчера|как обычно|то же(?: самое)?|тоже самое|такой же|снова то же)"
)


def parse_repeat_request(text: str):
    """«Повтори вчерашний завтрак» → {'meal_type': 'завтрак', 'day_offset': -1}; None — не про повтор."""
    lowered = (text or "").lower()
    if not _REPEAT_RE.match(lowered):
        return None
    ctx = parse_meal_context(lowered)
    day_offset = ctx.day_offset if ctx.day_offset else (-1 if "вчера" in lowered else 0)
    return {"meal_type": ctx.meal_type, "day_offset": day_offset}


def is_obvious_meal_message(text: str) -> bool:
    """«Съел 200 г риса» — очевидная запись о еде, модели для маршрутизации не нужно."""
    lowered = text.lower()
    has_meal_keyword = any(keyword in lowered for keyword in MEAL_KEYWORDS)
    has_food_indicator = any(x in lowered for x in FOOD_INDICATORS)
    return has_meal_keyword and (has_food_indicator or any(char.isdigit() for char in text))


async def handle_recipe_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    await route_text(update, context, update.message.text or "")


async def route_text(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str):
    """
    Маршрутизация текста — из сообщения или из расшифровки голосового.

    Порядок важен: сначала кнопки и ожидаемые ответы, затем дешёвые
    распознавания без модели (вода, повтор, очевидная еда), и только потом —
    определение намерения моделью.
    """
    user = update.effective_user

    # Кнопки основной клавиатуры
    shortcuts = {
        "📊 Мой день": statistics_command,
        "📝 Дневник": diary_command,
        "🍳 Рецепты": recipes_command,
        "📖 Сохранённое": my_recipes_command,
        "🔁 Повторить": repeat_command,
        "❓ Помощь": help_command,
    }
    if message_text in shortcuts:
        await shortcuts[message_text](update, context)
        return
    if message_text == "⚙️ Настройки":
        await show_settings_as_message(update)
        return

    # Ожидаемый ответ о часовом поясе
    if context.user_data.get('awaiting_timezone'):
        if await handle_timezone_answer(update, context, message_text):
            return

    # Ожидаемый ввод веса
    if await handle_weight_input(update, context, message_text):
        return

    # Ожидаемое уточнение по предыдущему сообщению о еде
    if context.user_data.get('pending_clarification'):
        await process_clarification(update, context, message_text)
        return

    if looks_like_prompt_extraction(message_text):
        logger.warning(f"User {user.id} attempted prompt extraction: {message_text[:60]}")
        await update.message.reply_text(
            "Давайте лучше про еду 🙂 Спросите что-нибудь о питании или напишите, что вы съели.",
            reply_markup=get_main_keyboard()
        )
        return

    # Вода — без модели
    water_ml = parse_water(message_text)
    if water_ml:
        await log_water_message(update, water_ml)
        return

    # Повтор недавнего приёма пищи — без модели
    repeat = parse_repeat_request(message_text)
    if repeat is not None:
        await handle_repeat_request(update, context, repeat)
        return

    # Очевидная запись о еде — сразу к расчёту, минуя определение намерения
    if is_obvious_meal_message(message_text):
        await handle_food_message(update, context, message_text, is_meal_record=True)
        return

    await update.message.chat.send_action(action="typing")

    try:
        intent_data = await ai_service.detect_intent(message_text)
        intent = intent_data.get('intent', 'general_question')
        entities = intent_data.get('entities', {})

        logger.info(f"User {user.id} intent: {intent}")

        message_lower = message_text.lower()
        has_meal_keyword = any(keyword in message_lower for keyword in MEAL_KEYWORDS)

        # Сообщение о своих ограничениях — сохраняем, а не отвечаем советом
        if intent in ("general_question", "nutrition_advice", "save_dietary_info"):
            if intent == "save_dietary_info" or any(k in message_lower for k in DIETARY_KEYWORDS):
                if await try_save_dietary_info(update, message_text, entities):
                    return

        if intent == "greeting":
            stats = await statistics_service.get_daily_stats(user.id)
            hint = ""
            if stats and stats["entries_count"]:
                hint = f"\n\nСегодня уже набрано {stats['calories']} из {stats['target_calories']} ккал."
            await update.message.reply_text(
                f"👋 Привет, {user.first_name}!{hint}\n\n"
                "Напишите или скажите, что съели, — запишу и посчитаю.",
                reply_markup=get_main_keyboard()
            )
            return

        if intent == "calorie_count":
            await handle_food_message(update, context, message_text, has_meal_keyword)
            return

        if intent == "statistics":
            await statistics_command(update, context)
            return

        if intent == "diary":
            await show_diary(update, context, period="today")
            return

        if intent == "meal_plan":
            await show_meal_plan_options_message(update)
            return

        if intent == "recipe_search":
            ingredients = entities.get('ingredients') or extract_ingredients(message_text)
            if not ingredients:
                await recipes_command(update, context)
                return
            await handle_recipe_request(update, context, ingredients)
            return

        # Вопрос о питании или свободный разговор
        await update.message.chat.send_action(action="typing")
        answer = await ai_service.answer_general_question(message_text)
        await safe_reply(update.message, answer, parse_mode="Markdown", reply_markup=get_main_keyboard())

    except AIServiceUnavailable:
        await update.message.reply_text(SERVICE_UNAVAILABLE, reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Error in route_text: {e}", exc_info=True)
        await update.message.reply_text(
            "Не получилось обработать сообщение — это сбой на моей стороне.\n"
            "Попробуйте ещё раз, пожалуйста.",
            reply_markup=get_main_keyboard()
        )


def extract_ingredients(text: str):
    """Достать список продуктов из свободной фразы."""
    stop_words = {"что", "как", "мне", "приготовить", "сделать", "можно", "блюдо",
                  "рецепт", "рецепты", "из", "есть", "дома", "только", "хочу"}
    words = [w.strip(" .,!?—-").lower() for w in text.replace(',', ' ').split()]
    return [w for w in words if len(w) > 2 and w not in stop_words][:8]


async def try_save_dietary_info(update: Update, message_text: str, entities) -> bool:
    """
    Сохранить упомянутые аллергии и режим питания.

    Returns True, если что-то распознали и сохранили.
    """
    message_lower = message_text.lower()

    allergies = list(entities.get('allergies') or [])
    prefs = list(entities.get('dietary_preferences') or [])

    if not allergies:
        allergies = [a for a in KNOWN_ALLERGENS if a in message_lower]
    if not prefs:
        if 'вегетариан' in message_lower:
            prefs.append('вегетарианство')
        if 'веган' in message_lower:
            prefs.append('веганство')
        if 'без глютена' in message_lower:
            prefs.append('без глютена')

    if not allergies and not prefs:
        return False

    user = update.effective_user
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user.id))
        db_user = result.scalar_one_or_none()
        if not db_user:
            return False

        saved = []
        if allergies:
            db_user.allergies = sorted(set((db_user.allergies or []) + allergies))
            saved.extend(allergies)
        if prefs:
            db_user.dietary_preferences = sorted(set((db_user.dietary_preferences or []) + prefs))
            saved.extend(prefs)

        await session.commit()
        logger.info(f"User {user.id} dietary info updated: {saved}")

    await safe_reply(
        update.message,
        f"✅ Запомнил: *{esc(', '.join(saved))}*\n\n"
        "Больше не предложу это ни в рецептах, ни в перекусах.\n"
        "_Изменить — в настройках._",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
    return True


# =====================
# ЕДА: РАСЧЁТ И ЗАПИСЬ
# =====================

async def handle_food_message(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              message_text: str, is_meal_record: bool):
    """Расчёт КБЖУ по тексту и, если это запись о еде, сохранение в дневник."""
    await update.message.chat.send_action(action="typing")

    try:
        nutrition_info = await ai_service.calculate_nutrition_from_text(message_text)
    except AIServiceUnavailable:
        await update.message.reply_text(SERVICE_UNAVAILABLE, reply_markup=get_main_keyboard())
        return

    if not nutrition_info or (not nutrition_info.get("items") and not nutrition_info.get("needs_clarification")):
        await safe_reply(update.message, NOT_UNDERSTOOD_FOOD, parse_mode="Markdown",
                         reply_markup=get_main_keyboard())
        return

    if nutrition_info.get('needs_clarification'):
        question = nutrition_info.get('clarification_question') or "Уточните способ приготовления"
        context.user_data['pending_clarification'] = {
            'original_text': message_text,
            'is_meal_record': is_meal_record,
        }
        await update.message.reply_text(
            f"🤔 {question}",
            reply_markup=get_main_keyboard()
        )
        return

    await deliver_nutrition_result(update, context, nutrition_info, message_text, is_meal_record)


async def deliver_nutrition_result(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                   nutrition_info, source_text: str, is_meal_record: bool):
    """Показать результат расчёта и при необходимости записать его в дневник."""
    items = nutrition_info.get('items', [])
    if not items:
        await safe_reply(update.message, NOT_UNDERSTOOD_FOOD, parse_mode="Markdown",
                         reply_markup=get_main_keyboard())
        return

    body = render_nutrition_items(items)

    if not is_meal_record:
        await safe_reply(
            update.message,
            f"📊 *Пищевая ценность*\n\n{body}\n\n"
            "_Чтобы записать это в дневник, напишите «съел…»._",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return

    db_user = await get_db_user(update.effective_user.id)
    if not db_user:
        await update.message.reply_text(
            "Не нашёл ваш профиль. Отправьте /start, чтобы я мог вести дневник.",
            reply_markup=get_main_keyboard()
        )
        return

    # Когда это было: слова самого сообщения важнее подсказок модели.
    meal_ctx = parse_meal_context(source_text)
    if not meal_ctx.mentioned:
        if nutrition_info.get("meal_type"):
            meal_ctx.meal_type = nutrition_info["meal_type"]
        if nutrition_info.get("day_offset"):
            meal_ctx.day_offset = nutrition_info["day_offset"]

    moment_utc, meal_type, day_offset = resolve_moment(meal_ctx, user_zone(db_user))

    entry_ids, meal_type = await save_meal_entries(
        update.effective_user.id, items, notes=source_text, moment_utc=moment_utc, meal_type=meal_type
    )

    if entry_ids is None:
        await update.message.reply_text(
            "Не нашёл ваш профиль. Отправьте /start, чтобы я мог вести дневник.",
            reply_markup=get_main_keyboard()
        )
        return

    footer = await day_stats_footer(update.effective_user.id, day_offset)

    await safe_reply(
        update.message,
        f"✅ Записал {describe_when(day_offset, meal_type)}\n\n{body}{footer}",
        parse_mode="Markdown",
        reply_markup=meal_saved_keyboard(entry_ids)
    )


async def process_clarification(update: Update, context: ContextTypes.DEFAULT_TYPE, answer_text: str):
    """Обработать ответ на уточняющий вопрос о способе приготовления."""
    pending = context.user_data.pop('pending_clarification', None)
    if not pending:
        return

    original_text = pending.get('original_text', '')
    is_meal_record = pending.get('is_meal_record', False)
    clarified_text = f"{original_text} (уточнение: {answer_text})"

    logger.info(f"Processing clarification for user {update.effective_user.id}")
    await update.message.chat.send_action(action="typing")

    try:
        nutrition_info = await ai_service.calculate_nutrition_from_text(clarified_text)
    except AIServiceUnavailable:
        await update.message.reply_text(SERVICE_UNAVAILABLE, reply_markup=get_main_keyboard())
        return

    if not nutrition_info:
        await safe_reply(update.message, NOT_UNDERSTOOD_FOOD, parse_mode="Markdown",
                         reply_markup=get_main_keyboard())
        return

    # Повторно уточнять не просим: спрашиваем максимум один раз, иначе диалог
    # превращается в допрос. Считаем по тому, что есть.
    await deliver_nutrition_result(update, context, nutrition_info, original_text, is_meal_record)


# =====================
# ВОДА
# =====================

async def log_water(telegram_id: int, millilitres: int):
    """Записать воду отдельной строкой дневника; вернуть (id, всего за день, цель)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        db_user = result.scalar_one_or_none()
        if not db_user:
            return None, 0, 2000

        entry = DiaryEntry(
            user_id=db_user.id,
            date=utc_now(),
            meal_type=WATER_MEAL_TYPE,
            food_name=f"Вода {millilitres} мл",
            portion_size=float(millilitres),
            calories=0, protein=0, fats=0, carbs=0,
            data_source=DataSource.MANUAL,
        )
        session.add(entry)
        await session.commit()
        entry_id = entry.id
        target = water_target_ml(db_user.weight)

    stats = await statistics_service.get_daily_stats(telegram_id)
    total = stats.get("water_ml", millilitres) if stats else millilitres
    return entry_id, total, target


async def log_water_message(update: Update, millilitres: int):
    entry_id, total, target = await log_water(update.effective_user.id, millilitres)
    if entry_id is None:
        await update.message.reply_text("Не нашёл ваш профиль. Отправьте /start.", reply_markup=get_main_keyboard())
        return

    share = min(total / target, 1.0) if target else 0
    bar = "🟦" * int(round(share * 8)) + "▫️" * (8 - int(round(share * 8)))
    left = max(target - total, 0)
    tail = f"до нормы ещё {format_litres(left)} л" if left else "норма на сегодня выполнена 🎉"

    await safe_reply(
        update.message,
        f"💧 Записал {millilitres} мл\n\n{bar}\n"
        f"За день: *{esc(format_litres(total))}* из *{esc(format_litres(target))}* л — {tail}",
        parse_mode="Markdown",
        reply_markup=meal_saved_keyboard([entry_id])
    )


# =====================
# ПОВТОРЫ
# =====================

async def list_recent_meals(telegram_id: int, days: int = 14, limit: int = 8):
    """
    Недавние приёмы пищи, сгруппированные по одному сообщению.

    Возвращает список групп: {ids, names, calories, meal_type, day_label}.
    Одинаковые наборы продуктов схлопываются — остаётся самый свежий.
    """
    db_user = await get_db_user(telegram_id)
    if not db_user:
        return []
    zone = user_zone(db_user)
    today = local_today(zone)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DiaryEntry)
            .where(DiaryEntry.user_id == db_user.id)
            .where(DiaryEntry.date >= utc_now() - timedelta(days=days))
            .where(DiaryEntry.meal_type != WATER_MEAL_TYPE)
            .order_by(DiaryEntry.date.desc())
        )
        entries = result.scalars().all()

    groups = []
    seen_signatures = set()
    current_key = None
    current = None

    for entry in entries:
        key = (entry.notes or "", entry.date.replace(second=0, microsecond=0))
        if key != current_key:
            if current:
                groups.append(current)
            current_key = key
            current = {"entries": [entry]}
        else:
            current["entries"].append(entry)
    if current:
        groups.append(current)

    prepared = []
    for group in groups:
        group_entries = group["entries"]
        signature = tuple(sorted(e.food_name for e in group_entries))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)

        first = group_entries[0]
        local_dt = to_local(first.date, zone)
        delta = (today - local_dt.date()).days
        day_label = "сегодня" if delta == 0 else ("вчера" if delta == 1 else local_dt.strftime("%d.%m"))

        prepared.append({
            "ids": [e.id for e in group_entries],
            "names": [short_food_name(e.food_name) for e in group_entries],
            "calories": round(sum(e.calories or 0 for e in group_entries)),
            "meal_type": first.meal_type,
            "day_label": day_label,
            "day_offset": -delta,
        })
        if len(prepared) >= limit:
            break

    return prepared


def repeat_button_text(group) -> str:
    names = ", ".join(group["names"])
    if len(names) > 34:
        names = names[:31].rstrip(", ") + "…"
    emoji = MEAL_EMOJI.get(group["meal_type"], "🍽")
    return f"{emoji} {names} · {group['calories']} ккал"


async def show_repeat_menu(telegram_id: int, context: ContextTypes.DEFAULT_TYPE, message=None, query=None):
    """Показать недавние приёмы пищи кнопками."""
    groups = await list_recent_meals(telegram_id)
    context.user_data['repeat_groups'] = groups

    if not groups:
        text = ("🔁 Пока нечего повторять — запишите первый приём пищи, "
                "и он появится здесь одной кнопкой.")
        if query:
            await safe_edit(query.message, text)
        else:
            await safe_reply(message, text, reply_markup=get_main_keyboard())
        return

    keyboard = [
        [InlineKeyboardButton(repeat_button_text(group), callback_data=f"repeat_{index}")]
        for index, group in enumerate(groups)
    ]
    text = ("🔁 *Повторить приём пищи*\n\n"
            "Нажмите — запишу то же самое в текущий приём пищи.\n"
            "_Можно и словами: «как вчера на завтрак»._")

    if query:
        await safe_edit(query.message, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await safe_reply(message, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def repeat_group_entries(telegram_id: int, entry_ids, moment_utc=None, meal_type: str = None):
    """Скопировать записи в новый приём пищи. Возвращает (new_ids, meal_type, calories)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        db_user = result.scalar_one_or_none()
        if not db_user:
            return None, None, 0

        result = await session.execute(
            select(DiaryEntry)
            .where(DiaryEntry.id.in_(entry_ids))
            .where(DiaryEntry.user_id == db_user.id)
        )
        originals = result.scalars().all()
        if not originals:
            return None, None, 0

        zone = user_zone(db_user)
        moment = moment_utc or utc_now()
        meal_type = meal_type or meal_type_by_time(to_local(moment, zone))

        copies = []
        for original in originals:
            copy = DiaryEntry(
                user_id=db_user.id,
                date=moment,
                meal_type=meal_type,
                food_name=original.food_name,
                portion_size=original.portion_size,
                calories=original.calories,
                protein=original.protein,
                fats=original.fats,
                carbs=original.carbs,
                data_source=original.data_source,
                recipe_id=original.recipe_id,
                notes="Повтор",
            )
            session.add(copy)
            copies.append(copy)

        await session.commit()
        return [c.id for c in copies], meal_type, round(sum(c.calories or 0 for c in copies))


async def repeat_group_callback(query, context: ContextTypes.DEFAULT_TYPE, index: int):
    groups = context.user_data.get('repeat_groups') or []
    if index >= len(groups):
        await query.answer("Список устарел — откройте «Повторить» ещё раз", show_alert=True)
        return

    group = groups[index]
    new_ids, meal_type, calories = await repeat_group_entries(query.from_user.id, group["ids"])
    if not new_ids:
        await query.answer("Эти записи уже удалены", show_alert=True)
        return

    await query.answer("Записано")
    footer = await day_stats_footer(query.from_user.id)
    await safe_reply(
        query.message,
        f"✅ Записал в {meal_type}: *{esc(', '.join(group['names']))}* — {calories} ккал{footer}",
        parse_mode="Markdown",
        reply_markup=meal_saved_keyboard(new_ids)
    )


async def handle_repeat_request(update: Update, context: ContextTypes.DEFAULT_TYPE, request):
    """«Повтори вчерашний завтрак» — найти подходящий приём пищи и записать."""
    groups = await list_recent_meals(update.effective_user.id)
    context.user_data['repeat_groups'] = groups

    wanted_meal = request.get("meal_type")
    wanted_offset = request.get("day_offset", 0)

    candidate = None
    if wanted_meal:
        # Сначала нужный день, потом любой ближайший с таким приёмом пищи
        candidate = next((g for g in groups if g["meal_type"] == wanted_meal and g["day_offset"] == wanted_offset), None)
        candidate = candidate or next((g for g in groups if g["meal_type"] == wanted_meal and g["day_offset"] < 0), None)
        candidate = candidate or next((g for g in groups if g["meal_type"] == wanted_meal), None)

    if not candidate:
        if wanted_meal and groups:
            await safe_reply(
                update.message,
                f"Не нашёл записанный {wanted_meal} за последние дни. Вот что можно повторить:",
            )
        await show_repeat_menu(update.effective_user.id, context, message=update.message)
        return

    # Повтор — это про еду, которую едят сейчас: «вчерашний» относится к
    # источнику, а запись ложится на текущий момент. Название приёма пищи
    # берём из просьбы («повтори завтрак» → завтрак), иначе — по времени.
    new_ids, meal_type, calories = await repeat_group_entries(
        update.effective_user.id, candidate["ids"], moment_utc=utc_now(), meal_type=wanted_meal
    )
    if not new_ids:
        await show_repeat_menu(update.effective_user.id, context, message=update.message)
        return

    footer = await day_stats_footer(update.effective_user.id)
    await safe_reply(
        update.message,
        f"✅ Записал в {meal_type}: *{esc(', '.join(candidate['names']))}* — {calories} ккал{footer}",
        parse_mode="Markdown",
        reply_markup=meal_saved_keyboard(new_ids)
    )


# =====================
# РЕЦЕПТЫ
# =====================

async def handle_recipe_request(update: Update, context: ContextTypes.DEFAULT_TYPE, ingredients):
    """Подбор рецептов с учётом аллергий и режима питания."""
    user = update.effective_user
    db_user = await get_db_user(user.id)

    user_allergies = (db_user.allergies if db_user else None) or []
    user_prefs = (db_user.dietary_preferences if db_user else None) or []

    await update.message.reply_text("🍳 Подбираю рецепты — это займёт до полминуты…")

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(update, stop_typing))

    try:
        result = await ai_service.generate_recipes(
            ingredients,
            dietary_preferences=user_prefs or None,
            allergies=user_allergies or None
        )
    except AIServiceUnavailable:
        await update.message.reply_text(SERVICE_UNAVAILABLE, reply_markup=get_main_keyboard())
        return
    finally:
        stop_typing.set()
        typing_task.cancel()

    blocked = result.get("blocked_allergens") or []
    if blocked:
        await safe_reply(
            update.message,
            f"⚠️ Среди этих продуктов есть то, на что у вас указана аллергия: "
            f"*{esc(', '.join(blocked))}*.\n\n"
            "Не буду предлагать рецепты с ними. Напишите другие продукты — подберу из них.\n\n"
            "_Если аллергии больше нет, уберите её в настройках._",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return

    recipes = result.get("recipes") or []
    if not recipes:
        await update.message.reply_text(
            "Не получилось собрать рецепт из этих продуктов.\n\n"
            "Попробуйте добавить пару позиций — например, крупу или овощи.",
            reply_markup=get_main_keyboard()
        )
        return

    context.user_data['last_recipes'] = recipes
    logger.info(f"Saved {len(recipes)} recipes for user {user.id}")

    for i, recipe in enumerate(recipes):
        recipe_text = format_recipe(recipe)
        keyboard = [[
            InlineKeyboardButton("💾 Сохранить", callback_data=f"save_recipe_{i}"),
            InlineKeyboardButton("📝 Я это съел", callback_data=f"eat_recipe_{i}"),
        ]]
        await safe_reply(update.message, recipe_text, parse_mode="Markdown",
                         reply_markup=InlineKeyboardMarkup(keyboard))


def format_recipe(recipe) -> str:
    """Единое представление рецепта."""
    nutrition = recipe.get("nutrition", {})
    ingredients = "\n".join(
        f"• {esc(ing.get('name', ''))} — {esc(ing.get('amount', ''))}"
        for ing in recipe.get("ingredients", [])
    )
    meta = " · ".join(filter(None, [
        esc(recipe.get("cuisine_type")),
        esc(recipe.get("difficulty")),
        f"{recipe.get('cooking_time', 30)} мин",
    ]))

    return f"""🍳 *{esc(recipe.get('name', 'Рецепт'))}*
_{meta}_

{esc(recipe.get('description', ''))}

*Понадобится*
{ingredients}

*Приготовление*
{esc(recipe.get('instructions', ''))}

*На порцию*
🔥 {format_amount(nutrition.get('calories', 0))} ккал · Б {format_amount(nutrition.get('protein', 0))} · Ж {format_amount(nutrition.get('fats', 0))} · У {format_amount(nutrition.get('carbs', 0))}"""


def format_ingredients(ingredients: list) -> str:
    """Форматирование списка ингредиентов"""
    return "\n".join([f"• {esc(ing.get('name', ''))} - {esc(ing.get('amount', ''))}" for ing in ingredients])


# =====================
# ДНЕВНИК
# =====================

async def show_diary(update: Update, context: ContextTypes.DEFAULT_TYPE, period: str = "today", query=None):
    """Показать дневник за указанный период"""
    user = update.effective_user if update else query.from_user
    db_user = await get_db_user(user.id)
    zone = user_zone(db_user)
    today = local_today(zone)

    if period == "yesterday":
        day = today - timedelta(days=1)
        start_date, end_date = local_day_bounds(zone, day)
        period_text, date_header = "за вчера", day.strftime("%d.%m.%Y")
    elif period == "week":
        first = today - timedelta(days=6)
        start_date, end_date = local_range_bounds(zone, first, today)
        period_text = "за 7 дней"
        date_header = f"{first.strftime('%d.%m')} — {today.strftime('%d.%m.%Y')}"
    else:
        period = "today"
        start_date, end_date = local_day_bounds(zone, today)
        period_text, date_header = "за сегодня", today.strftime("%d.%m.%Y")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DiaryEntry)
            .join(User)
            .where(User.telegram_id == user.id)
            .where(DiaryEntry.date >= start_date)
            .where(DiaryEntry.date < end_date)
            .order_by(DiaryEntry.date)
        )
        all_entries = result.scalars().all()

    water_ml = sum((e.portion_size or 0) for e in all_entries if e.meal_type == WATER_MEAL_TYPE)
    entries = [e for e in all_entries if e.meal_type != WATER_MEAL_TYPE]

    nav = []
    if period != "today":
        nav.append(InlineKeyboardButton("Сегодня", callback_data="diary_today"))
    if period != "yesterday":
        nav.append(InlineKeyboardButton("Вчера", callback_data="diary_yesterday"))
    if period != "week":
        nav.append(InlineKeyboardButton("7 дней", callback_data="diary_week"))

    keyboard = [nav] if nav else []

    if not entries and not water_ml:
        empty_text = (
            f"📝 *Дневник {period_text}* · {date_header}\n\n"
            "Записей нет.\n\n"
            "Просто напишите или скажите, что съели:\n"
            "_«Съел омлет из двух яиц»_\n"
            "_«Вчера на ужин борщ и кусок хлеба»_"
        )
        keyboard.append([InlineKeyboardButton("🔁 Повторить недавнее", callback_data="repeat_menu")])
        markup = InlineKeyboardMarkup(keyboard)
        if query:
            await safe_edit(query.message, empty_text, parse_mode="Markdown", reply_markup=markup)
        else:
            await safe_reply(update.message, empty_text, parse_mode="Markdown", reply_markup=markup)
        return

    total_calories = sum(e.calories or 0 for e in entries)
    total_protein = sum(e.protein or 0 for e in entries)
    total_fats = sum(e.fats or 0 for e in entries)
    total_carbs = sum(e.carbs or 0 for e in entries)

    if period == "week":
        by_day = {}
        for e in entries:
            by_day.setdefault(to_local(e.date, zone).strftime("%d.%m"), []).append(e)
        entries_text = "\n".join(
            f"📅 *{day}* — {sum(x.calories or 0 for x in day_entries):.0f} ккал "
            f"({len(day_entries)} зап.)"
            for day, day_entries in by_day.items()
        ) or "_Еды не записано_"
    else:
        by_meal = {}
        for e in entries:
            by_meal.setdefault(e.meal_type or "перекус", []).append(e)

        blocks = []
        for meal_type, meal_entries in by_meal.items():
            meal_calories = sum(x.calories or 0 for x in meal_entries)
            lines = [f"*{meal_label(meal_type)}* — {meal_calories:.0f} ккал"]
            for e in meal_entries:
                lines.append(
                    f"  {to_local(e.date, zone).strftime('%H:%M')} · {esc(e.food_name)}\n"
                    f"  🔥 {format_amount(e.calories)} · Б {format_amount(e.protein)} · "
                    f"Ж {format_amount(e.fats)} · У {format_amount(e.carbs)}"
                )
            blocks.append("\n".join(lines))
        entries_text = "\n\n".join(blocks) or "_Еды не записано_"

    water_text = f"\n💧 Вода: {esc(format_litres(water_ml))} л" if water_ml else ""

    diary_text = f"""📝 *Дневник {period_text}* · {date_header}

{entries_text}

━━━━━━━━━━━━━━━
*Итого:* 🔥 {total_calories:.0f} ккал
Б {total_protein:.0f} · Ж {total_fats:.0f} · У {total_carbs:.0f}{water_text}"""

    if period == "today":
        keyboard.append([
            InlineKeyboardButton("🔁 Повторить", callback_data="repeat_menu"),
            InlineKeyboardButton("🗑 Удалить запись", callback_data="diary_pick_delete"),
        ])

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    if query:
        await safe_edit(query.message, diary_text, parse_mode="Markdown", reply_markup=markup)
    else:
        await safe_reply(update.message, diary_text, parse_mode="Markdown", reply_markup=markup)


async def show_delete_picker(query):
    """Показать список записей за сегодня для точечного удаления."""
    user = query.from_user
    db_user = await get_db_user(user.id)
    zone = user_zone(db_user)
    start, end = local_day_bounds(zone, local_today(zone))

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DiaryEntry)
            .join(User)
            .where(User.telegram_id == user.id)
            .where(DiaryEntry.date >= start)
            .where(DiaryEntry.date < end)
            .order_by(DiaryEntry.date)
        )
        entries = result.scalars().all()

    if not entries:
        await query.answer("За сегодня записей нет", show_alert=True)
        return

    keyboard = []
    for e in entries[:20]:
        title = e.food_name if len(e.food_name) <= 34 else e.food_name[:31] + "…"
        keyboard.append([InlineKeyboardButton(
            f"❌ {to_local(e.date, zone).strftime('%H:%M')} · {title}",
            callback_data=f"del_entry_{e.id}"
        )])
    keyboard.append([InlineKeyboardButton("◀️ Назад к дневнику", callback_data="diary_today")])

    await safe_edit(
        query.message,
        "🗑 *Что удалить?*\n\nНажмите на запись — уберу её из дневника.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def delete_diary_entry(query, entry_id: int):
    """Удалить одну запись дневника."""
    user = query.from_user

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DiaryEntry).join(User)
            .where(DiaryEntry.id == entry_id)
            .where(User.telegram_id == user.id)
        )
        entry = result.scalar_one_or_none()

        if not entry:
            await query.answer("Запись не найдена", show_alert=True)
            return

        name = entry.food_name
        await session.delete(entry)
        await session.commit()

    logger.info(f"User {user.id} deleted diary entry {entry_id}")
    await query.answer(f"Удалено: {name[:40]}")
    await show_delete_picker(query)


async def undo_meal(query, ids_raw: str):
    """Отменить только что созданную запись целиком."""
    user = query.from_user
    try:
        entry_ids = [int(x) for x in ids_raw.split(",") if x]
    except ValueError:
        await query.answer("Не получилось разобрать запись", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DiaryEntry).join(User)
            .where(DiaryEntry.id.in_(entry_ids))
            .where(User.telegram_id == user.id)
        )
        entries = result.scalars().all()

        if not entries:
            await query.answer("Запись уже удалена", show_alert=True)
            return

        for entry in entries:
            await session.delete(entry)
        await session.commit()

    logger.info(f"User {user.id} undid meal entries {entry_ids}")
    await query.answer("Запись отменена")
    await safe_edit(
        query.message,
        "↩️ Запись отменена — в дневнике её больше нет.\n\n"
        "Напишите заново, если хотите записать иначе.",
    )


async def clear_diary_today(query):
    """Очистить дневник за сегодня"""
    user = query.from_user
    db_user = await get_db_user(user.id)

    if not db_user:
        await query.answer("Профиль не найден", show_alert=True)
        return

    zone = user_zone(db_user)
    today = local_today(zone)
    start, end = local_day_bounds(zone, today)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DiaryEntry)
            .where(DiaryEntry.user_id == db_user.id)
            .where(DiaryEntry.date >= start)
            .where(DiaryEntry.date < end)
        )
        entries = result.scalars().all()

        count = len(entries)
        for entry in entries:
            await session.delete(entry)
        await session.commit()

    logger.info(f"User {user.id} cleared {count} diary entries for today")
    await query.answer(f"Удалено записей: {count}")
    await safe_edit(
        query.message,
        f"📝 *Дневник за сегодня* · {today.strftime('%d.%m.%Y')}\n\nЗаписей больше нет.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Вчера", callback_data="diary_yesterday"),
            InlineKeyboardButton("7 дней", callback_data="diary_week"),
        ]])
    )


# =====================
# СТАТИСТИКА (callbacks)
# =====================

async def show_weekly_stats(query):
    """Показать статистику за неделю"""
    user = query.from_user
    stats = await statistics_service.get_weekly_stats(user.id)

    if not stats or stats["days_tracked"] == 0:
        await safe_edit(
            query.message,
            "📈 *Неделя*\n\nПока мало данных — поведите дневник несколько дней, и я покажу динамику.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📊 Сегодня", callback_data="stats_today")
            ]])
        )
        return

    target = stats["target_calories"]
    avg = stats["avg_calories"]
    diff = avg - target
    if abs(diff) < 100:
        verdict = "Держитесь нормы — так и надо."
    elif diff > 0:
        verdict = f"В среднем на {diff} ккал больше нормы."
    else:
        verdict = f"В среднем на {abs(diff)} ккал меньше нормы."

    text = f"""📈 *За 7 дней*

Дней с записями: {stats["days_tracked"]}

*В среднем за день*
🔥 {avg} из {target} ккал
🥩 Белки: {stats["avg_protein"]} г
🧈 Жиры: {stats["avg_fats"]} г
🍞 Углеводы: {stats["avg_carbs"]} г

{verdict}"""

    await safe_edit(
        query.message, text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Сегодня", callback_data="stats_today")],
            [InlineKeyboardButton("📝 Дневник за неделю", callback_data="diary_week")],
        ])
    )


async def stats_today_callback(query):
    """Показать статистику за сегодня (callback)"""
    user = query.from_user
    stats = await statistics_service.get_daily_stats(user.id)

    if not stats or (stats["entries_count"] == 0 and not stats.get("water_ml")):
        await safe_edit(
            query.message,
            "📊 Пока нечего показать. Напишите или скажите, что вы ели сегодня.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Повторить недавнее", callback_data="repeat_menu")],
                [InlineKeyboardButton("📝 Дневник", callback_data="diary_today")],
            ])
        )
        return

    dashboard = statistics_service.format_daily_dashboard(stats)
    deficit_info = statistics_service.get_deficit_info(stats)

    await safe_edit(query.message, dashboard, parse_mode="Markdown",
                    reply_markup=stats_keyboard(deficit_info))


async def suggest_snack_callback(query):
    """Подобрать перекус на основе дефицитов"""
    user = query.from_user
    stats = await statistics_service.get_daily_stats(user.id)

    if not stats:
        await query.answer("Нет данных для анализа", show_alert=True)
        return

    deficit_info = statistics_service.get_deficit_info(stats)

    if not deficit_info or not deficit_info["has_room_for_snack"]:
        await query.answer("Всё в норме, добирать нечего 🎉", show_alert=True)
        return

    deficits = deficit_info["deficits"]
    order = [("protein", "белок"), ("carbs", "углеводы"), ("fats", "жиры")]

    recommendations = ""
    for key, label in order:
        if key in deficits:
            snacks = get_snack_recommendations(
                key,
                deficits[key]["deficit"],
                deficit_info["calories_remaining"],
                stats.get("user_allergies", []),
                count=3,
                dietary_preferences=stats.get("user_preferences", []),
            )
            if snacks:
                recommendations = format_snack_recommendations(snacks, label)
                break

    if not recommendations:
        await query.answer("Не нашёл подходящих вариантов под ваши ограничения", show_alert=True)
        return

    text = (
        f"💡 *Осталось {deficit_info['calories_remaining']} ккал*\n\n"
        f"{recommendations}"
    )

    await safe_edit(
        query.message, text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ К статистике", callback_data="stats_today")
        ]])
    )


# =====================
# СОХРАНЁННЫЕ РЕЦЕПТЫ
# =====================

async def my_recipes_command(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None):
    """Показать сохранённые рецепты пользователя"""
    user = update.effective_user if update else query.from_user

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Recipe).join(User)
            .where(User.telegram_id == user.id)
            .order_by(Recipe.created_at.desc())
            .limit(10)
        )
        recipes = result.scalars().all()

    if not recipes:
        text = ("📖 *Сохранённые рецепты*\n\n"
                "Пока пусто.\n\n"
                "Спросите «что приготовить из…», и под понравившимся рецептом нажмите «💾 Сохранить».")
        if query:
            await safe_edit(query.message, text, parse_mode="Markdown")
        else:
            await safe_reply(update.message, text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return

    lines = [
        f"{i}. *{esc(r.name)}* — {format_amount(r.calories)} ккал, {r.cooking_time or '?'} мин"
        for i, r in enumerate(recipes, 1)
    ]

    keyboard, row = [], []
    for i, recipe in enumerate(recipes, 1):
        row.append(InlineKeyboardButton(str(i), callback_data=f"view_recipe_{recipe.id}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    text = "📖 *Сохранённые рецепты*\n\n" + "\n".join(lines) + "\n\nНажмите номер, чтобы открыть."
    if query:
        await safe_edit(query.message, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await safe_reply(update.message, text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def view_saved_recipe(query, recipe_id: int):
    """Показать детали сохранённого рецепта"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Recipe).join(User)
            .where(Recipe.id == recipe_id)
            .where(User.telegram_id == query.from_user.id)
        )
        recipe = result.scalar_one_or_none()

    if not recipe:
        await query.answer("Рецепт не найден", show_alert=True)
        return

    ingredients = "\n".join(
        f"• {esc(ing.get('name', ''))} — {esc(ing.get('amount', ''))}"
        for ing in (recipe.ingredients or [])
    )

    recipe_text = f"""📖 *{esc(recipe.name)}*

{esc(recipe.description or '')}

*Понадобится*
{ingredients}

*Приготовление*
{esc(recipe.instructions)}

*На порцию*
🔥 {format_amount(recipe.calories)} ккал · Б {format_amount(recipe.protein)} · Ж {format_amount(recipe.fats)} · У {format_amount(recipe.carbs)}
⏱ {recipe.cooking_time or '?'} мин"""

    keyboard = [
        [InlineKeyboardButton("📝 Я это съел", callback_data=f"add_saved_to_diary_{recipe.id}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_recipe_{recipe.id}")],
        [InlineKeyboardButton("◀️ К списку", callback_data="saved_recipes")],
    ]

    await safe_edit(query.message, recipe_text, parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard))


async def delete_saved_recipe(query, recipe_id: int):
    """Удалить рецепт из коллекции"""
    user = query.from_user

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Recipe).join(User)
            .where(Recipe.id == recipe_id)
            .where(User.telegram_id == user.id)
        )
        recipe = result.scalar_one_or_none()

        if not recipe:
            await query.answer("Рецепт не найден", show_alert=True)
            return

        await session.delete(recipe)
        await session.commit()

    await query.answer("Рецепт удалён")
    await safe_edit(query.message, "🗑 Рецепт удалён из коллекции.")


async def add_saved_recipe_to_diary(query, recipe_id: int):
    """Добавить сохранённый рецепт в дневник питания"""
    user = query.from_user

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Recipe).join(User)
            .where(Recipe.id == recipe_id)
            .where(User.telegram_id == user.id)
        )
        recipe = result.scalar_one_or_none()

    if not recipe:
        await query.answer("Рецепт не найден", show_alert=True)
        return

    items = [{
        "name": recipe.name,
        "amount": "1 порция",
        "cooking_method": "готовое",
        "calories": recipe.calories,
        "protein": recipe.protein,
        "fats": recipe.fats,
        "carbs": recipe.carbs,
    }]
    entry_ids, meal_type = await save_meal_entries(user.id, items, notes="Из сохранённых рецептов",
                                                   source=DataSource.DATABASE)
    if entry_ids is None:
        await query.answer("Профиль не найден", show_alert=True)
        return

    await query.answer("Записано в дневник")
    footer = await day_stats_footer(user.id)
    await safe_reply(
        query.message,
        f"✅ Записал в {meal_type}: *{esc(recipe.name)}* — {format_amount(recipe.calories)} ккал{footer}",
        parse_mode="Markdown",
        reply_markup=meal_saved_keyboard(entry_ids)
    )


async def save_recipe_to_collection(query, recipe_index: str, context: ContextTypes.DEFAULT_TYPE):
    """Сохранить рецепт в коллекцию пользователя"""
    user = query.from_user

    try:
        recipes = context.user_data.get('last_recipes', [])
        index = int(recipe_index)

        if not recipes or index >= len(recipes):
            await query.answer("Рецепт устарел — найдите его заново", show_alert=True)
            return

        recipe = recipes[index]

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.telegram_id == user.id))
            db_user = result.scalar_one_or_none()

            if not db_user:
                await query.answer("Профиль не найден", show_alert=True)
                return

            existing = await session.execute(
                select(Recipe).where(Recipe.user_id == db_user.id, Recipe.name == recipe['name'])
            )
            if existing.scalar_one_or_none():
                await query.answer("Этот рецепт уже сохранён", show_alert=True)
                return

            nutrition = recipe.get('nutrition', {})
            saved_recipe = Recipe(
                user_id=db_user.id,
                name=recipe['name'],
                description=recipe.get('description', ''),
                cuisine_type=recipe.get('cuisine_type', ''),
                difficulty=recipe.get('difficulty', ''),
                ingredients=recipe.get('ingredients', []),
                instructions=recipe.get('instructions', ''),
                cooking_time=recipe.get('cooking_time', 30),
                servings=recipe.get('servings', 2),
                calories=nutrition.get('calories', 0),
                protein=nutrition.get('protein', 0),
                fats=nutrition.get('fats', 0),
                carbs=nutrition.get('carbs', 0),
                source='ai_generated'
            )
            session.add(saved_recipe)
            await session.commit()

        logger.info(f"User {user.id} saved recipe '{recipe['name']}'")
        await query.answer("Сохранено — ищите в «Рецепты → Сохранённые»")

    except Exception as e:
        logger.error(f"Error saving recipe to collection: {e}", exc_info=True)
        await query.answer("Не получилось сохранить рецепт", show_alert=True)


async def eat_recipe(query, recipe_index: str, context: ContextTypes.DEFAULT_TYPE):
    """Записать порцию только что предложенного рецепта в дневник."""
    recipes = context.user_data.get('last_recipes', [])
    try:
        index = int(recipe_index)
    except ValueError:
        await query.answer("Рецепт не найден", show_alert=True)
        return

    if not recipes or index >= len(recipes):
        await query.answer("Рецепт устарел — найдите его заново", show_alert=True)
        return

    recipe = recipes[index]
    nutrition = recipe.get("nutrition", {})
    items = [{
        "name": recipe.get("name", "Блюдо"),
        "amount": "1 порция",
        "cooking_method": "готовое",
        "calories": nutrition.get("calories", 0),
        "protein": nutrition.get("protein", 0),
        "fats": nutrition.get("fats", 0),
        "carbs": nutrition.get("carbs", 0),
    }]

    entry_ids, meal_type = await save_meal_entries(
        query.from_user.id, items, notes="Из предложенного рецепта"
    )

    if entry_ids is None:
        await query.answer("Профиль не найден", show_alert=True)
        return

    await query.answer("Записано в дневник")
    footer = await day_stats_footer(query.from_user.id)
    await safe_reply(
        query.message,
        f"✅ Записал в {meal_type}: *{esc(recipe.get('name'))}* — "
        f"{format_amount(nutrition.get('calories', 0))} ккал{footer}",
        parse_mode="Markdown",
        reply_markup=meal_saved_keyboard(entry_ids)
    )


# =====================
# НАСТРОЙКИ
# =====================

def build_settings_view(db_user):
    """Собрать текст и кнопки настроек — один источник для обоих способов показа."""
    allergies = ", ".join(db_user.allergies) if db_user.allergies else "нет"
    diet = ", ".join(db_user.dietary_preferences) if db_user.dietary_preferences else "нет"
    reminders = "включены" if db_user.reminders_enabled else "выключены"
    zone_text = describe_zone(db_user.timezone)

    text = f"""⚙️ *Настройки*

*Профиль*
{db_user.age or '—'} лет · {esc(db_user.weight) if db_user.weight else '—'} кг · {esc(db_user.height) if db_user.height else '—'} см
Цель: {human_goal(db_user.goal)}
Активность: {human_activity(db_user.activity_level)}

*Ограничения*
🚫 Аллергии: {esc(allergies)}
🥗 Питание: {esc(diet)}

*Норма*
🔥 {db_user.target_calories or '—'} ккал в день

🕒 Часовой пояс: {esc(zone_text)}
🔔 Напоминания: {reminders}"""

    keyboard = [
        [InlineKeyboardButton("✏️ Профиль", callback_data="edit_profile")],
        [InlineKeyboardButton("🥗 Аллергии и питание", callback_data="edit_diet")],
        [InlineKeyboardButton("🎯 Цели по КБЖУ", callback_data="edit_goals")],
        [InlineKeyboardButton(f"🕒 Часовой пояс: {zone_text}", callback_data="set_timezone")],
        [InlineKeyboardButton(
            f"🔔 Напоминания: {'вкл' if db_user.reminders_enabled else 'выкл'}",
            callback_data="toggle_reminders")],
        [InlineKeyboardButton("📊 Отчёты и чек-ины", callback_data="edit_report_settings")],
        [InlineKeyboardButton("🎯 Челленджи", callback_data="challenge_suggest")],
    ]

    return text, InlineKeyboardMarkup(keyboard)


async def show_settings(query):
    """Показать настройки через callback"""
    db_user = await get_db_user(query.from_user.id)

    if not db_user:
        await safe_edit(query.message, "Профиль не найден. Отправьте /start, чтобы начать.")
        return

    text, markup = build_settings_view(db_user)
    result = await safe_edit(query.message, text, parse_mode="Markdown", reply_markup=markup)
    if result is None:
        await safe_reply(query.message, text, parse_mode="Markdown", reply_markup=markup)


async def show_settings_as_message(update: Update):
    """Показать настройки через обычное сообщение (из кнопки)"""
    db_user = await get_db_user(update.effective_user.id)

    if not db_user:
        await update.message.reply_text(
            "Профиль не найден. Отправьте /start, чтобы начать.",
            reply_markup=get_main_keyboard()
        )
        return

    text, markup = build_settings_view(db_user)
    await safe_reply(update.message, text, parse_mode="Markdown", reply_markup=markup)


async def edit_diet(query):
    """Настройки диеты и аллергий"""
    db_user = await get_db_user(query.from_user.id)

    allergies = (db_user.allergies if db_user else None) or []
    prefs = (db_user.dietary_preferences if db_user else None) or []

    text = "🥗 *Аллергии и питание*\n\n"
    if allergies:
        text += f"🚫 Не предлагаю: *{esc(', '.join(allergies))}*\n"
    if prefs:
        text += f"✅ Режим питания: *{esc(', '.join(prefs))}*\n"
    if not allergies and not prefs:
        text += "Ограничений пока нет.\n"

    text += (
        "\nЧтобы добавить — просто напишите мне обычным сообщением:\n"
        "_«У меня аллергия на молоко»_\n"
        "_«Я вегетарианец»_\n\n"
        "Учту и в рецептах, и в подборе перекусов."
    )

    keyboard = []
    if allergies or prefs:
        keyboard.append([InlineKeyboardButton("🗑 Убрать все ограничения", callback_data="clear_diet")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="show_settings")])

    await safe_edit(query.message, text, parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard))


async def clear_diet_restrictions(query):
    """Очистить все ограничения по диете и аллергиям"""
    user = query.from_user

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user.id))
        db_user = result.scalar_one_or_none()

        if db_user:
            db_user.allergies = None
            db_user.dietary_preferences = None
            await session.commit()
            logger.info(f"User {user.id} cleared diet restrictions")

    await query.answer("Ограничения убраны")
    await edit_diet(query)


async def edit_goals(query):
    """Показать цели по КБЖУ"""
    db_user = await get_db_user(query.from_user.id)

    if db_user and db_user.target_calories:
        text = f"""🎯 *Цели по КБЖУ*

Цель: {human_goal(db_user.goal)}

*На день*
🔥 {db_user.target_calories} ккал
🥩 Белки: {format_amount(db_user.target_protein)} г
🧈 Жиры: {format_amount(db_user.target_fats)} г
🍞 Углеводы: {format_amount(db_user.target_carbs)} г

_Считаю по вашим данным: {db_user.age} лет, {esc(db_user.weight)} кг, {esc(db_user.height)} см, {human_activity(db_user.activity_level).lower()}._

Измените профиль — пересчитаю автоматически.
Задать вручную: /goals"""
    else:
        text = ("🎯 *Цели не установлены*\n\n"
                "Заполните профиль, и я рассчитаю норму: возраст, вес, рост, цель и активность.")

    await safe_edit(
        query.message, text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Изменить профиль", callback_data="edit_profile")],
            [InlineKeyboardButton("◀️ Назад", callback_data="show_settings")],
        ])
    )


async def toggle_reminders_callback(query):
    """Переключить напоминания"""
    user = query.from_user

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user.id))
        db_user = result.scalar_one_or_none()

        if not db_user:
            await query.answer("Профиль не найден", show_alert=True)
            return

        db_user.reminders_enabled = not db_user.reminders_enabled
        await session.commit()
        state = db_user.reminders_enabled

    await query.answer("Напоминания включены" if state else "Напоминания выключены")
    await show_settings(query)


async def edit_report_settings_callback(query):
    """Настройки отчётов"""
    db_user = await get_db_user(query.from_user.id)

    if not db_user:
        await query.answer("Профиль не найден", show_alert=True)
        return

    def mark(flag):
        return "вкл" if flag else "выкл"

    text = f"""📊 *Отчёты и чек-ины*

Что я присылаю сам — по вашему времени ({esc(describe_zone(db_user.timezone))}):

🌆 Итоги дня в {db_user.daily_report_time or '21:00'} — {mark(db_user.daily_report_enabled)}
📈 Обзор недели по воскресеньям — {mark(db_user.weekly_report_enabled)}
☀️ Утренний чек-ин в {db_user.morning_checkin_time or '08:00'} — {mark(db_user.morning_checkin_enabled)}
⚖️ Напоминание взвеситься — {mark(db_user.weigh_reminder_enabled)}

Нажмите, чтобы переключить."""

    keyboard = [
        [InlineKeyboardButton(f"🌆 Итоги дня: {mark(db_user.daily_report_enabled)}",
                              callback_data="toggle_daily_report")],
        [InlineKeyboardButton(f"📈 Обзор недели: {mark(db_user.weekly_report_enabled)}",
                              callback_data="toggle_weekly_report")],
        [InlineKeyboardButton(f"☀️ Утренний чек-ин: {mark(db_user.morning_checkin_enabled)}",
                              callback_data="toggle_morning_checkin")],
        [InlineKeyboardButton(f"⚖️ Напоминание о весе: {mark(db_user.weigh_reminder_enabled)}",
                              callback_data="toggle_weigh_reminder")],
        [InlineKeyboardButton("◀️ Назад", callback_data="show_settings")],
    ]

    await safe_edit(query.message, text, parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard))


async def toggle_report_flag(query, field: str, label: str):
    """Общий переключатель флагов уведомлений."""
    user = query.from_user

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user.id))
        db_user = result.scalar_one_or_none()

        if not db_user:
            await query.answer("Профиль не найден", show_alert=True)
            return

        new_value = not getattr(db_user, field)
        setattr(db_user, field, new_value)
        await session.commit()

    await query.answer(f"{label}: {'включено' if new_value else 'выключено'}")
    await edit_report_settings_callback(query)


async def show_main_menu(query):
    """Показать главное меню"""
    stats = await statistics_service.get_daily_stats(query.from_user.id)
    line = ""
    if stats and stats["entries_count"]:
        line = f"\n\nСегодня: {stats['calories']} из {stats['target_calories']} ккал."

    await safe_edit(
        query.message,
        f"👋 Чем помочь?{line}\n\nНапишите или скажите, что съели, или выберите действие.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Мой день", callback_data="stats_today")],
            [InlineKeyboardButton("🔁 Повторить", callback_data="repeat_menu")],
            [InlineKeyboardButton("📝 Дневник", callback_data="diary_today")],
            [InlineKeyboardButton("⚙️ Настройки", callback_data="show_settings")],
        ])
    )


# =====================
# ЧЕЛЛЕНДЖИ
# =====================

async def suggest_challenge_callback(query):
    """Предложить челлендж пользователю"""
    db_user = await get_db_user(query.from_user.id)

    if db_user and db_user.active_challenge:
        challenge = db_user.active_challenge
        text = f"""🎯 *{esc(challenge.get('name', 'Челлендж'))}*

Идёт прямо сейчас.
✅ Выполнено дней: {challenge.get('days_completed', 0)}
❌ Пропущено: {challenge.get('days_failed', 0)}

Проверяю результат каждый вечер."""

        keyboard = [
            [InlineKeyboardButton("📊 Подробнее", callback_data="challenge_status")],
            [InlineKeyboardButton("❌ Прекратить", callback_data="challenge_cancel")],
            [InlineKeyboardButton("◀️ Назад", callback_data="show_settings")],
        ]
        await safe_edit(query.message, text, parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(keyboard))
        return

    text = """🎯 *Мини-челленджи*

Небольшая цель на несколько дней — я напомню и отмечу прогресс.
Бросить можно в любой момент, без осуждения.

Что попробуем?"""

    keyboard = [
        [InlineKeyboardButton(f"{info['name']} · {info['duration']} дн.",
                              callback_data=f"challenge_start_{challenge_type}")]
        for challenge_type, info in CHALLENGE_TYPES.items()
    ]
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="show_settings")])

    await safe_edit(query.message, text, parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard))


async def start_challenge_callback(query, challenge_type: str):
    """Начать челлендж"""
    challenge = await challenge_service.start_challenge(query.from_user.id, challenge_type)

    if challenge:
        info = CHALLENGE_TYPES.get(challenge_type, {})
        text = f"""✅ *Поехали!*

🎯 {esc(info.get('name', challenge_type))}
{esc(info.get('description', ''))}

Длительность: {info.get('duration', 7)} дней
Проверяю каждый вечер по вашему дневнику.

Удачи! 💪"""
        keyboard = [[InlineKeyboardButton("📊 Мой день", callback_data="stats_today")]]
    else:
        text = "Не получилось начать — возможно, челлендж уже идёт."
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="challenge_suggest")]]

    await safe_edit(query.message, text, parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard))


async def cancel_challenge_callback(query):
    """Отменить челлендж"""
    success = await challenge_service.cancel_challenge(query.from_user.id)

    text = ("Челлендж остановлен. Это нормально — не каждый эксперимент заходит."
            if success else "Активного челленджа нет.")

    await safe_edit(
        query.message, text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎯 Выбрать другой", callback_data="challenge_suggest"),
            InlineKeyboardButton("◀️ Меню", callback_data="back_to_menu"),
        ]])
    )


async def show_challenge_status_callback(query):
    """Показать статус челленджа"""
    db_user = await get_db_user(query.from_user.id)

    if db_user and db_user.active_challenge:
        challenge = db_user.active_challenge
        info = CHALLENGE_TYPES.get(challenge.get('type', ''), {})

        completed = challenge.get('days_completed', 0)
        failed = challenge.get('days_failed', 0)
        total = info.get('duration', 7)
        remaining = max(total - completed - failed, 0)

        progress_bar = "✅" * completed + "❌" * failed + "⬜" * remaining

        text = f"""🎯 *{esc(challenge.get('name', 'Челлендж'))}*

{esc(info.get('description', ''))}

{progress_bar}

✅ Выполнено: {completed}
❌ Пропущено: {failed}
⬜ Осталось: {remaining}"""
    else:
        text = "Активного челленджа нет. Хотите начать?"

    await safe_edit(
        query.message, text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Прекратить", callback_data="challenge_cancel"),
            InlineKeyboardButton("◀️ Меню", callback_data="back_to_menu"),
        ]])
    )


# =====================
# ВЕС
# =====================

async def log_weight_callback(query, context: ContextTypes.DEFAULT_TYPE):
    """Запросить вес для записи"""
    await safe_edit(
        query.message,
        "⚖️ Напишите ваш вес числом — например, *75,5*",
        parse_mode="Markdown"
    )
    context.user_data['awaiting_weight'] = True


async def handle_weight_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = None) -> bool:
    """Обработать ввод веса"""
    if not context.user_data.get('awaiting_weight'):
        return False

    user = update.effective_user
    text = (text if text is not None else update.message.text or "").strip().replace(',', '.')

    try:
        weight = float(text)
    except ValueError:
        # Человек написал что-то другое — не держим его в ловушке ожидания веса.
        context.user_data['awaiting_weight'] = False
        await update.message.reply_text(
            "Вес не записал — не увидел числа. Если захотите, нажмите «Записать вес» ещё раз."
        )
        return False

    if weight < 30 or weight > 300:
        await update.message.reply_text("Проверьте значение — я ожидаю вес от 30 до 300 кг.")
        return True

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user.id))
        db_user = result.scalar_one_or_none()

        if not db_user:
            context.user_data['awaiting_weight'] = False
            await update.message.reply_text("Профиль не найден. Отправьте /start.")
            return True

        session.add(WeightLog(user_id=db_user.id, weight=weight, date=local_today(user_zone(db_user))))

        old_weight = db_user.weight
        db_user.weight = weight

        # Вес влияет на норму калорий — пересчитываем, чтобы цель не устаревала.
        recalculated = None
        if all([db_user.age, db_user.height, db_user.gender, db_user.activity_level, db_user.goal]):
            recalculated = await ai_service.calculate_daily_calories(
                weight=weight, height=db_user.height, age=db_user.age,
                gender=db_user.gender, activity_level=db_user.activity_level, goal=db_user.goal
            )
            db_user.target_calories = recalculated['calories']
            db_user.target_protein = recalculated['protein']
            db_user.target_fats = recalculated['fats']
            db_user.target_carbs = recalculated['carbs']

        await session.commit()

    diff_text = ""
    if old_weight:
        diff = weight - old_weight
        if abs(diff) >= 0.1:
            sign = "+" if diff > 0 else ""
            diff_text = f"\nИзменение: {sign}{diff:.1f} кг"

    target_text = ""
    if recalculated:
        target_text = f"\n\nОбновил норму: *{recalculated['calories']}* ккал в день"

    context.user_data['awaiting_weight'] = False

    await safe_reply(
        update.message,
        f"✅ Записал вес: *{weight}* кг{diff_text}{target_text}",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
    return True


# =====================
# УТРЕННИЙ ЧЕК-ИН И ПЛАНЫ
# =====================

async def set_day_plan_callback(query, plan_type: str):
    """Установить план на день"""
    user = query.from_user

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user.id))
        db_user = result.scalar_one_or_none()
        if db_user:
            db_user.today_plan_mode = plan_type
            db_user.plan_mode_date = local_today(user_zone(db_user))
            await session.commit()

    if plan_type == "strict":
        text = ("🎯 *Держим цель*\n\n"
                "Хорошо! Подскажу, если увижу риск перебора.\n\nУдачного дня 💪")
    else:
        text = ("😌 *Свободный день*\n\n"
                "Принято. Считать буду, но напоминать почти не стану.\n\nНаслаждайтесь 🌟")

    await safe_edit(
        query.message, text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🍽 План питания на день", callback_data="quick_meal_plan")],
            [InlineKeyboardButton("📊 Мой день", callback_data="stats_today")],
        ])
    )


MEAL_PLAN_FORMATS = {
    "no_cooking": "без готовки, только готовые продукты",
    "simple": "с минимальной готовкой — сварить или разогреть",
    "full": "полноценные домашние блюда",
    "fast": "быстрый вариант, включая полуфабрикаты",
}


def meal_plan_options_keyboard(with_back: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🥗 Совсем без готовки", callback_data="meal_plan_no_cooking")],
        [InlineKeyboardButton("🍳 Минимум готовки", callback_data="meal_plan_simple")],
        [InlineKeyboardButton("🍲 Готовлю нормально", callback_data="meal_plan_full")],
        [InlineKeyboardButton("⚡️ Быстро и на бегу", callback_data="meal_plan_fast")],
    ]
    if with_back:
        rows.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(rows)


async def show_meal_plan_options_callback(query):
    """Показать опции планов питания"""
    await safe_edit(
        query.message,
        "🍽 *План на день*\n\nСколько времени готовы потратить на готовку?",
        parse_mode="Markdown",
        reply_markup=meal_plan_options_keyboard()
    )


async def show_meal_plan_options_message(update: Update):
    """То же меню планов, но в ответ на текстовый запрос."""
    await safe_reply(
        update.message,
        "🍽 *План на день*\n\nСколько времени готовы потратить на готовку?",
        parse_mode="Markdown",
        reply_markup=meal_plan_options_keyboard(with_back=False)
    )


async def generate_meal_plan_callback(query, plan_type: str, context: ContextTypes.DEFAULT_TYPE):
    """Генерировать план питания"""
    user = query.from_user

    await safe_edit(query.message, "⏳ Составляю план — это займёт несколько секунд…")

    db_user = await get_db_user(user.id)

    if not db_user or not db_user.target_calories:
        await safe_edit(
            query.message,
            "Сначала нужен профиль — тогда посчитаю норму и составлю план.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✏️ Заполнить профиль", callback_data="edit_profile")
            ]])
        )
        return

    allergies = db_user.allergies or []
    prefs = db_user.dietary_preferences or []
    plan_desc = MEAL_PLAN_FORMATS.get(plan_type, "сбалансированный")

    prompt = f"""Составь план питания на день ({plan_desc}).

Целевая калорийность: {db_user.target_calories} ккал
Белки: {db_user.target_protein} г, жиры: {db_user.target_fats} г, углеводы: {db_user.target_carbs} г
Аллергии (исключить полностью): {', '.join(allergies) if allergies else 'нет'}
Режим питания: {', '.join(prefs) if prefs else 'обычный'}

Формат:
🌅 Завтрак: [блюдо] (~X ккал)
🌞 Обед: [блюдо] (~X ккал)
🌙 Ужин: [блюдо] (~X ккал)
🍎 Перекус: [вариант] (~X ккал)

Пиши кратко и по-русски, обращайся на «вы», без вступлений."""

    try:
        response = await ai_service.generate_response(prompt, user.id)
    except AIServiceUnavailable:
        await safe_edit(
            query.message, SERVICE_UNAVAILABLE,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")
            ]])
        )
        return

    text = f"""🍽 *План на сегодня*
_{plan_desc}_

{response}

_Съели что-то из плана — напишите мне, запишу в дневник._"""

    await safe_edit(
        query.message, text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Другой формат", callback_data="quick_meal_plan")],
            [InlineKeyboardButton("📊 Мой день", callback_data="stats_today")],
        ])
    )


# =====================
# ГОЛОС
# =====================

MAX_VOICE_SECONDS = 120


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Голосовое сообщение → текст → обычная маршрутизация.

    Люди в Telegram чаще говорят, чем печатают, особенно на ходу. Расшифровку
    показываем, чтобы было понятно, что именно я услышал и записал.
    """
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    duration = voice.duration or 0
    if duration > MAX_VOICE_SECONDS:
        await update.message.reply_text(
            "Голосовое длиннее двух минут — скажите, пожалуйста, короче и только про еду 🙂",
            reply_markup=get_main_keyboard()
        )
        return

    notice = await update.message.reply_text("🎤 Слушаю…")

    try:
        voice_file = await context.bot.get_file(voice.file_id)
        audio_bytes = bytes(await voice_file.download_as_bytearray())
        filename = "voice.ogg" if update.message.voice else (getattr(voice, "file_name", None) or "audio.mp3")
        transcript = await ai_service.transcribe_audio(audio_bytes, filename)
    except AIServiceUnavailable:
        await notice.edit_text(SERVICE_UNAVAILABLE)
        return
    except Exception as e:
        logger.error(f"Error processing voice message: {e}", exc_info=True)
        await notice.edit_text("Не получилось разобрать запись. Попробуйте написать текстом.")
        return

    transcript = (transcript or "").strip()
    if not transcript:
        await notice.edit_text("В записи не разобрал слов — попробуйте ещё раз или напишите текстом.")
        return

    await notice.edit_text(f"🎤 «{transcript}»")
    await route_text(update, context, transcript)


# =====================
# ФОТО
# =====================

async def handle_photo_lazy_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Оценка блюда по фотографии.
    """
    user = update.effective_user
    caption = (update.message.caption or "").strip()

    await update.message.chat.send_action(action="typing")
    notice = await update.message.reply_text("🔍 Смотрю, что на фото…")

    try:
        photo = update.message.photo[-1]  # самое большое доступное разрешение
        photo_file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await photo_file.download_as_bytearray())

        result = await ai_service.analyze_food_photo(image_bytes, caption)
    except AIServiceUnavailable:
        await notice.edit_text(SERVICE_UNAVAILABLE)
        return
    except Exception as e:
        logger.error(f"Error processing photo: {e}", exc_info=True)
        await notice.edit_text(
            "Не получилось разобрать фото. Попробуйте описать блюдо текстом — посчитаю по описанию."
        )
        return

    items = result.get("items") or []
    if not items:
        await notice.edit_text(
            "Не смог распознать еду на снимке 🤔\n\n"
            "Напишите, что это за блюдо, — посчитаю по описанию."
        )
        return

    # Подпись к фото может содержать «вчера на ужин» — учитываем при записи
    db_user = await get_db_user(user.id)
    meal_ctx = parse_meal_context(caption)
    moment_utc, meal_type, day_offset = resolve_moment(meal_ctx, user_zone(db_user))

    context.user_data['last_photo_items'] = items
    context.user_data['last_photo_name'] = result.get("dish_name", "Блюдо с фото")
    context.user_data['last_photo_when'] = (moment_utc.isoformat(), meal_type, day_offset)

    body = render_nutrition_items(items)
    confidence = result.get("confidence", "средняя")
    advice = result.get("advice")
    advice_line = f"\n\n💡 {esc(advice)}" if advice else ""

    await notice.delete()
    await safe_reply(
        update.message,
        f"📸 *{esc(result.get('dish_name', 'Блюдо с фото'))}*\n\n{body}\n\n"
        f"_Точность оценки: {esc(confidence)}. По фото трудно определить вес и масло — "
        f"напишите детали, если хотите точнее._{advice_line}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Записать в дневник", callback_data="diary_add_from_photo")],
            [InlineKeyboardButton("✏️ Уточнить детали", callback_data="refine_photo_estimate")],
        ])
    )


async def add_photo_estimate_to_diary(query, context: ContextTypes.DEFAULT_TYPE):
    """Записать оценку по фото в дневник."""
    items = context.user_data.get('last_photo_items')

    if not items:
        await safe_edit(query.message, "Оценка устарела — пришлите фото ещё раз.")
        return

    when = context.user_data.get('last_photo_when')
    moment_utc = meal_type = None
    day_offset = 0
    if when:
        try:
            moment_utc = datetime.fromisoformat(when[0])
            meal_type, day_offset = when[1], when[2]
        except (ValueError, TypeError, IndexError):
            moment_utc = None

    entry_ids, meal_type = await save_meal_entries(
        query.from_user.id, items, notes="Оценка по фото", moment_utc=moment_utc, meal_type=meal_type
    )

    if entry_ids is None:
        await query.answer("Профиль не найден", show_alert=True)
        return

    context.user_data.pop('last_photo_items', None)
    context.user_data.pop('last_photo_when', None)

    total = sum(i.get("calories", 0) for i in items)
    footer = await day_stats_footer(query.from_user.id, day_offset)
    await query.answer("Записано в дневник")
    await safe_edit(
        query.message,
        f"✅ Записал {describe_when(day_offset, meal_type)} — {format_amount(total)} ккал\n\n"
        f"{render_nutrition_items(items)}{footer}",
        parse_mode="Markdown",
        reply_markup=meal_saved_keyboard(entry_ids)
    )


# =====================
# РОУТЕР CALLBACK-КНОПОК
# =====================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на inline кнопки"""
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"Button callback: {data} from user {query.from_user.id}")

    try:
        # Дневник
        if data == "diary_today":
            await show_diary(None, context, period="today", query=query)
        elif data == "diary_yesterday":
            await show_diary(None, context, period="yesterday", query=query)
        elif data == "diary_week":
            await show_diary(None, context, period="week", query=query)
        elif data == "diary_clear_today":
            await clear_diary_today(query)
        elif data == "diary_pick_delete":
            await show_delete_picker(query)
        elif data.startswith("del_entry_"):
            await delete_diary_entry(query, int(data.rsplit("_", 1)[1]))
        elif data.startswith("undo_meal_"):
            await undo_meal(query, data[len("undo_meal_"):])

        # Повторы
        elif data == "repeat_menu":
            await show_repeat_menu(query.from_user.id, context, query=query)
        elif data.startswith("repeat_"):
            await repeat_group_callback(query, context, int(data.rsplit("_", 1)[1]))

        # Статистика
        elif data == "stats_week":
            await show_weekly_stats(query)
        elif data == "stats_today":
            await stats_today_callback(query)
        elif data == "suggest_snack":
            await suggest_snack_callback(query)

        # Настройки
        elif data in ("settings", "show_settings"):
            await show_settings(query)
        elif data == "edit_diet":
            await edit_diet(query)
        elif data == "edit_goals":
            await edit_goals(query)
        elif data == "clear_diet":
            await clear_diet_restrictions(query)
        elif data == "toggle_reminders":
            await toggle_reminders_callback(query)
        elif data == "set_timezone":
            await ask_timezone(query.message, context)
        elif data == "edit_report_settings":
            await edit_report_settings_callback(query)
        elif data == "toggle_daily_report":
            await toggle_report_flag(query, "daily_report_enabled", "Итоги дня")
        elif data == "toggle_weekly_report":
            await toggle_report_flag(query, "weekly_report_enabled", "Обзор недели")
        elif data == "toggle_morning_checkin":
            await toggle_report_flag(query, "morning_checkin_enabled", "Утренний чек-ин")
        elif data == "toggle_weigh_reminder":
            await toggle_report_flag(query, "weigh_reminder_enabled", "Напоминание о весе")

        # Меню
        elif data == "back_to_menu":
            await show_main_menu(query)
        elif data == "find_recipes":
            await safe_reply(
                query.message,
                "🍳 Напишите продукты, которые есть дома — подберу рецепты.\n\n"
                "_курица, рис, помидоры_",
                parse_mode="Markdown"
            )
        elif data == "calc_calories":
            await safe_reply(
                query.message,
                "📊 Напишите блюдо и количество — посчитаю КБЖУ.\n\n"
                "_куриная грудка 200 г_",
                parse_mode="Markdown"
            )

        # Рецепты
        elif data == "saved_recipes":
            await my_recipes_command(None, context, query=query)
        elif data.startswith("save_recipe_"):
            await save_recipe_to_collection(query, data.rsplit("_", 1)[1], context)
        elif data.startswith("eat_recipe_"):
            await eat_recipe(query, data.rsplit("_", 1)[1], context)
        elif data.startswith("view_recipe_"):
            await view_saved_recipe(query, int(data.rsplit("_", 1)[1]))
        elif data.startswith("delete_recipe_"):
            await delete_saved_recipe(query, int(data.rsplit("_", 1)[1]))
        elif data.startswith("add_saved_to_diary_"):
            await add_saved_recipe_to_diary(query, int(data.rsplit("_", 1)[1]))

        # Челленджи
        elif data == "challenge_suggest":
            await suggest_challenge_callback(query)
        elif data.startswith("challenge_start_"):
            await start_challenge_callback(query, data[len("challenge_start_"):])
        elif data == "challenge_cancel":
            await cancel_challenge_callback(query)
        elif data == "challenge_status":
            await show_challenge_status_callback(query)

        # Вес
        elif data == "log_weight":
            await log_weight_callback(query, context)
        elif data == "skip_weigh":
            await safe_edit(query.message, "Хорошо, напомню в следующий раз 👌")

        # Утренний чек-ин и планы
        elif data == "plan_strict":
            await set_day_plan_callback(query, "strict")
        elif data == "plan_relaxed":
            await set_day_plan_callback(query, "relaxed")
        elif data == "quick_meal_plan":
            await show_meal_plan_options_callback(query)
        elif data.startswith("meal_plan_"):
            await generate_meal_plan_callback(query, data[len("meal_plan_"):], context)

        # Фото
        elif data == "diary_add_from_photo":
            await add_photo_estimate_to_diary(query, context)
        elif data == "refine_photo_estimate":
            await safe_edit(
                query.message,
                "✏️ Напишите, что было на тарелке и примерный вес.\n\n"
                "_Например: куриная грудка 200 г, рис 150 г, салат с маслом_",
                parse_mode="Markdown"
            )
        else:
            logger.warning(f"Unhandled callback data: {data}")

    except Exception as e:
        logger.error(f"Error handling callback {data}: {e}", exc_info=True)
        try:
            await query.answer("Не получилось выполнить действие", show_alert=True)
        except Exception:
            pass
