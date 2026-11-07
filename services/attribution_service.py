"""
Social Media Attribution & Campaign Analytics Service for WAJ VTU.
Calculates visits, signups, transactions, and revenue attributed to social media
campaigns (WhatsApp Status, WhatsApp Groups, Twitter/X, Instagram, Facebook, Web Push).
"""
import logging
from decimal import Decimal
from sqlalchemy import func
from models import db, User, Transaction, SocialTrafficClick

logger = logging.getLogger(__name__)

PLATFORM_DEFINITIONS = {
    "whatsapp_status": {
        "name": "WhatsApp Status",
        "icon": "📱",
        "color": "#16a34a",
        "bg": "#f0fdf4",
        "border": "#86efac",
    },
    "whatsapp_group": {
        "name": "WhatsApp Groups & Broadcast",
        "icon": "📢",
        "color": "#0d9488",
        "bg": "#f0fdfa",
        "border": "#99f6e4",
    },
    "twitter": {
        "name": "Twitter / X",
        "icon": "✖️",
        "color": "#09090b",
        "bg": "#f4f4f5",
        "border": "#d4d4d8",
    },
    "instagram": {
        "name": "Instagram",
        "icon": "📸",
        "color": "#db2777",
        "bg": "#fdf2f8",
        "border": "#fbcfe8",
    },
    "facebook": {
        "name": "Facebook",
        "icon": "📘",
        "color": "#2563eb",
        "bg": "#eff6ff",
        "border": "#bfdbfe",
    },
    "web_push": {
        "name": "Web Push (Daily Deals)",
        "icon": "🔔",
        "color": "#ea580c",
        "bg": "#fff7ed",
        "border": "#fed7aa",
    },
}


def get_social_attribution_metrics():
    """
    Returns aggregated traffic, signups, transactions, and revenue
    for each promotional channel.
    """
    metrics = {}
    for key, info in PLATFORM_DEFINITIONS.items():
        metrics[key] = {
            "key": key,
            "name": info["name"],
            "icon": info["icon"],
            "color": info["color"],
            "bg": info["bg"],
            "border": info["border"],
            "clicks": 0,
            "signups": 0,
            "orders": 0,
            "revenue": Decimal("0.00"),
            "conversion_rate": 0.0,
        }

    # 1. Clicks from SocialTrafficClick
    try:
        click_rows = (
            db.session.query(
                SocialTrafficClick.utm_source,
                func.count(SocialTrafficClick.id).label("count")
            )
            .group_by(SocialTrafficClick.utm_source)
            .all()
        )
        for src, cnt in click_rows:
            s_key = (src or "").lower().strip()
            if s_key in metrics:
                metrics[s_key]["clicks"] = int(cnt or 0)
    except Exception as exc:
        logger.warning(f"[attribution_service] Could not query clicks: {exc}")

    # 2. Signups from User.utm_source
    try:
        user_rows = (
            db.session.query(
                User.utm_source,
                func.count(User.id).label("count")
            )
            .filter(User.utm_source.isnot(None))
            .group_by(User.utm_source)
            .all()
        )
        for src, cnt in user_rows:
            s_key = (src or "").lower().strip()
            if s_key in metrics:
                metrics[s_key]["signups"] = int(cnt or 0)
    except Exception as exc:
        logger.warning(f"[attribution_service] Could not query signups: {exc}")

    # 3. Successful Transactions & Revenue by User.utm_source
    try:
        tx_rows = (
            db.session.query(
                User.utm_source,
                func.count(Transaction.id).label("orders"),
                func.coalesce(func.sum(Transaction.amount), 0).label("revenue")
            )
            .join(User, Transaction.user_id == User.id)
            .filter(User.utm_source.isnot(None))
            .filter(Transaction.status == "SUCCESS")
            .group_by(User.utm_source)
            .all()
        )
        for src, orders, rev in tx_rows:
            s_key = (src or "").lower().strip()
            if s_key in metrics:
                metrics[s_key]["orders"] = int(orders or 0)
                metrics[s_key]["revenue"] = Decimal(str(rev or 0))
    except Exception as exc:
        logger.warning(f"[attribution_service] Could not query transactions: {exc}")

    # 4. Calculate conversion rates & totals
    total_clicks = 0
    total_signups = 0
    total_orders = 0
    total_revenue = Decimal("0.00")

    for key, data in metrics.items():
        clicks = data["clicks"]
        signups = data["signups"]
        if clicks > 0:
            data["conversion_rate"] = round((signups / clicks) * 100, 1)
        elif signups > 0:
            data["conversion_rate"] = 100.0

        total_clicks += clicks
        total_signups += signups
        total_orders += data["orders"]
        total_revenue += data["revenue"]

    # Identify top channel
    top_channel = None
    max_rev = Decimal("0.00")
    for key, data in metrics.items():
        if data["revenue"] > max_rev or (max_rev == Decimal("0.00") and data["signups"] > 0):
            max_rev = data["revenue"]
            top_channel = data["name"]

    return {
        "channels": list(metrics.values()),
        "summary": {
            "total_clicks": total_clicks,
            "total_signups": total_signups,
            "total_orders": total_orders,
            "total_revenue": total_revenue,
            "top_channel": top_channel or "Building Audience",
        }
    }

