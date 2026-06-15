"""Customer profile routes: wallet, addresses, favorites, notifications, referrals, membership."""

from __future__ import annotations

from fastapi import APIRouter, Header

from core import db, get_user_from_token, new_id, now_utc, strip_mongo
from models import AddressIn, WalletTopupIn

router = APIRouter(tags=["customer"])


# --------------------------------------------------------------- wallet


@router.get("/wallet")
async def wallet_get(authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    txs = (
        await db.wallet_transactions.find({"user_id": user["user_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(100)
    )
    return {"balance": user.get("wallet_balance", 0.0), "transactions": txs}


@router.post("/wallet/topup")
async def wallet_topup(body: WalletTopupIn, authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    await db.users.update_one(
        {"user_id": user["user_id"]}, {"$inc": {"wallet_balance": body.amount}}
    )
    await db.wallet_transactions.insert_one(
        {
            "user_id": user["user_id"],
            "type": "credit",
            "amount": body.amount,
            "reason": "Wallet top-up",
            "created_at": now_utc().isoformat(),
        }
    )
    return {"ok": True}


# --------------------------------------------------------------- addresses


@router.get("/addresses")
async def list_addresses(authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    rows = await db.addresses.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(50)
    return {"items": rows}


@router.post("/addresses")
async def add_address(body: AddressIn, authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    addr = {"address_id": new_id("adr"), "user_id": user["user_id"], **body.dict()}
    await db.addresses.insert_one(dict(addr))
    return strip_mongo(await db.addresses.find_one({"address_id": addr["address_id"]}, {"_id": 0}))


@router.delete("/addresses/{address_id}")
async def delete_address(address_id: str, authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    await db.addresses.delete_one({"address_id": address_id, "user_id": user["user_id"]})
    return {"ok": True}


# --------------------------------------------------------------- favorites


@router.get("/favorites")
async def list_favorites(authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    rows = await db.favorites.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(200)
    partner_ids = [r["partner_id"] for r in rows]
    partners = (
        await db.partners.find({"partner_id": {"$in": partner_ids}}, {"_id": 0}).to_list(200)
        if partner_ids
        else []
    )
    return {"items": partners}


@router.post("/favorites/{partner_id}")
async def toggle_favorite(partner_id: str, authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    existing = await db.favorites.find_one(
        {"user_id": user["user_id"], "partner_id": partner_id}, {"_id": 0}
    )
    if existing:
        await db.favorites.delete_one({"user_id": user["user_id"], "partner_id": partner_id})
        return {"favorited": False}
    await db.favorites.insert_one(
        {"user_id": user["user_id"], "partner_id": partner_id, "created_at": now_utc().isoformat()}
    )
    return {"favorited": True}


# --------------------------------------------------------------- notifications


@router.get("/notifications")
async def list_notifications(authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    rows = (
        await db.notifications.find({"user_id": user["user_id"]}, {"_id": 0})
        .sort("created_at", -1)
        .to_list(200)
    )
    return {"items": rows}


@router.post("/notifications/{notif_id}/read")
async def mark_notif_read(notif_id: str, authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    await db.notifications.update_one(
        {"notif_id": notif_id, "user_id": user["user_id"]}, {"$set": {"read": True}}
    )
    return {"ok": True}


# --------------------------------------------------------------- referrals


@router.get("/referrals")
async def referrals(authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    refs = await db.referrals.find({"referrer_id": user["user_id"]}, {"_id": 0}).to_list(100)
    return {
        "code": user.get("referral_code"),
        "earnings": sum(r.get("amount", 0) for r in refs),
        "invited": len(refs),
        "history": refs,
    }


# --------------------------------------------------------------- membership


@router.post("/membership/subscribe")
async def membership_subscribe(authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"is_plus": True}})
    return {"ok": True}
