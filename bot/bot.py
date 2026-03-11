"""
Главный файл Telegram бота
"""
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters
)
from config import settings
from bot.handlers import (
    start_command,
    help_command,
    recipes_command,
    calories_command,
    diary_command,
    profile_command,
    statistics_command,
    handle_recipe_search,
    button_callback,
    handle_photo_lazy_mode
)
from services.scheduler_service import scheduler_service
import logging

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def setup_bot() -> Application:
    """Настройка и инициализация бота"""
    
    # Создаём приложение
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    
    # Импортируем обработчики регистрации
    from bot.conversation import (
        AGE, GENDER, WEIGHT, HEIGHT, GOAL, ACTIVITY, ALLERGIES, DIET, CONFIRM,
        start_registration,
        registration_age,
        registration_gender,
        registration_weight,
        registration_height,
        registration_goal,
        registration_activity,
        registration_allergies,
        registration_diet,
        registration_confirm,
        registration_cancel
    )
    
    # Импортируем обработчики редактирования профиля
    from bot.edit_profile import (
        EDIT_MENU, EDIT_AGE, EDIT_GENDER, EDIT_WEIGHT, EDIT_HEIGHT, EDIT_GOAL, EDIT_ACTIVITY,
        show_edit_menu,
        start_edit_age, save_age,
        start_edit_gender, save_gender,
        start_edit_weight, save_weight,
        start_edit_height, save_height,
        start_edit_goal, save_goal,
        start_edit_activity, save_activity,
        finish_editing,
        cancel_editing
    )
    
    # ConversationHandler для регистрации (должен быть первым!)
    registration_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_age)],
            GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_gender)],
            WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_weight)],
            HEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_height)],
            GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_goal)],
            ACTIVITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_activity)],
            ALLERGIES: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_allergies)],
            DIET: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_diet)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, registration_confirm)],
        },
        fallbacks=[CommandHandler("cancel", registration_cancel)],
        name="registration",
        persistent=False
    )
    
    # ConversationHandler для редактирования профиля
    edit_profile_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(show_edit_menu, pattern="^edit_profile$")],
        states={
            EDIT_MENU: [
                CallbackQueryHandler(start_edit_age, pattern="^edit_age$"),
                CallbackQueryHandler(start_edit_gender, pattern="^edit_gender$"),
                CallbackQueryHandler(start_edit_weight, pattern="^edit_weight$"),
                CallbackQueryHandler(start_edit_height, pattern="^edit_height$"),
                CallbackQueryHandler(start_edit_goal, pattern="^edit_goal$"),
                CallbackQueryHandler(start_edit_activity, pattern="^edit_activity$"),
                CallbackQueryHandler(finish_editing, pattern="^back_to_settings$"),
            ],
            EDIT_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_age)],
            EDIT_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_gender)],
            EDIT_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_weight)],
            EDIT_HEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_height)],
            EDIT_GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_goal)],
            EDIT_ACTIVITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_activity)],
        },
        fallbacks=[CommandHandler("cancel", cancel_editing)],
        name="edit_profile",
        persistent=False
    )
    
    # Регистрируем ConversationHandlers
    application.add_handler(registration_handler)
    application.add_handler(edit_profile_handler)
    
    # Регистрируем остальные обработчики команд
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("recipes", recipes_command))
    application.add_handler(CommandHandler("calories", calories_command))
    application.add_handler(CommandHandler("diary", diary_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("stats", statistics_command))
    application.add_handler(CommandHandler("statistics", statistics_command))
    
    # Обработчик callback кнопок
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Обработчик текстовых сообщений (для поиска рецептов)
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_recipe_search
        )
    )
    
    # Обработчик фото (ленивый режим учёта еды)
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo_lazy_mode
        )
    )
    
    logger.info("Bot initialized successfully")
    return application


async def start_bot():
    """Запуск бота"""
    application = setup_bot()
    
    # Инициализация базы данных
    from database import init_db
    await init_db()
    logger.info("Database initialized")
    
    # Инициализация приложения
    await application.initialize()
    await application.start()
    
    # Настраиваем scheduler с ботом
    scheduler_service.set_bot(application.bot)
    await scheduler_service.start()
    logger.info("Reminder scheduler started")
    
    # Запуск polling
    logger.info("Starting bot polling...")
    await application.updater.start_polling(drop_pending_updates=True)
    
    # Держим бота запущенным
    import signal
    import asyncio
    
    stop = asyncio.Event()
    
    def signal_handler(signum, frame):
        logger.info("Received stop signal, shutting down...")
        stop.set()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    await stop.wait()
    
    # Остановка
    await scheduler_service.stop()
    logger.info("Reminder scheduler stopped")
    await application.updater.stop()
    await application.stop()
    await application.shutdown()


if __name__ == "__main__":
    import asyncio
    asyncio.run(start_bot())

