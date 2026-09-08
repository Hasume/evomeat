"""
Планировщик: напоминания, отчёты и чек-ины во времени КАЖДОГО пользователя.

Раньше расписание жило по Москве: человеку из Новосибирска «пора обедать»
приходило в 18:00, а сброс дневных состояний в 05:00 пропадал при любом
деплое в это время. Теперь каждый тик смотрит на локальное время пользователя,
а «уже отправлено сегодня» хранится датами по его календарю.
"""
import asyncio
import logging
from datetime import datetime, time
from typing import List, Optional, Set, Tuple

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from sqlalchemy import select

from database.models import User
from database.database import AsyncSessionLocal
from services.reminder_service import reminder_service
from services.report_service import report_service
from services.challenge_service import challenge_service
from utils.timeutil import get_zone, local_now

logger = logging.getLogger(__name__)

TICK_SECONDS = 300

# Окна проверок — в локальном времени пользователя.
REMINDER_TIMES = [time(10, 0), time(14, 0), time(19, 0)]
REMINDER_WINDOW_MINUTES = 15
DEFAULT_DAILY_REPORT_HOUR = 21
WEEKLY_REPORT_HOUR = 12
DEFAULT_MORNING_CHECKIN_HOUR = 8
WEIGH_REMINDER_HOUR = 10
WEIGH_REMINDER_WEEKDAYS = (2, 6)  # среда и воскресенье
CHALLENGE_CHECK_HOUR = 22

ACTION_REMINDER = "reminder"
ACTION_DAILY_REPORT = "daily_report"
ACTION_WEEKLY_REPORT = "weekly_report"
ACTION_MORNING_CHECKIN = "morning_checkin"
ACTION_WEIGH_REMINDER = "weigh_reminder"
ACTION_CHALLENGE_CHECK = "challenge_check"


def hour_of(value: Optional[str], default: int) -> int:
    """«21:00» → 21; мусор в поле → значение по умолчанию."""
    try:
        return max(0, min(23, int(str(value).split(":")[0])))
    except (ValueError, TypeError, AttributeError):
        return default


def reminder_slot(now_local: datetime) -> Optional[int]:
    """Номер окна напоминаний, если оно сейчас открыто (±15 минут от 10, 14, 19)."""
    minutes_now = now_local.hour * 60 + now_local.minute
    for index, slot in enumerate(REMINDER_TIMES):
        if abs(minutes_now - (slot.hour * 60 + slot.minute)) <= REMINDER_WINDOW_MINUTES:
            return index
    return None


def plan_actions(user: User, now_local: datetime) -> List[str]:
    """
    Что пора отправить пользователю в этот момент его времени.

    Чистая функция без базы — её поведение проверяется тестами напрямую.
    """
    actions: List[str] = []
    if user.target_calories is None:
        return actions

    today = now_local.date()
    hour = now_local.hour

    if user.reminders_enabled and reminder_slot(now_local) is not None:
        actions.append(ACTION_REMINDER)

    if (user.daily_report_enabled
            and hour == hour_of(user.daily_report_time, DEFAULT_DAILY_REPORT_HOUR)
            and user.last_daily_report_date != today):
        actions.append(ACTION_DAILY_REPORT)

    weekly_day = user.weekly_report_day if user.weekly_report_day is not None else 6
    if (user.weekly_report_enabled
            and now_local.weekday() == weekly_day
            and hour == WEEKLY_REPORT_HOUR
            and user.last_weekly_report_date != today):
        actions.append(ACTION_WEEKLY_REPORT)

    if (user.morning_checkin_enabled
            and hour == hour_of(user.morning_checkin_time, DEFAULT_MORNING_CHECKIN_HOUR)
            and user.plan_mode_date != today):
        actions.append(ACTION_MORNING_CHECKIN)

    if (user.weigh_reminder_enabled
            and now_local.weekday() in WEIGH_REMINDER_WEEKDAYS
            and hour == WEIGH_REMINDER_HOUR
            and user.last_weigh_reminder_date != today):
        actions.append(ACTION_WEIGH_REMINDER)

    challenge = user.active_challenge or {}
    if (challenge
            and hour == CHALLENGE_CHECK_HOUR
            and challenge.get("last_checked_date") != today.isoformat()):
        actions.append(ACTION_CHALLENGE_CHECK)

    return actions


class SchedulerService:
    """Планировщик автоматических сообщений"""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._bot: Optional[Bot] = None
        # (id пользователя, дата, окно) — в этом окне напоминание уже разбирали.
        self._reminded: Set[Tuple[int, str, int]] = set()

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
        while self._running:
            try:
                await asyncio.sleep(TICK_SECONDS)
                if not self._bot:
                    logger.warning("Bot not set, skipping scheduler tick")
                    continue
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)

    async def tick(self):
        """Один проход по всем пользователям."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User)
                .where(User.is_active == True)  # noqa: E712 — сравнение колонки
                .where(User.target_calories.isnot(None))
            )
            users = result.scalars().all()

        for user in users:
            try:
                now_local = local_now(get_zone(user.timezone))
                for action in plan_actions(user, now_local):
                    await self._dispatch(user, action, now_local)
            except Exception as e:
                logger.error(f"Scheduler error for user {user.telegram_id}: {e}", exc_info=True)

        self._forget_old_reminder_slots()

    async def _dispatch(self, user: User, action: str, now_local: datetime):
        if action == ACTION_REMINDER:
            await self._send_smart_reminder(user, now_local)
        elif action == ACTION_DAILY_REPORT:
            await self._send_daily_report(user)
        elif action == ACTION_WEEKLY_REPORT:
            await self._send_weekly_report(user)
        elif action == ACTION_MORNING_CHECKIN:
            await self._send_morning_checkin(user, now_local)
        elif action == ACTION_WEIGH_REMINDER:
            await self._send_weigh_reminder(user, now_local)
        elif action == ACTION_CHALLENGE_CHECK:
            await self._check_challenge(user)

    # --- отправка ---

    async def _send_message(self, telegram_id: int, text: str, reply_markup=None, parse_mode=None) -> bool:
        """Отправить сообщение; если пользователь заблокировал бота — выключить рассылки."""
        if not self._bot:
            return False
        try:
            await self._bot.send_message(chat_id=telegram_id, text=text,
                                         reply_markup=reply_markup, parse_mode=parse_mode)
            await asyncio.sleep(0.3)  # не упираться в лимиты Telegram при массовой рассылке
            return True
        except TelegramError as e:
            lowered = str(e).lower()
            if "blocked" in lowered or "deactivated" in lowered or "chat not found" in lowered:
                logger.warning(f"User {telegram_id} is unreachable, disabling notifications")
                await self._disable_user_notifications(telegram_id)
            elif "parse" in lowered or "entit" in lowered:
                # Разметка не прошла — лучше доставить без неё, чем потерять сообщение.
                try:
                    await self._bot.send_message(chat_id=telegram_id, text=text, reply_markup=reply_markup)
                    return True
                except TelegramError as inner:
                    logger.error(f"Telegram error sending to {telegram_id}: {inner}")
            else:
                logger.error(f"Telegram error sending to {telegram_id}: {e}")
        except Exception as e:
            logger.error(f"Error sending message to {telegram_id}: {e}")
        return False

    async def _send_smart_reminder(self, user: User, now_local: datetime):
        slot = reminder_slot(now_local)
        key = (user.id, now_local.date().isoformat(), slot if slot is not None else -1)
        if key in self._reminded:
            return
        # Окно закрываем сразу: за 15 минут картина дня не меняется,
        # а повторные проверки каждые 5 минут только дёргают базу.
        self._reminded.add(key)

        reminder = await reminder_service.generate_smart_reminder(user.telegram_id)
        if not reminder:
            return

        if reminder.get("is_question"):
            keyboard = [[
                InlineKeyboardButton("📊 Мой день", callback_data="stats_today"),
                InlineKeyboardButton("📝 Дневник", callback_data="diary_today"),
            ]]
        else:
            keyboard = [[InlineKeyboardButton("📊 Мой день", callback_data="stats_today")]]

        sent = await self._send_message(user.telegram_id, reminder["message"],
                                        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        if sent:
            await reminder_service.record_reminder_sent(user.telegram_id, reminder["type"])
            logger.info(f"Sent reminder to user {user.telegram_id}: {reminder['type']}")

    async def send_manual_check(self, telegram_id: int) -> Optional[dict]:
        """Проверить и отправить напоминание вручную (для тестирования)."""
        reminder = await reminder_service.generate_smart_reminder(telegram_id)
        if reminder and self._bot:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Мой день", callback_data="stats_today")]])
            if await self._send_message(telegram_id, reminder["message"], reply_markup=keyboard, parse_mode="Markdown"):
                await reminder_service.record_reminder_sent(telegram_id, reminder["type"])
                return reminder
        return None

    async def _send_daily_report(self, user: User):
        report = await report_service.generate_daily_report(user.telegram_id)
        if report:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("📝 Дневник", callback_data="diary_today"),
                InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
            ]])
            if await self._send_message(user.telegram_id, report, reply_markup=keyboard, parse_mode="Markdown"):
                logger.info(f"Sent daily report to user {user.telegram_id}")
        # Отмечаем день и когда отчёта нет (пустой дневник): иначе проверка
        # повторялась бы каждые пять минут весь час.
        await report_service.mark_daily_report_sent(user.telegram_id)

    async def _send_weekly_report(self, user: User):
        report = await report_service.generate_weekly_report(user.telegram_id)
        if report:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("📊 Подробнее", callback_data="stats_week"),
                InlineKeyboardButton("🎯 Челлендж", callback_data="challenge_suggest"),
            ]])
            if await self._send_message(user.telegram_id, report, reply_markup=keyboard, parse_mode="Markdown"):
                logger.info(f"Sent weekly report to user {user.telegram_id}")
        await report_service.mark_weekly_report_sent(user.telegram_id)

    async def _send_morning_checkin(self, user: User, now_local: datetime):
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎯 Держим цель", callback_data="plan_strict"),
            InlineKeyboardButton("😌 Свободный день", callback_data="plan_relaxed"),
        ]])
        await self._send_message(
            user.telegram_id,
            "Доброе утро! ☀️\n\nКакой сегодня план по еде: держим цель или свободный день?",
            reply_markup=keyboard,
        )
        # Дата фиксируется независимо от доставки — иначе при ошибке отправки
        # чек-ин повторялся бы каждые пять минут.
        await self._update_user(user.id, today_plan_mode="pending", plan_mode_date=now_local.date())

    async def _send_weigh_reminder(self, user: User, now_local: datetime):
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚖️ Записать вес", callback_data="log_weight"),
            InlineKeyboardButton("⏭ Не сегодня", callback_data="skip_weigh"),
        ]])
        if await self._send_message(
            user.telegram_id,
            "Если взвешиваетесь — сегодня хороший день отметить вес 📊",
            reply_markup=keyboard,
        ):
            logger.info(f"Sent weigh reminder to user {user.telegram_id}")
        await self._update_user(user.id, last_weigh_reminder_date=now_local.date())

    async def _check_challenge(self, user: User):
        progress = await challenge_service.check_daily_progress(user.telegram_id)
        if not progress:
            return

        result = await challenge_service.update_challenge_progress(
            user.telegram_id, progress["passed"], checked_date=progress.get("date")
        )
        if not result:
            return

        if result["status"] == "completed":
            text = challenge_service.format_challenge_result(result["result"], progress["challenge_name"])
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🎯 Новый челлендж", callback_data="challenge_suggest")
            ]])
        elif progress["passed"]:
            text = (f"✅ День засчитан! «{progress['challenge_name']}»: "
                    f"{result['days_completed']} из {progress['total_days']} дней")
            keyboard = None
        else:
            text = (f"📝 Сегодня не получилось: {progress['reason']}\n\n"
                    f"Ничего страшного — продолжаем завтра 💪")
            keyboard = None

        await self._send_message(user.telegram_id, text, reply_markup=keyboard)

    # --- служебное ---

    async def _update_user(self, user_id: int, **fields):
        async with AsyncSessionLocal() as session:
            user = await session.get(User, user_id)
            if not user:
                return
            for name, value in fields.items():
                setattr(user, name, value)
            await session.commit()

    async def _disable_user_notifications(self, telegram_id: int):
        """Отключает все уведомления для пользователя (заблокировал бота)"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            user = result.scalar_one_or_none()
            if user:
                user.reminders_enabled = False
                user.daily_report_enabled = False
                user.weekly_report_enabled = False
                user.morning_checkin_enabled = False
                user.weigh_reminder_enabled = False
                await session.commit()
                logger.warning(f"Disabled all notifications for user {telegram_id}")

    def _forget_old_reminder_slots(self):
        """Не даём множеству отметок расти бесконечно."""
        if len(self._reminded) < 5000:
            return
        dates = sorted({key[1] for key in self._reminded})
        keep_from = dates[-2] if len(dates) >= 2 else dates[0]
        self._reminded = {key for key in self._reminded if key[1] >= keep_from}


# Создаём глобальный экземпляр
scheduler_service = SchedulerService()
