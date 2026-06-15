"""Auth routes: OTP send/verify, Google session, me, logout."""

from __future__ import annotations

import random
import string
import uuid
from datetime import timedelta

import httpx
from fastapi import APIRouter, HTTPException, Header

from core import (
    db,
    ensure_partner_for_user,
    get_user_from_token,
    new_id,
    now_utc,
    strip_mongo,
)
from models import GoogleSessionIn, OtpSendIn, OtpVerifyIn

router = APIRouter(tags=["auth"])


@router.post("/auth/otp/send")
async def otp_send(body: OtpSendIn):
    otp = "".join(random.choices(string.digits, k=4))
    await db.otps.update_one(
        {"phone": body.phone},
        {"$set": {"otp": otp, "created_at": now_utc().isoformat()}},
        upsert=True,
    )
    return {"ok": True, "phone": body.phone, "otp": otp, "demo_note": "Mocked OTP (no SMS gateway)"}


@router.post("/auth/otp/verify")
async def otp_verify(body: OtpVerifyIn):
    record = await db.otps.find_one({"phone": body.phone}, {"_id": 0})
    if not record or record.get("otp") != body.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
    user = await db.users.find_one({"phone": body.phone}, {"_id": 0})
    if not user:
        user = {
            "user_id": new_id("usr"),
            "phone": body.phone,
            "name": body.name or f"User {body.phone[-4:]}",
            "email": None,
            "picture": None,
            "wallet_balance": 250.0,
            "referral_code": ("QP" + uuid.uuid4().hex[:6]).upper(),
            "is_plus": False,
            "role": body.role,
            "created_at": now_utc().isoformat(),
        }
        await db.users.insert_one(dict(user))
        if body.role == "partner":
            await ensure_partner_for_user(user)
    elif body.role == "partner" and user.get("role") != "partner":
        await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"role": "partner"}})
        user["role"] = "partner"
        await ensure_partner_for_user(user)
    token = uuid.uuid4().hex + uuid.uuid4().hex
    await db.user_sessions.insert_one(
        {
            "session_token": token,
            "user_id": user["user_id"],
            "expires_at": now_utc() + timedelta(days=7),
            "created_at": now_utc(),
        }
    )
    await db.otps.delete_one({"phone": body.phone})
    return {"token": token, "user": strip_mongo(user)}


@router.post("/auth/google/session")
async def google_session(body: GoogleSessionIn):
    async with httpx.AsyncClient(timeout=15.0) as hx:
        resp = await hx.get(
            "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
            headers={"X-Session-ID": body.session_id},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="OAuth session invalid")
    data = resp.json()
    email = data["email"]
    user = await db.users.find_one({"email": email}, {"_id": 0})
    if not user:
        user = {
            "user_id": new_id("usr"),
            "phone": None,
            "name": data.get("name", email.split("@")[0]),
            "email": email,
            "picture": data.get("picture"),
            "wallet_balance": 250.0,
            "referral_code": ("QP" + uuid.uuid4().hex[:6]).upper(),
            "is_plus": False,
            "role": body.role,
            "created_at": now_utc().isoformat(),
        }
        await db.users.insert_one(dict(user))
        if body.role == "partner":
            await ensure_partner_for_user(user)
    elif body.role == "partner" and user.get("role") != "partner":
        await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"role": "partner"}})
        user["role"] = "partner"
        await ensure_partner_for_user(user)
    token = data["session_token"]
    await db.user_sessions.update_one(
        {"session_token": token},
        {
            "$set": {
                "session_token": token,
                "user_id": user["user_id"],
                "expires_at": now_utc() + timedelta(days=7),
                "created_at": now_utc(),
            }
        },
        upsert=True,
    )
    return {"token": token, "user": strip_mongo(user)}


@router.get("/auth/me")
async def auth_me(authorization: str | None = Header(default=None)):
    user = await get_user_from_token(authorization)
    return {"user": user}


@router.post("/auth/logout")
async def auth_logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "", 1).strip()
        await db.user_sessions.delete_one({"session_token": token})
    return {"ok": True}
