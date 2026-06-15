"""Admin routes: seed demo data."""

from __future__ import annotations

from fastapi import APIRouter

from core import db, new_id

router = APIRouter(tags=["admin"])


@router.post("/admin/seed")
async def seed_db():
    """Idempotently populate customer-side demo content."""
    if await db.partners.count_documents({}) > 0:
        return {"ok": True, "msg": "already seeded"}

    categories = [
        {"slug": "laundry", "name": "Laundry", "icon": "https://images.unsplash.com/photo-1604335399105-a0c585fd81a1?w=400", "color": "#F4B400"},
        {"slug": "dry-cleaning", "name": "Dry Cleaning", "icon": "https://images.unsplash.com/photo-1489274495757-95c7c837b101?w=400", "color": "#16A34A"},
        {"slug": "ironing", "name": "Ironing", "icon": "https://images.unsplash.com/photo-1489274495757-95c7c837b101?w=400", "color": "#3B82F6"},
        {"slug": "premium-laundry", "name": "Premium Laundry", "icon": "https://images.unsplash.com/photo-1778731660255-215c9172e18d?w=400", "color": "#A855F7"},
        {"slug": "express", "name": "Express", "icon": "https://images.unsplash.com/photo-1632923565835-6582b54f2105?w=400", "color": "#EF4444"},
        {"slug": "shoe-cleaning", "name": "Shoe Cleaning", "icon": "https://images.unsplash.com/photo-1460353581641-37baddab0fa2?w=400", "color": "#06B6D4"},
        {"slug": "carpet-cleaning", "name": "Carpet", "icon": "https://images.unsplash.com/photo-1604335399105-a0c585fd81a1?w=400", "color": "#F59E0B"},
        {"slug": "curtain-cleaning", "name": "Curtain", "icon": "https://images.unsplash.com/photo-1632923565835-6582b54f2105?w=400", "color": "#10B981"},
        {"slug": "blanket-cleaning", "name": "Blanket", "icon": "https://images.unsplash.com/photo-1778731660255-215c9172e18d?w=400", "color": "#8B5CF6"},
    ]
    await db.categories.insert_many([dict(c) for c in categories])

    banners = [
        {"banner_id": new_id("bnr"), "title": "Flat 50% Off", "subtitle": "On first order. Code: FRESH50", "image": "https://images.unsplash.com/photo-1778731660255-215c9172e18d?w=1200", "cta": "Order Now"},
        {"banner_id": new_id("bnr"), "title": "QuickPress Plus", "subtitle": "Free pickup & delivery", "image": "https://images.unsplash.com/photo-1545873509-33e944ca7655?w=1200", "cta": "Join Plus"},
        {"banner_id": new_id("bnr"), "title": "Express in 6 Hours", "subtitle": "Fresh clothes, super fast", "image": "https://images.unsplash.com/photo-1632923565835-6582b54f2105?w=1200", "cta": "Try Express"},
    ]
    await db.banners.insert_many([dict(b) for b in banners])

    partner_blueprints = [
        {"name": "FreshFold Premium", "image": "https://images.unsplash.com/photo-1778731660255-215c9172e18d?w=800", "logo": "https://images.unsplash.com/photo-1604335399105-a0c585fd81a1?w=200", "rating": 4.8, "reviews": 1287, "distance_km": 0.9, "eta": "60 min", "min_price": 49, "sponsored": True, "categories": ["laundry", "premium-laundry"]},
        {"name": "Sparkle Dry Clean", "image": "https://images.unsplash.com/photo-1489274495757-95c7c837b101?w=800", "logo": "https://images.unsplash.com/photo-1632923565835-6582b54f2105?w=200", "rating": 4.7, "reviews": 932, "distance_km": 1.4, "eta": "75 min", "min_price": 99, "sponsored": False, "categories": ["dry-cleaning", "premium-laundry"]},
        {"name": "Iron King", "image": "https://images.unsplash.com/photo-1489274495757-95c7c837b101?w=800", "logo": "https://images.unsplash.com/photo-1778731660255-215c9172e18d?w=200", "rating": 4.6, "reviews": 654, "distance_km": 2.1, "eta": "50 min", "min_price": 9, "sponsored": False, "categories": ["ironing"]},
        {"name": "ShoeSpa", "image": "https://images.unsplash.com/photo-1460353581641-37baddab0fa2?w=800", "logo": "https://images.unsplash.com/photo-1460353581641-37baddab0fa2?w=200", "rating": 4.9, "reviews": 412, "distance_km": 3.2, "eta": "120 min", "min_price": 199, "sponsored": True, "categories": ["shoe-cleaning"]},
        {"name": "Express Wash Co", "image": "https://images.unsplash.com/photo-1632923565835-6582b54f2105?w=800", "logo": "https://images.unsplash.com/photo-1604335399105-a0c585fd81a1?w=200", "rating": 4.5, "reviews": 2104, "distance_km": 1.1, "eta": "30 min", "min_price": 79, "sponsored": False, "categories": ["express", "laundry"]},
        {"name": "Royal Carpet Care", "image": "https://images.unsplash.com/photo-1604335399105-a0c585fd81a1?w=800", "logo": "https://images.unsplash.com/photo-1778731660255-215c9172e18d?w=200", "rating": 4.7, "reviews": 287, "distance_km": 4.0, "eta": "180 min", "min_price": 299, "sponsored": False, "categories": ["carpet-cleaning", "curtain-cleaning", "blanket-cleaning"]},
    ]

    partners = []
    for p in partner_blueprints:
        partners.append(
            {
                "partner_id": new_id("ptr"),
                "is_open": True,
                "address": "Sector 18, Noida",
                "about": "We use eco-friendly detergents and ISO-certified processes. Your clothes are in safe hands.",
                "gallery": [
                    "https://images.unsplash.com/photo-1778731660255-215c9172e18d?w=800",
                    "https://images.unsplash.com/photo-1632923565835-6582b54f2105?w=800",
                    "https://images.unsplash.com/photo-1604335399105-a0c585fd81a1?w=800",
                ],
                "working_hours": "8:00 AM – 10:00 PM",
                **p,
            }
        )
    await db.partners.insert_many([dict(p) for p in partners])

    service_templates = {
        "laundry": [("Wash & Fold (per kg)", 49), ("Wash & Iron (per kg)", 79), ("Bedsheet wash", 99)],
        "dry-cleaning": [("Shirt", 99), ("Trouser", 129), ("Suit (2pc)", 399), ("Saree", 299)],
        "ironing": [("Shirt", 9), ("Trouser", 12), ("Kurta", 15)],
        "premium-laundry": [("Premium Wash (per kg)", 149), ("Silk Garment", 249)],
        "express": [("Express Wash & Fold (per kg)", 99), ("Express Iron (per piece)", 19)],
        "shoe-cleaning": [("Sneaker Deep Clean", 299), ("Leather Shoe Polish", 199)],
        "carpet-cleaning": [("Carpet (sqft)", 19)],
        "curtain-cleaning": [("Curtain (per panel)", 99)],
        "blanket-cleaning": [("Blanket Single", 149), ("Blanket Double", 249)],
    }
    services = []
    for p in partners:
        for cat in p["categories"]:
            for name, price in service_templates.get(cat, []):
                services.append(
                    {
                        "service_id": new_id("srv"),
                        "partner_id": p["partner_id"],
                        "category": cat,
                        "name": name,
                        "price": price,
                        "unit": "kg" if "kg" in name else "piece",
                        "image": p["image"],
                    }
                )
    await db.services.insert_many([dict(s) for s in services])

    coupons = [
        {"code": "FRESH50", "title": "50% off first order", "subtitle": "Up to ₹150", "type": "percent", "value": 50, "expires": "2026-12-31"},
        {"code": "WELCOME100", "title": "Flat ₹100 off", "subtitle": "On orders above ₹399", "type": "flat", "value": 100, "expires": "2026-12-31"},
        {"code": "EXPRESS25", "title": "25% off Express", "subtitle": "Express orders only", "type": "percent", "value": 25, "expires": "2026-12-31"},
    ]
    await db.coupons.insert_many([dict(c) for c in coupons])

    ads = [
        {"ad_id": new_id("ad"), "placement": "home_recommended", "title": "FreshFold Premium", "image": "https://images.unsplash.com/photo-1778731660255-215c9172e18d?w=600", "partner_id": partners[0]["partner_id"]},
        {"ad_id": new_id("ad"), "placement": "search_boost", "title": "Sparkle Dry Clean", "image": "https://images.unsplash.com/photo-1489274495757-95c7c837b101?w=600", "partner_id": partners[1]["partner_id"]},
    ]
    await db.ads.insert_many([dict(a) for a in ads])

    return {"ok": True, "seeded": {"partners": len(partners), "services": len(services), "categories": len(categories)}}
