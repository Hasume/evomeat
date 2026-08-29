"""
Тесты для AI сервиса
"""
import pytest
from services.ai_service import AIService, extract_json


@pytest.fixture
def ai_service():
    """Фикстура для AI сервиса"""
    return AIService()


@pytest.mark.asyncio
async def test_generate_recipes(ai_service):
    """Генерация рецептов возвращает список рецептов и список блокировок."""
    result = await ai_service.generate_recipes(["курица", "рис", "помидоры"])

    assert isinstance(result, dict)
    assert isinstance(result.get("recipes"), list)
    assert isinstance(result.get("blocked_allergens"), list)


@pytest.mark.asyncio
async def test_generate_recipes_blocks_allergens(ai_service):
    """Запрос с аллергеном не уходит в модель, а возвращает причину отказа."""
    result = await ai_service.generate_recipes(
        ["творог", "молоко", "грецкие орехи"],
        allergies=["молоко", "орехи"],
    )

    assert result["recipes"] == []
    assert "молоко" in result["blocked_allergens"]


@pytest.mark.asyncio
async def test_calculate_daily_calories(ai_service):
    """Тест расчёта калорий"""
    result = await ai_service.calculate_daily_calories(
        weight=70,
        height=175,
        age=30,
        gender="male",
        activity_level="moderate",
        goal="maintain"
    )

    assert "calories" in result
    assert "protein" in result
    assert "fats" in result
    assert "carbs" in result
    assert result["calories"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("gender,floor", [("female", 1200), ("male", 1500)])
async def test_calories_never_below_safe_minimum(ai_service, gender, floor):
    """Норма не опускается ниже безопасного минимума даже у худых пользователей."""
    result = await ai_service.calculate_daily_calories(
        weight=42, height=150, age=25,
        gender=gender, activity_level="sedentary", goal="lose_weight"
    )

    assert result["calories"] >= floor
    assert "min_calories" in result["warnings"]


@pytest.mark.asyncio
async def test_macros_stay_positive_and_balanced(ai_service):
    """Углеводы не уходят в минус, а белок не занимает больше трети калорий."""
    result = await ai_service.calculate_daily_calories(
        weight=150, height=160, age=45,
        gender="male", activity_level="sedentary", goal="lose_weight"
    )

    assert result["carbs"] >= 0
    assert result["protein"] * 4 <= result["calories"] * 0.36


def test_extract_json_handles_markdown_fence():
    """JSON достаётся и из markdown-обёртки, которой модель часто оборачивает ответ."""
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json("Извините, не могу составить рецепт.") is None


def test_normalize_nutrition_fills_missing_fields():
    """Позиция без amount не ломает вывод, а total пересчитывается по позициям."""
    normalized = AIService._normalize_nutrition({
        "items": [
            {"name": "Банан", "calories": 105, "protein": 1.3, "fats": 0.4, "carbs": 27},
            {"name": "Кофе", "amount": "200 мл", "calories": 5},
        ]
    })

    assert normalized["items"][0]["amount"] == "порция"
    assert normalized["total"]["calories"] == 110
    assert normalized["total"]["protein"] == 1.3
