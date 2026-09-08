"""
Учёт воды по сообщению «выпил стакан воды».

Вода — не еда: у неё нет калорий, и отправлять её в модель для расчёта КБЖУ
бессмысленно. Считаем объём здесь и пишем отдельной строкой дневника.
"""
import re
from typing import Optional

WATER_MEAL_TYPE = "вода"

_WATER_RE = re.compile(r"\bвод(?:а|ы|у|ой|е|ичк\w*)\b|\bводич\w*")

# Слова, которые допустимы в сообщении только про воду.
_ALLOWED_RE = re.compile(
    r"\b(пол-?литра|поллитра|выпил[аи]?|попил[аи]?|пью|пила|пил|я|ещё|еще|только|сейчас|уже|утром|дн[её]м|вечером|"
    r"вод\w*|стакан\w*|кружк\w*|бутылк\w*|бутылочк\w*|чашк\w*|литр\w*|мл|л|"
    r"минерал\w*|газирован\w*|без|газа|с|газом|т[её]пл\w*|холодн\w*|горяч\w*|"
    r"и|запиши|добавь|плюс|один|одну|два|две|три|четыре|пять|шесть|пол|половин\w*|полтора|целый|целую|большой|большую|маленький|маленькую)\b"
)

_NUM_WORDS = {
    "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "пол": 0.5, "половину": 0.5, "половина": 0.5, "полтора": 1.5,
}

_CONTAINER_ML = {
    "стакан": 250,
    "кружк": 300,
    "чашк": 200,
    "бутылк": 500,
    "бутылочк": 500,
}


def parse_water(text: str) -> Optional[int]:
    """
    Вернуть объём воды в мл, если сообщение только про воду. Иначе None.

    «Выпил стакан воды» → 250, «0,5 л воды» → 500, «два стакана воды» → 500,
    «выпил воды» → 250 по умолчанию. «Выпил воды и съел яблоко» → None:
    здесь есть еда, пусть считает модель.
    """
    lowered = (text or "").lower().replace("ё", "е")
    if not _WATER_RE.search(lowered) and not re.search(r"\bвод", lowered):
        return None

    leftover = _ALLOWED_RE.sub(" ", lowered)
    leftover = re.sub(r"[\d.,+\-х×x\s]", "", leftover)
    if len(leftover) > 2:
        return None

    if re.search(r"пол-?\s?литра|поллитра", lowered):
        return 500

    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(мл|л\b|литр)", lowered)
    if match:
        value = float(match.group(1).replace(",", "."))
        unit = match.group(2)
        millilitres = value if unit == "мл" else value * 1000
        return int(round(millilitres))

    match = re.search(r"(\d+(?:[.,]\d+)?|один|одну|два|две|три|четыре|пять|шесть|пол|половину|полтора)?\s*(стакан|кружк|чашк|бутылочк|bутылк|бутылк)", lowered)
    if match:
        raw_count = match.group(1)
        if raw_count is None:
            count = 1.0
        elif raw_count in _NUM_WORDS:
            count = _NUM_WORDS[raw_count]
        else:
            count = float(raw_count.replace(",", "."))
        container = match.group(2)
        millilitres = _CONTAINER_ML.get(container, 250)
        return int(round(count * millilitres))

    match = re.search(r"(?<![\d.,])(\d{2,4})(?![\d.,])", lowered)
    if match:
        value = int(match.group(1))
        if 50 <= value <= 5000:
            return value

    return 250


def water_target_ml(weight_kg: Optional[float]) -> int:
    """Ориентир по воде: 30 мл на кг веса, в разумных границах."""
    if not weight_kg:
        return 2000
    return int(min(max(weight_kg * 30, 1500), 3500))


def format_litres(millilitres: float) -> str:
    litres = millilitres / 1000
    text = f"{litres:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")
