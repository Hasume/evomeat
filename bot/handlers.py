"""
Обработчики команд Telegram бота
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta, date
from database.models import User, DiaryEntry, Recipe, SubscriptionType, DataSource, WeightLog, Challenge
from database.database import AsyncSessionLocal
from services import AIService, NutritionAPI
from services.statistics_service import statistics_service, get_snack_recommendations, format_snack_recommendations, NutrientStatus
from services.reminder_service import reminder_service
from services.report_service import report_service
from services.challenge_service import challenge_service, CHALLENGE_TYPES
from config import settings
import logging
import asyncio

logger = logging.getLogger(__name__)

# Инициализация сервисов
ai_service = AIService()
nutrition_api = NutritionAPI()


async def keep_typing(update: Update, stop_event: asyncio.Event):
    """
    Поддерживает анимацию 'печатает...' для длительных операций
    """
    try:
        while not stop_event.is_set():
            await update.message.chat.send_action(action="typing")
            await asyncio.sleep(4)  # Обновляем каждые 4 секунды (typing длится 5 сек)
    except Exception as e:
        logger.error(f"Error in keep_typing: {e}")


def get_main_keyboard():
    """Создает основную клавиатуру для быстрого доступа"""
    keyboard = [
        [KeyboardButton("🍳 Найти рецепты"), KeyboardButton("📊 Калории")],
        [KeyboardButton("📖 Мои рецепты"), KeyboardButton("📝 Дневник")],
        [KeyboardButton("📈 Статистика"), KeyboardButton("⚙️ Настройки")],
        [KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    logger.info(f"🚀 START command from user {user.id} ({user.username or 'no username'})")
    
    try:
        # Создаём или получаем пользователя из БД
        async with AsyncSessionLocal() as session:
            try:
                result = await session.execute(
                    select(User).where(User.telegram_id == user.id)
                )
                db_user = result.scalar_one_or_none()
                
                # Проверяем заполнен ли профиль
                if db_user and db_user.age is not None:
                    # Профиль заполнен - показываем главное меню
                    welcome_text = f"""👋 С возвращением, {user.first_name}!

Чем могу помочь сегодня?
• 🍳 Найти рецепты
• 📊 Посчитать калории
• 📝 Посмотреть дневник
• ⚙️ Настройки"""
                    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard())
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
                
                # Профиль не заполнен - запускаем регистрацию
                from bot.conversation import start_registration
                return await start_registration(update, context)
            except Exception as db_error:
                logger.error(f"❌ Database error for user {user.id}: {db_error}", exc_info=True)
                await update.message.reply_text(
                    "Произошла ошибка при регистрации. Попробуйте еще раз через минуту.",
                    reply_markup=get_main_keyboard()
                )
                return
        
    except Exception as e:
        logger.error(f"❌ Critical error in start_command for user {user.id}: {e}", exc_info=True)
        await update.message.reply_text(
            "Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже или обратитесь к администратору."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
🤖 **Как пользоваться ботом:**

**🍳 Найти рецепты**
Нажмите кнопку или напишите ингредиенты:
• _"курица, рис, помидоры"_
• _"что приготовить из картошки"_

**📊 Калории**
Нажмите кнопку или напишите:
• _"сколько калорий в 100г риса"_
• _"бжу куриной грудки"_
• _"рассчитай 200г гречки и 150г мяса"_

**📝 Дневник питания**
Записывайте что съели:
• _"съел 200г риса и куриную грудку"_
• _"выпил капучино 300мл"_
Бот сам спросит способ приготовления если нужно!

**📈 Статистика**
Напишите или нажмите кнопку:
• _"статистика"_, _"итоги"_, _"как я сегодня?"_
Покажу КБЖУ за день с оценкой мало/норм/много
и подскажу чем добрать если нужно!

**📖 Мои рецепты**
Сохраняйте понравившиеся рецепты кнопкой 💾

**⚙️ Настройки**
• Редактировать профиль
• Указать аллергии и диету
• Включить/выключить напоминания
• Посмотреть цели по КБЖУ

💬 **Просто пишите!**
Я понимаю естественную речь и отвечу на вопросы о питании.
"""
    await update.message.reply_text(help_text, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def recipes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /recipes"""
    await update.message.reply_text(
        "🍳 **Поиск рецептов**\n\n"
        "Отправьте список ингредиентов через запятую.\n\n"
        "Например:\n"
        "• курица, рис, помидоры\n"
        "• картошка, лук, морковь\n"
        "• творог, яйца, мука",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )


async def handle_recipe_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений - интеллектуальная маршрутизация"""
    user = update.effective_user
    message_text = update.message.text
    
    # Обработка кнопок главной клавиатуры
    if message_text == "🍳 Найти рецепты":
        await recipes_command(update, context)
        return
    elif message_text == "📊 Калории":
        await calories_command(update, context)
        return
    elif message_text == "📝 Дневник":
        await diary_command(update, context)
        return
    elif message_text == "📖 Мои рецепты":
        await my_recipes_command(update, context)
        return
    elif message_text == "📈 Статистика":
        await statistics_command(update, context)
        return
    elif message_text == "⚙️ Настройки":
        await show_settings_as_message(update)
        return
    elif message_text == "❓ Помощь":
        await help_command(update, context)
        return
    
    # Проверка на ввод веса
    if await handle_weight_input(update, context):
        return
    
    # 0. Проверяем есть ли ожидающее уточнение (ответ на вопрос о способе приготовления)
    pending_clarification = context.user_data.get('pending_clarification')
    if pending_clarification:
        # Объединяем оригинальный текст с уточнением
        original_text = pending_clarification.get('original_text', '')
        is_meal_record = pending_clarification.get('is_meal_record', False)
        
        # Формируем новый запрос с уточнением
        clarified_text = f"{original_text} (способ приготовления: {message_text})"
        
        logger.info(f"Processing clarification for user {user.id}: '{clarified_text}'")
        
        # Очищаем pending
        context.user_data.pop('pending_clarification', None)
        
        # Пересчитываем с уточнением
        await update.message.chat.send_action(action="typing")
        
        try:
            nutrition_info = await ai_service.calculate_nutrition_from_text(clarified_text)
            logger.info(f"Clarification result: {nutrition_info}")
            
            # Если AI всё ещё хочет уточнение - спрашиваем
            if nutrition_info and nutrition_info.get('needs_clarification'):
                question = nutrition_info.get('clarification_question', 'Уточните способ приготовления')
                context.user_data['pending_clarification'] = {
                    'original_text': original_text,
                    'items': nutrition_info.get('items', []),
                    'is_meal_record': is_meal_record
                }
                await update.message.reply_text(f"🤔 {question}", reply_markup=get_main_keyboard())
                return
            
            if nutrition_info:
                items = nutrition_info.get('items', [])
                total = nutrition_info.get('total', {})
                
                if is_meal_record:
                    # Записываем в дневник
                    food_names = []
                    for item in items:
                        cooking = item.get('cooking_method', '')
                        if cooking and cooking != 'готовое':
                            food_names.append(f"{item.get('name', 'Блюдо')} ({item.get('amount', '')}, {cooking})")
                        else:
                            food_names.append(f"{item.get('name', 'Блюдо')} ({item.get('amount', '')})")
                    
                    total_calories = total.get('calories', sum(i.get('calories', 0) for i in items))
                    total_protein = total.get('protein', sum(i.get('protein', 0) for i in items))
                    total_fats = total.get('fats', sum(i.get('fats', 0) for i in items))
                    total_carbs = total.get('carbs', sum(i.get('carbs', 0) for i in items))
                    
                    async with AsyncSessionLocal() as session:
                        result = await session.execute(
                            select(User).where(User.telegram_id == user.id)
                        )
                        db_user = result.scalar_one_or_none()
                        
                        if db_user:
                            diary_entry = DiaryEntry(
                                user_id=db_user.id,
                                date=datetime.now(),
                                meal_type="приём пищи",
                                food_name=", ".join(food_names),
                                portion_size=1.0,
                                calories=total_calories,
                                protein=total_protein,
                                fats=total_fats,
                                carbs=total_carbs,
                                data_source=DataSource.AI_ESTIMATE,
                                notes=original_text
                            )
                            session.add(diary_entry)
                            await session.commit()
                    
                    response = "✅ **Записано в дневник!**\n\n"
                    for item in items:
                        cooking = item.get('cooking_method', '')
                        cooking_note = f" ({cooking})" if cooking and cooking != 'готовое' else ""
                        response += f"🍽 **{item.get('name', 'Продукт')}** ({item.get('amount', '')}){cooking_note}\n"
                        response += f"   🔥 {item.get('calories', 0)} ккал\n"
                        response += f"   🥩 Б: {item.get('protein', 0)}г | 🧈 Ж: {item.get('fats', 0)}г | 🍞 У: {item.get('carbs', 0)}г\n\n"
                    
                    if len(items) > 1:
                        response += f"━━━━━━━━━━━━━━━━━\n"
                        response += f"**Итого:** 🔥 {total_calories} ккал\n\n"
                    
                    response += "📝 Посмотреть дневник: кнопка **Дневник**"
                else:
                    # Просто показываем калории
                    response = "📊 **Пищевая ценность:**\n\n"
                    for item in items:
                        cooking = item.get('cooking_method', '')
                        cooking_note = f", {cooking}" if cooking and cooking != 'готовое' else ""
                        response += f"**{item['name']}** ({item['amount']}{cooking_note})\n"
                        response += f"🔥 {item['calories']} ккал\n"
                        response += f"🥩 Б: {item['protein']}г | 🧈 Ж: {item['fats']}г | 🍞 У: {item['carbs']}г\n\n"
                    
                    if len(items) > 1 and total:
                        response += "━━━━━━━━━━━━━━━━\n"
                        response += f"**Итого:** 🔥 {total['calories']} ккал"
                
                await update.message.reply_text(response, parse_mode="Markdown", reply_markup=get_main_keyboard())
                return
        except Exception as e:
            logger.error(f"Error processing clarification: {e}", exc_info=True)
        
        await update.message.reply_text(
            "Не удалось обработать. Попробуйте написать заново.",
            reply_markup=get_main_keyboard()
        )
        return
    
    # 1. Защита от попыток извлечь системный промпт
    prompt_extraction_keywords = [
        'что я написал', 'повтори', 'выше', 'инструкц', 'промпт', 'system', 
        'твои правила', 'твои принципы', 'ignore previous', 'your instructions',
        'what did i write', 'repeat above', 'show me your prompt'
    ]
    message_lower = message_text.lower()
    
    if any(keyword in message_lower for keyword in prompt_extraction_keywords):
        # Попытка извлечь промпт - блокируем
        logger.warning(f"User {user.id} attempted prompt extraction: {message_text[:50]}")
        await update.message.reply_text(
            "Я готов ответить на вопросы о питании и помочь с рецептами. Чем могу помочь? 😊",
            reply_markup=get_main_keyboard()
        )
        return
    
    # 1. Показываем индикатор "печатает..." во время анализа
    await update.message.chat.send_action(action="typing")
    
    try:
        intent_data = await ai_service.detect_intent(message_text)
        intent = intent_data.get('intent', 'general_question')
        confidence = intent_data.get('confidence', 0.5)
        entities = intent_data.get('entities', {})
        
        logger.info(f"User {user.id} intent: {intent} (confidence: {confidence})")
        
        # ВАЖНО: Проверяем сначала - это запись о приёме пищи?
        meal_keywords = ['съел', 'съела', 'поел', 'поела', 'ел ', 'ела ', 'кушал', 'кушала', 
                       'на завтрак', 'на обед', 'на ужин', 'перекусил', 'перекусила',
                       'позавтракал', 'пообедал', 'поужинал', 'выпил', 'выпила']
        message_lower = message_text.lower()
        
        # Если есть ключевые слова о еде + упоминание продуктов/граммов - это запись о еде
        has_meal_keyword = any(keyword in message_lower for keyword in meal_keywords)
        has_food_indicator = any(x in message_lower for x in ['грамм', 'гр', ' г ', 'порци', 'штук', 'кусок', 'тарелк', 'чашк', 'стакан'])
        
        if has_meal_keyword and (has_food_indicator or any(char.isdigit() for char in message_text)):
            # Переопределяем intent на calorie_count чтобы записать в дневник
            logger.info(f"Override intent to calorie_count (meal record detected)")
            intent = "calorie_count"
        
        # 2. Маршрутизация по намерению
        if intent == "greeting":
            await update.message.reply_text(
                f"👋 Привет, {user.first_name}!\n\n"
                "Я помогу вам с питанием и рецептами.\n"
                "Используйте кнопки ниже для навигации!",
                reply_markup=get_main_keyboard()
            )
            return
            
        elif intent == "general_question":
            # Дополнительная проверка: может это все-таки информация о диете?
            message_lower = message_text.lower()
            dietary_keywords = ['аллергия', 'не ем', 'не люблю', 'вегетариан', 'веган', 'непереносимость', 'не переношу']
            
            if any(keyword in message_lower for keyword in dietary_keywords):
                # Перенаправляем на сохранение диетической информации
                logger.info(f"Redirecting to save_dietary_info from general_question")
                entities['allergies'] = []
                entities['dietary_preferences'] = []
                
                # Извлекаем информацию
                for allergen in ['молоко', 'орех', 'глютен', 'яйц', 'рыб', 'морепродукт', 'соя', 'арахис', 'лактоз']:
                    if allergen in message_lower:
                        entities['allergies'].append(allergen)
                
                if 'вегетариан' in message_lower:
                    entities['dietary_preferences'].append('вегетарианство')
                if 'веган' in message_lower:
                    entities['dietary_preferences'].append('веганство')
                
                # Сохраняем
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(User).where(User.telegram_id == user.id)
                    )
                    db_user = result.scalar_one_or_none()
                    
                    if db_user:
                        saved_items = []
                        if entities['allergies']:
                            current_allergies = db_user.allergies or []
                            db_user.allergies = list(set(current_allergies + entities['allergies']))
                            saved_items.extend(entities['allergies'])
                        
                        if entities['dietary_preferences']:
                            current_prefs = db_user.dietary_preferences or []
                            db_user.dietary_preferences = list(set(current_prefs + entities['dietary_preferences']))
                            saved_items.extend(entities['dietary_preferences'])
                        
                        await session.commit()
                        
                        if saved_items:
                            items_text = ', '.join(saved_items)
                            await update.message.reply_text(
                                f"✅ Отлично, запомнил: **{items_text}**\n\nБуду учитывать при подборе рецептов!",
                                parse_mode="Markdown"
                            )
                        else:
                            await update.message.reply_text(
                                "✅ Принято! Буду учитывать ваши предпочтения."
                            )
                return
            
            await update.message.chat.send_action(action="typing")
            answer = await ai_service.answer_general_question(message_text)
            await update.message.reply_text(answer, parse_mode="Markdown", reply_markup=get_main_keyboard())
            return
            
        elif intent == "calorie_count":
            # Проверяем: это просто вопрос о калориях или запись о приёме пищи?
            meal_keywords = ['съел', 'съела', 'поел', 'поела', 'ел ', 'ела ', 'кушал', 'кушала', 
                           'на завтрак', 'на обед', 'на ужин', 'перекусил', 'перекусила',
                           'позавтракал', 'пообедал', 'поужинал', 'выпил', 'выпила']
            message_lower = message_text.lower()
            is_meal_record = any(keyword in message_lower for keyword in meal_keywords)
            
            await update.message.chat.send_action(action="typing")
            
            try:
                nutrition_info = await ai_service.calculate_nutrition_from_text(message_text)
                
                if nutrition_info:
                    # Проверяем нужно ли уточнение от AI
                    if nutrition_info.get('needs_clarification'):
                        question = nutrition_info.get('clarification_question', 'Уточните способ приготовления')
                        
                        # Сохраняем контекст для следующего сообщения
                        context.user_data['pending_clarification'] = {
                            'original_text': message_text,
                            'items': nutrition_info.get('items', []),
                            'is_meal_record': is_meal_record
                        }
                        
                        await update.message.reply_text(
                            f"🤔 {question}",
                            reply_markup=get_main_keyboard()
                        )
                        return
                    
                    # Если это запись о еде - сразу записываем (AI сам определил способ приготовления)
                    if is_meal_record:
                        items = nutrition_info.get('items', [])
                        total = nutrition_info.get('total', {})
                        
                        # Формируем данные для записи
                        food_names = []
                        for item in items:
                            cooking = item.get('cooking_method', '')
                            if cooking and cooking != 'готовое':
                                food_names.append(f"{item.get('name', 'Блюдо')} ({item.get('amount', '')}, {cooking})")
                            else:
                                food_names.append(f"{item.get('name', 'Блюдо')} ({item.get('amount', '')})")
                        
                        total_calories = total.get('calories', sum(i.get('calories', 0) for i in items))
                        total_protein = total.get('protein', sum(i.get('protein', 0) for i in items))
                        total_fats = total.get('fats', sum(i.get('fats', 0) for i in items))
                        total_carbs = total.get('carbs', sum(i.get('carbs', 0) for i in items))
                        
                        # Сохраняем в дневник
                        async with AsyncSessionLocal() as session:
                            result = await session.execute(
                                select(User).where(User.telegram_id == user.id)
                            )
                            db_user = result.scalar_one_or_none()
                            
                            if db_user:
                                diary_entry = DiaryEntry(
                                    user_id=db_user.id,
                                    date=datetime.now(),
                                    meal_type="приём пищи",
                                    food_name=", ".join(food_names),
                                    portion_size=1.0,
                                    calories=total_calories,
                                    protein=total_protein,
                                    fats=total_fats,
                                    carbs=total_carbs,
                                    data_source=DataSource.AI_ESTIMATE,
                                    notes=message_text
                                )
                                session.add(diary_entry)
                                await session.commit()
                                
                                logger.info(f"User {user.id} added meal to diary: {food_names}")
                        
                        # Формируем ответ
                        response = "✅ **Записано в дневник!**\n\n"
                        for item in items:
                            cooking = item.get('cooking_method', '')
                            cooking_note = f" ({cooking})" if cooking and cooking != 'готовое' else ""
                            response += f"🍽 **{item.get('name', 'Продукт')}** ({item.get('amount', '')}){cooking_note}\n"
                            response += f"   🔥 {item.get('calories', 0)} ккал\n"
                            response += f"   🥩 Б: {item.get('protein', 0)}г | 🧈 Ж: {item.get('fats', 0)}г | 🍞 У: {item.get('carbs', 0)}г\n\n"
                        
                        if len(items) > 1:
                            response += f"━━━━━━━━━━━━━━━━━\n"
                            response += f"**Итого:** 🔥 {total_calories} ккал\n\n"
                        
                        response += "📝 Посмотреть дневник: кнопка **Дневник**"
                        
                        await update.message.reply_text(response, parse_mode="Markdown", reply_markup=get_main_keyboard())
                        return
                    
                    # Просто показываем информацию о калориях (AI сам определил способ приготовления)
                    response = "📊 **Пищевая ценность:**\n\n"
                    
                    for item in nutrition_info.get('items', []):
                        cooking = item.get('cooking_method', '')
                        cooking_note = f", {cooking}" if cooking and cooking != 'готовое' else ""
                        response += f"**{item['name']}** ({item['amount']}{cooking_note})\n"
                        response += f"🔥 {item['calories']} ккал\n"
                        response += f"🥩 Белки: {item['protein']}г\n"
                        response += f"🧈 Жиры: {item['fats']}г\n"
                        response += f"🍞 Углеводы: {item['carbs']}г\n\n"
                    
                    total = nutrition_info.get('total', {})
                    if total:
                        response += "━━━━━━━━━━━━━━━━\n"
                        response += f"**Итого:**\n"
                        response += f"🔥 {total['calories']} ккал\n"
                        response += f"🥩 {total['protein']}г белков\n"
                        response += f"🧈 {total['fats']}г жиров\n"
                        response += f"🍞 {total['carbs']}г углеводов\n\n"
                    
                    response += "💡 _Чтобы записать в дневник, напишите:_\n"
                    response += "_\"Съел 200г риса и 150г курицы\"_"
                    
                    await update.message.reply_text(response, parse_mode="Markdown", reply_markup=get_main_keyboard())
                else:
                    await update.message.reply_text(
                        "Не удалось определить продукты. Попробуйте указать точнее, например:\n"
                        "\"200г вареной курицы, 100г риса\"",
                        reply_markup=get_main_keyboard()
                    )
            except Exception as e:
                logger.error(f"Error calculating nutrition: {e}")
                await update.message.reply_text(
                    "Произошла ошибка при подсчете калорий. Попробуйте еще раз.",
                    reply_markup=get_main_keyboard()
                )
            return
            
        elif intent == "nutrition_advice":
            # Дополнительная проверка: может это информация о диете, а не вопрос?
            message_lower = message_text.lower()
            statement_keywords = ['у меня', 'я не ем', 'я вегетариан', 'я веган', 'не люблю', 'не переношу']
            
            if any(keyword in message_lower for keyword in statement_keywords):
                # Это утверждение о себе, сохраняем
                logger.info(f"Redirecting to save_dietary_info from nutrition_advice")
                entities['allergies'] = []
                entities['dietary_preferences'] = []
                
                for allergen in ['молоко', 'орех', 'глютен', 'яйц', 'рыб', 'морепродукт', 'соя', 'арахис', 'лактоз']:
                    if allergen in message_lower:
                        entities['allergies'].append(allergen)
                
                if 'вегетариан' in message_lower:
                    entities['dietary_preferences'].append('вегетарианство')
                if 'веган' in message_lower:
                    entities['dietary_preferences'].append('веганство')
                
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(User).where(User.telegram_id == user.id)
                    )
                    db_user = result.scalar_one_or_none()
                    
                    if db_user:
                        saved_items = []
                        if entities['allergies']:
                            current_allergies = db_user.allergies or []
                            db_user.allergies = list(set(current_allergies + entities['allergies']))
                            saved_items.extend(entities['allergies'])
                        
                        if entities['dietary_preferences']:
                            current_prefs = db_user.dietary_preferences or []
                            db_user.dietary_preferences = list(set(current_prefs + entities['dietary_preferences']))
                            saved_items.extend(entities['dietary_preferences'])
                        
                        await session.commit()
                        
                        if saved_items:
                            items_text = ', '.join(saved_items)
                            await update.message.reply_text(
                                f"✅ Отлично, запомнил: **{items_text}**\n\nБуду учитывать при подборе рецептов!",
                                parse_mode="Markdown"
                            )
                        else:
                            await update.message.reply_text(
                                "✅ Принято! Буду учитывать ваши предпочтения."
                            )
                return
            
            # Это реальный вопрос, даем совет
            await update.message.chat.send_action(action="typing")
            # Даем совет по питанию
            answer = await ai_service.answer_general_question(message_text)
            await update.message.reply_text(answer, parse_mode="Markdown", reply_markup=get_main_keyboard())
            return
        
        elif intent == "save_dietary_info":
            # Сохраняем информацию об аллергиях/диете
            allergies = entities.get('allergies', [])
            dietary_prefs = entities.get('dietary_preferences', [])
            
            # Если GPT не извлек конкретные значения, попробуем сами
            if not allergies and not dietary_prefs:
                message_lower = message_text.lower()
                # Простое извлечение аллергий
                for allergen in ['молоко', 'орех', 'глютен', 'яйц', 'рыб', 'морепродукт', 'соя', 'арахис', 'лактоз']:
                    if allergen in message_lower:
                        allergies.append(allergen)
                
                # Простое извлечение диетических предпочтений
                if 'вегетариан' in message_lower:
                    dietary_prefs.append('вегетарианство')
                if 'веган' in message_lower:
                    dietary_prefs.append('веганство')
            
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(User).where(User.telegram_id == user.id)
                )
                db_user = result.scalar_one_or_none()
                
                if db_user:
                    # Обновляем данные
                    saved_items = []
                    if allergies:
                        current_allergies = db_user.allergies or []
                        db_user.allergies = list(set(current_allergies + allergies))
                        saved_items.extend(allergies)
                        logger.info(f"User {user.id} allergies updated: {db_user.allergies}")
                    
                    if dietary_prefs:
                        current_prefs = db_user.dietary_preferences or []
                        db_user.dietary_preferences = list(set(current_prefs + dietary_prefs))
                        saved_items.extend(dietary_prefs)
                        logger.info(f"User {user.id} dietary preferences updated: {db_user.dietary_preferences}")
                    
                    await session.commit()
                    
                    # Короткий ответ
                    if saved_items:
                        items_text = ', '.join(saved_items)
                        response_text = f"✅ Отлично, запомнил: **{items_text}**\n\nБуду учитывать при подборе рецептов!"
                    else:
                        response_text = "✅ Принято! Буду учитывать ваши предпочтения."
                    
                    await update.message.reply_text(response_text, parse_mode="Markdown", reply_markup=get_main_keyboard())
                else:
                    await update.message.reply_text(
                        "Сначала используйте команду /start для регистрации",
                        reply_markup=get_main_keyboard()
                    )
            return
            
        elif intent == "meal_plan":
            await update.message.reply_text(
                "📅 **Составление плана питания**\n\n"
                "Эта функция находится в разработке и скоро будет доступна!\n\n"
                "Пока вы можете:\n"
                "• Искать рецепты по ингредиентам\n"
                "• Получать советы по питанию\n"
                "• Вести дневник питания",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard()
            )
            return
            
        elif intent == "diary":
            await update.message.reply_text(
                "📝 Посмотрите ваш дневник питания с помощью кнопки 📝 Дневник",
                reply_markup=get_main_keyboard()
            )
            return
        
        elif intent == "statistics":
            # Перенаправляем на команду статистики
            await statistics_command(update, context)
            return
            
        elif intent == "recipe_search":
            # Поиск рецептов
            ingredients = entities.get('ingredients', [])
            if not ingredients:
                # Пытаемся извлечь ингредиенты из текста
                words = [w.strip() for w in message_text.replace(',', ' ').split() if len(w.strip()) > 2]
                ingredients = words[:5] if words else []
            
            if not ingredients:
                await update.message.reply_text(
                    "🥘 Укажите ингредиенты через запятую, например:\n"
                    "\"курица, рис, морковь\"",
                    reply_markup=get_main_keyboard()
                )
                return
            
            # Показываем индикатор во время поиска рецептов
            await update.message.chat.send_action(action="typing")
        
        else:  # small_talk или unknown
            await update.message.chat.send_action(action="typing")
            answer = await ai_service.answer_general_question(message_text)
            await update.message.reply_text(answer, parse_mode="Markdown", reply_markup=get_main_keyboard())
            return
        
        # 3. Если дошли сюда - ищем рецепты
        ingredients = entities.get('ingredients', [])
        if not ingredients:
            ingredients = [word.strip() for word in message_text.replace(',', ' ').split() if len(word.strip()) > 2][:5]
        
        # Получаем аллергии и предпочтения пользователя из БД
        user_allergies = []
        user_dietary_prefs = []
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()
            if db_user:
                user_allergies = db_user.allergies or []
                user_dietary_prefs = db_user.dietary_preferences or []
        
        # Запускаем анимацию "печатает..." для длительной операции
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(keep_typing(update, stop_typing))
        
        try:
            recipes = await ai_service.generate_recipes(
                ingredients,
                dietary_preferences=user_dietary_prefs if user_dietary_prefs else None,
                allergies=user_allergies if user_allergies else None
            )
        finally:
            stop_typing.set()
            await typing_task
        
        if not recipes:
            await update.message.reply_text(
                "😔 Не удалось найти рецепты с указанными ингредиентами.\n"
                "Попробуйте другие продукты!",
                reply_markup=get_main_keyboard()
            )
            return
        
        # Отправляем рецепты
        await update.message.reply_text(
            f"✨ Найдено {len(recipes)} рецептов! Сейчас отправлю их вам.",
            reply_markup=get_main_keyboard()
        )
        
        # Сохраняем рецепты в context для последующего добавления в дневник
        context.user_data['last_recipes'] = recipes
        logger.info(f"Saved {len(recipes)} recipes for user {update.effective_user.id}")
        
        for i, recipe in enumerate(recipes, 1):
            recipe_text = f"""**{i}. {recipe['name']}** 
{recipe.get('cuisine_type', 'Классика')} • {recipe.get('difficulty', 'Средне')} • {recipe.get('cooking_time', 30)} мин

{recipe.get('description', '')}

**Ингредиенты:**
{format_ingredients(recipe['ingredients'])}

**Приготовление:**
{recipe['instructions']}

**Пищевая ценность (на порцию):**
🔥 Калории: {recipe['nutrition']['calories']} ккал
🥩 Белки: {recipe['nutrition']['protein']} г
🧈 Жиры: {recipe['nutrition']['fats']} г
🍞 Углеводы: {recipe['nutrition']['carbs']} г
"""
            keyboard = [[InlineKeyboardButton("💾 Сохранить рецепт", callback_data=f"save_recipe_{i-1}")]]
            inline_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(recipe_text, reply_markup=inline_markup, parse_mode="Markdown")
        
        # После всех рецептов отправляем сообщение с основной клавиатурой
        await update.message.reply_text(
            "👆 Нажмите '💾 Сохранить рецепт' чтобы добавить в коллекцию.",
            reply_markup=get_main_keyboard()
        )
    
    except Exception as e:
        logger.error(f"Error in handle_recipe_search: {e}", exc_info=True)
        await update.message.reply_text(
            "😔 Произошла ошибка при обработке вашего запроса.\n"
            "Попробуйте еще раз или используйте кнопку ❓ Помощь",
            reply_markup=get_main_keyboard()
        )


def format_ingredients(ingredients: list) -> str:
    """Форматирование списка ингредиентов"""
    return "\n".join([f"• {ing['name']} - {ing['amount']}" for ing in ingredients])


async def calories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /calories"""
    user = update.effective_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user and db_user.target_calories:
            await update.message.reply_text(
                f"""
📊 **Ваши целевые показатели:**

🔥 Калории: {db_user.target_calories} ккал/день
🥩 Белки: {db_user.target_protein} г
🧈 Жиры: {db_user.target_fats} г
🍞 Углеводы: {db_user.target_carbs} г

Используйте /profile для обновления данных.
""",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard()
            )
        else:
            await update.message.reply_text(
                "Для расчёта нормы калорий, пожалуйста, заполните профиль:\n"
                "/profile",
                reply_markup=get_main_keyboard()
            )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /profile"""
    user = update.effective_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if not db_user:
            await update.message.reply_text(
                "Сначала используйте /start для регистрации",
                reply_markup=get_main_keyboard()
            )
            return
        
        # Формируем информацию о профиле
        profile_text = f"""
👤 **Ваш профиль**

**Основная информация:**
• Имя: {user.first_name}
• Username: @{user.username or 'не указан'}
• Подписка: {db_user.subscription_type.value}

**Персональные данные:**
• Возраст: {db_user.age or 'не указан'}
• Вес: {db_user.weight or 'не указан'} кг
• Рост: {db_user.height or 'не указан'} см
• Пол: {db_user.gender or 'не указан'}
• Активность: {db_user.activity_level or 'не указана'}
• Цель: {db_user.goal or 'не указана'}

**Ограничения питания:**
• Аллергии: {', '.join(db_user.allergies) if db_user.allergies else 'нет'}
• Диета: {', '.join(db_user.dietary_preferences) if db_user.dietary_preferences else 'нет'}

**Целевые показатели:**
• Калории: {db_user.target_calories or 'не установлены'} ккал/день
• Белки: {db_user.target_protein or '-'} г
• Жиры: {db_user.target_fats or '-'} г
• Углеводы: {db_user.target_carbs or '-'} г

📝 **Для изменения данных:**
• Аллергии/диета: просто напишите "У меня аллергия на..."
• Цели: /goals калории белки жиры углеводы
"""
        
        await update.message.reply_text(profile_text, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def diary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /diary - показывает дневник за сегодня"""
    await show_diary(update, context, period="today")


async def statistics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик команды статистика / итоги / как я сегодня? / дашборд
    Показывает дневной дашборд с оценкой Б/Ж/У
    """
    user = update.effective_user
    
    await update.message.chat.send_action(action="typing")
    
    # Получаем статистику за сегодня
    stats = await statistics_service.get_daily_stats(user.id)
    
    if not stats:
        await update.message.reply_text(
            "📊 **Статистика**\n\n"
            "Пока нет данных. Добавьте первую запись в дневник!\n\n"
            "💡 Напишите что вы съели, например:\n"
            "_\"Съел омлет из 2 яиц и кофе\"_",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Форматируем дашборд
    dashboard = statistics_service.format_daily_dashboard(stats)
    
    # Проверяем дефициты для рекомендаций
    deficit_info = statistics_service.get_deficit_info(stats)
    
    # Добавляем рекомендации если есть дефицит и место для перекуса
    recommendations = ""
    if deficit_info and deficit_info["has_room_for_snack"]:
        # Определяем главный дефицит
        deficits = deficit_info["deficits"]
        if "protein" in deficits:
            snacks = get_snack_recommendations(
                "protein",
                deficits["protein"]["deficit"],
                deficit_info["calories_remaining"],
                stats.get("user_allergies", []),
                count=3
            )
            if snacks:
                recommendations = "\n\n" + format_snack_recommendations(snacks, "белок")
        elif "carbs" in deficits:
            snacks = get_snack_recommendations(
                "carbs",
                deficits["carbs"]["deficit"],
                deficit_info["calories_remaining"],
                stats.get("user_allergies", []),
                count=3
            )
            if snacks:
                recommendations = "\n\n" + format_snack_recommendations(snacks, "углеводы")
    
    # Формируем inline кнопки
    keyboard = [
        [InlineKeyboardButton("📅 Статистика за неделю", callback_data="stats_week")],
        [InlineKeyboardButton("📝 Открыть дневник", callback_data="diary_today")]
    ]
    
    # Добавляем кнопку рекомендаций если есть дефицит
    if deficit_info:
        keyboard.insert(0, [InlineKeyboardButton("💡 Подобрать перекус", callback_data="suggest_snack")])
    
    inline_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        dashboard + recommendations,
        parse_mode="Markdown",
        reply_markup=inline_markup
    )
    
    # Отправляем основную клавиатуру
    await update.message.reply_text(
        "Используйте кнопки для навигации:",
        reply_markup=get_main_keyboard()
    )


async def show_weekly_stats(query):
    """Показать статистику за неделю"""
    user = query.from_user
    
    stats = await statistics_service.get_weekly_stats(user.id)
    
    if not stats or stats["days_tracked"] == 0:
        await query.message.edit_text(
            "📈 **Статистика за неделю**\n\n"
            "Пока недостаточно данных. Ведите дневник несколько дней!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Статистика за сегодня", callback_data="stats_today")]
            ])
        )
        return
    
    # Форматируем статистику
    text = f"""📈 **Статистика за 7 дней**

📅 Дней с записями: {stats["days_tracked"]}

**Средние показатели в день:**
🔥 Калории: {stats["avg_calories"]} / {stats["target_calories"]} ккал
🥩 Белки: {stats["avg_protein"]} г
🧈 Жиры: {stats["avg_fats"]} г
🍞 Углеводы: {stats["avg_carbs"]} г
"""
    
    keyboard = [
        [InlineKeyboardButton("📊 Статистика за сегодня", callback_data="stats_today")],
        [InlineKeyboardButton("📝 Открыть дневник", callback_data="diary_week")]
    ]
    
    await query.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def suggest_snack_callback(query):
    """Подобрать перекус на основе дефицитов"""
    user = query.from_user
    
    # Получаем текущую статистику
    stats = await statistics_service.get_daily_stats(user.id)
    
    if not stats:
        await query.answer("Нет данных для анализа", show_alert=True)
        return
    
    deficit_info = statistics_service.get_deficit_info(stats)
    
    if not deficit_info or not deficit_info["has_room_for_snack"]:
        await query.answer("Всё в норме! Дефицитов нет 🎉", show_alert=True)
        return
    
    # Определяем главный дефицит
    deficits = deficit_info["deficits"]
    recommendations = ""
    
    if "protein" in deficits:
        snacks = get_snack_recommendations(
            "protein",
            deficits["protein"]["deficit"],
            deficit_info["calories_remaining"],
            stats.get("user_allergies", []),
            count=3
        )
        if snacks:
            recommendations = format_snack_recommendations(snacks, "белок")
    elif "carbs" in deficits:
        snacks = get_snack_recommendations(
            "carbs",
            deficits["carbs"]["deficit"],
            deficit_info["calories_remaining"],
            stats.get("user_allergies", []),
            count=3
        )
        if snacks:
            recommendations = format_snack_recommendations(snacks, "углеводы")
    elif "fats" in deficits:
        snacks = get_snack_recommendations(
            "fats",
            deficits["fats"]["deficit"],
            deficit_info["calories_remaining"],
            stats.get("user_allergies", []),
            count=3
        )
        if snacks:
            recommendations = format_snack_recommendations(snacks, "жиры")
    
    if recommendations:
        text = f"""💡 **Рекомендации по перекусу**

Осталось калорий: {deficit_info["calories_remaining"]} ккал

{recommendations}
"""
        await query.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Назад к статистике", callback_data="stats_today")]
            ])
        )
    else:
        await query.answer("Не удалось подобрать перекус", show_alert=True)


async def stats_today_callback(query):
    """Показать статистику за сегодня (callback)"""
    user = query.from_user
    
    stats = await statistics_service.get_daily_stats(user.id)
    
    if not stats:
        await query.message.edit_text(
            "📊 Статистика пока недоступна. Добавьте записи в дневник!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Открыть дневник", callback_data="diary_today")]
            ])
        )
        return
    
    dashboard = statistics_service.format_daily_dashboard(stats)
    deficit_info = statistics_service.get_deficit_info(stats)
    
    keyboard = [
        [InlineKeyboardButton("📅 Статистика за неделю", callback_data="stats_week")],
        [InlineKeyboardButton("📝 Открыть дневник", callback_data="diary_today")]
    ]
    
    if deficit_info:
        keyboard.insert(0, [InlineKeyboardButton("💡 Подобрать перекус", callback_data="suggest_snack")])
    
    await query.message.edit_text(
        dashboard,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_diary(update: Update, context: ContextTypes.DEFAULT_TYPE, period: str = "today", query=None):
    """Показать дневник за указанный период"""
    user = update.effective_user if update else query.from_user
    
    # Определяем период
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    if period == "today":
        start_date = today
        end_date = today + timedelta(days=1)
        period_text = "за сегодня"
        date_header = today.strftime("%d.%m.%Y")
    elif period == "yesterday":
        start_date = today - timedelta(days=1)
        end_date = today
        period_text = "за вчера"
        date_header = start_date.strftime("%d.%m.%Y")
    elif period == "week":
        start_date = today - timedelta(days=7)
        end_date = today + timedelta(days=1)
        period_text = "за 7 дней"
        date_header = f"{start_date.strftime('%d.%m')} - {today.strftime('%d.%m.%Y')}"
    else:
        start_date = today
        end_date = today + timedelta(days=1)
        period_text = "за сегодня"
        date_header = today.strftime("%d.%m.%Y")
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DiaryEntry)
            .join(User)
            .where(User.telegram_id == user.id)
            .where(DiaryEntry.date >= start_date)
            .where(DiaryEntry.date < end_date)
            .order_by(DiaryEntry.date)
        )
        entries = result.scalars().all()
        
        # Создаём inline кнопки навигации
        keyboard = []
        if period != "today":
            keyboard.append([InlineKeyboardButton("📅 Сегодня", callback_data="diary_today")])
        if period != "yesterday":
            keyboard.append([InlineKeyboardButton("📅 Вчера", callback_data="diary_yesterday")])
        if period != "week":
            keyboard.append([InlineKeyboardButton("📅 За 7 дней", callback_data="diary_week")])
        
        # Кнопка очистки только для сегодня
        if period == "today" and entries:
            keyboard.append([InlineKeyboardButton("🗑 Очистить за сегодня", callback_data="diary_clear_today")])
        
        inline_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        if not entries:
            empty_text = f"📝 **Дневник {period_text}** ({date_header})\n\n" \
                        f"Записей нет.\n\n" \
                        f"💡 Чтобы добавить приём пищи, просто напишите что вы съели, например:\n" \
                        f"• 'Съел омлет из 2 яиц'\n" \
                        f"• 'На обед борщ и хлеб'"
            
            if query:
                await query.message.edit_text(empty_text, parse_mode="Markdown", reply_markup=inline_markup)
            else:
                await update.message.reply_text(empty_text, parse_mode="Markdown", reply_markup=inline_markup)
                await update.message.reply_text("Используйте кнопки:", reply_markup=get_main_keyboard())
            return
        
        # Подсчёт общих показателей
        total_calories = sum(e.calories or 0 for e in entries)
        total_protein = sum(e.protein or 0 for e in entries)
        total_fats = sum(e.fats or 0 for e in entries)
        total_carbs = sum(e.carbs or 0 for e in entries)
        
        # Формируем текст дневника
        if period == "week":
            # Группируем по дням для недельного отчёта
            entries_by_day = {}
            for e in entries:
                day_key = e.date.strftime("%d.%m")
                if day_key not in entries_by_day:
                    entries_by_day[day_key] = []
                entries_by_day[day_key].append(e)
            
            entries_text = ""
            for day, day_entries in entries_by_day.items():
                day_calories = sum(e.calories or 0 for e in day_entries)
                entries_text += f"📅 **{day}** — {day_calories} ккал ({len(day_entries)} записей)\n"
        else:
            entries_text = "\n\n".join([
                f"⏰ {e.date.strftime('%H:%M')}\n"
                f"🍽 {e.food_name}\n"
                f"🔥 {e.calories or 0} ккал | 🥩 {e.protein or 0}г | 🧈 {e.fats or 0}г | 🍞 {e.carbs or 0}г"
                for e in entries
            ])
        
        diary_text = f"""📝 **Дневник {period_text}** ({date_header})

{entries_text}

━━━━━━━━━━━━━━━━━
**Итого {period_text}:**
🔥 {total_calories:.0f} ккал
🥩 {total_protein:.0f}г белков
🧈 {total_fats:.0f}г жиров
🍞 {total_carbs:.0f}г углеводов
"""
        
        if query:
            await query.message.edit_text(diary_text, parse_mode="Markdown", reply_markup=inline_markup)
        else:
            await update.message.reply_text(diary_text, parse_mode="Markdown", reply_markup=inline_markup)
            await update.message.reply_text("Используйте кнопки:", reply_markup=get_main_keyboard())


async def clear_diary_today(query):
    """Очистить дневник за сегодня"""
    user = query.from_user
    
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    
    async with AsyncSessionLocal() as session:
        # Получаем пользователя
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if not db_user:
            await query.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        # Удаляем записи за сегодня
        result = await session.execute(
            select(DiaryEntry)
            .where(DiaryEntry.user_id == db_user.id)
            .where(DiaryEntry.date >= today)
            .where(DiaryEntry.date < tomorrow)
        )
        entries = result.scalars().all()
        
        count = len(entries)
        for entry in entries:
            await session.delete(entry)
        
        await session.commit()
        
        logger.info(f"User {user.id} cleared {count} diary entries for today")
        
        await query.answer(f"✅ Удалено {count} записей за сегодня!")
        
        # Показываем обновлённый дневник
        await query.message.edit_text(
            f"📝 **Дневник за сегодня** ({today.strftime('%d.%m.%Y')})\n\n"
            f"Записей нет.\n\n"
            f"💡 Чтобы добавить приём пищи, просто напишите что вы съели.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 Вчера", callback_data="diary_yesterday")],
                [InlineKeyboardButton("📅 За 7 дней", callback_data="diary_week")]
            ])
        )


async def my_recipes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать сохранённые рецепты пользователя"""
    user = update.effective_user
    
    async with AsyncSessionLocal() as session:
        # Получаем сохранённые рецепты пользователя
        result = await session.execute(
            select(Recipe)
            .join(User)
            .where(User.telegram_id == user.id)
            .order_by(Recipe.created_at.desc())
            .limit(10)
        )
        recipes = result.scalars().all()
        
        if not recipes:
            await update.message.reply_text(
                "📖 **Мои рецепты**\n\n"
                "У вас пока нет сохранённых рецептов.\n\n"
                "💡 Чтобы сохранить рецепт:\n"
                "1. Напишите что хотите приготовить\n"
                "2. Получите рецепты от бота\n"
                "3. Нажмите '➕ Сохранить' под понравившимся рецептом",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard()
            )
            return
        
        # Формируем список рецептов
        recipes_text = "📖 **Мои сохранённые рецепты:**\n\n"
        
        for i, recipe in enumerate(recipes, 1):
            recipes_text += f"{i}. **{recipe.name}**\n"
            recipes_text += f"   🔥 {recipe.calories} ккал | ⏱ {recipe.cooking_time or '?'} мин\n\n"
        
        recipes_text += "👆 Нажмите на номер рецепта чтобы посмотреть подробности"
        
        # Создаём кнопки для просмотра деталей
        keyboard = []
        row = []
        for i, recipe in enumerate(recipes):
            row.append(InlineKeyboardButton(f"{i+1}", callback_data=f"view_recipe_{recipe.id}"))
            if len(row) == 5:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        await update.message.reply_text(
            recipes_text, 
            parse_mode="Markdown", 
            reply_markup=reply_markup
        )
        
        # Отправляем основную клавиатуру отдельным сообщением
        await update.message.reply_text(
            "Используйте кнопки ниже для навигации:",
            reply_markup=get_main_keyboard()
        )


async def view_saved_recipe(query, recipe_id: int):
    """Показать детали сохранённого рецепта"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Recipe).where(Recipe.id == recipe_id)
        )
        recipe = result.scalar_one_or_none()
        
        if not recipe:
            await query.answer("❌ Рецепт не найден", show_alert=True)
            return
        
        # Форматируем ингредиенты
        ingredients_text = ""
        if recipe.ingredients:
            for ing in recipe.ingredients:
                ingredients_text += f"• {ing.get('name', '')} - {ing.get('amount', '')}\n"
        
        recipe_text = f"""📖 **{recipe.name}**

{recipe.description or ''}

**Ингредиенты:**
{ingredients_text}
**Приготовление:**
{recipe.instructions}

**Пищевая ценность (на порцию):**
🔥 Калории: {recipe.calories} ккал
🥩 Белки: {recipe.protein} г
🧈 Жиры: {recipe.fats} г
🍞 Углеводы: {recipe.carbs} г
⏱ Время: {recipe.cooking_time or '?'} мин
"""
        
        keyboard = [
            [InlineKeyboardButton("📝 Добавить в дневник", callback_data=f"add_saved_to_diary_{recipe.id}")],
            [InlineKeyboardButton("🗑 Удалить из коллекции", callback_data=f"delete_recipe_{recipe.id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.edit_text(recipe_text, reply_markup=reply_markup, parse_mode="Markdown")


async def delete_saved_recipe(query, recipe_id: int):
    """Удалить рецепт из коллекции"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Recipe)
            .join(User)
            .where(Recipe.id == recipe_id)
            .where(User.telegram_id == user.id)
        )
        recipe = result.scalar_one_or_none()
        
        if not recipe:
            await query.answer("❌ Рецепт не найден", show_alert=True)
            return
        
        await session.delete(recipe)
        await session.commit()
        
        await query.answer("✅ Рецепт удалён из коллекции!")
        await query.message.edit_text(
            "✅ Рецепт удалён из коллекции.\n\n"
            "Нажмите 📖 Мои рецепты чтобы посмотреть оставшиеся."
        )


async def add_saved_recipe_to_diary(query, recipe_id: int):
    """Добавить сохранённый рецепт в дневник питания"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        # Получаем рецепт
        result = await session.execute(
            select(Recipe).where(Recipe.id == recipe_id)
        )
        recipe = result.scalar_one_or_none()
        
        if not recipe:
            await query.answer("❌ Рецепт не найден", show_alert=True)
            return
        
        # Получаем пользователя
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if not db_user:
            await query.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        # Создаем запись в дневнике
        diary_entry = DiaryEntry(
            user_id=db_user.id,
            date=datetime.now(),
            meal_type="приём пищи",
            food_name=recipe.name,
            portion_size=1.0,
            calories=recipe.calories,
            protein=recipe.protein,
            fats=recipe.fats,
            carbs=recipe.carbs,
            data_source=DataSource.DATABASE,
            recipe_id=recipe.id,
            notes=f"Из сохранённых рецептов"
        )
        
        session.add(diary_entry)
        await session.commit()
        
        await query.answer("✅ Добавлено в дневник!")
        await query.message.reply_text(
            f"✅ **{recipe.name}** добавлен в дневник питания!\n\n"
            f"🔥 {recipe.calories} ккал\n\n"
            f"Посмотреть дневник: 📝 Дневник",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на inline кнопки"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    logger.info(f"Button callback: {data} from user {query.from_user.id}")
    
    # Базовые кнопки главного меню
    if data == "find_recipes":
        await query.message.reply_text(
            "🍳 **Поиск рецептов**\n\n"
            "Отправьте список ингредиентов через запятую.\n\n"
            "Например:\n"
            "• курица, рис, помидоры\n"
            "• картошка, лук, морковь\n"
            "• творог, яйца, мука",
            parse_mode="Markdown"
        )
    elif data == "calc_calories":
        await query.message.reply_text(
            "📊 **Калькулятор калорий**\n\n"
            "Напишите название продукта и количество.\n\n"
            "Например:\n"
            "• яблоко 150г\n"
            "• куриная грудка 200г\n"
            "• овсянка 50г"
        )
    elif data == "diary":
        # Показываем дневник за сегодня
        await show_diary(None, context, period="today", query=query)
    
    # Навигация по дневнику
    elif data == "diary_today":
        await show_diary(None, context, period="today", query=query)
    elif data == "diary_yesterday":
        await show_diary(None, context, period="yesterday", query=query)
    elif data == "diary_week":
        await show_diary(None, context, period="week", query=query)
    elif data == "diary_clear_today":
        await clear_diary_today(query)
    
    # Статистика
    elif data == "stats_week":
        await show_weekly_stats(query)
    elif data == "stats_today":
        await stats_today_callback(query)
    elif data == "suggest_snack":
        await suggest_snack_callback(query)
    
    elif data == "settings" or data == "show_settings":
        await show_settings(query)

    # Кнопки настроек
    elif data == "edit_profile":
        await edit_profile(query)
    elif data == "edit_diet":
        await edit_diet(query)
    elif data == "edit_goals":
        await edit_goals(query)
    elif data == "clear_diet":
        await clear_diet_restrictions(query)
    elif data == "toggle_reminders":
        await toggle_reminders_callback(query)
    
    # Кнопки премиум подписок (временно отключены)
    # elif data == "buy_weekly":
    #     await process_subscription(query, "weekly")
    # elif data == "buy_unlimited":
    #     await process_subscription(query, "unlimited")
    # elif data == "buy_personalization":
    #     await process_subscription(query, "personalization")
    
    # Навигация назад
    elif data == "back_to_menu":
        await show_main_menu(query)
    
    # Сохранение рецепта в коллекцию
    elif data.startswith("save_recipe_"):
        recipe_index = data.split("_")[2]
        await save_recipe_to_collection(query, recipe_index, context)
    
    # Просмотр сохранённого рецепта
    elif data.startswith("view_recipe_"):
        recipe_id = int(data.split("_")[2])
        await view_saved_recipe(query, recipe_id)
    
    # Удаление рецепта из коллекции
    elif data.startswith("delete_recipe_"):
        recipe_id = int(data.split("_")[2])
        await delete_saved_recipe(query, recipe_id)
    
    # Добавление сохранённого рецепта в дневник
    elif data.startswith("add_saved_to_diary_"):
        recipe_id = int(data.split("_")[4])
        await add_saved_recipe_to_diary(query, recipe_id)
    
    # === ЧЕЛЛЕНДЖИ ===
    elif data == "challenge_suggest":
        await suggest_challenge_callback(query)
    elif data.startswith("challenge_start_"):
        challenge_type = data.replace("challenge_start_", "")
        await start_challenge_callback(query, challenge_type)
    elif data == "challenge_cancel":
        await cancel_challenge_callback(query)
    elif data == "challenge_status":
        await show_challenge_status_callback(query)
    
    # === ВЕС ===
    elif data == "log_weight":
        await log_weight_callback(query, context)
    elif data == "skip_weigh":
        await query.message.edit_text("Хорошо, напомню в следующий раз! 👌")
    
    # === УТРЕННИЙ ЧЕК-ИН ===
    elif data == "plan_strict":
        await set_day_plan_callback(query, "strict")
    elif data == "plan_relaxed":
        await set_day_plan_callback(query, "relaxed")
    
    # === БЫСТРЫЕ ПЛАНЫ ===
    elif data == "quick_meal_plan":
        await show_meal_plan_options_callback(query)
    elif data.startswith("meal_plan_"):
        plan_type = data.replace("meal_plan_", "")
        await generate_meal_plan_callback(query, plan_type, context)
    
    # === ОТЧЁТЫ И НАСТРОЙКИ ===
    elif data == "edit_report_settings":
        await edit_report_settings_callback(query)
    elif data == "toggle_daily_report":
        await toggle_daily_report_callback(query)
    elif data == "toggle_weekly_report":
        await toggle_weekly_report_callback(query)
    elif data == "toggle_morning_checkin":
        await toggle_morning_checkin_callback(query)
    elif data == "toggle_weigh_reminder":
        await toggle_weigh_reminder_callback(query)
    
    # === ЛЕНИВЫЙ РЕЖИМ (фото) ===
    elif data == "diary_add_from_photo":
        await add_photo_estimate_to_diary(query, context)
    elif data == "refine_photo_estimate":
        await query.message.edit_text(
            "📝 Напиши уточнение — что было на тарелке и примерный размер порции.\n"
            "Например: «куриная грудка 200г с рисом 150г»"
        )


# Временно отключено - будет доработано позже
# async def show_premium_options(query):
#     """Показать опции премиум подписок"""
#     text = """
# 💎 **Премиум функции**
# 
# **🌟 Недельные планы** - 499₽
# • Генерация планов питания на неделю
# • Список покупок
# • Рецепты с точным КБЖУ
# 
# **🚀 Безлимит** - 999₽
# • Неограниченные рецепты
# • Расширенная история дневника
# • Экспорт данных
# 
# **👤 Персонализация** - 1499₽
# • Индивидуальный план питания
# • Рекомендации по БЖУ
# • Консультации по питанию
# • Учёт аллергий и предпочтений
# 
# Выберите подписку:
# """
#     
#     keyboard = [
#         [InlineKeyboardButton("🌟 Недельные планы - 499₽", callback_data="buy_weekly")],
#         [InlineKeyboardButton("🚀 Безлимит - 999₽", callback_data="buy_unlimited")],
#         [InlineKeyboardButton("👤 Персонализация - 1499₽", callback_data="buy_personalization")],
#         [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
#     ]
#     reply_markup = InlineKeyboardMarkup(keyboard)
#     
#     await query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def show_settings(query):
    """Показать настройки через callback"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if not db_user:
            await query.message.edit_text("Используйте /start для регистрации")
            return
        
        # Формируем текст с текущими настройками
        allergies_text = ', '.join(db_user.allergies) if db_user.allergies else "Нет"
        diet_text = ', '.join(db_user.dietary_preferences) if db_user.dietary_preferences else "Нет"
        
        goal_map = {
            "lose_weight": "Похудение",
            "maintain": "Поддержание веса",
            "gain_weight": "Набор массы"
        }
        goal_text = goal_map.get(db_user.goal, "Не указана")
        
        reminders_status = "✅ Вкл" if db_user.reminders_enabled else "❌ Выкл"
        
        text = f"""⚙️ Настройки

📋 Основные данные:
• Возраст: {db_user.age or '?'} лет
• Вес: {db_user.weight or '?'} кг
• Цель: {goal_text}

🚫 Ограничения:
• Аллергии: {allergies_text}
• Диета: {diet_text}

🔥 Калории: {db_user.target_calories or '?'} ккал/день
🔔 Напоминания: {reminders_status}

Выберите что хотите изменить:"""
    
    keyboard = [
        [InlineKeyboardButton("👤 Основные данные", callback_data="edit_profile")],
        [InlineKeyboardButton("🥗 Диета и аллергии", callback_data="edit_diet")],
        [InlineKeyboardButton("🎯 Просмотр целей", callback_data="edit_goals")],
        [InlineKeyboardButton(f"🔔 Напоминания: {reminders_status}", callback_data="toggle_reminders")],
        [InlineKeyboardButton("📊 Отчёты и уведомления", callback_data="edit_report_settings")],
        [InlineKeyboardButton("🎯 Челленджи", callback_data="challenge_suggest")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.message.edit_text(text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in show_settings: {e}")
        # Если edit_text не работает, отправим новое сообщение
        await query.message.reply_text(text, reply_markup=reply_markup)


async def show_settings_as_message(update: Update):
    """Показать настройки через обычное сообщение (из кнопки)"""
    user = update.effective_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if not db_user:
            await update.message.reply_text(
                "Используйте /start для регистрации",
                reply_markup=get_main_keyboard()
            )
            return
        
        # Формируем текст с текущими настройками
        allergies_text = ', '.join(db_user.allergies) if db_user.allergies else "Нет"
        diet_text = ', '.join(db_user.dietary_preferences) if db_user.dietary_preferences else "Нет"
        
        goal_map = {
            "lose_weight": "Похудение",
            "maintain": "Поддержание веса",
            "gain_weight": "Набор массы"
        }
        goal_text = goal_map.get(db_user.goal, "Не указана")
        
        text = f"""
⚙️ **Настройки**

**Основные данные:**
👤 Возраст: {db_user.age or '?'} лет
⚖️ Вес: {db_user.weight or '?'} кг
🎯 Цель: {goal_text}

**Ограничения:**
🚫 Аллергии: {allergies_text}
🥗 Диета: {diet_text}

**Целевые показатели:**
🔥 Калории: {db_user.target_calories or '?'} ккал/день
🥩 Белки: {db_user.target_protein or '?'} г
🧈 Жиры: {db_user.target_fats or '?'} г
🍞 Углеводы: {db_user.target_carbs or '?'} г

Выберите что хотите изменить:
"""
    
    keyboard = [
        [InlineKeyboardButton("👤 Основные данные", callback_data="edit_profile")],
        [InlineKeyboardButton("🥗 Диета и аллергии", callback_data="edit_diet")],
        [InlineKeyboardButton("🎯 Просмотр целей", callback_data="edit_goals")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


async def edit_profile(query):
    """Редактирование профиля"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        profile_info = f"""
👤 **Ваш профиль**

Имя: {user.first_name}
Username: @{user.username or 'не указан'}
Подписка: {db_user.subscription_type.value if db_user else 'FREE'}

Для изменения данных используйте команду /profile
"""
    
    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="settings")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(profile_info, reply_markup=reply_markup, parse_mode="Markdown")


async def clear_diet_restrictions(query):
    """Очистить все ограничения по диете и аллергиям"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.allergies = None
            db_user.dietary_preferences = None
            await session.commit()
            
            await query.answer("✅ Все ограничения очищены!")
            logger.info(f"User {user.id} cleared diet restrictions")
    
    # Возвращаемся в меню диеты
    await edit_diet(query)


async def edit_diet(query):
    """Настройки диеты и аллергий"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        current_allergies = db_user.allergies if db_user and db_user.allergies else []
        current_prefs = db_user.dietary_preferences if db_user and db_user.dietary_preferences else []
        
        text = """
🥗 **Диета и аллергии**

"""
        
        if current_allergies:
            text += f"**Ваши аллергии:**\n🚫 {', '.join(current_allergies)}\n\n"
        
        if current_prefs:
            text += f"**Диетические предпочтения:**\n✅ {', '.join(current_prefs)}\n\n"
        
        if not current_allergies and not current_prefs:
            text += "У вас пока не указаны ограничения.\n\n"
        
        text += """
📝 **Чтобы добавить информацию:**

Используйте основную клавиатуру внизу экрана, а затем просто напишите:
• "У меня аллергия на молоко"
• "Я вегетарианец"
• "Не ем глютен"

Я автоматически сохраню и буду учитывать при подборе рецептов!
"""
    
    keyboard = []
    if current_allergies or current_prefs:
        keyboard.append([InlineKeyboardButton("🗑 Очистить все ограничения", callback_data="clear_diet")])
    keyboard.append([InlineKeyboardButton("◀️ Назад в настройки", callback_data="show_settings")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def toggle_reminders_callback(query):
    """Переключить напоминания"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            # Переключаем
            new_state = not db_user.reminders_enabled
            db_user.reminders_enabled = new_state
            await session.commit()
            
            status = "включены" if new_state else "отключены"
            await query.answer(f"🔔 Напоминания {status}!")
            logger.info(f"User {user.id} toggled reminders: {new_state}")
            
            # Обновляем меню настроек
            await show_settings(query)
        else:
            await query.answer("❌ Пользователь не найден", show_alert=True)


async def edit_goals(query):
    """Настройки целей"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user and db_user.target_calories:
            goal_map = {
                "lose_weight": "Похудение",
                "maintain": "Поддержание веса",
                "gain_weight": "Набор массы"
            }
            goal_text = goal_map.get(db_user.goal, "Не указана")
            
            text = f"""
🎯 **Ваши целевые показатели**

**Текущая цель:** {goal_text}

**Суточная норма:**
🔥 Калории: {db_user.target_calories} ккал
🥩 Белки: {db_user.target_protein} г
🧈 Жиры: {db_user.target_fats} г
🍞 Углеводы: {db_user.target_carbs} г

**На основе:**
• Возраст: {db_user.age} лет
• Вес: {db_user.weight} кг
• Рост: {db_user.height} см
• Активность: {db_user.activity_level}

💡 _Для изменения целей измените основные данные через "Основные данные"_

Целевые показатели пересчитаются автоматически!
"""
        else:
            text = """
🎯 **Целевые показатели не установлены**

Заполните профиль для автоматического расчета:
• Возраст
• Вес, рост
• Цель (похудение/набор/поддержание)
• Уровень активности

Используйте кнопку "Основные данные" для заполнения.
"""
    
    keyboard = [[InlineKeyboardButton("◀️ Назад в настройки", callback_data="show_settings")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")


# Временно отключено - будет доработано позже
# async def process_subscription(query, subscription_type: str):
#     """Обработка покупки подписки"""
#     prices = {
#         "weekly": ("🌟 Недельные планы", 499),
#         "unlimited": ("🚀 Безлимит", 999),
#         "personalization": ("👤 Персонализация", 1499)
#     }
#     
#     name, price = prices[subscription_type]
#     
#     text = f"""
# 💳 **Оформление подписки**
# 
# {name} - {price}₽
# 
# **Что входит:**
# """
#     
#     if subscription_type == "weekly":
#         text += """
# • Генерация планов питания на неделю
# • Список покупок
# • Рецепты с точным КБЖУ
# • 7 дней доступа
# """
#     elif subscription_type == "unlimited":
#         text += """
# • Неограниченные рецепты
# • Расширенная история дневника (365 дней)
# • Экспорт данных в PDF/Excel
# • 30 дней доступа
# """
#     else:
#         text += """
# • Индивидуальный план питания
# • Рекомендации по БЖУ
# • Консультации по питанию
# • Учёт аллергий и предпочтений
# • 90 дней доступа
# """
#     
#     text += f"""
# 
# 💰 К оплате: {price}₽
# 
# Для оплаты используйте команду:
# `/pay {subscription_type}`
# 
# Принимаем: 💳 Карты, Telegram Stars
# """
#     
#     keyboard = [
#         [InlineKeyboardButton("💳 Оплатить", callback_data=f"pay_{subscription_type}")],
#         [InlineKeyboardButton("◀️ Назад", callback_data="premium")]
#     ]
#     reply_markup = InlineKeyboardMarkup(keyboard)
#     
#     await query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")


async def show_main_menu(query):
    """Показать главное меню"""
    user = query.from_user
    
    text = f"""
👋 Привет, {user.first_name}!

Чем могу помочь сегодня?
"""
    
    keyboard = [
        [InlineKeyboardButton("🍳 Найти рецепты", callback_data="find_recipes")],
        [InlineKeyboardButton("📊 Калькулятор калорий", callback_data="calc_calories")],
        [InlineKeyboardButton("📝 Мой дневник", callback_data="diary")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(text, reply_markup=reply_markup)


async def save_recipe_to_collection(query, recipe_index: str, context: ContextTypes.DEFAULT_TYPE):
    """Сохранить рецепт в коллекцию пользователя"""
    user = query.from_user
    
    try:
        # Получаем сохраненные рецепты из контекста
        recipes = context.user_data.get('last_recipes', [])
        index = int(recipe_index)
        
        logger.info(f"Saving recipe to collection: user={user.id}, index={index}, recipes_count={len(recipes)}")
        
        if not recipes or index >= len(recipes):
            logger.warning(f"Recipe not found: index={index}, recipes={recipes}")
            await query.answer("❌ Рецепт не найден. Попробуйте найти рецепт снова.", show_alert=True)
            return
        
        recipe = recipes[index]
        
        # Сохраняем в БД
        async with AsyncSessionLocal() as session:
            # Получаем пользователя
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()
            
            if not db_user:
                await query.answer("❌ Пользователь не найден", show_alert=True)
                return
            
            # Проверяем, не сохранён ли уже этот рецепт
            existing = await session.execute(
                select(Recipe).where(
                    Recipe.user_id == db_user.id,
                    Recipe.name == recipe['name']
                )
            )
            if existing.scalar_one_or_none():
                await query.answer("ℹ️ Этот рецепт уже сохранён!", show_alert=True)
                return
            
            # Создаем запись рецепта
            saved_recipe = Recipe(
                user_id=db_user.id,
                name=recipe['name'],
                description=recipe.get('description', ''),
                cuisine_type=recipe.get('cuisine_type', ''),
                difficulty=recipe.get('difficulty', ''),
                ingredients=recipe['ingredients'],
                instructions=recipe['instructions'],
                cooking_time=recipe.get('cooking_time', 30),
                servings=recipe.get('servings', 2),
                calories=recipe['nutrition']['calories'],
                protein=recipe['nutrition']['protein'],
                fats=recipe['nutrition']['fats'],
                carbs=recipe['nutrition']['carbs'],
                source='ai_generated'
            )
            
            session.add(saved_recipe)
            await session.commit()
            
            logger.info(f"User {user.id} saved recipe '{recipe['name']}' to collection")
            
            await query.answer("✅ Рецепт сохранён!")
            
            await query.message.reply_text(
                f"✅ **Рецепт сохранён в коллекцию!**\n\n"
                f"📖 {recipe['name']}\n"
                f"🔥 {recipe['nutrition']['calories']} ккал\n\n"
                f"Посмотреть все рецепты: 📖 Мои рецепты",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard()
            )
    
    except Exception as e:
        logger.error(f"Error saving recipe to collection: {e}", exc_info=True)
        await query.answer("❌ Ошибка при сохранении рецепта", show_alert=True)


# =====================
# ЧЕЛЛЕНДЖИ
# =====================

async def suggest_challenge_callback(query):
    """Предложить челлендж пользователю"""
    user = query.from_user
    
    # Проверяем нет ли активного челленджа
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user and db_user.active_challenge:
            # Есть активный челлендж - показываем статус
            challenge = db_user.active_challenge
            text = f"""🎯 **У тебя уже есть активный челлендж:**

**{challenge.get('name', 'Челлендж')}**

Прогресс: {challenge.get('days_completed', 0)} дней выполнено
Провалов: {challenge.get('days_failed', 0)}

Хочешь продолжить или отменить?"""
            
            keyboard = [
                [InlineKeyboardButton("✅ Продолжаю", callback_data="challenge_status")],
                [InlineKeyboardButton("❌ Отменить челлендж", callback_data="challenge_cancel")],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
            ]
            await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            return
    
    # Показываем доступные челленджи
    text = """🎯 **Мини-челленджи**

Выбери челлендж, который хочешь попробовать:

Я буду просто напоминать и отмечать прогресс. Можно отказаться в любой момент."""
    
    keyboard = []
    for challenge_type, info in CHALLENGE_TYPES.items():
        keyboard.append([
            InlineKeyboardButton(
                f"{info['name']} ({info['duration']} дн.)", 
                callback_data=f"challenge_start_{challenge_type}"
            )
        ])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")])
    
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def start_challenge_callback(query, challenge_type: str):
    """Начать челлендж"""
    user = query.from_user
    
    challenge = await challenge_service.start_challenge(user.id, challenge_type)
    
    if challenge:
        info = CHALLENGE_TYPES.get(challenge_type, {})
        text = f"""✅ **Челлендж начат!**

🎯 **{info.get('name', challenge_type)}**
{info.get('description', '')}

📆 Длительность: {info.get('duration', 7)} дней
📊 Начало: сегодня

Я буду проверять каждый вечер и сообщать результаты. Удачи! 💪"""
        
        keyboard = [[InlineKeyboardButton("📊 Статистика", callback_data="stats_today")]]
    else:
        text = "❌ Не удалось начать челлендж. Возможно, у тебя уже есть активный."
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="challenge_suggest")]]
    
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def cancel_challenge_callback(query):
    """Отменить челлендж"""
    user = query.from_user
    
    success = await challenge_service.cancel_challenge(user.id)
    
    if success:
        text = "✅ Челлендж отменён. Это нормально — не все эксперименты удаются. Может, попробуем другой?"
    else:
        text = "❌ Не удалось отменить челлендж."
    
    keyboard = [[
        InlineKeyboardButton("🎯 Новый челлендж", callback_data="challenge_suggest"),
        InlineKeyboardButton("◀️ Меню", callback_data="back_to_menu")
    ]]
    
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_challenge_status_callback(query):
    """Показать статус челленджа"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user and db_user.active_challenge:
            challenge = db_user.active_challenge
            info = CHALLENGE_TYPES.get(challenge.get('type', ''), {})
            
            completed = challenge.get('days_completed', 0)
            failed = challenge.get('days_failed', 0)
            total = info.get('duration', 7)
            remaining = total - completed - failed
            
            progress_bar = "✅" * completed + "❌" * failed + "⬜" * remaining
            
            text = f"""🎯 **{challenge.get('name', 'Челлендж')}**

{info.get('description', '')}

**Прогресс:**
{progress_bar}

✅ Выполнено: {completed} дней
❌ Пропущено: {failed} дней
⬜ Осталось: {remaining} дней

Проверяю результаты каждый вечер."""
        else:
            text = "У тебя нет активного челленджа. Хочешь начать?"
    
    keyboard = [[
        InlineKeyboardButton("❌ Отменить", callback_data="challenge_cancel"),
        InlineKeyboardButton("◀️ Меню", callback_data="back_to_menu")
    ]]
    
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


# =====================
# ЗАПИСЬ ВЕСА
# =====================

async def log_weight_callback(query, context: ContextTypes.DEFAULT_TYPE):
    """Запросить вес для записи"""
    await query.message.edit_text(
        "⚖️ **Запись веса**\n\n"
        "Напиши свой вес (например: 75.5)",
        parse_mode="Markdown"
    )
    context.user_data['awaiting_weight'] = True


async def handle_weight_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Обработать ввод веса"""
    if not context.user_data.get('awaiting_weight'):
        return False
    
    user = update.effective_user
    text = update.message.text.strip().replace(',', '.')
    
    try:
        weight = float(text)
        if weight < 30 or weight > 300:
            await update.message.reply_text("❌ Вес должен быть от 30 до 300 кг. Попробуй ещё раз.")
            return True
        
        # Сохраняем вес
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()
            
            if db_user:
                # Создаём запись
                weight_log = WeightLog(
                    user_id=db_user.id,
                    weight=weight,
                    date=date.today()
                )
                session.add(weight_log)
                
                # Обновляем текущий вес
                old_weight = db_user.weight
                db_user.weight = weight
                await session.commit()
                
                # Формируем ответ
                if old_weight:
                    diff = weight - old_weight
                    sign = "+" if diff > 0 else ""
                    diff_text = f"\nИзменение: {sign}{diff:.1f} кг" if abs(diff) >= 0.1 else ""
                else:
                    diff_text = ""
                
                await update.message.reply_text(
                    f"✅ **Вес записан: {weight} кг**{diff_text}\n\n"
                    "Отлично, что следишь за прогрессом! 📊",
                    parse_mode="Markdown",
                    reply_markup=get_main_keyboard()
                )
        
        context.user_data['awaiting_weight'] = False
        return True
        
    except ValueError:
        await update.message.reply_text("❌ Не понял. Напиши число, например: 75.5")
        return True


# =====================
# УТРЕННИЙ ЧЕК-ИН
# =====================

async def set_day_plan_callback(query, plan_type: str):
    """Установить план на день"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.today_plan_mode = plan_type
            await session.commit()
    
    if plan_type == "strict":
        text = """🎯 **Режим: Держим цель!**

Отлично! Сегодня стараемся не выходить за лимиты.
Я буду мягко напоминать, если увижу риск перебора.

Удачного дня! 💪"""
    else:
        text = """😌 **Режим: Свободный день**

Хорошо! Сегодня отдыхаем от строгих подсчётов.
Напоминания будут минимальными.

Наслаждайся днём! 🌟"""
    
    keyboard = [[InlineKeyboardButton("📊 Статистика", callback_data="stats_today")]]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


# =====================
# БЫСТРЫЕ ПЛАНЫ ПИТАНИЯ
# =====================

async def show_meal_plan_options_callback(query):
    """Показать опции планов питания"""
    text = """🍽 **Быстрый план на день**

Какой формат тебе подходит?"""
    
    keyboard = [
        [InlineKeyboardButton("🥗 Без готовки", callback_data="meal_plan_no_cooking")],
        [InlineKeyboardButton("🍳 С минимальной готовкой", callback_data="meal_plan_simple")],
        [InlineKeyboardButton("🍲 Полноценные блюда", callback_data="meal_plan_full")],
        [InlineKeyboardButton("⚡️ Быстрый фаст-вариант", callback_data="meal_plan_fast")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")]
    ]
    
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def generate_meal_plan_callback(query, plan_type: str, context: ContextTypes.DEFAULT_TYPE):
    """Генерировать план питания"""
    user = query.from_user
    
    await query.message.edit_text("⏳ Составляю план...")
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if not db_user or not db_user.target_calories:
            await query.message.edit_text(
                "❌ Сначала заполни профиль для расчёта калорий.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("👤 Заполнить", callback_data="edit_profile")
                ]])
            )
            return
        
        target = db_user.target_calories
        allergies = db_user.allergies or []
        prefs = db_user.dietary_preferences or []
    
    # Генерируем план через AI
    plan_descriptions = {
        "no_cooking": "без готовки, только готовые продукты",
        "simple": "с минимальной готовкой (варка, разогрев)",
        "full": "полноценные домашние блюда",
        "fast": "быстрый вариант с фастфудом и полуфабрикатами"
    }
    
    plan_desc = plan_descriptions.get(plan_type, "сбалансированный")
    
    prompt = f"""Составь план питания на день ({plan_desc}).

Целевые калории: {target} ккал
Ограничения: {', '.join(allergies) if allergies else 'нет'}
Предпочтения: {', '.join(prefs) if prefs else 'нет'}

Формат:
🌅 Завтрак: [блюдо] (~X ккал)
🌞 Обед: [блюдо] (~X ккал)
🌙 Ужин: [блюдо] (~X ккал)
🍎 Перекусы: [варианты] (~X ккал)

Итого: ~{target} ккал

Кратко, практично, вкусно."""

    try:
        response = await ai_service.generate_response(prompt, user.id)
        
        text = f"""🍽 **План питания на сегодня**
_(формат: {plan_desc})_

{response}

---
💡 Это примерный план. Записывай что ешь в дневник!"""
        
        keyboard = [
            [InlineKeyboardButton("📝 Открыть дневник", callback_data="diary_today")],
            [InlineKeyboardButton("🔄 Другой план", callback_data="quick_meal_plan")],
            [InlineKeyboardButton("◀️ Меню", callback_data="back_to_menu")]
        ]
        
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error generating meal plan: {e}")
        await query.message.edit_text(
            "❌ Не удалось составить план. Попробуй позже.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")
            ]])
        )


# =====================
# НАСТРОЙКИ ОТЧЁТОВ
# =====================

async def edit_report_settings_callback(query):
    """Настройки отчётов"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if not db_user:
            await query.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        daily = "✅" if db_user.daily_report_enabled else "❌"
        weekly = "✅" if db_user.weekly_report_enabled else "❌"
        morning = "✅" if db_user.morning_checkin_enabled else "❌"
        weigh = "✅" if db_user.weigh_reminder_enabled else "❌"
        
        text = f"""📊 **Настройки отчётов**

{daily} Вечерний отчёт (21:00)
{weekly} Еженедельный обзор (воскресенье)
{morning} Утренний чек-ин (08:00)
{weigh} Напоминания взвеситься

Нажми, чтобы включить/выключить:"""
    
    keyboard = [
        [InlineKeyboardButton(f"📊 Вечерний отчёт {daily}", callback_data="toggle_daily_report")],
        [InlineKeyboardButton(f"📈 Еженедельный обзор {weekly}", callback_data="toggle_weekly_report")],
        [InlineKeyboardButton(f"☀️ Утренний чек-ин {morning}", callback_data="toggle_morning_checkin")],
        [InlineKeyboardButton(f"⚖️ Напоминания о весе {weigh}", callback_data="toggle_weigh_reminder")],
        [InlineKeyboardButton("◀️ Назад в настройки", callback_data="show_settings")]
    ]
    
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def toggle_daily_report_callback(query):
    """Переключить вечерний отчёт"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.daily_report_enabled = not db_user.daily_report_enabled
            await session.commit()
            status = "включён" if db_user.daily_report_enabled else "отключён"
            await query.answer(f"📊 Вечерний отчёт {status}")
    
    await edit_report_settings_callback(query)


async def toggle_weekly_report_callback(query):
    """Переключить еженедельный отчёт"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.weekly_report_enabled = not db_user.weekly_report_enabled
            await session.commit()
            status = "включён" if db_user.weekly_report_enabled else "отключён"
            await query.answer(f"📈 Еженедельный обзор {status}")
    
    await edit_report_settings_callback(query)


async def toggle_morning_checkin_callback(query):
    """Переключить утренний чек-ин"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.morning_checkin_enabled = not db_user.morning_checkin_enabled
            await session.commit()
            status = "включён" if db_user.morning_checkin_enabled else "отключён"
            await query.answer(f"☀️ Утренний чек-ин {status}")
    
    await edit_report_settings_callback(query)


async def toggle_weigh_reminder_callback(query):
    """Переключить напоминания о весе"""
    user = query.from_user
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user.id)
        )
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.weigh_reminder_enabled = not db_user.weigh_reminder_enabled
            await session.commit()
            status = "включены" if db_user.weigh_reminder_enabled else "отключены"
            await query.answer(f"⚖️ Напоминания о весе {status}")
    
    await edit_report_settings_callback(query)


# =====================
# ЛЕНИВЫЙ РЕЖИМ (фото)
# =====================

async def add_photo_estimate_to_diary(query, context: ContextTypes.DEFAULT_TYPE):
    """Записать оценку по фото в дневник"""
    user = query.from_user
    estimate = context.user_data.get('last_photo_estimate')
    
    if not estimate:
        await query.message.edit_text("❌ Оценка устарела. Отправь фото ещё раз.")
        return
    
    description = estimate.get('description', 'Блюдо по фото')
    
    try:
        nutrition = await ai_service.calculate_nutrition_from_text(description)
        
        if not nutrition:
            await query.message.edit_text("❌ Не удалось рассчитать БЖУ. Попробуй описать блюдо текстом.")
            return
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == user.id)
            )
            db_user = result.scalar_one_or_none()
            
            if db_user:
                entry = DiaryEntry(
                    user_id=db_user.id,
                    food_name=description,
                    calories=nutrition.get("calories", 0),
                    protein=nutrition.get("protein", 0),
                    fats=nutrition.get("fats", 0),
                    carbs=nutrition.get("carbs", 0),
                    date=datetime.now(),
                    data_source=DataSource.AI_ESTIMATE
                )
                session.add(entry)
                await session.commit()
                
                await query.message.edit_text(
                    f"✅ Записано в дневник!\n\n"
                    f"🍽 {description}\n"
                    f"🔥 {nutrition.get('calories', 0)} ккал | "
                    f"Б: {nutrition.get('protein', 0)}г | "
                    f"Ж: {nutrition.get('fats', 0)}г | "
                    f"У: {nutrition.get('carbs', 0)}г\n\n"
                    f"⚠️ Это грубая оценка по фото."
                )
    except Exception as e:
        logger.error(f"Error adding photo estimate to diary: {e}")
        await query.message.edit_text("❌ Ошибка при записи. Попробуй ещё раз.")


async def handle_photo_lazy_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка фото в ленивом режиме"""
    user = update.effective_user
    
    # Получаем фото
    photo = update.message.photo[-1]  # Берём самое большое
    file = await context.bot.get_file(photo.file_id)
    
    await update.message.reply_text("🔍 Анализирую фото...")
    
    # Для MVP пока используем caption или просим описание
    caption = update.message.caption or ""
    
    if caption:
        # Есть описание - используем его
        prompt = f"""Оцени примерно калории и БЖУ блюда по описанию: "{caption}"

Это грубая оценка по фото, дай диапазон.
Формат ответа:
🍽 [название блюда]
🔥 ~XXX-XXX ккал
🥩 Б: ~XXг | 🧈 Ж: ~XXг | 🍞 У: ~XXг

⚠️ Это грубая оценка. Для точного расчёта укажи вес порции."""
    else:
        prompt = """Пользователь отправил фото еды без описания.
Попроси его написать что на тарелке или хотя бы примерное название блюда."""
        
        await update.message.reply_text(
            "📸 Вижу фото!\n\n"
            "Чтобы я мог оценить калории, напиши что это за блюдо.\n"
            "Например: «борщ с мясом» или «паста карбонара»",
            reply_markup=get_main_keyboard()
        )
        
        context.user_data['photo_pending'] = True
        return
    
    try:
        response = await ai_service.generate_response(prompt, user.id)
        
        keyboard = [
            [InlineKeyboardButton("📝 Записать в дневник", callback_data="diary_add_from_photo")],
            [InlineKeyboardButton("🔧 Уточнить", callback_data="refine_photo_estimate")]
        ]
        
        await update.message.reply_text(
            f"📊 **Оценка по фото:**\n\n{response}\n\n"
            "_Если хочешь — можем уточнить._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        # Сохраняем для возможной записи
        context.user_data['last_photo_estimate'] = {
            'description': caption,
            'response': response
        }
        
    except Exception as e:
        logger.error(f"Error processing photo: {e}")
        await update.message.reply_text(
            "❌ Не удалось обработать фото. Попробуй написать текстом.",
            reply_markup=get_main_keyboard()
        )
