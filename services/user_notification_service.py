"""
In-App Notification Service for WAJ VTU.
Manages user notifications for wallet credits, purchases, referral bonuses, and deal alerts.
"""
import logging
from datetime import datetime, timezone
from models import db, UserNotification

logger = logging.getLogger(__name__)


def create_user_notification(user_id, title, message, notification_type="SYSTEM", link_url=None):
    """Creates a new in-app notification for a user."""
    if not user_id or not title:
        return None
    try:
        notif = UserNotification(
            user_id=int(user_id),
            title=str(title).strip()[:150],
            message=str(message).strip() if message else "",
            notification_type=str(notification_type or "SYSTEM").upper(),
            link_url=str(link_url)[:255] if link_url else None,
            is_read=False,
        )
        db.session.add(notif)
        db.session.commit()
        return notif
    except Exception as exc:
        db.session.rollback()
        logger.error(f"[user_notifications] Error creating notification: {exc}")
        return None


def broadcast_user_notification(title, message, notification_type="PROMO", link_url=None):
    """Broadcasts an in-app notification to all registered website users."""
    from models import User
    try:
        users = User.query.filter(User.email.isnot(None)).all()
        notifs = []
        for u in users:
            notifs.append(
                UserNotification(
                    user_id=u.id,
                    title=str(title).strip()[:150],
                    message=str(message).strip() if message else "",
                    notification_type=str(notification_type or "PROMO").upper(),
                    link_url=str(link_url)[:255] if link_url else None,
                    is_read=False,
                )
            )
        if notifs:
            db.session.bulk_save_objects(notifs)
            db.session.commit()
        return len(notifs)
    except Exception as exc:
        db.session.rollback()
        logger.error(f"[user_notifications] Error broadcasting notification: {exc}")
        return 0


def get_user_notifications(user_id, limit=20):
    """Retrieves the recent notifications and unread count for a user."""
    if not user_id:
        return {"unread_count": 0, "notifications": []}
    try:
        unread_count = UserNotification.query.filter_by(user_id=int(user_id), is_read=False).count()
        recent = (
            UserNotification.query.filter_by(user_id=int(user_id))
            .order_by(UserNotification.created_at.desc())
            .limit(limit)
            .all()
        )

        notif_list = []
        for n in recent:
            notif_list.append({
                "id": n.id,
                "title": n.title,
                "message": n.message,
                "type": n.notification_type,
                "link_url": n.link_url,
                "is_read": n.is_read,
                "created_at": n.created_at.strftime("%b %d, %I:%M %p") if n.created_at else "",
            })
        return {
            "unread_count": unread_count,
            "notifications": notif_list,
        }
    except Exception as exc:
        logger.error(f"[user_notifications] Error fetching notifications: {exc}")
        return {"unread_count": 0, "notifications": []}


def mark_notification_as_read(notification_id, user_id):
    """Marks a single notification as read."""
    try:
        notif = UserNotification.query.filter_by(id=int(notification_id), user_id=int(user_id)).first()
        if notif:
            notif.is_read = True
            db.session.commit()
            return True
        return False
    except Exception:
        db.session.rollback()
        return False


def mark_all_notifications_as_read(user_id):
    """Marks all unread notifications for a user as read."""
    try:
        UserNotification.query.filter_by(user_id=int(user_id), is_read=False).update({"is_read": True})
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False

