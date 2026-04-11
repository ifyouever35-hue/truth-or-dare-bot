"""
app/admin/routes/webhooks.py — Входящие webhook'и от платёжных систем.

WayForPay отправляет POST с JSON после каждой транзакции.
Мы проверяем HMAC-подпись и активируем подписку.
"""
import hashlib
import hmac
import logging
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import select

from app.config import settings
from app.database.models import Payment, User
from app.database.session import get_db_context
from app.services.user_service import activate_verified

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["webhooks"])


def _verify_wayforpay_signature(data: dict, secret: str) -> bool:
    """Проверяем HMAC-MD5 подпись от WayForPay."""
    sign_fields = [
        "merchantAccount", "orderReference", "amount",
        "currency", "authCode", "cardPan", "transactionStatus", "reasonCode",
    ]
    sign_string = ";".join(str(data.get(f, "")) for f in sign_fields)
    expected = hmac.new(
        secret.encode(), sign_string.encode(), hashlib.md5
    ).hexdigest()
    return hmac.compare_digest(expected, data.get("merchantSignature", ""))


@router.post("/wayforpay/callback")
async def wayforpay_callback(request: Request):
    """
    WayForPay callback.
    Документация: https://wiki.wayforpay.com/view/852091
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Проверяем подпись
    if settings.wayforpay_secret_key:
        if not _verify_wayforpay_signature(data, settings.wayforpay_secret_key):
            logger.warning("WayForPay: invalid signature for order %s", data.get("orderReference"))
            raise HTTPException(status_code=403, detail="Invalid signature")

    order_ref = data.get("orderReference", "")
    transaction_status = data.get("transactionStatus", "")

    logger.info("WayForPay callback: order=%s status=%s", order_ref, transaction_status)

    if transaction_status != "Approved":
        # Платёж не прошёл — логируем и отвечаем OK (WayForPay требует подтверждения)
        return _wayforpay_response(order_ref, "accept")

    # Извлекаем tg_id из orderReference: "verified_{tg_id}_{timestamp}"
    parts = order_ref.split("_")
    if len(parts) < 2 or parts[0] != "verified":
        return _wayforpay_response(order_ref, "accept")

    try:
        tg_id = int(parts[1])
    except ValueError:
        logger.error("WayForPay: cannot parse tg_id from %s", order_ref)
        return _wayforpay_response(order_ref, "accept")

    async with get_db_context() as db:
        # Дедупликация — проверяем, не обработан ли уже этот order
        existing = await db.execute(
            select(Payment).where(Payment.external_id == order_ref)
        )
        if existing.scalar_one_or_none():
            logger.info("WayForPay: order %s already processed", order_ref)
            return _wayforpay_response(order_ref, "accept")

        user_result = await db.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()
        if not user:
            logger.error("WayForPay: user tg_id=%s not found", tg_id)
            return _wayforpay_response(order_ref, "accept")

        # Активируем подписку
        await activate_verified(db, user)

        # Сохраняем транзакцию
        payment = Payment(
            user_id=user.id,
            provider="wayforpay",
            product="verified_30d",
            amount=float(data.get("amount", 0)),
            currency=data.get("currency", "UAH"),
            status="success",
            external_id=order_ref,
            completed_at=datetime.utcnow(),
        )
        db.add(payment)

        logger.info("WayForPay: activated Verified for tg_id=%s", tg_id)

        # Уведомляем пользователя в боте
        try:
            from app.bot.instance import bot
            exp_date = user.verified_expires_at.strftime("%d.%m.%Y")
            await bot.send_message(
                tg_id,
                f"🎉 <b>Оплата прошла!</b>\n\n"
                f"Verified 18+ активирован до {exp_date}.\n"
                f"Спасибо за поддержку! 💎",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Cannot notify user %s: %s", tg_id, e)

    return _wayforpay_response(order_ref, "accept")


def _wayforpay_response(order_ref: str, status: str) -> dict:
    """WayForPay требует подтверждения получения callback."""
    import time
    sign_string = f"{order_ref};{status};{int(time.time())}"
    signature = hmac.new(
        settings.wayforpay_secret_key.encode() if settings.wayforpay_secret_key else b"",
        sign_string.encode(),
        hashlib.md5,
    ).hexdigest()
    return {
        "orderReference": order_ref,
        "status": status,
        "time": int(time.time()),
        "signature": signature,
    }
