"""
Web Push Notification Service for WAJ VTU.
Manages browser push subscriptions and broadcasts alerts via VAPID / pywebpush.
"""
import os
import json
import logging
from datetime import datetime, timezone
from flask import current_app
from pywebpush import webpush, WebPushException

from models import db, PushSubscription

logger = logging.getLogger(__name__)


def get_vapid_public_key():
    return (
        os.getenv("VAPID_PUBLIC_KEY")
        or (current_app.config.get("VAPID_PUBLIC_KEY") if current_app else "")
        or "BLMQBmguZC6QUa_njPJ9qKhuqroPSDFPI7prLrHkw7YJyd5xCa2-QdTbhtzwWet2GIDr1C42OtFSBiKod-TnCsI"
    ).strip()


def get_vapid_private_key():
    return (
        os.getenv("VAPID_PRIVATE_KEY")
        or (current_app.config.get("VAPID_PRIVATE_KEY") if current_app else "")
        or "Vqc8IFTiTkX44liuu53zV97W29kC8u3fihAMKVlBp34"
    ).strip()


def get_vapid_claims():
    email = os.getenv("VAPID_CLAIM_EMAIL", "mailto:oadedamola07@gmail.com").strip()
    if not email.startswith("mailto:"):
        email = f"mailto:{email}"
    return {"sub": email}


def save_subscription(sub_data, user_id=None, user_agent=None):
    """
    Saves or updates a browser Web Push subscription.
    Accepts standard PushSubscription JSON:
    {"endpoint": "...", "keys": {"p256dh": "...", "auth": "..."}}
    """
    if not sub_data or not isinstance(sub_data, dict):
        return None, "Invalid subscription payload."

    endpoint = (sub_data.get("endpoint") or "").strip()
    keys = sub_data.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()

    if not endpoint or not p256dh or not auth:
        return None, "Missing endpoint, p256dh, or auth keys."

    try:
        sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
        if not sub:
            sub = PushSubscription(
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                user_id=user_id,
                user_agent=(user_agent or "")[:250] if user_agent else None,
            )
            db.session.add(sub)
        else:
            sub.p256dh = p256dh
            sub.auth = auth
            if user_id:
                sub.user_id = user_id
            if user_agent:
                sub.user_agent = user_agent[:250]
        db.session.commit()
        return sub, "Subscribed successfully."
    except Exception as exc:
        db.session.rollback()
        logger.error(f"[push_service] Error saving subscription: {exc}")
        return None, str(exc)


def remove_subscription(endpoint):
    """Removes a subscription when user opts out or unregisters."""
    if not endpoint:
        return False
    try:
        sub = PushSubscription.query.filter_by(endpoint=endpoint.strip()).first()
        if sub:
            db.session.delete(sub)
            db.session.commit()
            return True
        return False
    except Exception as exc:
        db.session.rollback()
        logger.error(f"[push_service] Error removing subscription: {exc}")
        return False


def send_single_push(subscription, payload):
    """
    Sends one push notification to a specific PushSubscription.
    Automatically removes dead/expired subscriptions (HTTP 404 / 410).
    """
    sub_info = {
        "endpoint": subscription.endpoint,
        "keys": {
            "p256dh": subscription.p256dh,
            "auth": subscription.auth,
        },
    }

    private_key = get_vapid_private_key()
    claims = get_vapid_claims()

    data_str = json.dumps(payload) if isinstance(payload, dict) else str(payload)

    try:
        response = webpush(
            subscription_info=sub_info,
            data=data_str,
            vapid_private_key=private_key,
            vapid_claims=claims,
            timeout=10,
        )
        return True, "Delivered"
    except WebPushException as exc:
        # Check if subscription has expired or unsubscribed
        status_code = getattr(exc.response, "status_code", None) if hasattr(exc, "response") else None
        if status_code in (404, 410):
            try:
                db.session.delete(subscription)
                db.session.commit()
                logger.info(f"[push_service] Deleted expired subscription {subscription.id} (HTTP {status_code})")
            except Exception:
                db.session.rollback()
            return False, f"Expired (HTTP {status_code})"
        logger.warning(f"[push_service] WebPush delivery failure: {exc}")
        return False, str(exc)
    except Exception as exc:
        logger.error(f"[push_service] Unexpected push error: {exc}")
        return False, str(exc)


def broadcast_push(title, body, url=None, icon=None):
    """
    Broadcasts a push notification to all active subscribers.
    """
    payload = {
        "title": title or "WAJ VTU Deal Alert 🚀",
        "body": body or "Check our latest data and airtime discounts!",
        "url": url or "/",
        "icon": icon or "/static/images/waj-vtu-logo.jpeg",
    }

    subscriptions = PushSubscription.query.all()
    sent = 0
    failed = 0

    for sub in subscriptions:
        ok, _ = send_single_push(sub, payload)
        if ok:
            sent += 1
        else:
            failed += 1

    return {
        "total": len(subscriptions),
        "sent": sent,
        "failed": failed,
    }

