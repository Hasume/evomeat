"""
Сервис для работы с OpenFoodFacts API
"""
import aiohttp
from typing import Dict, Optional, List
from config import settings
import logging

logger = logging.getLogger(__name__)


class NutritionAPI:
    """Клиент для работы с OpenFoodFacts API"""
    
    BASE_URL = "https://world.openfoodfacts.org/api/v2"
    
    def __init__(self):
        self.headers = {
            "User-Agent": settings.OPENFOODFACTS_USER_AGENT
        }
    
    async def search_product(self, query: str) -> Optional[Dict]:
        """Поиск продукта по названию"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.BASE_URL}/search"
                params = {
                    "search_terms": query,
                    "page_size": 5,
                    "fields": "product_name,nutriments,brands,quantity,image_url"
                }
                
                async with session.get(url, params=params, headers=self.headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("products", [])
                    else:
                        logger.error(f"Error searching product: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Exception in search_product: {e}")
            return None
    
    async def get_product_by_barcode(self, barcode: str) -> Optional[Dict]:
        """Получить информацию о продукте по штрих-коду"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.BASE_URL}/product/{barcode}"
                
                async with session.get(url, headers=self.headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("status") == 1:
                            return data.get("product")
                    return None
        except Exception as e:
            logger.error(f"Exception in get_product_by_barcode: {e}")
            return None
    
    def extract_nutrition(self, product: Dict) -> Dict:
        """Извлечь пищевую ценность из данных продукта"""
        nutriments = product.get("nutriments", {})
        
        return {
            "name": product.get("product_name", "Неизвестный продукт"),
            "calories": nutriments.get("energy-kcal_100g", 0),
            "protein": nutriments.get("proteins_100g", 0),
            "fats": nutriments.get("fat_100g", 0),
            "carbs": nutriments.get("carbohydrates_100g", 0),
            "brand": product.get("brands", ""),
            "quantity": product.get("quantity", ""),
            "image_url": product.get("image_url", "")
        }

