"""
Из фразы о еде достаём КОГДА это было: «вчера», «на завтрак», «в 8 утра».

Раньше всё записывалось на «сейчас» и приём пищи выбирался по часам: фраза
«на завтрак ел овсянку», отправленная в обед, ложилась в обед.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from utils.telegram_ui import (
    MEAL_BREAKFAST, MEAL_LUNCH, MEAL_DINNER, MEAL_SNACK, meal_type_by_time,
)
from utils.timeutil import local_now, to_utc_naive

# Когда человек называет приём пищи без времени — ставим типичное.
TYPICAL_MEAL_TIMES = {
    MEAL_BREAKFAST: time(9, 0),
    MEAL_LUNCH: time(13, 30),
    MEAL_DINNER: time(19, 30),
    MEAL_SNACK: time(16, 30),
}

PART_OF_DAY_TIMES = {
    "morning": time(9, 0),
    "afternoon": time(14, 0),
    "evening": time(20, 0),
    "night": time(23, 30),
}

# Прошлый день без указания времени — считаем серединой дня.
DEFAULT_PAST_TIME = time(14, 0)

_DAY_PATTERNS = [
    (re.compile(r"\bпозавчера\b|\bпозавчерашн\w*"), -2),
    (re.compile(r"\bвчера\b|\bвчерашн\w*"), -1),
]

_MEAL_PATTERNS = [
    (re.compile(r"\bзавтрак\w*|\bпозавтракал\w*"), MEAL_BREAKFAST),
    (re.compile(r"\bобед\w*|\bпообедал\w*"), MEAL_LUNCH),
    (re.compile(r"\bужин\w*|\bпоужинал\w*"), MEAL_DINNER),
    (re.compile(r"\bперекус\w*"), MEAL_SNACK),
]

_PART_PATTERNS = [
    (re.compile(r"\bутром\b|\bс утра\b|\bпод утро\b"), "morning"),
    (re.compile(r"\bдн[её]м\b|\bв полдень\b"), "afternoon"),
    (re.compile(r"\bвечером\b|\bпод вечер\b"), "evening"),
    (re.compile(r"\bночью\b|\bна ночь\b"), "night"),
]

# «в 8 утра», «в 14:30», «в 7 вечера», «в 19». Голое «в 2» без пояснения
# принимаем только если дальше не идёт единица измерения («в 2 яйца»).
_TIME_RE = re.compile(
    r"\bв\s+(\d{1,2})(?::(\d{2}))?\s*(утра|дня|вечера|ночи|час(?:а|ов)?)?(?=\W|$)(?:\s*(\w+))?"
)
_UNIT_WORDS = ("г", "гр", "грамм", "мл", "л", "шт", "штук", "кус", "яйц", "стакан", "порци", "ложк", "бутыл", "кружк", "чашк")


@dataclass
class MealContext:
    day_offset: int = 0
    meal_type: Optional[str] = None
    explicit_time: Optional[time] = None
    part_of_day: Optional[str] = None

    @property
    def mentioned(self) -> bool:
        return bool(self.day_offset or self.meal_type or self.explicit_time or self.part_of_day)


def _adjust_hour(hour: int, qualifier: Optional[str]) -> int:
    if not qualifier:
        return hour
    if qualifier.startswith("дня") or qualifier.startswith("вечера"):
        return hour + 12 if hour < 12 else hour
    if qualifier.startswith("ночи"):
        if hour == 12:
            return 0
        return hour + 12 if hour >= 9 else hour
    if qualifier.startswith("утра") and hour == 12:
        return 0
    return hour


def parse_meal_context(text: str) -> MealContext:
    """Разобрать указания на день, приём пищи и время."""
    lowered = (text or "").lower()
    ctx = MealContext()

    for pattern, offset in _DAY_PATTERNS:
        if pattern.search(lowered):
            ctx.day_offset = offset
            break

    for pattern, meal in _MEAL_PATTERNS:
        if pattern.search(lowered):
            ctx.meal_type = meal
            break

    for pattern, part in _PART_PATTERNS:
        if pattern.search(lowered):
            ctx.part_of_day = part
            break

    match = _TIME_RE.search(lowered)
    if match:
        hour = int(match.group(1))
        minutes = int(match.group(2)) if match.group(2) else 0
        qualifier = match.group(3)
        following = match.group(4) or ""
        has_context = bool(match.group(2) or qualifier)
        looks_like_amount = any(following.startswith(unit) for unit in _UNIT_WORDS)
        if 0 <= hour <= 23 and minutes <= 59 and (has_context or (5 <= hour <= 23 and not looks_like_amount)):
            ctx.explicit_time = time(_adjust_hour(hour, qualifier) % 24, minutes)

    return ctx


def resolve_moment(ctx: MealContext, zone: ZoneInfo, now_local: Optional[datetime] = None) -> Tuple[datetime, str, int]:
    """
    Превратить контекст в момент записи.

    Returns:
        (момент в naive UTC, тип приёма пищи, итоговый сдвиг дня)
    """
    now_local = now_local or local_now(zone)
    day_offset = ctx.day_offset
    day = now_local.date() + timedelta(days=day_offset)

    at: Optional[time] = None
    if ctx.explicit_time:
        at = ctx.explicit_time
    elif ctx.meal_type:
        at = TYPICAL_MEAL_TIMES[ctx.meal_type]
    elif ctx.part_of_day:
        at = PART_OF_DAY_TIMES[ctx.part_of_day]
    elif day_offset < 0:
        at = DEFAULT_PAST_TIME

    if at is None:
        moment_local = now_local
    else:
        moment_local = datetime.combine(day, at, tzinfo=zone)
        if day_offset == 0 and moment_local > now_local + timedelta(hours=1):
            # «Вечером съел пиццу», сказанное утром, — это про вчерашний вечер.
            day_offset = -1
            moment_local = datetime.combine(day - timedelta(days=1), at, tzinfo=zone)
        elif day_offset == 0 and moment_local > now_local:
            moment_local = now_local

    meal_type = ctx.meal_type or meal_type_by_time(moment_local)
    return to_utc_naive(moment_local), meal_type, day_offset


def describe_when(day_offset: int, meal_type: str) -> str:
    """«в обед», «во вчерашний ужин», «в позавчерашний завтрак»."""
    if day_offset == -1:
        return f"во вчерашний {meal_type}"
    if day_offset == -2:
        return f"в позавчерашний {meal_type}"
    if day_offset < -2:
        return f"в {meal_type} ({abs(day_offset)} дн. назад)"
    return f"в {meal_type}"
