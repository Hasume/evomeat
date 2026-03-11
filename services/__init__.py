"""
Модуль сервисов
"""
from .nutrition_api import NutritionAPI
from .ai_service import AIService
from .statistics_service import (
    StatisticsService, 
    statistics_service,
    evaluate_nutrient,
    get_snack_recommendations,
    format_snack_recommendations,
    NutrientStatus,
    get_status_emoji
)
from .reminder_service import (
    ReminderService,
    reminder_service,
    ReminderType
)
from .scheduler_service import (
    SchedulerService,
    scheduler_service
)
from .report_service import (
    ReportService,
    report_service
)
from .challenge_service import (
    ChallengeService,
    challenge_service,
    CHALLENGE_TYPES
)

__all__ = [
    "NutritionAPI", 
    "AIService",
    "StatisticsService",
    "statistics_service",
    "evaluate_nutrient",
    "get_snack_recommendations",
    "format_snack_recommendations",
    "NutrientStatus",
    "get_status_emoji",
    "ReminderService",
    "reminder_service",
    "ReminderType",
    "SchedulerService",
    "scheduler_service",
    "ReportService",
    "report_service",
    "ChallengeService",
    "challenge_service",
    "CHALLENGE_TYPES",
]

