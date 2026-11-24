"""Click (Uzbekistan) SHOP-API integration: payment links + Prepare/Complete webhook."""
import hashlib
import hmac
import uuid
import random
import logging
import datetime
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import CLICK_SERVICE_ID, CLICK_MERCHANT_ID, CLICK_SECRET_KEY, CLICK_MERCHANT_USER_ID, PUBLIC_BASE_URL
from backend.db import get_db
from backend.models import User, Payment
from backend.auth import get_current_user_required
from backend.pipeline.quota import PLANS, activate_plan
from backend.runtime import rate_limit

logger = logging.getLogger("Tilmochbek-Billing")
router = APIRouter()

PAY_URL = "https://my.click.uz/services/pay"

# Standard Click SHOP-API error codes
OK, E_SIGN, E_AMOUNT, E_ACTION, E_PAID, E_NO_ORDER, E_NO_TRANS, E_UPDATE, E_REQUEST, E_CANCELLED = 0, -1, -2, -3, -4, -5, -6, -7, -8, -9
NOTES = {OK: "Success", E_SIGN: "SIGN CHECK FAILED!", E_AMOUNT: "Incorrect parameter amount", E_ACTION: "Action not found",
         E_PAID: "Already paid", E_NO_ORDER: "Order not found", E_NO_TRANS: "Transaction does not exist",
         E_UPDATE: "Failed to update user", E_REQUEST: "Error in request from click", E_CANCELLED: "Transaction cancelled"}


def click_enabled() -> bool:
    return bool(CLICK_SERVICE_ID and CLICK_MERCHANT_ID and CLICK_SECRET_KEY)


def make_sign(*parts: Any) -> str:
    return hashlib.md5("".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def pay_url(payment: Payment) -> str:
    params = {
        "service_id": CLICK_SERVICE_ID, "merchant_id": CLICK_MERCHANT_ID, "amount": f"{payment.amount:.2f}",
        "transaction_param": payment.id, "return_url": PUBLIC_BASE_URL,
    }
    if CLICK_MERCHANT_USER_ID:
        params["merchant_user_id"] = CLICK_MERCHANT_USER_ID
    return f"{PAY_URL}?{urlencode(params)}"


async def create_click_payment(db: AsyncSession, user: User, plan_key: str) -> Tuple[Payment, str]:
    if not click_enabled():
        raise ValueError("Click ulanmagan.")
    if plan_key not in PLANS or plan_key == "free":
        raise ValueError("Noto'g'ri tarif.")
    payment = Payment(id=uuid.uuid4().hex[:24], user_id=user.id, plan=plan_key, amount=int(PLANS[plan_key]["price"]))
    db.add(payment)
    await db.commit()
    return payment, pay_url(payment)


def _reply(f: Dict[str, str], code: int, **extra) -> Dict[str, Any]:
    out = {"click_trans_id": f.get("click_trans_id"), "merchant_trans_id": f.get("merchant_trans_id"),
           "error": code, "error_note": NOTES.get(code, "Error")}
    out.update(extra)
    return out


async def _notify_paid(user: User, plan_key: str) -> None:
    if not user.telegram_id:
        return
    try:
        from backend import telegram_bot as tb
        if tb.bot:
            await tb.bot.send_message(
                user.telegram_id,
                f"✅ To'lov qabul qilindi! <b>{PLANS[plan_key]['name']}</b> tarifi yoqildi "
                f"({user.plan_expires_at:%Y-%m-%d} gacha). Rahmat!", parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Could not notify user {user.id}: {e}")


async def handle_click(db: AsyncSession, f: Dict[str, str]) -> Dict[str, Any]:
    """Processes a Click Prepare (action=0) / Complete (action=1) request."""
    required = ["click_trans_id", "service_id", "merchant_trans_id", "amount", "action", "sign_time", "sign_string"]
    if not click_enabled() or any(k not in f for k in required):
        return _reply(f, E_REQUEST)
    action = f["action"]
    if action not in ("0", "1"):
        return _reply(f, E_ACTION)

    if action == "0":
        expected = make_sign(f["click_trans_id"], f["service_id"], CLICK_SECRET_KEY, f["merchant_trans_id"],
                             f["amount"], action, f["sign_time"])
    else:
        if "merchant_prepare_id" not in f:
            return _reply(f, E_REQUEST)
        expected = make_sign(f["click_trans_id"], f["service_id"], CLICK_SECRET_KEY, f["merchant_trans_id"],
                             f["merchant_prepare_id"], f["amount"], action, f["sign_time"])
    if f["service_id"] != CLICK_SERVICE_ID or not hmac.compare_digest(expected, f["sign_string"].lower()):
        return _reply(f, E_SIGN)

    payment = (await db.execute(select(Payment).where(Payment.id == f["merchant_trans_id"]))).scalar_one_or_none()
    if not payment:
        return _reply(f, E_NO_ORDER)
    try:
        if abs(float(f["amount"]) - float(payment.amount)) > 0.01:
            return _reply(f, E_AMOUNT)
    except ValueError:
        return _reply(f, E_REQUEST)
    if payment.status == "paid":
        return _reply(f, E_PAID)
    if payment.status == "cancelled":
        return _reply(f, E_CANCELLED)

    if action == "0":  # ---- Prepare
        dup = (await db.execute(select(Payment).where(Payment.click_trans_id == f["click_trans_id"],
                                                      Payment.id != payment.id))).scalar_one_or_none()
        if dup:
            return _reply(f, E_PAID)
        if not payment.prepare_id:
            payment.prepare_id = random.randint(10_000_000, 2_000_000_000)
        payment.click_trans_id = f["click_trans_id"]
        payment.status = "prepared"
        await db.commit()
        return _reply(f, OK, merchant_prepare_id=payment.prepare_id)

    # ---- Complete
    if payment.status != "prepared" or str(payment.prepare_id) != f["merchant_prepare_id"] \
            or payment.click_trans_id != f["click_trans_id"]:
        return _reply(f, E_NO_TRANS)
    try:
        click_error = int(f.get("error", "0") or 0)
    except ValueError:
        click_error = -1
    if click_error < 0:
        payment.status = "cancelled"
        await db.commit()
        return _reply(f, E_CANCELLED)

    user = (await db.execute(select(User).where(User.id == payment.user_id))).scalar_one_or_none()
    if not user:
        return _reply(f, E_UPDATE)
    payment.status = "paid"
    payment.paid_at = datetime.datetime.utcnow()
    await activate_plan(db, user, payment.plan)  # commits payment + plan atomically
    logger.info(f"Payment {payment.id} paid: user {user.id} -> {payment.plan}")
    await _notify_paid(user, payment.plan)
    return _reply(f, OK, merchant_confirm_id=payment.prepare_id)


@router.post("/api/click")
@router.post("/api/click/prepare")
@router.post("/api/click/complete")
async def click_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    fields = {k: str(v) for k, v in form.items()}
    return JSONResponse(await handle_click(db, fields))


class BuyRequest(BaseModel):
    plan: str


@router.post("/api/payments/click")
async def start_click_payment(req: BuyRequest, request: Request, user: User = Depends(get_current_user_required),
                              db: AsyncSession = Depends(get_db)):
    rate_limit(request, "pay", 10)
    if not click_enabled():
        raise HTTPException(status_code=503, detail="Click orqali to'lov hozircha yoqilmagan. Telegram bot orqali to'lang.")
    try:
        _, url = await create_click_payment(db, user, req.plan)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"url": url}
