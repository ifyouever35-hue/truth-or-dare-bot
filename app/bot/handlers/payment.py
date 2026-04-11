"""
app/bot/handlers/payment.py — Оплата Stars и WayForPay.

Stars flow: send_invoice → pre_checkout_query → successful_payment
WayForPay flow: generate link → user pays → webhook callback → activate
"""
import hashlib
import hmac
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import main_menu_kb, shop_kb
from app.config import settings
from app.database.models import Payment, User
from app.services.user_service import activate_verified, add_stars

router = Router()

# ─── Магазин Stars ────────────────────────────────────────────────────────────

STARS_PACKS = {
    "small":  (settings.stars_pack_small_price,  settings.stars_pack_small_bonus,  "50 Stars"),
    "medium": (settings.stars_pack_medium_price, settings.stars_pack_medium_bonus, "170 Stars (+13%)"),
    "large":  (settings.stars_pack_large_price,  settings.stars_pack_large_bonus,  "420 Stars (+20%)"),
}


@router.callback_query(F.data.startswith("shop:stars:"))
async def cb_buy_stars_pack(call: CallbackQuery, user: User) -> None:
    pack_key = call.data.split(":")[2]
    if pack_key not in STARS_PACKS:
        await call.answer("❌ Неизвестный пакет.", show_alert=True)
        return

    price_stars, bonus_stars, label = STARS_PACKS[pack_key]

    # Редактируем карточку магазина — показываем что идёт оплата
    # (инвойс откроется отдельным окном поверх — это поведение Telegram)
    try:
        await call.message.edit_text(
            f"⭐ <b>{label}</b>\n\n"
            f"Зачисляем <b>{bonus_stars} ⭐</b> на ваш баланс.\n\n"
            f"👆 Подтвердите оплату в открывшемся окне.\n"
            f"После оплаты Stars зачислятся автоматически.",
            reply_markup=_cancel_kb(),
            parse_mode="HTML",
        )
    except Exception:
        pass

    await call.message.bot.send_invoice(
        chat_id=call.from_user.id,
        title=f"⭐ {label}",
        description=f"Зачисляем {bonus_stars} Stars на ваш баланс в боте",
        payload=f"stars_pack:{pack_key}:{call.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=label, amount=price_stars)],
    )

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    cancel_builder = InlineKeyboardBuilder()
    cancel_builder.row(InlineKeyboardButton(text="« Отмена — вернуться в магазин", callback_data="menu:shop"))
    await call.message.bot.send_message(
        chat_id=call.from_user.id,
        text="Не хотите платить? Нажмите отмену.",
        reply_markup=cancel_builder.as_markup(),
    )
    await call.answer()


# ─── Покупка Verified через Stars ─────────────────────────────────────────────

@router.callback_query(F.data == "payment:verified:stars")
async def cb_buy_verified_stars(call: CallbackQuery, user: User) -> None:
    is_renewal = user.is_verification_active
    price = settings.verified_stars_price
    if is_renewal:
        price = max(price - settings.verified_renewal_discount, 1)

    try:
        await call.message.edit_text(
            f"💎 <b>Verified 18+</b>\n\n"
            f"Стоимость: <b>{price} ⭐</b>\n\n"
            f"👆 Подтвердите оплату в открывшемся окне.\n"
            f"После оплаты Verified активируется автоматически.",
            reply_markup=_cancel_kb(),
            parse_mode="HTML",
        )
    except Exception:
        pass

    await call.message.bot.send_invoice(
        chat_id=call.from_user.id,
        title="💎 Verified 18+ (30 дней)",
        description="Доступ ко всем взрослым комнатам на 30 дней",
        payload=f"verified_30d:{call.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Verified 18+", amount=price)],
    )

    # Кнопка отмены под инвойсом — вернуться в меню
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    cancel_builder = InlineKeyboardBuilder()
    cancel_builder.row(InlineKeyboardButton(text="« Отмена — вернуться в меню", callback_data="menu:main"))
    await call.message.bot.send_message(
        chat_id=call.from_user.id,
        text="Не хотите платить? Нажмите отмену.",
        reply_markup=cancel_builder.as_markup(),
    )
    await call.answer()


def _cancel_kb():
    """Кнопка отмены — вернуться в магазин."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="« Отмена", callback_data="menu:shop"))
    return builder.as_markup()


# ─── Pre-checkout (обязательный ответ в течение 10 сек) ──────────────────────

@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


# ─── Успешная оплата ──────────────────────────────────────────────────────────

@router.message(F.successful_payment)
async def successful_payment_handler(
    message: Message,
    user: User,
    db: AsyncSession,
) -> None:
    payment = message.successful_payment
    payload = payment.invoice_payload

    # Удаляем системное сообщение об оплате от Telegram
    try:
        await message.delete()
    except Exception:
        pass

    # Ищем карточку с кнопкой «Отмена» которую мы показывали — редактируем её
    from app.utils.redis_client import redis_client
    last_msg_id = await redis_client.get_last_message(user.tg_id)

    async def _edit_or_send(text: str, kb):
        """Редактируем карточку если можем, иначе отправляем новую."""
        if last_msg_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=last_msg_id,
                    text=text,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass
        sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
        await redis_client.set_last_message(user.tg_id, sent.message_id)

    # ── Verified 18+ ──────────────────────────────────────────────────────────
    if payload.startswith("verified_30d:"):
        await activate_verified(db, user)

        record = Payment(
            user_id=user.id,
            provider="stars",
            product="verified_30d",
            amount=payment.total_amount,
            currency="XTR",
            status="success",
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            completed_at=datetime.utcnow(),
        )
        db.add(record)

        exp_date = user.verified_expires_at.strftime("%d.%m.%Y")
        await _edit_or_send(
            f"🎉 <b>Verified 18+ активирован!</b>\n\n"
            f"Действует до: {exp_date}\n"
            f"Теперь доступны все взрослые комнаты 🔥\n\n"
            f"Спасибо за поддержку! 💎",
            main_menu_kb(),
        )
        return

    # ── Stars пакеты ──────────────────────────────────────────────────────────
    if payload.startswith("stars_pack:"):
        parts = payload.split(":")
        pack_key = parts[1] if len(parts) > 1 else ""

        bonus_map = {
            "small":  settings.stars_pack_small_bonus,
            "medium": settings.stars_pack_medium_bonus,
            "large":  settings.stars_pack_large_bonus,
        }
        bonus = bonus_map.get(pack_key, 0)

        if bonus:
            await add_stars(db, user, bonus)

            record = Payment(
                user_id=user.id,
                provider="stars",
                product=f"stars_pack_{pack_key}",
                amount=payment.total_amount,
                currency="XTR",
                status="success",
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
                completed_at=datetime.utcnow(),
            )
            db.add(record)

            await _edit_or_send(
                f"⭐ <b>Зачислено {bonus} Stars!</b>\n\n"
                f"Текущий баланс: <b>{user.stars_balance + bonus} ⭐</b>\n\n"
                f"Используй Stars для откупа от заданий в игре.",
                shop_kb(),
            )


# ─── WayForPay ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "payment:method:wayforpay")
async def cb_pay_wayforpay(call: CallbackQuery, user: User) -> None:
    if not settings.wayforpay_merchant_account:
        await call.answer("❌ Оплата картой временно недоступна.", show_alert=True)
        return

    order_ref = f"verified_{user.tg_id}_{int(time.time())}"
    order_date = int(time.time())
    amount = settings.verified_price_uah
    currency = "UAH"

    sign_string = ";".join([
        settings.wayforpay_merchant_account,
        settings.wayforpay_merchant_domain,
        order_ref, str(order_date), str(amount), currency,
        "1", "Verified 18+", str(amount), "1",
    ])
    signature = hmac.new(
        settings.wayforpay_secret_key.encode(),
        sign_string.encode(),
        hashlib.md5,
    ).hexdigest()

    pay_url = (
        f"https://secure.wayforpay.com/pay?"
        f"merchantAccount={settings.wayforpay_merchant_account}"
        f"&merchantDomain={settings.wayforpay_merchant_domain}"
        f"&orderReference={order_ref}&orderDate={order_date}"
        f"&amount={amount}&currency={currency}&orderTimeout=900"
        f"&productName=Verified+18%2B&productPrice={amount}&productCount=1"
        f"&merchantSignature={signature}"
        f"&serviceUrl={settings.wayforpay_merchant_domain}/api/wayforpay/callback"
        f"&returnUrl={settings.wayforpay_merchant_domain}/payment/success"
    )

    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"💳 Оплатить {amount} ₴", url=pay_url))
    builder.row(InlineKeyboardButton(text="« Назад", callback_data="menu:get_verified"))

    await call.message.edit_text(
        f"💳 <b>Оплата картой</b>\n\n"
        f"Сумма: <b>{amount} ₴</b>\n\n"
        f"После оплаты Verified активируется автоматически.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await call.answer()
