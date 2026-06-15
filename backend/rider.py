"""Rider application routes for QuickPress.

Namespaced under /rider/*. Reuses the same backend / database / auth as the
customer and partner apps. Adds rider-specific endpoints: profile/onboarding,
online status, order assignment & OTP-based handover pipeline, location
tracking, earnings, wallet, payouts, notifications, support.
"""

from __future__ import annotations

import random
import string
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from core import (
    date_in_window,
    db,
    get_user_from_token,
    new_id,
    now_utc,
    strip_mongo,
)

router = APIRouter(prefix="/rider", tags=["rider"])


# ---------------------------------------------------------------- models


class RiderPersonalIn(BaseModel):
    full_name: str
    mobile: str
    email: str | None = None
    address: str
    emergency_contact: str
    emergency_name: str | None = None


class RiderVehicleIn(BaseModel):
    vehicle_type: str  # Bicycle | Scooter | Motorcycle | Electric Scooter | Electric Bike
    vehicle_brand: str
    vehicle_model: str
    vehicle_number: str
    fuel_type: str  # Petrol | Electric | None (bicycle)


class RiderDocumentIn(BaseModel):
    doc_type: str  # aadhaar | license | rc | insurance | puc | profile_photo | selfie
    number: str | None = None
    url: str | None = None


class RiderBankIn(BaseModel):
    account_holder: str
    account_number: str
    ifsc: str
    bank_name: str
    upi_id: str | None = None


class RiderStatusIn(BaseModel):
    is_online: bool


class LocationIn(BaseModel):
    lat: float
    lng: float
    accuracy: float | None = None


class RiderActionIn(BaseModel):
    lat: float | None = None
    lng: float | None = None
    image_url: str | None = None


class OtpVerifyActionIn(BaseModel):
    otp: str
    lat: float | None = None
    lng: float | None = None
    image_url: str | None = None


class PayoutReqIn(BaseModel):
    amount: float


class SupportTicketIn(BaseModel):
    subject: str
    message: str


# ---------------------------------------------------------------- helpers


async def _audit(rider_id: str | None, user_id: str | None, action: str, payload: dict | None = None):
    await db.rider_audit.insert_one(
        {
            "audit_id": new_id("aud"),
            "rider_id": rider_id,
            "user_id": user_id,
            "action": action,
            "payload": payload or {},
            "at": now_utc().isoformat(),
        }
    )


async def _ensure_rider_for_user(user: dict) -> dict:
    existing = await db.riders.find_one({"user_id": user["user_id"]}, {"_id": 0})
    if existing:
        return existing
    rider = {
        "rider_id": new_id("rdr"),
        "user_id": user["user_id"],
        "full_name": user.get("name") or "",
        "mobile": user.get("phone") or "",
        "email": user.get("email"),
        "address": "",
        "emergency_contact": "",
        "emergency_name": "",
        "vehicle_type": None,
        "vehicle_brand": None,
        "vehicle_model": None,
        "vehicle_number": None,
        "fuel_type": None,
        "documents": {},  # keyed by doc_type
        "bank": None,
        "profile_photo": None,
        "selfie": None,
        "status": "Draft",  # Draft -> Pending -> Approved | Rejected | Suspended
        "onboarding_step": 1,  # 1..5
        "is_online": False,
        "wallet_balance": 0.0,
        "pending_balance": 0.0,
        "rating": 5.0,
        "ratings_count": 0,
        "deliveries_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "current_location": None,
        "created_at": now_utc().isoformat(),
    }
    await db.riders.insert_one(dict(rider))
    await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"role": "rider"}})
    await _audit(rider["rider_id"], user["user_id"], "rider_created")
    return rider


async def _get_rider(authorization: str | None) -> dict:
    user = await get_user_from_token(authorization)
    r = await db.riders.find_one({"user_id": user["user_id"]}, {"_id": 0})
    if not r:
        r = await _ensure_rider_for_user(user)
    return r


# ---------------------------------------------------------------- profile / onboarding


@router.get("/me")
async def rider_me(authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    return {"rider": r}


@router.post("/onboarding/personal")
async def onboarding_personal(body: RiderPersonalIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    if r["status"] in ("Approved", "Suspended"):
        raise HTTPException(400, "Already approved")
    await db.riders.update_one(
        {"rider_id": r["rider_id"]},
        {"$set": {**body.dict(), "onboarding_step": max(2, r.get("onboarding_step", 1))}},
    )
    await _audit(r["rider_id"], r["user_id"], "onboarding_personal", body.dict())
    return strip_mongo(await db.riders.find_one({"rider_id": r["rider_id"]}, {"_id": 0}))


@router.post("/onboarding/vehicle")
async def onboarding_vehicle(body: RiderVehicleIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    if r["status"] in ("Approved", "Suspended"):
        raise HTTPException(400, "Already approved")
    await db.riders.update_one(
        {"rider_id": r["rider_id"]},
        {"$set": {**body.dict(), "onboarding_step": max(3, r.get("onboarding_step", 1))}},
    )
    await _audit(r["rider_id"], r["user_id"], "onboarding_vehicle", body.dict())
    return strip_mongo(await db.riders.find_one({"rider_id": r["rider_id"]}, {"_id": 0}))


@router.post("/onboarding/document")
async def onboarding_document(body: RiderDocumentIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    if r["status"] in ("Approved", "Suspended"):
        raise HTTPException(400, "Already approved")
    docs = r.get("documents", {}) or {}
    docs[body.doc_type] = {
        "number": body.number,
        "url": body.url,
        "status": "Pending",
        "uploaded_at": now_utc().isoformat(),
    }
    update: dict[str, Any] = {"documents": docs, "onboarding_step": max(3, r.get("onboarding_step", 1))}
    if body.doc_type == "profile_photo" and body.url:
        update["profile_photo"] = body.url
    if body.doc_type == "selfie" and body.url:
        update["selfie"] = body.url
    await db.riders.update_one({"rider_id": r["rider_id"]}, {"$set": update})
    await _audit(r["rider_id"], r["user_id"], "onboarding_document", {"doc_type": body.doc_type})
    return strip_mongo(await db.riders.find_one({"rider_id": r["rider_id"]}, {"_id": 0}))


@router.post("/onboarding/bank")
async def onboarding_bank(body: RiderBankIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    if r["status"] in ("Approved", "Suspended"):
        raise HTTPException(400, "Already approved")
    await db.riders.update_one(
        {"rider_id": r["rider_id"]},
        {"$set": {"bank": body.dict(), "onboarding_step": max(5, r.get("onboarding_step", 1))}},
    )
    await _audit(r["rider_id"], r["user_id"], "onboarding_bank")
    return strip_mongo(await db.riders.find_one({"rider_id": r["rider_id"]}, {"_id": 0}))


@router.post("/onboarding/submit")
async def onboarding_submit(authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    if r["status"] in ("Approved", "Suspended"):
        raise HTTPException(400, "Already approved")
    required = ["full_name", "mobile", "address", "vehicle_type", "vehicle_number"]
    missing = [k for k in required if not r.get(k)]
    if missing:
        raise HTTPException(400, f"Missing: {', '.join(missing)}")
    if not r.get("bank"):
        raise HTTPException(400, "Missing: bank")
    needed_docs = ["aadhaar", "license", "rc", "insurance", "puc", "profile_photo", "selfie"]
    docs = r.get("documents", {}) or {}
    miss_docs = [d for d in needed_docs if d not in docs]
    if miss_docs:
        raise HTTPException(400, f"Missing documents: {', '.join(miss_docs)}")

    await db.riders.update_one(
        {"rider_id": r["rider_id"]},
        {"$set": {"status": "Pending", "submitted_at": now_utc().isoformat(), "onboarding_step": 5}},
    )
    await db.notifications.insert_one(
        {
            "notif_id": new_id("ntf"),
            "user_id": r["user_id"],
            "audience": "rider",
            "title": "Application Submitted",
            "body": "Your rider application is under review. We'll notify you once approved.",
            "type": "system",
            "read": False,
            "created_at": now_utc().isoformat(),
        }
    )
    await _audit(r["rider_id"], r["user_id"], "onboarding_submitted")
    return strip_mongo(await db.riders.find_one({"rider_id": r["rider_id"]}, {"_id": 0}))


# ---------------------------------------------------------------- status / location


@router.post("/status")
async def set_online_status(body: RiderStatusIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    if r["status"] != "Approved" and body.is_online:
        raise HTTPException(400, "Rider not approved")
    await db.riders.update_one(
        {"rider_id": r["rider_id"]},
        {"$set": {"is_online": body.is_online, "last_status_at": now_utc().isoformat()}},
    )
    await _audit(r["rider_id"], r["user_id"], "status_change", {"is_online": body.is_online})
    return {"ok": True, "is_online": body.is_online}


@router.post("/location")
async def update_location(body: LocationIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    loc = {"lat": body.lat, "lng": body.lng, "accuracy": body.accuracy, "at": now_utc().isoformat()}
    await db.riders.update_one({"rider_id": r["rider_id"]}, {"$set": {"current_location": loc}})
    # also append to active order if any
    active = await db.orders.find_one(
        {
            "rider_id": r["rider_id"],
            "status": {"$nin": ["Delivered", "Cancelled", "Refunded"]},
        },
        {"_id": 0, "order_id": 1},
    )
    if active:
        await db.orders.update_one(
            {"order_id": active["order_id"]},
            {"$set": {"rider_location": loc}, "$push": {"rider_track": {"$each": [loc], "$slice": -200}}},
        )
    return {"ok": True}


# ---------------------------------------------------------------- dashboard


def _amt(o: dict) -> float:
    return float(o.get("total", 0))


@router.get("/dashboard")
async def rider_dashboard(authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    orders = await db.orders.find({"rider_id": r["rider_id"]}, {"_id": 0}).to_list(2000)
    delivered = [o for o in orders if o.get("status") == "Delivered"]

    def in_window(o, days):
        return date_in_window(o.get("delivered_at") or o.get("created_at", ""), days)

    today = [o for o in delivered if in_window(o, 1)]
    week = [o for o in delivered if in_window(o, 7)]
    month = [o for o in delivered if in_window(o, 30)]
    today_earn = round(sum(o.get("rider_earning", _amt(o) * 0.15) for o in today), 2)
    week_earn = round(sum(o.get("rider_earning", _amt(o) * 0.15) for o in week), 2)
    month_earn = round(sum(o.get("rider_earning", _amt(o) * 0.15) for o in month), 2)
    active = [
        o for o in orders if o.get("status") not in ["Delivered", "Cancelled", "Refunded"]
    ]
    rejected = r.get("rejected_count", 0)
    accepted = r.get("accepted_count", 0)
    acc_rate = round(100.0 * accepted / max(1, accepted + rejected), 1) if (accepted + rejected) else 100.0
    comp_rate = (
        round(100.0 * len(delivered) / max(1, len(delivered) + len([o for o in orders if o.get("status") in ("Cancelled", "Refunded")])), 1)
        if delivered
        else 100.0
    )
    return {
        "today_deliveries": len(today),
        "today_earnings": today_earn,
        "weekly_earnings": week_earn,
        "monthly_earnings": month_earn,
        "acceptance_rate": acc_rate,
        "completion_rate": comp_rate,
        "rating": r.get("rating", 5.0),
        "active_orders": len(active),
        "is_online": r.get("is_online", False),
        "status": r.get("status", "Pending"),
        "wallet_balance": r.get("wallet_balance", 0.0),
        "pending_balance": r.get("pending_balance", 0.0),
    }


# ---------------------------------------------------------------- orders / assignment / pipeline


def _serialize_order(o: dict, customer: dict | None, partner: dict | None) -> dict:
    return {
        **o,
        "customer": {
            "user_id": (customer or {}).get("user_id"),
            "name": (customer or {}).get("name"),
            "phone": (customer or {}).get("phone"),
            "picture": (customer or {}).get("picture"),
        }
        if customer
        else None,
        "partner": {
            "partner_id": (partner or {}).get("partner_id"),
            "name": (partner or {}).get("name"),
            "phone": (partner or {}).get("phone"),
            "image": (partner or {}).get("image"),
            "address": (partner or {}).get("address"),
            "lat": (partner or {}).get("lat"),
            "lng": (partner or {}).get("lng"),
        }
        if partner
        else None,
    }


async def _hydrate(order: dict) -> dict:
    customer = await db.users.find_one({"user_id": order.get("user_id")}, {"_id": 0})
    partner = await db.partners.find_one({"partner_id": order.get("partner_id")}, {"_id": 0})
    return _serialize_order(order, customer, partner)


@router.get("/orders/available")
async def available_orders(authorization: str | None = Header(default=None)):
    """Orders waiting for a rider to accept. Visible only when online + approved."""
    r = await _get_rider(authorization)
    if r["status"] != "Approved" or not r.get("is_online"):
        return {"items": []}
    rows = (
        await db.orders.find(
            {
                "rider_id": {"$in": [None, ""]},
                "status": {"$in": ["Partner Assigned", "Pickup Scheduled", "Ready"]},
            },
            {"_id": 0},
        )
        .sort("created_at", -1)
        .to_list(50)
    )
    items = []
    for o in rows:
        items.append(await _hydrate(o))
    return {"items": items}


@router.get("/orders/active")
async def active_orders(authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    rows = (
        await db.orders.find(
            {
                "rider_id": r["rider_id"],
                "status": {"$nin": ["Delivered", "Cancelled", "Refunded"]},
            },
            {"_id": 0},
        )
        .sort("created_at", -1)
        .to_list(50)
    )
    return {"items": [await _hydrate(o) for o in rows]}


@router.get("/orders/history")
async def history_orders(authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    rows = (
        await db.orders.find(
            {"rider_id": r["rider_id"], "status": {"$in": ["Delivered", "Cancelled", "Refunded"]}},
            {"_id": 0},
        )
        .sort("delivered_at", -1)
        .to_list(200)
    )
    return {"items": [await _hydrate(o) for o in rows]}


@router.get("/orders/{order_id}")
async def get_order(order_id: str, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    o = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    if not o:
        raise HTTPException(404, "Order not found")
    if o.get("rider_id") and o["rider_id"] != r["rider_id"]:
        raise HTTPException(403, "Not your order")
    return await _hydrate(o)


@router.post("/orders/{order_id}/accept")
async def accept_order(order_id: str, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    if r["status"] != "Approved":
        raise HTTPException(400, "Not approved")
    o = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    if not o:
        raise HTTPException(404, "Order not found")
    if o.get("rider_id"):
        raise HTTPException(400, "Already assigned")
    pickup_otp = "".join(random.choices(string.digits, k=4))
    delivery_otp = "".join(random.choices(string.digits, k=4))
    await db.orders.update_one(
        {"order_id": order_id},
        {
            "$set": {
                "rider_id": r["rider_id"],
                "rider_assigned_at": now_utc().isoformat(),
                "rider_pickup_otp": pickup_otp,
                "rider_delivery_otp": delivery_otp,
                "rider": {
                    "rider_id": r["rider_id"],
                    "name": r.get("full_name") or "Rider",
                    "phone": r.get("mobile"),
                    "vehicle": r.get("vehicle_number"),
                    "vehicle_type": r.get("vehicle_type"),
                    "rating": r.get("rating", 5.0),
                    "picture": r.get("profile_photo"),
                },
                "status": "Rider Assigned",
            },
            "$push": {"timeline": {"status": "Rider Assigned", "at": now_utc().isoformat()}},
        },
    )
    await db.riders.update_one({"rider_id": r["rider_id"]}, {"$inc": {"accepted_count": 1}})
    await db.notifications.insert_one(
        {
            "notif_id": new_id("ntf"),
            "user_id": o["user_id"],
            "title": "Rider Assigned",
            "body": f"{r.get('full_name') or 'Your rider'} is on the way for pickup.",
            "type": "order",
            "read": False,
            "created_at": now_utc().isoformat(),
        }
    )
    await _audit(r["rider_id"], r["user_id"], "order_accepted", {"order_id": order_id})
    return await _hydrate(await db.orders.find_one({"order_id": order_id}, {"_id": 0}))


@router.post("/orders/{order_id}/reject")
async def reject_order(order_id: str, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    await db.riders.update_one({"rider_id": r["rider_id"]}, {"$inc": {"rejected_count": 1}})
    await db.orders.update_one(
        {"order_id": order_id},
        {"$addToSet": {"rejected_by_riders": r["rider_id"]}},
    )
    await _audit(r["rider_id"], r["user_id"], "order_rejected", {"order_id": order_id})
    return {"ok": True}


async def _push_status(order_id: str, status: str, extra: dict | None = None):
    update = {"$set": {"status": status, **(extra or {})}, "$push": {"timeline": {"status": status, "at": now_utc().isoformat()}}}
    if status == "Delivered":
        update["$set"]["delivered_at"] = now_utc().isoformat()
    await db.orders.update_one({"order_id": order_id}, update)


async def _require_my_order(order_id: str, rider: dict) -> dict:
    o = await db.orders.find_one({"order_id": order_id, "rider_id": rider["rider_id"]}, {"_id": 0})
    if not o:
        raise HTTPException(404, "Order not found")
    return o


# Step 1: Pickup from customer
@router.post("/orders/{order_id}/arrive-customer-pickup")
async def arrive_customer_pickup(order_id: str, body: RiderActionIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    await _require_my_order(order_id, r)
    await _push_status(order_id, "Arrived At Customer", {"customer_pickup_arrival": body.dict()})
    await _audit(r["rider_id"], r["user_id"], "arrived_customer_pickup", {"order_id": order_id})
    return await _hydrate(await db.orders.find_one({"order_id": order_id}, {"_id": 0}))


@router.post("/orders/{order_id}/customer-pickup")
async def customer_pickup(order_id: str, body: RiderActionIn, authorization: str | None = Header(default=None)):
    """Rider picks up clothes from customer (image + GPS)."""
    r = await _get_rider(authorization)
    await _require_my_order(order_id, r)
    if not body.image_url:
        raise HTTPException(400, "Pickup image required")
    await _push_status(
        order_id,
        "Picked Up",
        {
            "customer_pickup_image": body.image_url,
            "customer_pickup_location": {"lat": body.lat, "lng": body.lng},
            "customer_pickup_at": now_utc().isoformat(),
        },
    )
    await _audit(r["rider_id"], r["user_id"], "customer_pickup", {"order_id": order_id})
    return await _hydrate(await db.orders.find_one({"order_id": order_id}, {"_id": 0}))


# Step 2: Handover to partner -> partner enters OTP
@router.post("/orders/{order_id}/arrive-partner")
async def arrive_partner(order_id: str, body: RiderActionIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    await _require_my_order(order_id, r)
    await _push_status(order_id, "Arrived At Partner", {"partner_arrival": body.dict()})
    return await _hydrate(await db.orders.find_one({"order_id": order_id}, {"_id": 0}))


@router.post("/orders/{order_id}/partner-handover")
async def partner_handover(order_id: str, body: OtpVerifyActionIn, authorization: str | None = Header(default=None)):
    """Partner enters OTP to confirm clothes received from rider."""
    r = await _get_rider(authorization)
    o = await _require_my_order(order_id, r)
    expected = o.get("rider_pickup_otp")
    if not expected or body.otp != expected:
        raise HTTPException(400, "Invalid OTP")
    if not body.image_url:
        raise HTTPException(400, "Handover image required")
    await _push_status(
        order_id,
        "Delivered To Partner",
        {
            "partner_handover_image": body.image_url,
            "partner_handover_location": {"lat": body.lat, "lng": body.lng},
            "partner_handover_at": now_utc().isoformat(),
        },
    )
    await _audit(r["rider_id"], r["user_id"], "partner_handover", {"order_id": order_id})
    return await _hydrate(await db.orders.find_one({"order_id": order_id}, {"_id": 0}))


# Step 3: Partner ready -> rider picks up dispatch from partner with OTP
@router.post("/orders/{order_id}/partner-dispatch")
async def partner_dispatch(order_id: str, body: OtpVerifyActionIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    o = await _require_my_order(order_id, r)
    if o.get("status") not in ("Ready", "Processing"):
        # allow demo-driven advance: partner clicked "Ready"
        pass
    # The partner shares an OTP to dispatch — we use delivery_otp's partner-facing variant.
    expected = o.get("rider_dispatch_otp") or o.get("rider_pickup_otp")
    # Generate dispatch otp if missing
    if not o.get("rider_dispatch_otp"):
        expected = "".join(random.choices(string.digits, k=4))
        await db.orders.update_one({"order_id": order_id}, {"$set": {"rider_dispatch_otp": expected}})
    if body.otp != expected:
        raise HTTPException(400, "Invalid OTP")
    if not body.image_url:
        raise HTTPException(400, "Dispatch image required")
    await _push_status(
        order_id,
        "Out For Delivery",
        {
            "partner_dispatch_image": body.image_url,
            "partner_dispatch_location": {"lat": body.lat, "lng": body.lng},
            "partner_dispatch_at": now_utc().isoformat(),
        },
    )
    await _audit(r["rider_id"], r["user_id"], "partner_dispatch", {"order_id": order_id})
    return await _hydrate(await db.orders.find_one({"order_id": order_id}, {"_id": 0}))


@router.get("/orders/{order_id}/dispatch-otp")
async def get_dispatch_otp(order_id: str, authorization: str | None = Header(default=None)):
    """Returns the OTP the rider must enter (in real life partner shares verbally).
    For demo purposes we expose it so the partner-app (or this demo) can show it."""
    r = await _get_rider(authorization)
    o = await _require_my_order(order_id, r)
    otp = o.get("rider_dispatch_otp")
    if not otp:
        otp = "".join(random.choices(string.digits, k=4))
        await db.orders.update_one({"order_id": order_id}, {"$set": {"rider_dispatch_otp": otp}})
    return {"otp": otp}


# Step 4: Final delivery -> customer enters OTP
@router.post("/orders/{order_id}/arrive-customer-delivery")
async def arrive_customer_delivery(order_id: str, body: RiderActionIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    await _require_my_order(order_id, r)
    await _push_status(order_id, "Arrived At Drop", {"customer_delivery_arrival": body.dict()})
    return await _hydrate(await db.orders.find_one({"order_id": order_id}, {"_id": 0}))


@router.post("/orders/{order_id}/final-delivery")
async def final_delivery(order_id: str, body: OtpVerifyActionIn, authorization: str | None = Header(default=None)):
    """Customer enters OTP to confirm receipt."""
    r = await _get_rider(authorization)
    o = await _require_my_order(order_id, r)
    expected = o.get("rider_delivery_otp")
    if not expected or body.otp != expected:
        raise HTTPException(400, "Invalid OTP")
    if not body.image_url:
        raise HTTPException(400, "Delivery image required")
    earning = round(float(o.get("total", 0)) * 0.15 + 20, 2)  # 15% + base
    await _push_status(
        order_id,
        "Delivered",
        {
            "customer_delivery_image": body.image_url,
            "customer_delivery_location": {"lat": body.lat, "lng": body.lng},
            "rider_earning": earning,
        },
    )
    await db.riders.update_one(
        {"rider_id": r["rider_id"]},
        {
            "$inc": {
                "deliveries_count": 1,
                "wallet_balance": earning,
            }
        },
    )
    await db.wallet_transactions.insert_one(
        {
            "txn_id": new_id("txn"),
            "rider_id": r["rider_id"],
            "type": "credit",
            "amount": earning,
            "reason": f"Delivery earning ({order_id})",
            "order_id": order_id,
            "created_at": now_utc().isoformat(),
        }
    )
    await db.notifications.insert_one(
        {
            "notif_id": new_id("ntf"),
            "user_id": o["user_id"],
            "title": "Order Delivered",
            "body": "Your order has been delivered. Thank you for using QuickPress!",
            "type": "order",
            "read": False,
            "created_at": now_utc().isoformat(),
        }
    )
    await _audit(r["rider_id"], r["user_id"], "final_delivery", {"order_id": order_id, "earning": earning})
    return await _hydrate(await db.orders.find_one({"order_id": order_id}, {"_id": 0}))


# Customer-facing helper: get the OTP the customer must say to rider
@router.get("/orders/{order_id}/delivery-otp")
async def get_delivery_otp(order_id: str, authorization: str | None = Header(default=None)):
    """Returns the customer's delivery OTP — visible to the assigned rider only for verification matching."""
    r = await _get_rider(authorization)
    o = await _require_my_order(order_id, r)
    return {"otp": o.get("rider_delivery_otp")}


# ---------------------------------------------------------------- earnings / wallet / payouts


@router.get("/earnings")
async def earnings(authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    orders = await db.orders.find(
        {"rider_id": r["rider_id"], "status": "Delivered"}, {"_id": 0}
    ).to_list(2000)

    def sum_window(days):
        return round(
            sum(o.get("rider_earning", _amt(o) * 0.15) for o in orders if date_in_window(o.get("delivered_at") or o.get("created_at", ""), days)),
            2,
        )

    today = sum_window(1)
    week = sum_window(7)
    month = sum_window(30)
    # daily breakdown for last 7 days
    from datetime import datetime, timezone
    by_day: dict[str, float] = {}
    for o in orders:
        ts = o.get("delivered_at") or o.get("created_at", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            day = dt.strftime("%a")
            by_day[day] = round(by_day.get(day, 0) + float(o.get("rider_earning", _amt(o) * 0.15)), 2)
        except Exception:
            continue
    days_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    chart = [{"day": d, "amount": by_day.get(d, 0)} for d in days_order]
    return {
        "today": today,
        "weekly": week,
        "monthly": month,
        "tips": 0.0,
        "bonuses": round(min(week * 0.05, 200), 2),
        "incentives": round(min(month * 0.03, 500), 2),
        "chart": chart,
        "deliveries": len(orders),
    }


@router.get("/wallet")
async def wallet(authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    txns = (
        await db.wallet_transactions.find({"rider_id": r["rider_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(200)
    )
    return {
        "available_balance": r.get("wallet_balance", 0.0),
        "pending_balance": r.get("pending_balance", 0.0),
        "transactions": txns,
    }


@router.post("/payouts/request")
async def request_payout(body: PayoutReqIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    if body.amount < 100:
        raise HTTPException(400, "Minimum payout is ₹100")
    if r.get("wallet_balance", 0) < body.amount:
        raise HTTPException(400, "Insufficient balance")
    payout = {
        "payout_id": new_id("rpo"),
        "rider_id": r["rider_id"],
        "amount": body.amount,
        "status": "Pending",
        "bank": r.get("bank"),
        "created_at": now_utc().isoformat(),
    }
    await db.rider_payouts.insert_one(dict(payout))
    await db.riders.update_one(
        {"rider_id": r["rider_id"]},
        {"$inc": {"wallet_balance": -body.amount, "pending_balance": body.amount}},
    )
    await db.wallet_transactions.insert_one(
        {
            "txn_id": new_id("txn"),
            "rider_id": r["rider_id"],
            "type": "debit",
            "amount": body.amount,
            "reason": "Payout requested",
            "created_at": now_utc().isoformat(),
        }
    )
    await _audit(r["rider_id"], r["user_id"], "payout_requested", {"amount": body.amount})
    return strip_mongo(await db.rider_payouts.find_one({"payout_id": payout["payout_id"]}, {"_id": 0}))


@router.get("/payouts")
async def payouts(authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    rows = (
        await db.rider_payouts.find({"rider_id": r["rider_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(200)
    )
    return {"items": rows}


@router.get("/settlements")
async def settlements(authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    orders = await db.orders.find(
        {"rider_id": r["rider_id"], "status": "Delivered"}, {"_id": 0}
    ).sort("delivered_at", -1).to_list(200)
    items = [
        {
            "order_id": o["order_id"],
            "amount": o.get("rider_earning", round(_amt(o) * 0.15, 2)),
            "status": "Settled",
            "at": o.get("delivered_at") or o.get("created_at"),
        }
        for o in orders
    ]
    return {"items": items}


# ---------------------------------------------------------------- notifications / support


@router.get("/notifications")
async def list_notifications(authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    rows = (
        await db.notifications.find(
            {"user_id": user["user_id"], "$or": [{"audience": "rider"}, {"audience": None}, {"audience": {"$exists": False}}]},
            {"_id": 0},
        )
        .sort("created_at", -1)
        .to_list(200)
    )
    return {"items": rows}


@router.post("/notifications/{notif_id}/read")
async def read_notification(notif_id: str, authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    await db.notifications.update_one({"notif_id": notif_id, "user_id": user["user_id"]}, {"$set": {"read": True}})
    return {"ok": True}


@router.post("/support")
async def support_create(body: SupportTicketIn, authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    ticket = {
        "ticket_id": new_id("tkt"),
        "rider_id": r["rider_id"],
        "user_id": r["user_id"],
        "subject": body.subject,
        "message": body.message,
        "status": "Open",
        "created_at": now_utc().isoformat(),
    }
    await db.support_tickets.insert_one(dict(ticket))
    return strip_mongo(await db.support_tickets.find_one({"ticket_id": ticket["ticket_id"]}, {"_id": 0}))


@router.get("/support")
async def support_list(authorization: str | None = Header(default=None)):
    r = await _get_rider(authorization)
    rows = (
        await db.support_tickets.find({"rider_id": r["rider_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(200)
    )
    return {"items": rows}


# ---------------------------------------------------------------- admin / demo helpers


@router.post("/admin/approve")
async def admin_approve(authorization: str | None = Header(default=None)):
    """Demo helper: rider self-approves (in real life an admin would). Idempotent."""
    r = await _get_rider(authorization)
    await db.riders.update_one(
        {"rider_id": r["rider_id"]},
        {"$set": {"status": "Approved", "approved_at": now_utc().isoformat()}},
    )
    await db.notifications.insert_one(
        {
            "notif_id": new_id("ntf"),
            "user_id": r["user_id"],
            "audience": "rider",
            "title": "Application Approved",
            "body": "Welcome aboard! You can now go online and start accepting deliveries.",
            "type": "system",
            "read": False,
            "created_at": now_utc().isoformat(),
        }
    )
    await _audit(r["rider_id"], r["user_id"], "approved")
    return strip_mongo(await db.riders.find_one({"rider_id": r["rider_id"]}, {"_id": 0}))


@router.post("/demo-seed")
async def demo_seed(authorization: str | None = Header(default=None)):
    """Idempotently seed a few customer-side orders that this rider can pick up,
    plus one already-assigned active order and a few delivered ones for stats."""
    r = await _get_rider(authorization)

    # Ensure there's at least one partner with location
    partner = await db.partners.find_one({}, {"_id": 0})
    if not partner:
        partner = {
            "partner_id": new_id("ptr"),
            "name": "FreshFold Demo",
            "image": "https://images.unsplash.com/photo-1778731660255-215c9172e18d?w=800",
            "logo": "https://images.unsplash.com/photo-1604335399105-a0c585fd81a1?w=200",
            "address": "Sector 18, Noida",
            "phone": "+91 98765 11111",
            "lat": 28.5675,
            "lng": 77.3210,
            "rating": 4.8,
            "is_open": True,
            "status": "Approved",
            "created_at": now_utc().isoformat(),
        }
        await db.partners.insert_one(dict(partner))
    else:
        # backfill location if missing
        if not partner.get("lat"):
            await db.partners.update_one(
                {"partner_id": partner["partner_id"]}, {"$set": {"lat": 28.5675, "lng": 77.3210, "phone": partner.get("phone") or "+91 98765 11111"}}
            )

    # Ensure a few customer users with location
    customers = []
    for i in range(3):
        uid = f"demo_cust_{i+1}"
        await db.users.update_one(
            {"user_id": uid},
            {
                "$setOnInsert": {
                    "user_id": uid,
                    "name": ["Aarav Sharma", "Priya Patel", "Rohan Mehta"][i],
                    "phone": ["+919811100001", "+919811100002", "+919811100003"][i],
                    "email": None,
                    "role": "customer",
                    "wallet_balance": 0,
                    "created_at": now_utc().isoformat(),
                }
            },
            upsert=True,
        )
        customers.append(uid)

    # Create available orders (no rider assigned)
    existing_avail = await db.orders.count_documents(
        {"rider_id": {"$in": [None, ""]}, "status": {"$in": ["Partner Assigned", "Ready"]}}
    )
    if existing_avail < 3:
        coords = [
            (28.5710, 77.3260),
            (28.5650, 77.3180),
            (28.5723, 77.3300),
        ]
        for i, uid in enumerate(customers):
            total = float(random.randint(199, 899))
            order = {
                "order_id": new_id("ord"),
                "user_id": uid,
                "partner_id": partner["partner_id"],
                "partner_name": partner["name"],
                "partner_image": partner.get("image"),
                "items": [{"service_id": "demo", "name": random.choice(["Wash & Fold", "Dry Clean", "Ironing"]), "price": total, "qty": 1}],
                "address": {
                    "label": "Home",
                    "line1": f"Flat {100+i}, Sector 18",
                    "city": "Noida",
                    "pincode": "201301",
                    "lat": coords[i][0],
                    "lng": coords[i][1],
                },
                "pickup_date": "Today",
                "pickup_slot": "Now",
                "payment_method": random.choice(["UPI", "COD"]),
                "subtotal": total,
                "delivery_fee": 29,
                "discount": 0,
                "wallet_used": 0,
                "total": total + 29,
                "status": random.choice(["Partner Assigned", "Ready"]),
                "rider_id": None,
                "timeline": [
                    {"status": "Order Created", "at": now_utc().isoformat()},
                    {"status": "Partner Assigned", "at": now_utc().isoformat()},
                ],
                "eta_minutes": random.randint(20, 45),
                "created_at": now_utc().isoformat(),
            }
            await db.orders.insert_one(dict(order))

    # Create 5 delivered historical orders for stats
    existing_hist = await db.orders.count_documents({"rider_id": r["rider_id"], "status": "Delivered"})
    if existing_hist < 4:
        for i in range(5):
            total = float(random.randint(199, 1499))
            earning = round(total * 0.15 + 20, 2)
            created = (now_utc() - timedelta(days=random.randint(0, 25), hours=random.randint(0, 23))).isoformat()
            o = {
                "order_id": new_id("ord"),
                "user_id": customers[i % len(customers)],
                "partner_id": partner["partner_id"],
                "partner_name": partner["name"],
                "partner_image": partner.get("image"),
                "items": [{"service_id": "demo", "name": "Wash & Fold", "price": total, "qty": 1}],
                "address": {"label": "Home", "line1": "Sector 18", "city": "Noida", "pincode": "201301", "lat": 28.57 + random.uniform(-0.01, 0.01), "lng": 77.32 + random.uniform(-0.01, 0.01)},
                "pickup_date": "Today",
                "pickup_slot": "10-12 PM",
                "payment_method": "UPI",
                "subtotal": total,
                "delivery_fee": 29,
                "discount": 0,
                "total": total + 29,
                "status": "Delivered",
                "rider_id": r["rider_id"],
                "rider_earning": earning,
                "rider_assigned_at": created,
                "delivered_at": created,
                "timeline": [{"status": "Delivered", "at": created}],
                "created_at": created,
            }
            await db.orders.insert_one(dict(o))
            await db.wallet_transactions.insert_one(
                {
                    "txn_id": new_id("txn"),
                    "rider_id": r["rider_id"],
                    "type": "credit",
                    "amount": earning,
                    "reason": f"Delivery earning ({o['order_id']})",
                    "order_id": o["order_id"],
                    "created_at": created,
                }
            )
        delivered_total = round(sum(float(random.randint(199, 1499)) * 0.15 + 20 for _ in range(5)), 2)
        await db.riders.update_one(
            {"rider_id": r["rider_id"]},
            {"$set": {"deliveries_count": 5, "accepted_count": 5, "rating": 4.8, "ratings_count": 5, "wallet_balance": delivered_total}},
        )

    # A welcome notification
    if not await db.notifications.find_one({"user_id": r["user_id"], "title": "Welcome to QuickPress Rider"}):
        await db.notifications.insert_one(
            {
                "notif_id": new_id("ntf"),
                "user_id": r["user_id"],
                "audience": "rider",
                "title": "Welcome to QuickPress Rider",
                "body": "Go online to start receiving delivery assignments near you.",
                "type": "system",
                "read": False,
                "created_at": now_utc().isoformat(),
            }
        )

    return {"ok": True}
