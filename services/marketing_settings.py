"""
Configuration and settings management for WAJ VTU Marketing Automation.
All marketing parameters are database-backed and admin-configurable.
"""
from decimal import Decimal
from datetime import datetime, timezone
from models import db, MarketingSetting


DEFAULT_SETTINGS = {
    "REFERRAL_ENABLED": {
        "value": "true",
        "description": "Enable or disable the customer referral system."
    },
    "REFERRAL_REWARD_AMOUNT": {
        "value": "100.00",
        "description": "Referral reward amount in NGN credited to the referrer's wallet."
    },
    "REFERRAL_MIN_TRANSACTION": {
        "value": "500.00",
        "description": "Minimum successful transaction amount (NGN) for a referral reward to qualify."
    },
    "REFERRAL_REWARD_ON_FIRST_ONLY": {
        "value": "true",
        "description": "Reward only on the referred customer's first qualifying transaction."
    },
    "REFERRAL_MAX_REWARDS_PER_REFERRER": {
        "value": "0",
        "description": "Maximum rewards a single referrer can earn (0 = unlimited)."
    },
    "DAILY_DEAL_ENABLED": {
        "value": "true",
        "description": "Enable daily data deal automatic generation."
    },
    "DAILY_DEAL_GENERATION_TIME": {
        "value": "08:00",
        "description": "Time of day (HH:MM in Africa/Lagos) for automatic deal generation."
    },
    "REENGAGEMENT_7_DAYS_ENABLED": {
        "value": "true",
        "description": "Enable 7-day inactivity segment re-engagement."
    },
    "REENGAGEMENT_14_DAYS_ENABLED": {
        "value": "true",
        "description": "Enable 14-day inactivity segment re-engagement."
    },
    "REENGAGEMENT_30_DAYS_ENABLED": {
        "value": "true",
        "description": "Enable 30-day inactivity segment re-engagement."
    },
    "REENGAGEMENT_7_DAYS_TEMPLATE": {
        "value": (
            "👋 We haven't seen you on WAJ VTU in a while!\n\n"
            "Need data or airtime today? 📶\n\n"
            "Check our latest offers:\n{website_url}\n\n"
            "WAJ VTU — Fast. Simple. Reliable."
        ),
        "description": "Message template for 7-day inactive customers."
    },
    "REENGAGEMENT_14_DAYS_TEMPLATE": {
        "value": (
            "🔥 New data deals are available on WAJ VTU!\n\n"
            "MTN • Glo • Airtel\n\n"
            "Come check today's offers:\n{website_url}\n\n"
            "WAJ VTU — Fast. Simple. Reliable."
        ),
        "description": "Message template for 14-day inactive customers."
    },
    "REENGAGEMENT_30_DAYS_TEMPLATE": {
        "value": (
            "We miss you! ❤️\n\n"
            "Come back to WAJ VTU and check out our latest affordable plans.\n\n"
            "{website_url}\n\n"
            "WAJ VTU — Fast. Simple. Reliable."
        ),
        "description": "Message template for 30-day inactive customers."
    },
}


def seed_default_marketing_settings():
    """Ensure all default marketing settings exist in the database."""
    now = datetime.now(timezone.utc)
    for key, data in DEFAULT_SETTINGS.items():
        existing = MarketingSetting.query.filter_by(key=key).first()
        if not existing:
            db.session.add(MarketingSetting(
                key=key,
                value=data["value"],
                description=data.get("description", ""),
                updated_at=now
            ))
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def get_marketing_setting(key, default=None):
    """Retrieve raw string setting value."""
    record = MarketingSetting.query.filter_by(key=key).first()
    if record is not None and record.value is not None:
        return record.value
    if default is not None:
        return default
    if key in DEFAULT_SETTINGS:
        return DEFAULT_SETTINGS[key]["value"]
    return default


def get_setting_decimal(key, default="0.00"):
    """Retrieve setting parsed as Decimal."""
    val = get_marketing_setting(key, default)
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal(str(default))


def get_setting_bool(key, default=False):
    """Retrieve setting parsed as boolean."""
    val = get_marketing_setting(key, str(default))
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def get_setting_int(key, default=0):
    """Retrieve setting parsed as integer."""
    val = get_marketing_setting(key, str(default))
    try:
        return int(val)
    except Exception:
        return int(default)


def set_marketing_setting(key, value, description=None):
    """Create or update a marketing setting."""
    now = datetime.now(timezone.utc)
    record = MarketingSetting.query.filter_by(key=key).first()
    if record:
        record.value = str(value)
        if description:
            record.description = description
        record.updated_at = now
    else:
        record = MarketingSetting(
            key=key,
            value=str(value),
            description=description or DEFAULT_SETTINGS.get(key, {}).get("description", ""),
            updated_at=now
        )
        db.session.add(record)
    db.session.commit()
    return record


def get_all_marketing_settings():
    """Retrieve all settings as a key -> dict mapping."""
    rows = MarketingSetting.query.all()
    result = {}
    for r in rows:
        result[r.key] = {
            "value": r.value,
            "description": r.description or "",
            "updated_at": r.updated_at
        }
    return result
