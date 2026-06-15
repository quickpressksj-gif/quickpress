"""Customer-side order routes: create, list, get, advance, cancel, review, mock payment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Header

from core import (
    CUSTOMER_PIPELINE,
    db,
    get_user_from_token,
    new_id,
    now_utc,
    strip_mongo,
)
from models import OrderIn, ReviewIn

router = APIRouter(tags=["orders"])


@router.post("/orders")
async def create_order(body: OrderIn, authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    partner = await db.partners.find_one({"partner_id": body.partner_id}, {"_id": 0})
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    subtotal = sum(i.price * i.qty for i in body.items)
    delivery_fee = 0.0 if user.get("is_plus") else 29.0
    discount = 0.0
    coupon_msg = None
    if body.coupon_code:
        coupon = await db.coupons.find_one({"code": body.coupon_code.upper()}, {"_id": 0})
        if coupon:
            if coupon.get("type") == "flat":
                discount = float(coupon.get("value", 0))
            else:
                discount = round(subtotal * float(coupon.get("value", 0)) / 100.0, 2)
            coupon_msg = f"{coupon['code']} applied"
    wallet_used = 0.0
    if body.use_wallet:
        wallet_used = min(user.get("wallet_balance", 0.0), subtotal + delivery_fee - discount)
        await db.users.update_one(
            {"user_id": user["user_id"]},
            {"$inc": {"wallet_balance": -wallet_used}},
        )
        await db.wallet_transactions.insert_one(
            {
                "user_id": user["user_id"],
                "type": "debit",
                "amount": wallet_used,
                "reason": "Order payment",
                "created_at": now_utc().isoformat(),
            }
        )
    total = max(0.0, subtotal + delivery_fee - discount - wallet_used)
    order = {
        "order_id": new_id("ord"),
        "user_id": user["user_id"],
        "partner_id": body.partner_id,
        "partner_name": partner["name"],
        "partner_image": partner.get("image"),
        "items": [i.dict() for i in body.items],
        "address": body.address.dict(),
        "pickup_date": body.pickup_date,
        "pickup_slot": body.pickup_slot,
        "payment_method": body.payment_method,
        "instructions": body.instructions,
        "subtotal": subtotal,
        "delivery_fee": delivery_fee,
        "discount": discount,
        "wallet_used": wallet_used,
        "total": total,
        "coupon_code": body.coupon_code,
        "coupon_msg": coupon_msg,
        "status": "Order Created",
        "timeline": [{"status": "Order Created", "at": now_utc().isoformat()}],
        "rider": {
            "name": "Rohit Sharma",
            "phone": "+91 98765 43210",
            "vehicle": "DL 8C XX 1234",
            "rating": 4.8,
            "picture": "https://images.unsplash.com/photo-1617347454431-f49d7ff5c3b1?w=200",
        },
        "eta_minutes": 45,
        "created_at": now_utc().isoformat(),
    }
    await db.orders.insert_one(dict(order))
    await db.orders.update_one(
        {"order_id": order["order_id"]},
        {
            "$set": {"status": "Partner Assigned"},
            "$push": {"timeline": {"status": "Partner Assigned", "at": now_utc().isoformat()}},
        },
    )
    await db.notifications.insert_one(
        {
            "notif_id": new_id("ntf"),
            "user_id": user["user_id"],
            "title": "Order placed",
            "body": f"Your order with {partner['name']} has been placed.",
            "type": "order",
            "read": False,
            "created_at": now_utc().isoformat(),
        }
    )
    return strip_mongo(await db.orders.find_one({"order_id": order["order_id"]}, {"_id": 0}))


@router.get("/orders")
async def list_orders(authorization: str | None = Header(default=None), status: str | None = None):
    user = await get_user_from_token(authorization)
    query: dict[str, Any] = {"user_id": user["user_id"]}
    if status == "active":
        query["status"] = {"$nin": ["Delivered", "Cancelled", "Refunded"]}
    elif status == "completed":
        query["status"] = "Delivered"
    elif status == "cancelled":
        query["status"] = {"$in": ["Cancelled", "Refunded"]}
    rows = await db.orders.find(query, {"_id": 0}).sort("created_at", -1).to_list(200)
    return {"items": rows}


@router.get("/orders/{order_id}")
async def get_order(order_id: str, authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    order = await db.orders.find_one({"order_id": order_id, "user_id": user["user_id"]}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/orders/{order_id}/advance")
async def advance_order(order_id: str, authorization: str | None = Header(default=None)):
    """Demo helper: progress order to the next status (used by tracking refresh)."""
    user = await get_user_from_token(authorization)
    order = await db.orders.find_one({"order_id": order_id, "user_id": user["user_id"]}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    cur = order["status"]
    if cur in CUSTOMER_PIPELINE:
        idx = CUSTOMER_PIPELINE.index(cur)
        if idx + 1 < len(CUSTOMER_PIPELINE):
            nxt = CUSTOMER_PIPELINE[idx + 1]
            await db.orders.update_one(
                {"order_id": order_id},
                {
                    "$set": {"status": nxt},
                    "$push": {"timeline": {"status": nxt, "at": now_utc().isoformat()}},
                },
            )
    return strip_mongo(await db.orders.find_one({"order_id": order_id}, {"_id": 0}))


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    order = await db.orders.find_one({"order_id": order_id, "user_id": user["user_id"]}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order["status"] in ["Delivered", "Cancelled", "Refunded"]:
        raise HTTPException(status_code=400, detail="Cannot cancel this order")
    await db.orders.update_one(
        {"order_id": order_id},
        {
            "$set": {"status": "Cancelled"},
            "$push": {"timeline": {"status": "Cancelled", "at": now_utc().isoformat()}},
        },
    )
    if order.get("wallet_used", 0) > 0:
        await db.users.update_one(
            {"user_id": user["user_id"]},
            {"$inc": {"wallet_balance": order["wallet_used"]}},
        )
        await db.wallet_transactions.insert_one(
            {
                "user_id": user["user_id"],
                "type": "credit",
                "amount": order["wallet_used"],
                "reason": "Order cancellation refund",
                "created_at": now_utc().isoformat(),
            }
        )
    return {"ok": True}


@router.post("/orders/{order_id}/review")
async def review_order(
    order_id: str, body: ReviewIn, authorization: str | None = Header(default=None)
):
    user = await get_user_from_token(authorization)
    order = await db.orders.find_one({"order_id": order_id, "user_id": user["user_id"]}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    await db.reviews.insert_one(
        {
            "review_id": new_id("rev"),
            "user_id": user["user_id"],
            "partner_id": order["partner_id"],
            "rating": body.rating,
            "comment": body.comment,
            "created_at": now_utc().isoformat(),
        }
    )
    return {"ok": True}


@router.post("/payments/mock")
async def mock_payment(payload: dict, authorization: str | None = Header(default=None)):
    await get_user_from_token(authorization)
    return {
        "ok": True,
        "payment_id": new_id("pay"),
        "status": "captured",
        "method": payload.get("method", "UPI"),
    }
