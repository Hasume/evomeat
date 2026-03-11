"""
Тесты для AI сервиса
"""
import pytest
from services.ai_service import AIService


@pytest.fixture
def ai_service():
    """Фикстура для AI сервиса"""
    return AIService()


@pytest.mark.asyncio
async def test_generate_recipes(ai_service):
    """Тест генерации рецептов"""
    ingredients = ["курица", "рис", "помидоры"]
    recipes = await ai_service.generate_recipes(ingredients)
    
    assert isinstance(recipes, list)
    # В реальных условиях должны получить рецепты
    # Для теста можем проверить структуру


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

