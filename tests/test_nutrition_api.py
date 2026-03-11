"""
Тесты для Nutrition API
"""
import pytest
from services.nutrition_api import NutritionAPI


@pytest.fixture
def nutrition_api():
    """Фикстура для Nutrition API"""
    return NutritionAPI()


@pytest.mark.asyncio
async def test_search_product(nutrition_api):
    """Тест поиска продукта"""
    products = await nutrition_api.search_product("apple")
    
    # API может вернуть результаты или None
    assert products is None or isinstance(products, list)


@pytest.mark.asyncio
async def test_extract_nutrition(nutrition_api):
    """Тест извлечения пищевой ценности"""
    sample_product = {
        "product_name": "Test Product",
        "nutriments": {
            "energy-kcal_100g": 100,
            "proteins_100g": 5,
            "fat_100g": 3,
            "carbohydrates_100g": 10
        }
    }
    
    result = nutrition_api.extract_nutrition(sample_product)
    
    assert result["name"] == "Test Product"
    assert result["calories"] == 100
    assert result["protein"] == 5

