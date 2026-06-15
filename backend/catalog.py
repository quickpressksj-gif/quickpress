"""Public catalog routes: categories, banners, partners, services, ads, coupons."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from core import db

router = APIRouter(tags=["catalog"])


@router.get("/categories")
async def list_categories():
    rows = await db.categories.find({}, {"_id": 0}).to_list(100)
    return {"items": rows}


@router.get("/banners")
async def list_banners():
    rows = await db.banners.find({}, {"_id": 0}).to_list(100)
    return {"items": rows}


@router.get("/partners")
async def list_partners(category: str | None = None, sort: str | None = None):
    query: dict[str, Any] = {}
    if category:
        query["categories"] = category
    rows = await db.partners.find(query, {"_id": 0}).to_list(200)
    if sort == "rating":
        rows.sort(key=lambda r: r.get("rating", 0), reverse=True)
    elif sort == "distance":
        rows.sort(key=lambda r: r.get("distance_km", 99))
    return {"items": rows}


@router.get("/partners/{partner_id}")
async def get_partner(partner_id: str):
    p = await db.partners.find_one({"partner_id": partner_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Partner not found")
    services = await db.services.find({"partner_id": partner_id}, {"_id": 0}).to_list(200)
    reviews = await db.reviews.find({"partner_id": partner_id}, {"_id": 0}).to_list(50)
    return {"partner": p, "services": services, "reviews": reviews}


@router.get("/services")
async def list_services(partner_id: str | None = None):
    query = {"partner_id": partner_id} if partner_id else {}
    rows = await db.services.find(query, {"_id": 0}).to_list(500)
    return {"items": rows}


@router.get("/ads")
async def list_ads(placement: str | None = None):
    query = {"placement": placement} if placement else {}
    rows = await db.ads.find(query, {"_id": 0}).to_list(50)
    return {"items": rows}


@router.get("/coupons")
async def list_coupons():
    rows = await db.coupons.find({}, {"_id": 0}).to_list(50)
    return {"items": rows}
