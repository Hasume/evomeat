"""
Сервис для работы с OpenAI API (подбор рецептов)
"""
from openai import AsyncOpenAI
from typing import List, Dict, Optional
from config import settings
from prompts import get_prompt
import json
import logging
import re

logger = logging.getLogger(__name__)


class AIService:
    """Сервис для работы с AI (генерация рецептов, рекомендации)"""
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    
    async def detect_intent(self, user_message: str) -> Dict:
        """
        Определить намерение пользователя из сообщения
        
        Returns:
            Dict с intent, confidence, entities
        """
        system_prompt = """Ты - анализатор намерений пользователя в боте питания.

Твоя задача: определить, что хочет пользователь.

ВОЗМОЖНЫЕ НАМЕРЕНИЯ (intent):
1. "general_question" - общий вопрос о боте, возможностях, как пользоваться
2. "recipe_search" - поиск рецептов по ингредиентам
3. "calorie_count" - подсчет калорий/БЖУ продукта или блюда
4. "nutrition_advice" - вопрос о питании, диете, здоровье (НО НЕ когда просто сообщает о своих ограничениях!)
5. "diary" - вопросы о дневнике питания
6. "statistics" - запрос статистики, итогов дня. Примеры: "статистика", "итоги", "как я сегодня?", "дашборд", "сколько съел", "мои результаты"
7. "meal_plan" - запрос составить план питания
8. "greeting" - приветствие
9. "save_dietary_info" - ВАЖНО! Когда пользователь СООБЩАЕТ о своих аллергиях, диете, непереносимости, предпочтениях. 
   Примеры: "У меня аллергия на молоко", "Я вегетарианец", "Не ем глютен", "Не люблю рыбу"
10. "small_talk" - обычный разговор, не связанный с питанием

ВАЖНО: Различай "nutrition_advice" (вопрос/просьба совета) и "save_dietary_info" (утверждение о себе).
Если пользователь УТВЕРЖДАЕТ что-то о себе (я..., у меня..., не ем...) - это "save_dietary_info"!
Если пользователь СПРАШИВАЕТ (как?, что делать?, вредно ли?) - это "nutrition_advice"!
"статистика", "итоги", "как я сегодня" - это ВСЕГДА "statistics"!

ФОРМАТ ОТВЕТА (строго JSON):
{
  "intent": "название_намерения",
  "confidence": 0.0-1.0,
  "entities": {
    "ingredients": ["список", "ингредиентов"],  // если есть
    "food_items": ["продукты"],  // если есть
    "allergies": ["аллергии"],  // если есть
    "dietary_preferences": ["вегетарианство", "веганство"],  // если есть
    "goal": "цель пользователя",  // если есть
    "question": "суть вопроса"  // если есть
  }
}"""

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",  # быстрая модель для определения intent
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.3,
                max_tokens=200
            )
            
            result = json.loads(response.choices[0].message.content)
            logger.info(f"Detected intent: {result.get('intent')} (confidence: {result.get('confidence')})")
            return result
            
        except Exception as e:
            logger.error(f"Error detecting intent: {e}")
            # Fallback: базовое определение
            message_lower = user_message.lower()
            if any(word in message_lower for word in ['привет', 'здравствуй', 'добрый', 'hey', 'hi']):
                return {"intent": "greeting", "confidence": 0.8, "entities": {}}
            elif any(word in message_lower for word in ['статистик', 'итоги', 'как я сегодня', 'дашборд', 'сколько съел']):
                return {"intent": "statistics", "confidence": 0.9, "entities": {}}
            elif any(word in message_lower for word in ['рецепт', 'приготовить', 'сделать', 'блюдо', 'recipe']):
                return {"intent": "recipe_search", "confidence": 0.7, "entities": {}}
            elif any(word in message_lower for word in ['калори', 'бжу', 'белк', 'жир', 'углевод']):
                return {"intent": "calorie_count", "confidence": 0.7, "entities": {}}
            else:
                return {"intent": "general_question", "confidence": 0.5, "entities": {}}
    
    async def answer_general_question(self, question: str, context: Optional[Dict] = None) -> str:
        """
        Ответить на общий вопрос пользователя о боте/питании
        """
        from prompts import get_prompt
        
        system_prompt = get_prompt("nutrition_qa")
        
        context_info = ""
        if context:
            context_info = f"\n\nКонтекст: {json.dumps(context, ensure_ascii=False)}"
        
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question + context_info}
                ],
                temperature=0.7,
                max_tokens=500
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Error answering question: {e}")
            return "Извините, у меня возникла проблема с обработкой вашего вопроса. Попробуйте переформулировать или используйте команды /help"
    
    async def generate_recipes(
        self, 
        ingredients: List[str],
        dietary_preferences: Optional[List[str]] = None,
        allergies: Optional[List[str]] = None,
        cuisine_type: Optional[str] = None
    ) -> List[Dict]:
        """Генерация рецептов по списку ингредиентов"""
        
        # Формируем ограничения
        restrictions = []
        if dietary_preferences:
            restrictions.append(f"Диетические предпочтения: {', '.join(dietary_preferences)}")
        if allergies:
            restrictions.append(f"АЛЛЕРГИИ (НЕ использовать): {', '.join(allergies)}")
        if cuisine_type:
            restrictions.append(f"Предпочтительная кухня: {cuisine_type}")
        
        restrictions_text = "\n".join(restrictions) if restrictions else "Нет особых ограничений"
        
        # Получаем промпт из модуля
        prompt = get_prompt(
            "recipe_generation",
            count=3,
            ingredients=", ".join(ingredients),
            restrictions=restrictions_text
        )
        
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": get_prompt("main")},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=2500
            )
            
            content = response.choices[0].message.content
            
            # Парсим JSON
            try:
                recipes_data = json.loads(content)
                return recipes_data.get("recipes", [])
            except json.JSONDecodeError:
                logger.error("Failed to parse JSON from AI response")
                return []
                
        except Exception as e:
            logger.error(f"Error generating recipes: {e}")
            return []
    
    async def generate_weekly_plan(
        self,
        target_calories: int,
        goal: str = "maintain",
        dietary_preferences: Optional[List[str]] = None,
        allergies: Optional[List[str]] = None
    ) -> Dict:
        """Генерация недельного плана питания"""
        
        # Формируем ограничения и предпочтения
        restrictions = []
        if allergies:
            restrictions.append(f"Аллергии: {', '.join(allergies)}")
        if dietary_preferences:
            restrictions.append(f"Диета: {', '.join(dietary_preferences)}")
        
        restrictions_text = ", ".join(restrictions) if restrictions else "Нет"
        preferences_text = ", ".join(dietary_preferences) if dietary_preferences else "Любые"
        
        # Получаем промпт
        prompt = get_prompt(
            "weekly_plan",
            target_calories=target_calories,
            goal=goal,
            restrictions=restrictions_text,
            preferences=preferences_text
        )
        
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": get_prompt("main")},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=4000
            )
            
            content = response.choices[0].message.content
            
            try:
                plan_data = json.loads(content)
                return plan_data
            except json.JSONDecodeError:
                logger.error("Failed to parse JSON from AI response")
                return {}
                
        except Exception as e:
            logger.error(f"Error generating weekly plan: {e}")
            return {}
    
    async def calculate_nutrition_from_text(self, text: str) -> Dict:
        """
        Рассчитать калории и БЖУ из текстового описания продуктов
        """
        system_prompt = get_prompt("calorie_calculation")

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0.3,
                max_tokens=800
            )
            
            content = response.choices[0].message.content
            
            # Извлекаем JSON из ответа
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return result
            else:
                return json.loads(content)
                
        except Exception as e:
            logger.error(f"Error calculating nutrition from text: {e}")
            return {}
    
    async def generate_response(self, prompt: str, user_id: int = None) -> str:
        """
        Универсальный метод для генерации текстового ответа AI.
        Используется для планов питания, оценки по фото и др.
        """
        system_prompt = get_prompt("main")
        
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=1000
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Error in generate_response: {e}")
            return "Извините, произошла ошибка. Попробуйте ещё раз."
    
    async def calculate_daily_calories(
        self,
        weight: float,
        height: float,
        age: int,
        gender: str,
        activity_level: str,
        goal: str
    ) -> Dict:
        """Расчёт дневной нормы калорий и БЖУ"""
        
        # Базовый метаболизм (формула Миффлина-Сан Жеора)
        if gender.lower() == "male":
            bmr = 10 * weight + 6.25 * height - 5 * age + 5
        else:
            bmr = 10 * weight + 6.25 * height - 5 * age - 161
        
        # Коэффициенты активности
        activity_coefficients = {
            "sedentary": 1.2,
            "light": 1.375,
            "moderate": 1.55,
            "active": 1.725,
            "very_active": 1.9
        }
        
        coefficient = activity_coefficients.get(activity_level, 1.2)
        tdee = bmr * coefficient
        
        # Корректировка в зависимости от цели
        if goal == "lose_weight":
            target_calories = tdee - 500
        elif goal == "gain_weight":
            target_calories = tdee + 500
        else:  # maintain
            target_calories = tdee
        
        # Расчёт БЖУ (примерное распределение)
        protein = weight * 2  # 2г на кг веса
        fats = target_calories * 0.25 / 9  # 25% от калорий, 1г жира = 9 ккал
        carbs = (target_calories - (protein * 4 + fats * 9)) / 4
        
        return {
            "calories": int(target_calories),
            "protein": round(protein, 1),
            "fats": round(fats, 1),
            "carbs": round(carbs, 1),
            "bmr": round(bmr, 1),
            "tdee": round(tdee, 1)
        }

