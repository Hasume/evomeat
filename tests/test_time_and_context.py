"""
Тесты времени пользователя, разбора «когда это было», воды и планировщика.
"""
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from utils.timeutil import (
    get_zone, local_day_bounds, parse_clock, parse_reported_offset, zone_for_offset,
    describe_zone, to_local, combine_local,
)
from utils.meal_context import parse_meal_context, resolve_moment, describe_when
from utils.water import parse_water, water_target_ml, format_litres
from services.scheduler_service import (
    plan_actions, ACTION_REMINDER, ACTION_DAILY_REPORT, ACTION_MORNING_CHECKIN,
    ACTION_WEIGH_REMINDER, ACTION_CHALLENGE_CHECK, ACTION_WEEKLY_REPORT,
)

MSK = get_zone("Europe/Moscow")
NSK = get_zone("Asia/Novosibirsk")
UTC_NOW = datetime(2026, 9, 8, 11, 33)


# --- часовой пояс по названному времени ---

@pytest.mark.parametrize("said,expected_zone", [
    ("14:35", "Europe/Moscow"),
    ("14ч", "Europe/Moscow"),       # минуты не названы — берём у UTC, а не гадаем полчаса
    ("18 33", "Asia/Novosibirsk"),
    ("16", "Asia/Yekaterinburg"),
    ("7:31", "Etc/GMT+4"),          # UTC−4: в именах Etc знак перевёрнут
])
def test_zone_from_reported_time(said, expected_zone):
    offset = parse_reported_offset(said, UTC_NOW)
    assert zone_for_offset(offset) == expected_zone


def test_non_time_text_is_not_a_clock():
    assert parse_reported_offset("съел банан", UTC_NOW) is None
    assert parse_clock("привет") is None


def test_describe_zone_is_human_readable():
    assert describe_zone("Europe/Moscow") == "UTC+3 · Москва"
    assert describe_zone("Asia/Novosibirsk") == "UTC+7 · Новосибирск"
    assert describe_zone("мусор") == "UTC+3 · Москва"  # неизвестное имя → по умолчанию


def test_local_day_bounds_are_in_utc():
    """День новосибирца начинается в 17:00 UTC предыдущих суток."""
    start, end = local_day_bounds(NSK, date(2026, 9, 8))
    assert start == datetime(2026, 9, 7, 17, 0)
    assert end == datetime(2026, 9, 8, 17, 0)


def test_late_dinner_stays_in_user_day():
    """Ужин в 01:00 по Москве — это ещё «сегодня» для пользователя, а не «вчера» по UTC."""
    moment_utc = combine_local(MSK, date(2026, 9, 9), time(1, 0))  # 22:00 UTC 8 сентября
    start, end = local_day_bounds(MSK, date(2026, 9, 9))
    assert start <= moment_utc < end
    assert to_local(moment_utc, MSK).date() == date(2026, 9, 9)


# --- когда это было ---

NOW_LOCAL = datetime(2026, 9, 8, 13, 0, tzinfo=MSK)


@pytest.mark.parametrize("phrase,expected_day,expected_time,expected_meal", [
    ("на завтрак ел овсянку", date(2026, 9, 8), time(9, 0), "завтрак"),
    ("вчера вечером съел пиццу", date(2026, 9, 7), time(20, 0), "ужин"),
    ("позавчера на обед борщ", date(2026, 9, 6), time(13, 30), "обед"),
    ("в 8 утра выпил кофе", date(2026, 9, 8), time(8, 0), "завтрак"),
    ("съел 2 яйца", date(2026, 9, 8), time(13, 0), "обед"),          # без указаний — сейчас
    ("вечером съел салат", date(2026, 9, 7), time(20, 0), "ужин"),   # вечер ещё не наступил → вчера
    ("поужинал курицей", date(2026, 9, 7), time(19, 30), "ужин"),
])
def test_meal_context_resolution(phrase, expected_day, expected_time, expected_meal):
    ctx = parse_meal_context(phrase)
    moment_utc, meal, _ = resolve_moment(ctx, MSK, NOW_LOCAL)
    local = to_local(moment_utc, MSK)
    assert (local.date(), local.time().replace(second=0, microsecond=0), meal) == (expected_day, expected_time, expected_meal)


def test_amount_after_v_is_not_a_time():
    """«съел в 2 яйца омлет» — «2» здесь количество, а не время."""
    assert parse_meal_context("съел в 2 яйца омлет").explicit_time is None


def test_describe_when():
    assert describe_when(0, "обед") == "в обед"
    assert describe_when(-1, "ужин") == "во вчерашний ужин"
    assert describe_when(-2, "завтрак") == "в позавчерашний завтрак"


# --- вода ---

@pytest.mark.parametrize("phrase,expected", [
    ("выпил стакан воды", 250),
    ("0,5 л воды", 500),
    ("два стакана воды", 500),
    ("выпил воды", 250),
    ("300 мл воды", 300),
    ("поллитра воды", 500),
    ("вода", 250),
])
def test_water_parsing(phrase, expected):
    assert parse_water(phrase) == expected


@pytest.mark.parametrize("phrase", ["выпила воды и съела яблоко", "съел суп", "кофе с молоком"])
def test_water_parser_ignores_food(phrase):
    """Если в сообщении есть еда, воду не перехватываем — считает модель."""
    assert parse_water(phrase) is None


def test_water_target_and_format():
    assert water_target_ml(80) == 2400
    assert water_target_ml(None) == 2000
    assert water_target_ml(40) == 1500   # нижняя граница
    assert format_litres(750) == "0,75"
    assert format_litres(2000) == "2"


# --- планировщик по времени пользователя ---

def make_user(**overrides):
    base = dict(
        id=1, telegram_id=1, timezone="Asia/Novosibirsk", target_calories=1800,
        reminders_enabled=True,
        daily_report_enabled=True, daily_report_time="21:00", last_daily_report_date=None,
        weekly_report_enabled=True, weekly_report_day=6, last_weekly_report_date=None,
        morning_checkin_enabled=True, morning_checkin_time="08:00", plan_mode_date=None,
        weigh_reminder_enabled=True, last_weigh_reminder_date=None,
        active_challenge=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def at(hour, minute=0, day=8):
    # 8 сентября 2026 — вторник; 9-е — среда; 13-е — воскресенье
    return datetime(2026, 9, day, hour, minute, tzinfo=NSK)


def test_daily_report_due_at_local_evening_only_once():
    user = make_user()
    assert ACTION_DAILY_REPORT in plan_actions(user, at(21, 5))
    assert ACTION_DAILY_REPORT not in plan_actions(user, at(18, 5))

    user.last_daily_report_date = date(2026, 9, 8)
    assert ACTION_DAILY_REPORT not in plan_actions(user, at(21, 40))


def test_reminder_window_is_fifteen_minutes_around_slots():
    user = make_user()
    assert ACTION_REMINDER in plan_actions(user, at(10, 10))
    assert ACTION_REMINDER in plan_actions(user, at(13, 50))
    assert ACTION_REMINDER not in plan_actions(user, at(10, 20))
    user.reminders_enabled = False
    assert ACTION_REMINDER not in plan_actions(user, at(10, 10))


def test_morning_checkin_once_per_local_day():
    user = make_user()
    assert ACTION_MORNING_CHECKIN in plan_actions(user, at(8, 30))
    user.plan_mode_date = date(2026, 9, 8)
    assert ACTION_MORNING_CHECKIN not in plan_actions(user, at(8, 30))


def test_weigh_reminder_on_wednesday_and_sunday():
    user = make_user()
    assert ACTION_WEIGH_REMINDER not in plan_actions(user, at(10, 30, day=8))   # вторник
    assert ACTION_WEIGH_REMINDER in plan_actions(user, at(10, 30, day=9))       # среда
    assert ACTION_WEEKLY_REPORT in plan_actions(user, at(12, 15, day=13))      # воскресенье


def test_challenge_checked_once_per_day():
    user = make_user(active_challenge={"type": "no_fastfood", "last_checked_date": None})
    assert ACTION_CHALLENGE_CHECK in plan_actions(user, at(22, 10))
    user.active_challenge["last_checked_date"] = "2026-09-08"
    assert ACTION_CHALLENGE_CHECK not in plan_actions(user, at(22, 10))


def test_incomplete_profile_gets_nothing():
    user = make_user(target_calories=None)
    assert plan_actions(user, at(21, 0)) == []
