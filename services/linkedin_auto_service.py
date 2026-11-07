"""
Automated LinkedIn Posting Service for WAJ VTU.
Automatically broadcasts the daily deal of the day to LinkedIn at 8:00 AM Lagos time.
Idempotent per calendar day (Africa/Lagos timezone).
"""
import json
import logging
from datetime import datetime, timezone
from models import db, MarketingCampaign
from services.linkedin_service import post_to_linkedin, is_token_valid
from services.marketing_settings import get_marketing_setting, set_marketing_setting, get_setting_bool
from services.deal_service import generate_daily_deal, get_lagos_today
from services.social_campaign_service import generate_social_pack

logger = logging.getLogger("linkedin_auto_service")


def send_daily_deal_linkedin(force: bool = False) -> dict:
    """
    Auto-generates today's deal and posts it directly to LinkedIn.
    Idempotent: runs at most once per calendar day unless force=True.

    Returns dict with status:
      - {"status": "success", "date": ..., "post_urn": ..., "url": ...}
      - {"status": "skipped", "reason": ...}
      - {"status": "disabled", "reason": ...}
      - {"status": "error", "reason": ...}
    """
    today_str = str(get_lagos_today())
    setting_key = f"LINKEDIN_POST_SENT_{today_str}"

    if not force:
        already_sent = get_marketing_setting(setting_key)
        if already_sent == "true":
            logger.info("[linkedin_auto] Already posted to LinkedIn today (%s). Skipping.", today_str)
            return {"status": "skipped", "reason": f"Already posted today ({today_str})"}

    # Check admin toggle
    if not get_setting_bool("LINKEDIN_AUTO_POST_ENABLED", False):
        logger.info("[linkedin_auto] Automated LinkedIn posting is disabled in settings.")
        return {"status": "disabled", "reason": "LINKEDIN_AUTO_POST_ENABLED is disabled"}

    # Validate connected token
    token_json = get_marketing_setting("LINKEDIN_TOKEN", "")
    try:
        token_data = json.loads(token_json) if token_json else {}
    except (ValueError, TypeError):
        token_data = {}

    if not is_token_valid(token_data):
        logger.warning("[linkedin_auto] LinkedIn token is missing or expired. Admin reconnect required.")
        return {"status": "error", "reason": "LinkedIn token missing or expired — reconnect via Social Studio"}

    access_token = token_data.get("access_token")
    author_urn = token_data.get("author_urn")
    if not access_token or not author_urn:
        return {"status": "error", "reason": "LinkedIn token missing access_token or author_urn"}

    # Retrieve or generate today's deal
    deal, _ = generate_daily_deal(target_date=get_lagos_today())
    if not deal:
        logger.warning("[linkedin_auto] Could not generate or fetch today's deal.")
        return {"status": "error", "reason": "Could not generate daily deal"}

    # Build LinkedIn copy from social campaign pack
    website_url = get_marketing_setting("APP_BASE_URL", "https://wajvtu.com.ng")
    deal_dict = {
        "network": deal.network,
        "data_size": deal.data_size or deal.plan_name,
        "price": float(deal.price or 0),
        "validity": deal.validity or "30 Days",
    }
    pack = generate_social_pack(campaign_type="DAILY_DEAL", deal=deal_dict, base_url=website_url)
    li_text = pack.get("linkedin", {}).get("text", pack.get("facebook", {}).get("text", ""))

    # Optionally generate square promo flyer
    poster_bytes = None
    try:
        from services.poster_service import generate_square_poster
        buf = generate_square_poster(
            network=deal.network,
            plan_name=deal.data_size or deal.plan_name,
            price=str(deal.price),
            validity=deal.validity or "30 Days",
            website_url=website_url,
        )
        poster_bytes = buf.getvalue()
    except Exception as img_err:
        logger.warning("[linkedin_auto] Could not generate square flyer for LinkedIn: %s", img_err)

    # Post to LinkedIn
    result = post_to_linkedin(
        access_token=access_token,
        author_urn=author_urn,
        text=li_text,
        image_bytes=poster_bytes,
    )

    if result.get("status") == "success":
        set_marketing_setting(setting_key, "true")
        post_urn = result.get("post_urn", "")
        post_url = result.get("url", "")
        logger.info("[linkedin_auto] Successfully posted daily deal to LinkedIn: %s", post_urn)

        # Audit log in MarketingCampaign
        try:
            camp = MarketingCampaign(
                title=f"LinkedIn Daily Deal — {deal.network} {deal.data_size or deal.plan_name}"[:100],
                segment="LINKEDIN_FOLLOWERS",
                channel="LINKEDIN",
                template_body=li_text[:500],
                status="COMPLETED",
                total_recipients=1,
                sent_count=1,
                failed_count=0,
                scheduled_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )
            db.session.add(camp)
            db.session.commit()
        except Exception as camp_err:
            db.session.rollback()
            logger.warning("[linkedin_auto] Could not log MarketingCampaign: %s", camp_err)

        return {
            "status": "success",
            "date": today_str,
            "post_urn": post_urn,
            "url": post_url,
        }
    else:
        err_msg = result.get("message", "Unknown LinkedIn API error")
        logger.error("[linkedin_auto] LinkedIn post failed: %s", err_msg)
        return {"status": "error", "reason": err_msg}
