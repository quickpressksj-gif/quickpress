"""Partner application routes (consumed by future quickpress-partner-app).

Namespaced under /partner/*. Shares the same backend/DB as the customer app.
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Header

from core import (
    date_in_window,
    db,
    get_partner_for_user,
    get_user_from_token,
    new_id,
    now_utc,
    strip_mongo,
)
from models import (
    AdCampaignIn,
    DocumentIn,
    GalleryAddIn,
    OfferIn,
    PartnerPricingIn,
    PartnerServiceIn,
    PartnerSettingsIn,
    PartnerStatusIn,
    PartnerStoreIn,
    PayoutRequestIn,
    ReviewReplyIn,
)

router = APIRouter(prefix="/partner", tags=["partner"])


# ---------------------------------------------------------------- me/status/dashboard


@router.get("/me")
async def partner_me(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    return {"partner": p}


@router.post("/status")
async def partner_status(body: PartnerStatusIn, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    await db.partners.update_one({"partner_id": p["partner_id"]}, {"$set": {"is_open": body.is_open}})
    return {"ok": True, "is_open": body.is_open}


@router.get("/dashboard")
async def partner_dashboard(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    rows = await db.orders.find({"partner_id": p["partner_id"]}, {"_id": 0}).to_list(2000)

    def revenue(orders, days):
        return round(
            sum(
                o.get("total", 0)
                for o in orders
                if date_in_window(o.get("created_at", ""), days)
                and o.get("status") not in ["Cancelled", "Refunded"]
            ),
            2,
        )

    today_orders = [o for o in rows if date_in_window(o.get("created_at", ""), 1)]
    active = [o for o in rows if o.get("status") not in ["Delivered", "Cancelled", "Refunded"]]
    completed = [o for o in rows if o.get("status") == "Delivered"]
    cancelled = [o for o in rows if o.get("status") in ["Cancelled", "Refunded"]]
    reviews = await db.reviews.find({"partner_id": p["partner_id"]}, {"_id": 0}).to_list(500)
    avg_rating = (
        round(sum(r.get("rating", 0) for r in reviews) / len(reviews), 2)
        if reviews
        else p.get("rating", 4.5)
    )
    accepted = [
        o for o in rows if "Partner Assigned" in [t.get("status") for t in o.get("timeline", [])]
    ]
    rejected = [o for o in rows if o.get("rejected_by_partner")]
    acc_rate = (
        round(100.0 * len(accepted) / max(1, len(accepted) + len(rejected)), 1)
        if (accepted or rejected)
        else 100.0
    )
    comp_rate = (
        round(100.0 * len(completed) / max(1, len(completed) + len(cancelled)), 1)
        if (completed or cancelled)
        else 100.0
    )
    settlements = await db.settlements.find(
        {"partner_id": p["partner_id"], "status": "Pending"}, {"_id": 0}
    ).to_list(100)
    payouts_pending = await db.payouts.find(
        {"partner_id": p["partner_id"], "status": "Pending"}, {"_id": 0}
    ).to_list(100)
    return {
        "today_orders": len(today_orders),
        "active_orders": len(active),
        "completed_orders": len(completed),
        "cancelled_orders": len(cancelled),
        "today_revenue": revenue(rows, 1),
        "weekly_revenue": revenue(rows, 7),
        "monthly_revenue": revenue(rows, 30),
        "pending_settlements": round(sum(s.get("amount", 0) for s in settlements), 2),
        "pending_payouts": round(sum(p2.get("amount", 0) for p2 in payouts_pending), 2),
        "rating": avg_rating,
        "reviews_count": len(reviews),
        "acceptance_rate": acc_rate,
        "completion_rate": comp_rate,
        "is_open": p.get("is_open", False),
        "status": p.get("status", "Pending"),
    }


# ---------------------------------------------------------------- orders


@router.get("/orders")
async def partner_orders(
    authorization: str | None = Header(default=None), tab: str | None = None
):
    p = await get_partner_for_user(authorization)
    query: dict[str, Any] = {"partner_id": p["partner_id"]}
    mapping = {
        "new": ["Order Created"],
        "accepted": ["Partner Assigned"],
        "pickup": ["Pickup Scheduled", "Rider Assigned"],
        "picked": ["Picked Up"],
        "processing": ["Processing"],
        "ready": ["Ready", "Out For Delivery"],
        "completed": ["Delivered"],
        "cancelled": ["Cancelled", "Refunded"],
    }
    if tab and tab in mapping:
        query["status"] = {"$in": mapping[tab]}
    rows = await db.orders.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"items": rows}


async def _advance_to(order_id: str, partner_id: str, new_status: str) -> dict:
    o = await db.orders.find_one({"order_id": order_id, "partner_id": partner_id}, {"_id": 0})
    if not o:
        raise HTTPException(status_code=404, detail="Order not found")
    await db.orders.update_one(
        {"order_id": order_id},
        {
            "$set": {"status": new_status},
            "$push": {"timeline": {"status": new_status, "at": now_utc().isoformat()}},
        },
    )
    return strip_mongo(await db.orders.find_one({"order_id": order_id}, {"_id": 0}))


@router.post("/orders/{order_id}/accept")
async def p_accept(order_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    return await _advance_to(order_id, p["partner_id"], "Partner Assigned")


@router.post("/orders/{order_id}/reject")
async def p_reject(order_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    o = await db.orders.find_one(
        {"order_id": order_id, "partner_id": p["partner_id"]}, {"_id": 0}
    )
    if not o:
        raise HTTPException(status_code=404, detail="Order not found")
    await db.orders.update_one(
        {"order_id": order_id},
        {
            "$set": {"status": "Cancelled", "rejected_by_partner": True},
            "$push": {"timeline": {"status": "Cancelled", "at": now_utc().isoformat()}},
        },
    )
    return {"ok": True}


@router.post("/orders/{order_id}/schedule")
async def p_schedule(order_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    return await _advance_to(order_id, p["partner_id"], "Pickup Scheduled")


@router.post("/orders/{order_id}/assign-rider")
async def p_assign_rider(order_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    return await _advance_to(order_id, p["partner_id"], "Rider Assigned")


@router.post("/orders/{order_id}/picked-up")
async def p_picked(order_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    return await _advance_to(order_id, p["partner_id"], "Picked Up")


@router.post("/orders/{order_id}/start-processing")
async def p_processing(order_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    return await _advance_to(order_id, p["partner_id"], "Processing")


@router.post("/orders/{order_id}/ready")
async def p_ready(order_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    return await _advance_to(order_id, p["partner_id"], "Ready")


@router.post("/orders/{order_id}/out-for-delivery")
async def p_out(order_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    return await _advance_to(order_id, p["partner_id"], "Out For Delivery")


@router.post("/orders/{order_id}/complete")
async def p_complete(order_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    order = await _advance_to(order_id, p["partner_id"], "Delivered")
    gross = float(order.get("total", 0))
    commission = round(gross * 0.10, 2)
    net = round(gross - commission, 2)
    await db.settlements.insert_one(
        {
            "settlement_id": new_id("stl"),
            "partner_id": p["partner_id"],
            "order_id": order_id,
            "gross": gross,
            "commission": commission,
            "amount": net,
            "status": "Pending",
            "created_at": now_utc().isoformat(),
        }
    )
    return order


@router.post("/orders/{order_id}/images")
async def p_upload_images(
    order_id: str, body: dict, authorization: str | None = Header(default=None)
):
    p = await get_partner_for_user(authorization)
    urls = body.get("urls", [])
    await db.orders.update_one(
        {"order_id": order_id, "partner_id": p["partner_id"]},
        {"$set": {"processing_images": urls}},
    )
    return {"ok": True}


# ---------------------------------------------------------------- services/pricing


@router.get("/services")
async def p_services(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    rows = await db.services.find({"partner_id": p["partner_id"]}, {"_id": 0}).to_list(500)
    return {"items": rows}


@router.post("/services")
async def p_service_add(
    body: PartnerServiceIn, authorization: str | None = Header(default=None)
):
    p = await get_partner_for_user(authorization)
    s = {
        "service_id": new_id("srv"),
        "partner_id": p["partner_id"],
        "name": body.name,
        "category": body.category,
        "price": body.price,
        "unit": body.unit,
        "enabled": body.enabled,
        "image": p.get("image"),
    }
    await db.services.insert_one(dict(s))
    return strip_mongo(await db.services.find_one({"service_id": s["service_id"]}, {"_id": 0}))


@router.patch("/services/{service_id}")
async def p_service_edit(
    service_id: str, body: dict, authorization: str | None = Header(default=None)
):
    p = await get_partner_for_user(authorization)
    allowed = {k: v for k, v in body.items() if k in {"name", "price", "category", "unit", "enabled"}}
    if not allowed:
        raise HTTPException(status_code=400, detail="No editable fields provided")
    await db.services.update_one(
        {"service_id": service_id, "partner_id": p["partner_id"]}, {"$set": allowed}
    )
    return strip_mongo(await db.services.find_one({"service_id": service_id}, {"_id": 0}))


@router.delete("/services/{service_id}")
async def p_service_del(service_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    await db.services.delete_one({"service_id": service_id, "partner_id": p["partner_id"]})
    return {"ok": True}


@router.get("/pricing")
async def p_pricing_get(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    pricing = await db.partner_pricing.find_one({"partner_id": p["partner_id"]}, {"_id": 0})
    if not pricing:
        pricing = {
            "partner_id": p["partner_id"],
            "laundry_price": 49,
            "dry_cleaning_price": 99,
            "ironing_price": 9,
            "express_charges": 50,
            "pickup_charges": 0,
            "delivery_charges": 29,
            "urgent_charges": 99,
        }
        await db.partner_pricing.insert_one(dict(pricing))
    return pricing


@router.patch("/pricing")
async def p_pricing_set(
    body: PartnerPricingIn, authorization: str | None = Header(default=None)
):
    p = await get_partner_for_user(authorization)
    updates = {k: v for k, v in body.dict().items() if v is not None}
    await db.partner_pricing.update_one(
        {"partner_id": p["partner_id"]},
        {"$set": {"partner_id": p["partner_id"], **updates}},
        upsert=True,
    )
    return strip_mongo(await db.partner_pricing.find_one({"partner_id": p["partner_id"]}, {"_id": 0}))


# ---------------------------------------------------------------- store/gallery/settings


@router.patch("/store")
async def p_store_update(
    body: PartnerStoreIn, authorization: str | None = Header(default=None)
):
    p = await get_partner_for_user(authorization)
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if updates:
        await db.partners.update_one({"partner_id": p["partner_id"]}, {"$set": updates})
    return strip_mongo(await db.partners.find_one({"partner_id": p["partner_id"]}, {"_id": 0}))


@router.get("/gallery")
async def p_gallery(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    return {"items": p.get("gallery", [])}


@router.post("/gallery")
async def p_gallery_add(body: GalleryAddIn, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    await db.partners.update_one({"partner_id": p["partner_id"]}, {"$push": {"gallery": body.url}})
    return {"ok": True}


@router.delete("/gallery")
async def p_gallery_del(url: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    await db.partners.update_one({"partner_id": p["partner_id"]}, {"$pull": {"gallery": url}})
    return {"ok": True}


@router.patch("/settings")
async def p_settings(
    body: PartnerSettingsIn, authorization: str | None = Header(default=None)
):
    p = await get_partner_for_user(authorization)
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if updates:
        await db.partners.update_one({"partner_id": p["partner_id"]}, {"$set": updates})
    return strip_mongo(await db.partners.find_one({"partner_id": p["partner_id"]}, {"_id": 0}))


# ---------------------------------------------------------------- customers/reviews


@router.get("/customers")
async def p_customers(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    orders = await db.orders.find({"partner_id": p["partner_id"]}, {"_id": 0}).to_list(1000)
    by_user: dict[str, dict] = {}
    for o in orders:
        u = by_user.setdefault(
            o["user_id"],
            {
                "user_id": o["user_id"],
                "orders": 0,
                "revenue": 0.0,
                "last_order_at": o.get("created_at", ""),
            },
        )
        u["orders"] += 1
        if o.get("status") not in ["Cancelled", "Refunded"]:
            u["revenue"] += float(o.get("total", 0))
        if o.get("created_at", "") > u["last_order_at"]:
            u["last_order_at"] = o.get("created_at", "")
    ids = list(by_user.keys())
    users = (
        await db.users.find({"user_id": {"$in": ids}}, {"_id": 0}).to_list(500) if ids else []
    )
    user_map = {u["user_id"]: u for u in users}
    items = []
    for uid, stat in by_user.items():
        u = user_map.get(uid, {})
        items.append(
            {
                **stat,
                "name": u.get("name") or "Customer",
                "phone": u.get("phone"),
                "email": u.get("email"),
                "picture": u.get("picture"),
            }
        )
    items.sort(key=lambda x: x["revenue"], reverse=True)
    return {"items": items}


@router.get("/reviews")
async def p_reviews(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    rows = (
        await db.reviews.find({"partner_id": p["partner_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(200)
    )
    return {
        "items": rows,
        "average": (round(sum(r.get("rating", 0) for r in rows) / len(rows), 2) if rows else None),
    }


@router.post("/reviews/{review_id}/reply")
async def p_review_reply(
    review_id: str, body: ReviewReplyIn, authorization: str | None = Header(default=None)
):
    p = await get_partner_for_user(authorization)
    await db.reviews.update_one(
        {"review_id": review_id, "partner_id": p["partner_id"]},
        {"$set": {"partner_reply": body.reply}},
    )
    return {"ok": True}


# ---------------------------------------------------------------- earnings/settlements/payouts


@router.get("/earnings")
async def p_earnings(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    orders = await db.orders.find(
        {"partner_id": p["partner_id"], "status": "Delivered"}, {"_id": 0}
    ).to_list(2000)

    def gross(days):
        return round(
            sum(o.get("total", 0) for o in orders if date_in_window(o.get("created_at", ""), days)),
            2,
        )

    g_today, g_week, g_month = gross(1), gross(7), gross(30)
    commission_rate = 0.10
    return {
        "today": g_today,
        "weekly": g_week,
        "monthly": g_month,
        "gross_revenue": gross(365),
        "platform_commission": round(gross(365) * commission_rate, 2),
        "net_revenue": round(gross(365) * (1 - commission_rate), 2),
    }


@router.get("/settlements")
async def p_settlements(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    rows = (
        await db.settlements.find({"partner_id": p["partner_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(500)
    )
    return {"items": rows}


@router.get("/payouts")
async def p_payouts(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    rows = (
        await db.payouts.find({"partner_id": p["partner_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(500)
    )
    return {"items": rows}


@router.post("/payouts/request")
async def p_payouts_request(
    body: PayoutRequestIn, authorization: str | None = Header(default=None)
):
    p = await get_partner_for_user(authorization)
    payout = {
        "payout_id": new_id("po"),
        "partner_id": p["partner_id"],
        "amount": body.amount,
        "status": "Pending",
        "created_at": now_utc().isoformat(),
    }
    await db.payouts.insert_one(dict(payout))
    return strip_mongo(await db.payouts.find_one({"payout_id": payout["payout_id"]}, {"_id": 0}))


# ---------------------------------------------------------------- ads/marketing/docs


@router.get("/ads")
async def p_ads_list(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    rows = (
        await db.ad_campaigns.find({"partner_id": p["partner_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(200)
    )
    return {"items": rows}


@router.post("/ads")
async def p_ads_create(body: AdCampaignIn, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    camp = {
        "campaign_id": new_id("cmp"),
        "partner_id": p["partner_id"],
        "name": body.name,
        "campaign_type": body.campaign_type,
        "budget": body.budget,
        "daily_budget": body.daily_budget,
        "start_date": body.start_date,
        "end_date": body.end_date,
        "target_city": body.target_city,
        "target_area": body.target_area,
        "target_category": body.target_category,
        "status": "Active",
        "impressions": random.randint(800, 5000),
        "views": random.randint(400, 3000),
        "clicks": random.randint(50, 400),
        "orders_generated": random.randint(2, 30),
        "revenue_generated": round(random.uniform(500, 8000), 2),
        "created_at": now_utc().isoformat(),
    }
    camp["ctr"] = round(camp["clicks"] * 100 / max(1, camp["impressions"]), 2)
    camp["roi"] = round(
        (camp["revenue_generated"] - camp["budget"]) * 100 / max(1, camp["budget"]), 2
    )
    await db.ad_campaigns.insert_one(dict(camp))
    return strip_mongo(
        await db.ad_campaigns.find_one({"campaign_id": camp["campaign_id"]}, {"_id": 0})
    )


@router.delete("/ads/{campaign_id}")
async def p_ads_del(campaign_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    await db.ad_campaigns.delete_one(
        {"campaign_id": campaign_id, "partner_id": p["partner_id"]}
    )
    return {"ok": True}


@router.get("/marketing")
async def p_marketing_list(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    rows = await db.partner_offers.find({"partner_id": p["partner_id"]}, {"_id": 0}).to_list(100)
    return {"items": rows}


@router.post("/marketing")
async def p_marketing_add(body: OfferIn, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    off = {
        "offer_id": new_id("off"),
        "partner_id": p["partner_id"],
        "title": body.title,
        "code": body.code.upper(),
        "discount_pct": body.discount_pct,
        "valid_till": body.valid_till,
        "created_at": now_utc().isoformat(),
    }
    await db.partner_offers.insert_one(dict(off))
    return strip_mongo(
        await db.partner_offers.find_one({"offer_id": off["offer_id"]}, {"_id": 0})
    )


@router.delete("/marketing/{offer_id}")
async def p_marketing_del(offer_id: str, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    await db.partner_offers.delete_one({"offer_id": offer_id, "partner_id": p["partner_id"]})
    return {"ok": True}


@router.get("/documents")
async def p_docs(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    rows = await db.partner_documents.find({"partner_id": p["partner_id"]}, {"_id": 0}).to_list(50)
    return {"items": rows}


@router.post("/documents")
async def p_docs_add(body: DocumentIn, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    doc = {
        "doc_id": new_id("doc"),
        "partner_id": p["partner_id"],
        "doc_type": body.doc_type,
        "number": body.number,
        "url": body.url,
        "status": "Verified",
        "created_at": now_utc().isoformat(),
    }
    await db.partner_documents.update_one(
        {"partner_id": p["partner_id"], "doc_type": body.doc_type}, {"$set": doc}, upsert=True
    )
    return strip_mongo(
        await db.partner_documents.find_one(
            {"partner_id": p["partner_id"], "doc_type": body.doc_type}, {"_id": 0}
        )
    )


# ---------------------------------------------------------------- reports/notifs/support


@router.get("/reports")
async def p_reports(
    authorization: str | None = Header(default=None), period: str = "weekly"
):
    p = await get_partner_for_user(authorization)
    days = {
        "daily": 1,
        "weekly": 7,
        "monthly": 30,
        "service": 30,
        "revenue": 30,
        "customer": 30,
    }.get(period, 7)
    orders = await db.orders.find({"partner_id": p["partner_id"]}, {"_id": 0}).to_list(2000)
    orders = [o for o in orders if date_in_window(o.get("created_at", ""), days)]
    delivered = [o for o in orders if o.get("status") == "Delivered"]
    cancelled = [o for o in orders if o.get("status") in ["Cancelled", "Refunded"]]
    by_service: dict[str, dict] = {}
    for o in delivered:
        for it in o.get("items", []):
            s = by_service.setdefault(
                it["name"], {"name": it["name"], "qty": 0, "revenue": 0.0}
            )
            s["qty"] += it.get("qty", 0)
            s["revenue"] += float(it.get("price", 0)) * float(it.get("qty", 0))
    return {
        "period": period,
        "orders": len(orders),
        "delivered": len(delivered),
        "cancelled": len(cancelled),
        "revenue": round(sum(o.get("total", 0) for o in delivered), 2),
        "avg_order_value": round(
            sum(o.get("total", 0) for o in delivered) / max(1, len(delivered)), 2
        ),
        "by_service": sorted(
            list(by_service.values()), key=lambda x: x["revenue"], reverse=True
        ),
    }


@router.get("/notifications")
async def p_notifications(authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    rows = (
        await db.notifications.find({"user_id": user["user_id"], "audience": "partner"}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(200)
    )
    return {"items": rows}


@router.post("/support")
async def p_support(body: dict, authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    ticket = {
        "ticket_id": new_id("tkt"),
        "partner_id": p["partner_id"],
        "subject": body.get("subject", "Support request"),
        "message": body.get("message", ""),
        "status": "Open",
        "created_at": now_utc().isoformat(),
    }
    await db.support_tickets.insert_one(dict(ticket))
    return strip_mongo(
        await db.support_tickets.find_one({"ticket_id": ticket["ticket_id"]}, {"_id": 0})
    )


@router.get("/support")
async def p_support_list(authorization: str | None = Header(default=None)):
    p = await get_partner_for_user(authorization)
    rows = (
        await db.support_tickets.find({"partner_id": p["partner_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(200)
    )
    return {"items": rows}


# ---------------------------------------------------------------- demo seed


@router.post("/demo-seed")
async def p_demo_seed(authorization: str | None = Header(default=None)):
    """Idempotently populate a partner with demo orders/settlements/reviews/payouts."""
    p = await get_partner_for_user(authorization)
    if await db.orders.count_documents({"partner_id": p["partner_id"]}) > 0:
        return {"ok": True, "msg": "already seeded"}

    if await db.services.count_documents({"partner_id": p["partner_id"]}) == 0:
        proto = [
            ("Wash & Fold (per kg)", 49, "laundry", "kg"),
            ("Shirt Dry Clean", 99, "dry-cleaning", "piece"),
            ("Ironing", 9, "ironing", "piece"),
        ]
        for name, price, cat, unit in proto:
            await db.services.insert_one(
                {
                    "service_id": new_id("srv"),
                    "partner_id": p["partner_id"],
                    "name": name,
                    "category": cat,
                    "price": price,
                    "unit": unit,
                    "enabled": True,
                    "image": p.get("image"),
                }
            )

    statuses = [
        "Order Created",
        "Partner Assigned",
        "Processing",
        "Ready",
        "Out For Delivery",
        "Delivered",
        "Delivered",
        "Cancelled",
    ]
    demo_users = ["Aarav Sharma", "Priya Patel", "Rohan Mehta", "Anika Singh", "Vikram Iyer"]
    for i, st in enumerate(statuses):
        uid = new_id("usr")
        await db.users.update_one(
            {"user_id": uid},
            {
                "$setOnInsert": {
                    "user_id": uid,
                    "name": demo_users[i % len(demo_users)],
                    "phone": f"9000000{i:02d}",
                }
            },
            upsert=True,
        )
        total = round(random.uniform(149, 1499), 2)
        created = (
            now_utc() - timedelta(days=random.randint(0, 30), hours=random.randint(0, 23))
        ).isoformat()
        order = {
            "order_id": new_id("ord"),
            "user_id": uid,
            "partner_id": p["partner_id"],
            "partner_name": p["name"],
            "partner_image": p.get("image"),
            "items": [{"service_id": "demo", "name": "Wash & Fold", "price": total, "qty": 1}],
            "address": {
                "label": "Home",
                "line1": "Sector 18",
                "city": "Noida",
                "pincode": "201301",
            },
            "pickup_date": "Tomorrow",
            "pickup_slot": "10 - 12 PM",
            "payment_method": random.choice(["UPI", "COD", "CARD"]),
            "subtotal": total,
            "delivery_fee": 29,
            "discount": 0,
            "wallet_used": 0,
            "total": total,
            "status": st,
            "timeline": [
                {"status": "Order Created", "at": created},
                {"status": st, "at": created},
            ],
            "rider": {
                "name": "Rohit Sharma",
                "phone": "+91 98765 43210",
                "vehicle": "DL 8C XX 1234",
                "rating": 4.8,
                "picture": "https://images.unsplash.com/photo-1617347454431-f49d7ff5c3b1?w=200",
            },
            "eta_minutes": 45,
            "created_at": created,
        }
        await db.orders.insert_one(dict(order))
        if st == "Delivered":
            gross = total
            commission = round(gross * 0.10, 2)
            await db.settlements.insert_one(
                {
                    "settlement_id": new_id("stl"),
                    "partner_id": p["partner_id"],
                    "order_id": order["order_id"],
                    "gross": gross,
                    "commission": commission,
                    "amount": round(gross - commission, 2),
                    "status": random.choice(["Pending", "Approved", "Paid"]),
                    "created_at": created,
                }
            )

    for i in range(4):
        await db.reviews.insert_one(
            {
                "review_id": new_id("rev"),
                "partner_id": p["partner_id"],
                "user_id": new_id("usr"),
                "user_name": demo_users[i % len(demo_users)],
                "rating": random.choice([5, 5, 4, 5, 3]),
                "comment": random.choice(
                    [
                        "Clothes came back smelling amazing!",
                        "Fast and reliable, will order again.",
                        "Slight delay but quality was good.",
                        "Perfect pressing on my shirts.",
                    ]
                ),
                "created_at": (now_utc() - timedelta(days=random.randint(0, 20))).isoformat(),
            }
        )

    await db.payouts.insert_one(
        {
            "payout_id": new_id("po"),
            "partner_id": p["partner_id"],
            "amount": 4500.00,
            "status": "Paid",
            "created_at": (now_utc() - timedelta(days=7)).isoformat(),
        }
    )

    await db.notifications.insert_one(
        {
            "notif_id": new_id("ntf"),
            "user_id": p["user_id"],
            "audience": "partner",
            "title": "Welcome to QuickPress Partner",
            "body": "Your store is approved. Toggle Go Online to start receiving orders!",
            "type": "system",
            "read": False,
            "created_at": now_utc().isoformat(),
        }
    )
    return {"ok": True}
