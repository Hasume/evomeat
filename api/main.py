"""
FastAPI приложение для веб-хуков и административного API
"""
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db, User, DiaryEntry
from config import settings
from datetime import datetime, timedelta
import sentry_sdk
from prometheus_client import Counter, Histogram, generate_latest
from fastapi.responses import Response
import logging

# Инициализация Sentry
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=1.0,
    )

# Метрики Prometheus
requests_total = Counter('requests_total', 'Total requests', ['method', 'endpoint'])
request_duration = Histogram('request_duration_seconds', 'Request duration')

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Главная страница API"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Проверка здоровья приложения"""
    return {"status": "healthy"}


@app.get("/metrics")
async def metrics():
    """Метрики Prometheus"""
    return Response(content=generate_latest(), media_type="text/plain")


@app.get("/api/users/{telegram_id}")
async def get_user(telegram_id: int, db: AsyncSession = Depends(get_db)):
    """Получить информацию о пользователе"""
    result = await db.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "subscription_type": user.subscription_type.value,
        "target_calories": user.target_calories,
        "created_at": user.created_at.isoformat()
    }


@app.get("/api/users/{telegram_id}/diary")
async def get_user_diary(
    telegram_id: int,
    days: int = 7,
    db: AsyncSession = Depends(get_db)
):
    """Получить записи дневника пользователя"""
    # Получаем пользователя
    result = await db.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Получаем записи за последние N дней
    start_date = datetime.now() - timedelta(days=days)
    
    result = await db.execute(
        select(DiaryEntry)
        .where(DiaryEntry.user_id == user.id)
        .where(DiaryEntry.date >= start_date)
        .order_by(DiaryEntry.date.desc())
    )
    entries = result.scalars().all()
    
    return {
        "entries": [
            {
                "id": entry.id,
                "date": entry.date.isoformat(),
                "meal_type": entry.meal_type,
                "food_name": entry.food_name,
                "calories": entry.calories,
                "protein": entry.protein,
                "fats": entry.fats,
                "carbs": entry.carbs
            }
            for entry in entries
        ]
    }


@app.post("/api/webhook/stripe")
async def stripe_webhook(request: Request):
    """Webhook для обработки платежей Stripe"""
    import stripe
    
    stripe.api_key = settings.STRIPE_SECRET_KEY
    endpoint_secret = settings.STRIPE_WEBHOOK_SECRET
    
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    
    # Обработка событий
    if event['type'] == 'payment_intent.succeeded':
        payment_intent = event['data']['object']
        # Здесь логика обновления подписки пользователя
        logger.info(f"Payment succeeded: {payment_intent['id']}")
    
    return {"status": "success"}


@app.get("/api/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Общая статистика по боту"""
    # Количество пользователей
    result = await db.execute(select(User))
    total_users = len(result.scalars().all())
    
    # Количество записей в дневнике за последние 24 часа
    yesterday = datetime.now() - timedelta(days=1)
    result = await db.execute(
        select(DiaryEntry).where(DiaryEntry.created_at >= yesterday)
    )
    diary_entries_24h = len(result.scalars().all())
    
    return {
        "total_users": total_users,
        "diary_entries_last_24h": diary_entries_24h
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

