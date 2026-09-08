"""
Работа со временем.

Правило одно: в базе всё хранится в UTC (naive datetime), а показывается и
считается «по дням» во времени пользователя. Раньше границы дня брались по
часам сервера — в Docker это UTC, и ужин в 01:00 по Москве попадал во
вчерашний день.
"""
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Europe/Moscow"

# Российские пояса без перехода на летнее время — по смещению от UTC.
RU_ZONES_BY_OFFSET = {
    2: "Europe/Kaliningrad",
    3: "Europe/Moscow",
    4: "Europe/Samara",
    5: "Asia/Yekaterinburg",
    6: "Asia/Omsk",
    7: "Asia/Novosibirsk",
    8: "Asia/Irkutsk",
    9: "Asia/Yakutsk",
    10: "Asia/Vladivostok",
    11: "Asia/Magadan",
    12: "Asia/Kamchatka",
}

ZONE_CITY_NAMES = {
    "Europe/Kaliningrad": "Калининград",
    "Europe/Moscow": "Москва",
    "Europe/Samara": "Самара",
    "Asia/Yekaterinburg": "Екатеринбург",
    "Asia/Omsk": "Омск",
    "Asia/Novosibirsk": "Новосибирск",
    "Asia/Irkutsk": "Иркутск",
    "Asia/Yakutsk": "Якутск",
    "Asia/Vladivostok": "Владивосток",
    "Asia/Magadan": "Магадан",
    "Asia/Kamchatka": "Камчатка",
    "Asia/Almaty": "Алматы",
    "Asia/Tashkent": "Ташкент",
    "Asia/Tbilisi": "Тбилиси",
    "Asia/Yerevan": "Ереван",
    "Asia/Baku": "Баку",
    "Europe/Minsk": "Минск",
    "Europe/Kiev": "Киев",
    "Europe/Kyiv": "Киев",
    "Asia/Bishkek": "Бишкек",
}

# Пояса с получасовым смещением, которых нет среди российских.
FRACTIONAL_ZONES = {
    3.5: "Asia/Tehran",
    4.5: "Asia/Kabul",
    5.5: "Asia/Kolkata",
    5.75: "Asia/Kathmandu",
    6.5: "Asia/Yangon",
    9.5: "Australia/Darwin",
    10.5: "Australia/Adelaide",
    -3.5: "America/St_Johns",
}


def get_zone(name: Optional[str]) -> ZoneInfo:
    """Зона по имени; при мусоре в БД возвращаем московскую, а не падаем."""
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo(DEFAULT_TZ)


def utc_now() -> datetime:
    """Текущее время в формате хранения — naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(moment_utc: datetime, zone: ZoneInfo) -> datetime:
    """Наивное UTC из базы → aware-время пользователя."""
    if moment_utc.tzinfo is None:
        moment_utc = moment_utc.replace(tzinfo=timezone.utc)
    return moment_utc.astimezone(zone)


def to_utc_naive(moment_local: datetime) -> datetime:
    """Aware-время пользователя → naive UTC для записи в базу."""
    if moment_local.tzinfo is None:
        raise ValueError("expected an aware datetime")
    return moment_local.astimezone(timezone.utc).replace(tzinfo=None)


def local_now(zone: ZoneInfo) -> datetime:
    return datetime.now(zone)


def local_today(zone: ZoneInfo) -> date:
    return datetime.now(zone).date()


def combine_local(zone: ZoneInfo, day: date, at: time) -> datetime:
    """Дата и время пользователя → naive UTC."""
    return to_utc_naive(datetime.combine(day, at, tzinfo=zone))


def local_day_bounds(zone: ZoneInfo, day: date) -> Tuple[datetime, datetime]:
    """Границы календарного дня пользователя в UTC: [начало, конец)."""
    start = combine_local(zone, day, time.min)
    end = combine_local(zone, day + timedelta(days=1), time.min)
    return start, end


def local_range_bounds(zone: ZoneInfo, first_day: date, last_day: date) -> Tuple[datetime, datetime]:
    """Границы диапазона дней пользователя (оба включительно) в UTC."""
    start, _ = local_day_bounds(zone, first_day)
    _, end = local_day_bounds(zone, last_day)
    return start, end


def format_local(moment_utc: datetime, zone: ZoneInfo, fmt: str = "%H:%M") -> str:
    return to_local(moment_utc, zone).strftime(fmt)


# --- определение пояса ---

_CLOCK_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*[:.\-ч]\s*([0-5]\d)(?!\d)")
_SPACED_CLOCK_RE = re.compile(r"^\s*([01]?\d|2[0-3])\s+([0-5]\d)\s*$")
_HOUR_ONLY_RE = re.compile(r"^\s*([01]?\d|2[0-3])\s*(ч|час|часа|часов)?\s*$")


def parse_clock_parts(text: str) -> Optional[Tuple[int, Optional[int]]]:
    """«14:35» → (14, 35); «14ч» → (14, None). Минуты None — их не назвали."""
    if not text:
        return None
    match = _CLOCK_RE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = _SPACED_CLOCK_RE.match(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = _HOUR_ONLY_RE.match(text)
    if match:
        return int(match.group(1)), None
    return None


def parse_clock(text: str) -> Optional[time]:
    """«14:35», «14.35», «14 35», «14ч» → time. Иначе None."""
    parts = parse_clock_parts(text)
    if parts is None:
        return None
    hour, minute = parts
    return time(hour, minute or 0)


def offset_from_reported_time(reported: time, now_utc: Optional[datetime] = None) -> float:
    """
    Смещение пользователя от UTC в часах по названному им текущему времени.

    Разницу с UTC приводим к диапазону (−12; +14]. Округляем до целого часа,
    а полчаса допускаем только там, где такие пояса реально существуют.
    """
    now_utc = now_utc or utc_now()
    diff = (reported.hour * 60 + reported.minute) - (now_utc.hour * 60 + now_utc.minute)
    while diff > 14 * 60:
        diff -= 24 * 60
    while diff <= -12 * 60:
        diff += 24 * 60

    whole = round(diff / 60)
    if abs(diff - whole * 60) <= 20:
        return float(whole)
    half = round(diff / 30) * 0.5
    if half in FRACTIONAL_ZONES and abs(diff - half * 60) <= 10:
        return half
    return float(whole)


def parse_reported_offset(text: str, now_utc: Optional[datetime] = None) -> Optional[float]:
    """
    Смещение по фразе вида «сейчас 14:35»; None, если времени в тексте нет.

    Если названы только часы, минуты берём у UTC: во всех целочасовых поясах
    они совпадают, и «14ч» превращается в точное время вместо угадывания.
    """
    now_utc = now_utc or utc_now()
    parts = parse_clock_parts(text)
    if parts is None:
        return None
    hour, minute = parts
    if minute is None:
        minute = now_utc.minute
    return offset_from_reported_time(time(hour, minute), now_utc)


def zone_for_offset(offset_hours: float) -> str:
    """Имя пояса по смещению. Для России — города, для остальных — фиксированные Etc-зоны."""
    if offset_hours in FRACTIONAL_ZONES:
        return FRACTIONAL_ZONES[offset_hours]
    if float(offset_hours).is_integer():
        whole = int(offset_hours)
        if whole in RU_ZONES_BY_OFFSET:
            return RU_ZONES_BY_OFFSET[whole]
        if whole == 0:
            return "Etc/UTC"
        # В названиях Etc/GMT знак перевёрнут: Etc/GMT-3 — это UTC+3.
        return f"Etc/GMT{'-' if whole > 0 else '+'}{abs(whole)}"
    return zone_for_offset(round(offset_hours))


def zone_from_coordinates(latitude: float, longitude: float) -> str:
    """
    Пояс по геопозиции.

    Основной путь — timezonefinder (границы поясов по карте). Если библиотеки
    нет, оцениваем по долготе: 15° = 1 час.
    """
    try:
        from timezonefinder import TimezoneFinder  # noqa: WPS433 — необязательная зависимость

        global _TZ_FINDER
        if _TZ_FINDER is None:
            _TZ_FINDER = TimezoneFinder()
        found = _TZ_FINDER.timezone_at(lat=latitude, lng=longitude)
        if found:
            return found
    except Exception:  # noqa: BLE001 — любая проблема с библиотекой → запасной путь
        pass
    return zone_for_offset(round(longitude / 15))


_TZ_FINDER = None


def zone_offset_hours(zone: ZoneInfo, moment: Optional[datetime] = None) -> float:
    moment = moment or datetime.now(timezone.utc)
    offset = moment.astimezone(zone).utcoffset() or timedelta()
    return offset.total_seconds() / 3600


def describe_zone(name: Optional[str]) -> str:
    """«UTC+3 · Москва» — чтобы человек сразу понял, что пояс определился верно."""
    zone = get_zone(name)
    hours = zone_offset_hours(zone)
    sign = "+" if hours >= 0 else "−"
    label = f"UTC{sign}{abs(hours):g}"
    city = ZONE_CITY_NAMES.get(zone.key)
    return f"{label} · {city}" if city else label
