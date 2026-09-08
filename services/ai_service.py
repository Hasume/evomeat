"""
Сервис для работы с OpenAI API (подбор рецептов)
"""
from openai import AsyncOpenAI
from typing import List, Dict, Optional
from config import settings
from prompts import get_prompt
from utils.allergens import ALLERGEN_SOURCES
import json
import logging
import re

logger = logging.getLogger(__name__)


def _num(value) -> float:
    """Привести значение к числу: модель может вернуть строку или null."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    return float(match.group().replace(",", ".")) if match else 0.0


class AIServiceUnavailable(Exception):
    """
    Внешний сервис недоступен (нет ключа, квота, сеть, таймаут).

    Отделяет поломку инфраструктуры от «модель не поняла запрос»: раньше оба
    случая давали пользователю совет «уточните формулировку», и при отозванном
    ключе люди бесконечно переписывали корректные сообщения.
    """


def extract_json(content: str) -> Optional[dict]:
    """
    Достать JSON-объект из ответа модели.

    Модель может вернуть JSON в markdown-обёртке или дописать текст вокруг,
    а при отказе выполнить запрос — ответить обычной прозой.
    """
    if not content:
        return None

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    braced = re.search(r"\{.*\}", content, re.DOTALL)
    if braced:
        try:
            return json.loads(braced.group())
        except json.JSONDecodeError:
            pass

    return None


class AIService:
    """Сервис для работы с AI (генерация рецептов, рекомендации)"""

    # Модель с поддержкой строгого JSON и анализа изображений.
    MODEL_FAST = "gpt-4o-mini"
    MODEL_SMART = "gpt-4o"

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=60.0)

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
                model=self.MODEL_FAST,  # быстрая модель для определения intent
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.3,
                max_tokens=300,
                response_format={"type": "json_object"}
            )

            result = extract_json(response.choices[0].message.content)
            if result:
                logger.info(f"Detected intent: {result.get('intent')} (confidence: {result.get('confidence')})")
                return result
            logger.warning("Intent response was not valid JSON, falling back to keywords")

        except Exception as e:
            logger.error(f"Error detecting intent: {e}")

        # Запасное определение по ключевым словам: intent нужен только для
        # маршрутизации, поэтому здесь деградация допустима и незаметна.
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
                model=self.MODEL_FAST,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question + context_info}
                ],
                temperature=0.7,
                max_tokens=700
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"Error answering question: {e}")
            raise AIServiceUnavailable(str(e)) from e
    
    def find_conflicting_allergens(
        self,
        ingredients: List[str],
        allergies: Optional[List[str]]
    ) -> List[str]:
        """
        Найти аллергены пользователя среди запрошенных ингредиентов.

        Проверка выполняется до обращения к модели: если человек просит рецепт
        из того, что ему нельзя, ответить об этом нужно прямо, а не отдавать
        решение модели и терять её предупреждение при разборе ответа.
        """
        if not allergies:
            return []

        found = []
        text = " ".join(ingredients).lower()
        for allergen in allergies:
            token = str(allergen).lower().strip()
            if not token:
                continue
            # Сопоставляем по корню слова: «орехи» должно находить «грецкие орехи».
            root = token[:-1] if len(token) > 4 else token
            related = ALLERGEN_SOURCES.get(root, [])
            if root in text or any(word in text for word in related):
                found.append(allergen)
        return found

    async def generate_recipes(
        self,
        ingredients: List[str],
        dietary_preferences: Optional[List[str]] = None,
        allergies: Optional[List[str]] = None,
        cuisine_type: Optional[str] = None
    ) -> Dict:
        """
        Генерация рецептов по списку ингредиентов.

        Возвращает {"recipes": [...], "blocked_allergens": [...]} — вызывающий
        код должен различать «рецептов нет» и «рецепты нельзя из-за аллергии».
        """
        blocked = self.find_conflicting_allergens(ingredients, allergies)
        if blocked:
            logger.info(f"Recipe request blocked by allergens: {blocked}")
            return {"recipes": [], "blocked_allergens": blocked}

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
                model=self.MODEL_SMART,
                messages=[
                    {"role": "system", "content": get_prompt("main")},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=2500,
                response_format={"type": "json_object"}
            )
        except Exception as e:
            logger.error(f"Error generating recipes: {e}")
            raise AIServiceUnavailable(str(e)) from e

        content = response.choices[0].message.content
        recipes_data = extract_json(content)

        if not recipes_data:
            logger.error(f"Failed to parse JSON from AI response: {content[:200]!r}")
            return {"recipes": [], "blocked_allergens": []}

        recipes = [r for r in recipes_data.get("recipes", []) if self._is_valid_recipe(r)]
        return {"recipes": recipes, "blocked_allergens": []}

    @staticmethod
    def _is_valid_recipe(recipe) -> bool:
        """Отсеять рецепты без полей, на которые опирается отправка сообщения."""
        if not isinstance(recipe, dict):
            return False
        if not recipe.get("name") or not recipe.get("instructions"):
            return False
        nutrition = recipe.get("nutrition")
        if not isinstance(nutrition, dict) or "calories" not in nutrition:
            return False
        return isinstance(recipe.get("ingredients"), list)
    
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
        Рассчитать калории и БЖУ из текстового описания продуктов.

        Всегда возвращает структуру с ключами items/total; при недоступности
        сервиса поднимает AIServiceUnavailable, чтобы бот не выдавал сбой
        инфраструктуры за ошибку пользователя.
        """
        system_prompt = get_prompt("calorie_calculation")

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL_FAST,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                temperature=0.2,
                max_tokens=900,
                response_format={"type": "json_object"}
            )
        except Exception as e:
            logger.error(f"Error calculating nutrition from text: {e}")
            raise AIServiceUnavailable(str(e)) from e

        result = extract_json(response.choices[0].message.content)
        if not result:
            logger.error("Nutrition response was not valid JSON")
            return {}

        return self._normalize_nutrition(result)

    @staticmethod
    def _normalize_nutrition(result: Dict) -> Dict:
        """
        Привести ответ модели к предсказуемому виду.

        Гарантирует наличие amount у каждой позиции и пересчитывает total по
        позициям — раньше форматирование обращалось к item['amount'] напрямую и
        падало, если модель не вернула это поле.
        """
        items = []
        for raw in result.get("items") or []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            items.append({
                "name": str(raw.get("name")),
                "amount": str(raw.get("amount") or "порция"),
                "cooking_method": str(raw.get("cooking_method") or "готовое"),
                "calories": _num(raw.get("calories")),
                "protein": _num(raw.get("protein")),
                "fats": _num(raw.get("fats")),
                "carbs": _num(raw.get("carbs")),
            })

        total = {
            key: round(sum(item[key] for item in items), 1)
            for key in ("calories", "protein", "fats", "carbs")
        }

        meal_type = result.get("meal_type")
        if meal_type not in ("завтрак", "обед", "ужин", "перекус"):
            meal_type = None
        try:
            day_offset = int(result.get("day_offset") or 0)
        except (TypeError, ValueError):
            day_offset = 0
        if day_offset > 0 or day_offset < -7:
            day_offset = 0

        return {
            # Запрос на уточнение сохраняем как есть: модель может распознать
            # часть позиций и всё равно нуждаться в способе приготовления для
            # остальных — гасить вопрос нельзя, иначе продукт потеряется.
            "needs_clarification": bool(result.get("needs_clarification")),
            "clarification_question": result.get("clarification_question"),
            "items": items,
            "total": total,
            # Подсказки о времени приёма пищи — запасной путь, основной разбор
            # делает utils.meal_context по словам самого сообщения.
            "meal_type": meal_type,
            "day_offset": day_offset,
        }

    # Модели распознавания речи в порядке предпочтения: первая точнее и
    # дешевле на русском, вторая — запасная.
    TRANSCRIBE_MODELS = ("gpt-4o-mini-transcribe", "whisper-1")

    async def transcribe_audio(self, audio_bytes: bytes, filename: str = "voice.ogg") -> str:
        """
        Распознать голосовое сообщение.

        Telegram присылает голос в OGG/Opus; API принимает его напрямую. Если
        формат всё же отклонён и в системе есть ffmpeg, перекодируем в mp3 и
        повторяем. Пустая строка означает, что речи в записи не нашлось.
        """
        import io

        hint = (
            "Голосовая заметка о еде на русском языке: названия продуктов и блюд, "
            "вес в граммах, объём в миллилитрах, «съел», «выпил», «на завтрак», «вчера»."
        )

        payload = audio_bytes
        name = filename
        converted = False
        last_error: Optional[Exception] = None

        for attempt in range(2):
            for model in self.TRANSCRIBE_MODELS:
                try:
                    stream = io.BytesIO(payload)
                    stream.name = name
                    response = await self.client.audio.transcriptions.create(
                        model=model,
                        file=stream,
                        language="ru",
                        prompt=hint,
                    )
                    return (getattr(response, "text", "") or "").strip()
                except Exception as e:  # noqa: BLE001 — пробуем следующую модель
                    last_error = e
                    logger.warning(f"Transcription with {model} failed: {e}")

            if converted:
                break
            payload_mp3 = await self._convert_to_mp3(payload)
            if not payload_mp3:
                break
            payload, name, converted = payload_mp3, "voice.mp3", True

        raise AIServiceUnavailable(str(last_error) if last_error else "transcription failed")

    @staticmethod
    async def _convert_to_mp3(audio_bytes: bytes) -> Optional[bytes]:
        """Перекодировать через ffmpeg, если он установлен; иначе None."""
        import asyncio
        import shutil
        import subprocess

        if not shutil.which("ffmpeg"):
            return None

        def run() -> Optional[bytes]:
            result = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-f", "mp3", "-ac", "1", "-ar", "16000", "pipe:1"],
                input=audio_bytes, capture_output=True, timeout=60,
            )
            return result.stdout if result.returncode == 0 and result.stdout else None

        try:
            return await asyncio.to_thread(run)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ffmpeg conversion failed: {e}")
            return None

    async def analyze_food_photo(self, image_bytes: bytes, caption: str = "") -> Dict:
        """
        Оценить блюдо по фотографии.

        Изображение действительно отправляется в модель: прежняя версия
        скачивала файл и оценивала только подпись к нему.
        """
        import base64

        encoded = base64.b64encode(image_bytes).decode("utf-8")
        hint = f"\n\nПодпись пользователя: {caption}" if caption else ""

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL_SMART,
                messages=[
                    {"role": "system", "content": get_prompt("image_analysis")},
                    {"role": "user", "content": [
                        {"type": "text", "text":
                            "Определи блюдо на фото, оцени вес порции и посчитай КБЖУ. "
                            "Ответ строго в JSON по заданной схеме." + hint},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                    ]},
                ],
                temperature=0.2,
                max_tokens=900,
                response_format={"type": "json_object"}
            )
        except Exception as e:
            logger.error(f"Error analyzing food photo: {e}")
            raise AIServiceUnavailable(str(e)) from e

        result = extract_json(response.choices[0].message.content)
        if not result:
            logger.error("Photo analysis response was not valid JSON")
            return {}

        normalized = self._normalize_nutrition(result)
        normalized["dish_name"] = result.get("dish_name") or caption or "Блюдо с фото"
        normalized["confidence"] = result.get("confidence") or "средняя"
        normalized["advice"] = result.get("advice") or ""
        return normalized
    
    async def generate_response(self, prompt: str, user_id: int = None) -> str:
        """
        Универсальный метод для генерации текстового ответа AI.
        Используется для планов питания, оценки по фото и др.
        """
        system_prompt = get_prompt("main")

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL_FAST,
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
            raise AIServiceUnavailable(str(e)) from e
    
    # Нижние границы суточной калорийности, ниже которых рацион считается
    # небезопасным без наблюдения врача.
    MIN_CALORIES = {"male": 1500, "female": 1200}

    # Доля TDEE, на которую максимально изменяем калораж под цель.
    MAX_DEFICIT_SHARE = 0.20
    MAX_SURPLUS_SHARE = 0.15

    async def calculate_daily_calories(
        self,
        weight: float,
        height: float,
        age: int,
        gender: str,
        activity_level: str,
        goal: str
    ) -> Dict:
        """
        Расчёт дневной нормы калорий и БЖУ.

        Дополнительно возвращает `warnings` — список предупреждений, которые
        бот обязан показать пользователю (например, что расчёт упёрся в
        безопасный минимум).
        """
        warnings: List[str] = []

        # Базовый метаболизм (формула Миффлина-Сан Жеора)
        if gender.lower() == "male":
            bmr = 10 * weight + 6.25 * height - 5 * age + 5
        else:
            bmr = 10 * weight + 6.25 * height - 5 * age - 161

        activity_coefficients = {
            "sedentary": 1.2,
            "light": 1.375,
            "moderate": 1.55,
            "active": 1.725,
            "very_active": 1.9
        }

        coefficient = activity_coefficients.get(activity_level, 1.2)
        tdee = bmr * coefficient

        # Дефицит и профицит — доля от TDEE, а не фиксированные 500 ккал:
        # для человека с небольшим весом «минус 500» съедает почти треть нормы.
        if goal == "lose_weight":
            target_calories = tdee * (1 - self.MAX_DEFICIT_SHARE)
        elif goal == "gain_weight":
            target_calories = tdee * (1 + self.MAX_SURPLUS_SHARE)
        else:
            target_calories = tdee

        # Нижняя граница безопасности.
        floor = self.MIN_CALORIES.get(gender.lower(), 1200)
        if target_calories < floor:
            target_calories = floor
            warnings.append("min_calories")
        if goal == "lose_weight" and tdee < floor:
            # Поддержание уже ниже безопасного минимума — худеть питанием нельзя.
            warnings.append("deficit_not_advised")

        # Белок считаем от «расчётного» веса: при лишнем весе 2 г/кг фактической
        # массы дают долю белка, несовместимую со сбалансированным рационом.
        reference_weight = min(weight, self._healthy_reference_weight(height))
        protein = reference_weight * 1.8

        # Белок не должен занимать больше трети калорийности.
        max_protein = target_calories * 0.35 / 4
        if protein > max_protein:
            protein = max_protein

        # Жиры: 25% калорий, но не ниже физиологического минимума 0.8 г/кг.
        fats = target_calories * 0.25 / 9
        min_fats = reference_weight * 0.8
        if fats < min_fats:
            fats = min_fats

        carbs = (target_calories - (protein * 4 + fats * 9)) / 4

        # Углеводы не могут быть отрицательными: ужимаем белок и жиры до
        # минимально допустимых значений, сохраняя калорийность.
        if carbs < 50:
            protein = min(protein, target_calories * 0.30 / 4)
            fats = min(fats, target_calories * 0.25 / 9)
            carbs = (target_calories - (protein * 4 + fats * 9)) / 4
            if carbs < 0:
                carbs = 0
                warnings.append("macros_adjusted")

        return {
            "calories": int(round(target_calories)),
            "protein": round(protein, 1),
            "fats": round(fats, 1),
            "carbs": round(carbs, 1),
            "bmr": round(bmr, 1),
            "tdee": round(tdee, 1),
            "warnings": warnings,
        }

    @staticmethod
    def _healthy_reference_weight(height: float) -> float:
        """Вес при ИМТ 25 для этого роста — верхняя граница для расчёта белка."""
        meters = max(height, 100) / 100
        return 25 * meters * meters

