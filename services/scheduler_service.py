"""
Сервис планировщика для автоматических напоминаний и отчётов
"""
import asyncio
from datetime import datetime, time, timedelta, date
from typing import List, Optional
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from sqlalchemy import select
from database.models import User
from database.database import AsyncSessionLocal
from services.reminder_service import reminder_service
from services.report_service import report_service
from services.challenge_service import challenge_service
from config import settings
import logging
import pytz

logger = logging.getLogger(__name__)


# Время проверки напоминаний (по московскому времени)
CHECK_TIMES = [
    time(10, 0),   # Утро
    time(14, 0),   # Обед
    time(19, 0),   # Вечер
]

# Дефолтные времена для отчётов
DEFAULT_DAILY_REPORT_HOUR = 21  # 21:00
DEFAULT_WEEKLY_REPORT_HOUR = 12  # 12:00 в воскресенье
MORNING_CHECKIN_HOUR = 8  # 08:00


class SchedulerService:
    """Планировщик для автоматических напоминаний"""
    
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._bot: Optional[Bot] = None
    
    def set_bot(self, bot: Bot):
        """Установить экземпляр бота для отправки сообщений"""
        self._bot = bot
    
    async def start(self):
        """Запуск планировщика"""
        if self._running:
            logger.warning("Scheduler is already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_scheduler())
        logger.info("Scheduler started")
    
    async def stop(self):
        """Остановка планировщика"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler stopped")
    
    async def _run_scheduler(self):
        """Основной цикл планировщика"""
        last_reset_date = None
        
        while self._running:
            try:
                await asyncio.sleep(300)
                
                if not self._bot:
                    logger.warning("Bot not set, skipping scheduler tick")
                    continue
                
                now = datetime.now(pytz.timezone("Europe/Moscow"))
                current_time = now.time()
                current_hour = current_time.hour
                today = now.date()
                
                # Сброс дневных состояний в 5:00
                if current_hour == 5 and last_reset_date != today:
                    last_reset_date = today
                    await self._reset_daily_state()
                
                # Проверяем попадание в окно времени (±15 минут от целевого)
                should_check_reminders = False
                for check_time in CHECK_TIMES:
                    check_dt = datetime.combine(now.date(), check_time)
                    delta = abs((datetime.combine(now.date(), current_time) - check_dt).total_seconds())
                    if delta <= 900:  # 15 минут = 900 секунд
                        should_check_reminders = True
                        break
                
                if should_check_reminders:
                    await self._check_and_send_reminders()
                
                # Проверяем вечерние отчёты (20:00 - 22:00)
                if 20 <= current_hour <= 22:
                    await self._check_and_send_daily_reports()
                
                # Проверяем еженедельные отчёты (воскресенье, 11:00-13:00)
                if now.weekday() == 6 and 11 <= current_hour <= 13:
                    await self._check_and_send_weekly_reports()
                
                # Проверяем утренний чек-ин (8:00 - 9:00)
                if 8 <= current_hour <= 9:
                    await self._check_and_send_morning_checkins()
                
                # Проверяем напоминания взвеситься (среда и воскресенье, 10:00)
                if now.weekday() in [2, 6] and 9 <= current_hour <= 11:
                    await self._check_and_send_weigh_reminders()
                
                # Проверяем прогресс челленджей (вечером)
                if current_hour == 22:
                    await self._check_challenge_progress()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)
    
    async def _check_and_send_reminders(self):
        """Проверка и отправка напоминаний пользователям"""
        logger.debug("Checking for reminders to send...")
        
        try:
            async with AsyncSessionLocal() as session:
                # Получаем активных пользователей с включенными напоминаниями
                result = await session.execute(
                    select(User)
                    .where(User.is_active == True)
                    .where(User.reminders_enabled == True)
                    .where(User.target_calories.isnot(None))  # Только с заполненным профилем
                )
                users = result.scalars().all()
                
                logger.debug(f"Found {len(users)} users with reminders enabled")
                
                for user in users:
                    try:
                        # Генерируем напоминание
                        reminder = await reminder_service.generate_smart_reminder(user.telegram_id)
                        
                        if reminder:
                            # Отправляем напоминание
                            await self._send_reminder(user.telegram_id, reminder)
                            
                            # Записываем факт отправки
                            await reminder_service.record_reminder_sent(
                                user.telegram_id, 
                                reminder["type"]
                            )
                            
                            logger.info(f"Sent reminder to user {user.telegram_id}: {reminder['type']}")
                            
                            # Небольшая задержка между отправками
                            await asyncio.sleep(0.5)
                            
                    except Exception as e:
                        logger.error(f"Error processing reminder for user {user.telegram_id}: {e}")
                        
        except Exception as e:
            logger.error(f"Error in _check_and_send_reminders: {e}", exc_info=True)
    
    async def _send_reminder(self, telegram_id: int, reminder: dict):
        """Отправка напоминания пользователю"""
        if not self._bot:
            logger.error("Bot not set, cannot send reminder")
            return
        
        try:
            message = reminder["message"]
            
            keyboard = []
            if reminder.get("is_question"):
                keyboard.append([
                    InlineKeyboardButton("📊 Посмотреть статистику", callback_data="stats_today"),
                    InlineKeyboardButton("📝 Дневник", callback_data="diary_today")
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton("📊 Статистика", callback_data="stats_today")
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            await self._bot.send_message(
                chat_id=telegram_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            
        except TelegramError as e:
            if "blocked" in str(e).lower() or "deactivated" in str(e).lower():
                # Пользователь заблокировал бота - отключаем напоминания
                logger.warning(f"User {telegram_id} blocked the bot, disabling reminders")
                await reminder_service.toggle_reminders(telegram_id, False)
            else:
                logger.error(f"Telegram error sending reminder to {telegram_id}: {e}")
        except Exception as e:
            logger.error(f"Error sending reminder to {telegram_id}: {e}")
    
    async def send_manual_check(self, telegram_id: int) -> Optional[dict]:
        """
        Проверить и отправить напоминание вручную (для тестирования).
        Возвращает напоминание если было отправлено.
        """
        reminder = await reminder_service.generate_smart_reminder(telegram_id)
        
        if reminder and self._bot:
            await self._send_reminder(telegram_id, reminder)
            await reminder_service.record_reminder_sent(telegram_id, reminder["type"])
            return reminder
        
        return None
    
    async def _check_and_send_daily_reports(self):
        """Проверка и отправка вечерних отчётов"""
        
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(User)
                    .where(User.is_active == True)
                    .where(User.daily_report_enabled == True)
                    .where(User.target_calories.isnot(None))
                )
                users = result.scalars().all()
                
                for user in users:
                    try:
                        # Проверяем, нужно ли отправлять
                        if not await report_service.should_send_daily_report(user):
                            continue
                        
                        # Проверяем время пользователя
                        report_time = user.daily_report_time or "21:00"
                        try:
                            target_hour = int(report_time.split(":")[0])
                        except (ValueError, IndexError):
                            target_hour = 21
                        
                        try:
                            tz = pytz.timezone(user.timezone or "Europe/Moscow")
                            user_hour = datetime.now(tz).hour
                        except (pytz.UnknownTimeZoneError, Exception):
                            user_hour = datetime.now(pytz.timezone("Europe/Moscow")).hour
                        
                        # Отправляем только в нужное время (±1 час)
                        if abs(user_hour - target_hour) > 1:
                            continue
                        
                        # Генерируем отчёт
                        report = await report_service.generate_daily_report(user.telegram_id)
                        
                        if report and self._bot:
                            await self._send_daily_report(user.telegram_id, report)
                            await report_service.mark_daily_report_sent(user.telegram_id)
                            logger.info(f"Sent daily report to user {user.telegram_id}")
                            await asyncio.sleep(0.5)
                            
                    except Exception as e:
                        logger.error(f"Error sending daily report to {user.telegram_id}: {e}")
                        
        except Exception as e:
            logger.error(f"Error in _check_and_send_daily_reports: {e}", exc_info=True)
    
    async def _send_daily_report(self, telegram_id: int, report: str):
        """Отправка вечернего отчёта"""
        if not self._bot:
            return
        
        try:
            keyboard = [[
                InlineKeyboardButton("📝 Открыть дневник", callback_data="diary_today"),
                InlineKeyboardButton("⚙️ Настройки", callback_data="settings")
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self._bot.send_message(
                chat_id=telegram_id,
                text=report,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        except TelegramError as e:
            if "blocked" in str(e).lower():
                await self._disable_user_notifications(telegram_id)
            else:
                logger.error(f"Error sending daily report: {e}")
    
    async def _check_and_send_weekly_reports(self):
        """Проверка и отправка еженедельных отчётов"""
        
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(User)
                    .where(User.is_active == True)
                    .where(User.weekly_report_enabled == True)
                    .where(User.target_calories.isnot(None))
                )
                users = result.scalars().all()
                
                for user in users:
                    try:
                        if not await report_service.should_send_weekly_report(user):
                            continue
                        
                        report = await report_service.generate_weekly_report(user.telegram_id)
                        
                        if report and self._bot:
                            keyboard = [[
                                InlineKeyboardButton("📊 Подробная статистика", callback_data="stats_week"),
                                InlineKeyboardButton("🎯 Начать челлендж", callback_data="challenge_suggest")
                            ]]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            
                            await self._bot.send_message(
                                chat_id=user.telegram_id,
                                text=report,
                                parse_mode="Markdown",
                                reply_markup=reply_markup
                            )
                            await report_service.mark_weekly_report_sent(user.telegram_id)
                            logger.info(f"Sent weekly report to user {user.telegram_id}")
                            await asyncio.sleep(0.5)
                            
                    except Exception as e:
                        logger.error(f"Error sending weekly report to {user.telegram_id}: {e}")
                        
        except Exception as e:
            logger.error(f"Error in _check_and_send_weekly_reports: {e}", exc_info=True)
    
    async def _check_and_send_morning_checkins(self):
        """Проверка и отправка утренних чек-инов"""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(User)
                    .where(User.is_active == True)
                    .where(User.morning_checkin_enabled == True)
                    .where(User.target_calories.isnot(None))
                    .where(User.today_plan_mode.is_(None))
                )
                users = result.scalars().all()
                
                if not users:
                    return
                
                logger.info(f"Sending morning check-ins to {len(users)} users")
                
                for user in users:
                    try:
                        if self._bot:
                            keyboard = [[
                                InlineKeyboardButton("🎯 Держим цель!", callback_data="plan_strict"),
                                InlineKeyboardButton("😌 Свободный день", callback_data="plan_relaxed")
                            ]]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            
                            await self._bot.send_message(
                                chat_id=user.telegram_id,
                                text="Доброе утро! ☀️\n\nКакой план на еду сегодня: держим цель или свободный день?",
                                reply_markup=reply_markup
                            )
                            
                            # Ставим заглушку, чтобы не отправлять повторно
                            user.today_plan_mode = "pending"
                            await session.commit()
                            
                            await asyncio.sleep(0.5)
                            
                    except TelegramError as e:
                        if "blocked" in str(e).lower():
                            await self._disable_user_notifications(user.telegram_id)
                        else:
                            logger.error(f"Error sending morning check-in to {user.telegram_id}: {e}")
                    except Exception as e:
                        logger.error(f"Error sending morning check-in to {user.telegram_id}: {e}")
                        
        except Exception as e:
            logger.error(f"Error in _check_and_send_morning_checkins: {e}", exc_info=True)
    
    async def _check_and_send_weigh_reminders(self):
        """Проверка и отправка напоминаний взвеситься"""
        today = datetime.now(pytz.timezone("Europe/Moscow")).date()
        
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(User)
                    .where(User.is_active == True)
                    .where(User.weigh_reminder_enabled == True)
                    .where(User.target_calories.isnot(None))
                )
                users = result.scalars().all()
                
                for user in users:
                    try:
                        # Проверяем, не отправляли ли уже
                        if user.last_weigh_reminder_date == today:
                            continue
                        
                        if self._bot:
                            keyboard = [[
                                InlineKeyboardButton("⚖️ Записать вес", callback_data="log_weight"),
                                InlineKeyboardButton("⏭ Пропустить", callback_data="skip_weigh")
                            ]]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            
                            await self._bot.send_message(
                                chat_id=user.telegram_id,
                                text="Если взвешиваешься — сегодня хороший день отметить вес 📊",
                                reply_markup=reply_markup
                            )
                            
                            # Обновляем дату
                            user.last_weigh_reminder_date = today
                            await session.commit()
                            
                            logger.info(f"Sent weigh reminder to user {user.telegram_id}")
                            await asyncio.sleep(0.5)
                            
                    except Exception as e:
                        logger.error(f"Error sending weigh reminder to {user.telegram_id}: {e}")
                        
        except Exception as e:
            logger.error(f"Error in _check_and_send_weigh_reminders: {e}", exc_info=True)
    
    async def _check_challenge_progress(self):
        """Проверка прогресса по активным челленджам"""
        
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(User)
                    .where(User.is_active == True)
                    .where(User.active_challenge.isnot(None))
                )
                users = result.scalars().all()
                
                for user in users:
                    try:
                        # Проверяем прогресс за день
                        progress = await challenge_service.check_daily_progress(user.telegram_id)
                        
                        if progress:
                            # Обновляем прогресс
                            result = await challenge_service.update_challenge_progress(
                                user.telegram_id,
                                progress["passed"]
                            )
                            
                            if result and self._bot:
                                # Отправляем уведомление о результате дня
                                if result["status"] == "completed":
                                    # Челлендж завершён
                                    text = challenge_service.format_challenge_result(
                                        result["result"],
                                        progress["challenge_name"]
                                    )
                                    keyboard = [[
                                        InlineKeyboardButton("🎯 Новый челлендж", callback_data="challenge_suggest")
                                    ]]
                                else:
                                    # Прогресс
                                    if progress["passed"]:
                                        text = f"✅ День засчитан! Челлендж «{progress['challenge_name']}»: {result['days_completed']}/{progress['total_days']} дней"
                                    else:
                                        text = f"📝 Сегодня не получилось: {progress['reason']}\n\nНо не сдаёмся! Продолжаем завтра 💪"
                                    keyboard = None
                                
                                reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
                                
                                await self._bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=text,
                                    reply_markup=reply_markup
                                )
                                await asyncio.sleep(0.5)
                            
                    except Exception as e:
                        logger.error(f"Error checking challenge for {user.telegram_id}: {e}")
                        
        except Exception as e:
            logger.error(f"Error in _check_challenge_progress: {e}", exc_info=True)
    
    async def _reset_daily_state(self):
        """Сброс дневных состояний: today_plan_mode, reminders_sent_today"""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(User).where(User.is_active == True)
                )
                users = result.scalars().all()
                
                for user in users:
                    user.today_plan_mode = None
                    user.reminders_sent_today = 0
                
                await session.commit()
                logger.info(f"Reset daily state for {len(users)} users")
        except Exception as e:
            logger.error(f"Error resetting daily state: {e}", exc_info=True)
    
    async def _disable_user_notifications(self, telegram_id: int):
        """Отключает все уведомления для пользователя (заблокировал бота)"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()
            
            if user:
                user.reminders_enabled = False
                user.daily_report_enabled = False
                user.weekly_report_enabled = False
                user.morning_checkin_enabled = False
                user.weigh_reminder_enabled = False
                await session.commit()
                logger.warning(f"Disabled all notifications for user {telegram_id}")


# Создаём глобальный экземпляр
scheduler_service = SchedulerService()

