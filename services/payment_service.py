"""
Сервис для работы с платежами (Stripe и Telegram Payments)
"""
import stripe
from telegram import LabeledPrice
from config import settings
from database.models import Payment, User, SubscriptionType
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


class PaymentService:
    """Сервис обработки платежей"""
    
    PRICES = {
        SubscriptionType.WEEKLY_PLAN: settings.PRICE_WEEKLY_PLAN,
        SubscriptionType.UNLIMITED: settings.PRICE_UNLIMITED,
        SubscriptionType.PERSONALIZATION: settings.PRICE_PERSONALIZATION,
    }
    
    DURATIONS = {
        SubscriptionType.WEEKLY_PLAN: timedelta(days=30),
        SubscriptionType.UNLIMITED: timedelta(days=30),
        SubscriptionType.PERSONALIZATION: timedelta(days=30),
    }
    
    @staticmethod
    async def create_stripe_payment(
        subscription_type: SubscriptionType,
        user_email: str
    ) -> dict:
        """Создать платёж через Stripe"""
        try:
            amount = PaymentService.PRICES[subscription_type]
            
            # Создаём Payment Intent
            payment_intent = stripe.PaymentIntent.create(
                amount=amount,
                currency="rub",
                receipt_email=user_email,
                metadata={
                    "subscription_type": subscription_type.value
                }
            )
            
            return {
                "client_secret": payment_intent.client_secret,
                "payment_intent_id": payment_intent.id
            }
        except Exception as e:
            logger.error(f"Error creating Stripe payment: {e}")
            return None
    
    @staticmethod
    def create_telegram_invoice(
        subscription_type: SubscriptionType,
        title: str,
        description: str
    ) -> tuple:
        """Создать счёт для Telegram Payments"""
        amount = PaymentService.PRICES[subscription_type]
        
        prices = [LabeledPrice(label=title, amount=amount)]
        
        payload = f"{subscription_type.value}"
        
        return prices, payload
    
    @staticmethod
    async def process_successful_payment(
        db: AsyncSession,
        user: User,
        subscription_type: SubscriptionType,
        transaction_id: str,
        payment_provider: str = "telegram"
    ):
        """Обработать успешный платёж"""
        try:
            # Создаём запись о платеже
            payment = Payment(
                user_id=user.id,
                amount=PaymentService.PRICES[subscription_type],
                currency="RUB",
                subscription_type=subscription_type,
                status="completed",
                payment_provider=payment_provider,
                transaction_id=transaction_id,
                completed_at=datetime.utcnow()
            )
            db.add(payment)
            
            # Обновляем подписку пользователя
            user.subscription_type = subscription_type
            
            # Устанавливаем дату окончания подписки
            duration = PaymentService.DURATIONS.get(subscription_type)
            if duration:
                if user.subscription_expires_at and user.subscription_expires_at > datetime.utcnow():
                    # Продлеваем существующую подписку
                    user.subscription_expires_at += duration
                else:
                    # Новая подписка
                    user.subscription_expires_at = datetime.utcnow() + duration
            
            await db.commit()
            
            logger.info(f"Payment processed successfully for user {user.telegram_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error processing payment: {e}")
            await db.rollback()
            return False
    
    @staticmethod
    async def check_subscription_status(user: User) -> bool:
        """Проверить активность подписки"""
        if user.subscription_type == SubscriptionType.FREE:
            return False
        
        if user.subscription_expires_at:
            return user.subscription_expires_at > datetime.utcnow()
        
        return False
    
    @staticmethod
    def get_subscription_info(subscription_type: SubscriptionType) -> dict:
        """Получить информацию о подписке"""
        info = {
            SubscriptionType.WEEKLY_PLAN: {
                "name": "Недельные планы",
                "price": settings.PRICE_WEEKLY_PLAN,
                "features": [
                    "Генерация планов питания на неделю",
                    "Список покупок",
                    "Рецепты с точным КБЖУ"
                ]
            },
            SubscriptionType.UNLIMITED: {
                "name": "Безлимит",
                "price": settings.PRICE_UNLIMITED,
                "features": [
                    "Неограниченные рецепты",
                    "Расширенная история дневника",
                    "Экспорт данных",
                    "Приоритетная поддержка"
                ]
            },
            SubscriptionType.PERSONALIZATION: {
                "name": "Персонализация",
                "price": settings.PRICE_PERSONALIZATION,
                "features": [
                    "Всё из Безлимит",
                    "Индивидуальный план питания",
                    "Рекомендации по БЖУ",
                    "Учёт аллергий и предпочтений",
                    "Консультации по питанию"
                ]
            }
        }
        
        return info.get(subscription_type, {})

