"""
Модели базы данных
"""
from sqlalchemy import (
    Column, Integer, BigInteger, String, DateTime, Boolean, 
    Float, ForeignKey, Text, JSON, Enum as SQLEnum, Date
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime, date
import enum


Base = declarative_base()


class SubscriptionType(enum.Enum):
    """Типы подписок"""
    FREE = "free"
    WEEKLY_PLAN = "weekly_plan"
    UNLIMITED = "unlimited"
    PERSONALIZATION = "personalization"


class DataSource(enum.Enum):
    """Источник данных о питании"""
    MANUAL = "manual"          # Ввод вручную (пользователь указал КБЖУ сам)
    DATABASE = "database"      # Из базы данных продуктов
    AI_ESTIMATE = "ai_estimate"  # Оценка AI


class User(Base):
    """Модель пользователя"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    
    # Подписка
    subscription_type = Column(
        SQLEnum(SubscriptionType), 
        default=SubscriptionType.FREE
    )
    subscription_expires_at = Column(DateTime, nullable=True)
    
    # Персонализация
    age = Column(Integer, nullable=True)
    weight = Column(Float, nullable=True)
    height = Column(Float, nullable=True)
    gender = Column(String, nullable=True)
    activity_level = Column(String, nullable=True)
    goal = Column(String, nullable=True)  # похудение, набор массы, поддержание
    
    # Ограничения питания
    allergies = Column(JSON, nullable=True)  # список аллергий
    dietary_preferences = Column(JSON, nullable=True)  # вегетарианство и т.д.
    
    # Целевые показатели
    target_calories = Column(Integer, nullable=True)
    target_protein = Column(Float, nullable=True)
    target_fats = Column(Float, nullable=True)
    target_carbs = Column(Float, nullable=True)
    
    # Настройки напоминаний
    reminders_enabled = Column(Boolean, default=True)  # Включены ли напоминания
    max_reminders_per_day = Column(Integer, default=2)  # Макс. напоминаний в день
    last_reminder_date = Column(Date, nullable=True)  # Дата последнего напоминания
    reminders_sent_today = Column(Integer, default=0)  # Отправлено напоминаний сегодня
    last_reminder_type = Column(String, nullable=True)  # Тип последнего напоминания
    timezone = Column(String, default="Europe/Moscow")  # Часовой пояс пользователя
    
    # Настройки отчётов
    daily_report_enabled = Column(Boolean, default=True)  # Вечерний отчёт
    daily_report_time = Column(String, default="21:00")  # Время отчёта (HH:MM)
    last_daily_report_date = Column(Date, nullable=True)  # Дата последнего отчёта
    weekly_report_enabled = Column(Boolean, default=True)  # Еженедельный отчёт
    weekly_report_day = Column(Integer, default=6)  # День недели (0=пн, 6=вс)
    last_weekly_report_date = Column(Date, nullable=True)  # Дата последнего еженедельного
    
    # Взвешивания
    weigh_reminder_enabled = Column(Boolean, default=True)  # Напоминания взвеситься
    last_weigh_reminder_date = Column(Date, nullable=True)  # Последнее напоминание
    
    # Активный челлендж
    active_challenge = Column(JSON, nullable=True)  # {type, start_date, end_date, progress}
    
    # Утренний режим
    morning_checkin_enabled = Column(Boolean, default=False)  # Утренний чек-ин
    morning_checkin_time = Column(String, default="08:00")  # Время чек-ина
    today_plan_mode = Column(String, nullable=True)  # "strict" / "relaxed" / None
    
    # Метаданные
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    # Связи
    diary_entries = relationship("DiaryEntry", back_populates="user", cascade="all, delete-orphan")
    recipes = relationship("Recipe", back_populates="user", cascade="all, delete-orphan")
    weekly_plans = relationship("WeeklyPlan", back_populates="user", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    weight_logs = relationship("WeightLog", back_populates="user", cascade="all, delete-orphan")
    challenges = relationship("Challenge", back_populates="user", cascade="all, delete-orphan")


class DiaryEntry(Base):
    """Запись в дневнике питания"""
    __tablename__ = "diary_entries"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Время приёма пищи
    date = Column(DateTime, nullable=False, index=True)
    meal_type = Column(String, nullable=False)  # завтрак, обед, ужин, перекус
    
    # Блюдо
    food_name = Column(String, nullable=False)
    portion_size = Column(Float, nullable=False)  # в граммах
    
    # Пищевая ценность
    calories = Column(Float, nullable=False)
    protein = Column(Float, nullable=False)
    fats = Column(Float, nullable=False)
    carbs = Column(Float, nullable=False)
    
    # Источник данных
    data_source = Column(
        SQLEnum(DataSource), 
        default=DataSource.AI_ESTIMATE,
        nullable=False
    )
    
    # Дополнительно
    notes = Column(Text, nullable=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id"), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    user = relationship("User", back_populates="diary_entries")
    recipe = relationship("Recipe")


class Recipe(Base):
    """Рецепт"""
    __tablename__ = "recipes"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    # Основная информация
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    cuisine_type = Column(String, nullable=True)
    difficulty = Column(String, nullable=True)
    
    # Ингредиенты и приготовление
    ingredients = Column(JSON, nullable=False)  # список ингредиентов с количеством
    instructions = Column(Text, nullable=False)
    cooking_time = Column(Integer, nullable=True)  # в минутах
    servings = Column(Integer, default=1)
    
    # Пищевая ценность на порцию
    calories = Column(Float, nullable=False)
    protein = Column(Float, nullable=False)
    fats = Column(Float, nullable=False)
    carbs = Column(Float, nullable=False)
    
    # Метаданные
    image_url = Column(String, nullable=True)
    source = Column(String, nullable=True)  # API, user-generated
    tags = Column(JSON, nullable=True)
    is_public = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    user = relationship("User", back_populates="recipes")


class WeeklyPlan(Base):
    """Недельный план питания"""
    __tablename__ = "weekly_plans"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # План
    week_start = Column(DateTime, nullable=False)
    plan_data = Column(JSON, nullable=False)  # структура с рецептами на каждый день
    
    # Целевые показатели для плана
    target_calories = Column(Integer, nullable=True)
    target_protein = Column(Float, nullable=True)
    target_fats = Column(Float, nullable=True)
    target_carbs = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    # Связи
    user = relationship("User", back_populates="weekly_plans")


class WeightLog(Base):
    """Лог взвешиваний пользователя"""
    __tablename__ = "weight_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    weight = Column(Float, nullable=False)  # Вес в кг
    date = Column(Date, nullable=False, index=True)
    notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    user = relationship("User", back_populates="weight_logs")


class Challenge(Base):
    """Челленджи пользователей"""
    __tablename__ = "challenges"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Тип челленджа
    challenge_type = Column(String, nullable=False)  # no_sweets_evening, protein_week, etc.
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    
    # Период
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    duration_days = Column(Integer, nullable=False)
    
    # Прогресс
    days_completed = Column(Integer, default=0)
    days_failed = Column(Integer, default=0)
    status = Column(String, default="active")  # active, completed, failed, cancelled
    
    # Результат
    result = Column(String, nullable=True)  # success, partial, failed
    
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    # Связи
    user = relationship("User", back_populates="challenges")


class Payment(Base):
    """История платежей"""
    __tablename__ = "payments"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Платёж
    amount = Column(Integer, nullable=False)  # в копейках
    currency = Column(String, default="RUB")
    subscription_type = Column(SQLEnum(SubscriptionType), nullable=False)
    
    # Статус
    status = Column(String, nullable=False)  # pending, completed, failed, refunded
    payment_provider = Column(String, nullable=False)  # stripe, telegram
    transaction_id = Column(String, unique=True, nullable=True)
    
    # Метаданные
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    payment_metadata = Column(JSON, nullable=True)
    
    # Связи
    user = relationship("User", back_populates="payments")

