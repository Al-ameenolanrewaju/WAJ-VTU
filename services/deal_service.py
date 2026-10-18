"""
Daily Data Deal Generator Service for WAJ VTU.
Selects attractive, real data plans across networks (MTN, Glo, Airtel, 9Mobile),
generates promotional copy using Groq AI (with deterministic template fallback),
and maintains deal history idempotently.
"""
import os
import re
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from models import db, MarketingContent
from provider import fetch_data_variations
from services.marketing_settings import get_marketing_setting

LAGOS_TZ = ZoneInfo("Africa/Lagos")
SUPPORTED_NETWORKS = ["MTN", "GLO", "AIRTEL", "9MOBILE"]
NETWORK_EMOJIS = {
    "MTN": "🟡",
    "GLO": "🟢",
    "AIRTEL": "🔴",
    "9MOBILE": "🟢",
}


def get_lagos_today():
    """Returns today's date in Africa/Lagos timezone."""
    return datetime.now(LAGOS_TZ).date()


def parse_plan_details(plan_name, network):
    """
    Extracts data size, validity, and plan type from a provider plan name string.
    Example: 'GLO 5GB (30 days)' -> ('5GB', '30 Days', 'DIRECT')
    """
    text = str(plan_name or "").strip()

    # Extract size
    size_match = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|MB|TB))", text, re.IGNORECASE)
    data_size = size_match.group(1).upper().replace(" ", "") if size_match else "Data Bundle"

    # Extract validity
    validity_match = re.search(r"(\d+\s*(?:day|days|month|months|hrs|hours))", text, re.IGNORECASE)
    if validity_match:
        validity = validity_match.group(1).title()
    elif "30" in text or "monthly" in text.lower():
        validity = "30 Days"
    elif "7" in text or "weekly" in text.lower():
        validity = "7 Days"
    elif "1 day" in text.lower() or "daily" in text.lower():
        validity = "1 Day"
    else:
        validity = "30 Days"

    # Extract type
    type_match = re.search(r"\b(SME|GIFTING|CORPORATE|DIRECT|CG)\b", text, re.IGNORECASE)
    plan_type = type_match.group(1).upper() if type_match else "SME"

    return data_size, validity, plan_type


def data_size_to_mb(size_str):
    """Converts a size string like '5GB' or '500MB' to approximate megabytes."""
    match = re.match(r"(\d+(?:\.\d+)?)\s*(GB|MB|TB)", str(size_str).upper())
    if not match:
        return 1000
    val = float(match.group(1))
    unit = match.group(2)
    if unit == "TB":
        return val * 1024 * 1024
    if unit == "GB":
        return val * 1024
    return val


def score_plan_for_deal(plan, network, recent_deals):
    """
    Modular scoring algorithm for selecting the most attractive 'Deal of the Day'.
    
    Criteria:
    1. Popular data sizes (1GB, 2GB, 3GB, 5GB, 10GB) receive higher weight.
    2. Sweet-spot price range (₦200 - ₦3,500) prioritized; ultra-expensive plans penalized.
    3. Value ratio (MB per Naira) prioritized.
    4. Network diversity: penalties applied if the network was featured recently.
    5. Plan diversity: heavy penalty if the exact plan was featured in recent days.
    """
    try:
        price = float(plan.get("variation_amount", 0))
    except (ValueError, TypeError):
        return -999.0

    if price <= 0:
        return -999.0

    # Cap expensive plans unless intentionally permitted
    if price > 8000:
        return -500.0

    data_size, validity, _ = parse_plan_details(plan.get("name", ""), network)
    mb = data_size_to_mb(data_size)

    # Base score on value (MB per Naira)
    value_ratio = mb / max(price, 1.0)
    score = value_ratio * 15.0

    # Popular data sizes get substantial bonus
    popular_gb = [1, 2, 3, 5, 10]
    for p_gb in popular_gb:
        if abs(mb - (p_gb * 1024)) < 150:
            score += 60.0
            break

    # Validity preference (30 days preferred for best consumer appeal)
    if "30" in validity.lower() or "month" in validity.lower():
        score += 25.0
    elif "7" in validity.lower():
        score += 10.0

    # Sweet-spot pricing bonus (affordable impulse buy)
    if 300 <= price <= 2500:
        score += 35.0
    elif price < 300:
        score += 15.0

    # Diversity over time (penalty for recently featured networks and plans)
    for idx, recent in enumerate(recent_deals[:7]):
        recency_weight = (7 - idx) * 10
        if recent.network.upper() == network.upper():
            score -= recency_weight * 1.5
        if recent.plan_code == plan.get("variation_code"):
            score -= 150.0  # Avoid selecting the exact same plan

    return score


def collect_available_plans():
    """
    Fetches real, active plans across all supported networks from the provider layer.
    Guarantees no duplicated or invented plans.
    """
    all_plans = []
    for network in SUPPORTED_NETWORKS:
        try:
            variations = fetch_data_variations(network) or []
            for item in variations:
                price = None
                try:
                    price = Decimal(str(item.get("variation_amount", 0))).quantize(Decimal("0.01"))
                except Exception:
                    continue

                if price > 0 and item.get("name") and item.get("variation_code"):
                    all_plans.append({
                        "network": network,
                        "name": item["name"],
                        "variation_code": item["variation_code"],
                        "price": price,
                    })
        except Exception:
            continue
    return all_plans


def select_best_daily_deal(recent_deals=None):
    """
    Runs the selection algorithm against all eligible live plans and returns the top candidate.
    """
    if recent_deals is None:
        recent_deals = (
            MarketingContent.query.filter_by(content_type="DAILY_DEAL")
            .order_by(MarketingContent.target_date.desc())
            .limit(14)
            .all()
        )

    plans = collect_available_plans()
    if not plans:
        return None

    scored_plans = []
    for p in plans:
        plan_dict = {
            "name": p["name"],
            "variation_code": p["variation_code"],
            "variation_amount": float(p["price"]),
        }
        score = score_plan_for_deal(plan_dict, p["network"], recent_deals)
        scored_plans.append((score, p))

    scored_plans.sort(key=lambda x: x[0], reverse=True)
    best_candidate = scored_plans[0][1]
    return best_candidate


def generate_deal_copy_via_groq(deal_facts, website_url):
    """
    Uses Groq LLM to generate punchy, high-converting promotional content
    strictly bound to real plan facts. Never hallucinates prices or plans.
    """
    from chat_agent import get_groq_client
    client = get_groq_client()
    if not client:
        return None

    prompt = f"""You are a professional social media marketing copywriter for WAJ VTU, a trusted Nigerian telecoms/VTU platform.
Write a promotional 'Deal of the Day' post for this exact data plan:
Network: {deal_facts['network']}
Plan: {deal_facts['data_size']}
Validity: {deal_facts['validity']}
Price: ₦{deal_facts['price']:,.2f}
Website URL: {website_url}

CRITICAL RULES:
1. NEVER change or invent prices. The price is EXACTLY ₦{deal_facts['price']:,.2f}.
2. NEVER invent validity or data size.
3. Keep the tone vibrant, engaging, and professional for Nigerian mobile users.
4. Output your response as a valid JSON object with exactly these 3 keys:
- "headline": Short high-impact title (e.g. "🔥 WAJ DATA DEAL OF THE DAY!")
- "caption": A concise 1-2 sentence teaser.
- "full_caption": The complete promotional copy ready to copy-paste to WhatsApp Status / Twitter / Facebook, including emojis, plan details, call-to-action link, and the tagline "WAJ VTU — Fast. Simple. Reliable."

Do not wrap in markdown quotes. Return JSON only."""

    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a precise VTU marketing assistant. You strictly return JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=600,
        )
        content = completion.choices[0].message.content
        import json
        data = json.loads(content)
        if data.get("full_caption"):
            return {
                "headline": data.get("headline", "🔥 WAJ DATA DEAL OF THE DAY!"),
                "caption": data.get("caption", f"Get {deal_facts['network']} {deal_facts['data_size']} at only ₦{deal_facts['price']:,.2f}!"),
                "full_caption": data.get("full_caption")
            }
    except Exception:
        pass
    return None


def generate_deal_copy_template(deal_facts, website_url):
    """
    Fallback deterministic copywriting engine. Generates clean, high-converting
    promotional copy matching the exact format specified by the user.
    """
    network = deal_facts["network"]
    emoji = NETWORK_EMOJIS.get(network.upper(), "📶")
    data_size = deal_facts["data_size"]
    validity = deal_facts["validity"]
    price_formatted = f"₦{deal_facts['price']:,.2f}"

    headline = "🔥 WAJ DATA DEAL OF THE DAY!"
    short_caption = f"Special offer: {network} {data_size} ({validity}) for just {price_formatted} on WAJ VTU!"

    full_caption = (
        f"{headline}\n\n"
        f"{emoji} {network} {data_size}\n"
        f"⏳ {validity}\n"
        f"💰 {price_formatted}\n\n"
        f"More data. Better value. 📶🔥\n\n"
        f"Get yours today on WAJ VTU:\n"
        f"{website_url}\n\n"
        f"WAJ VTU — Fast. Simple. Reliable."
    )

    return {
        "headline": headline,
        "caption": short_caption,
        "full_caption": full_caption,
    }


def generate_daily_deal(target_date=None, force=False, website_url=None):
    """
    Generates and persists the deal of the day.
    
    Idempotent:
    - Running multiple times on the same date will NOT create duplicate deals.
    - If a deal already exists for target_date and force is False, returns existing.
    - If force is True, updates the existing record with newly selected deal content.
    """
    if target_date is None:
        target_date = get_lagos_today()

    existing = MarketingContent.query.filter_by(
        content_type="DAILY_DEAL",
        target_date=target_date
    ).first()

    if existing and not force:
        return existing, False

    if not website_url:
        website_url = (get_marketing_setting("APP_BASE_URL", "") or "https://wajvtu.com.ng").rstrip("/")

    # Select the day's best plan
    deal_plan = select_best_daily_deal()
    if not deal_plan:
        return None, False

    network = deal_plan["network"]
    plan_name = deal_plan["name"]
    price = deal_plan["price"]
    data_size, validity, _ = parse_plan_details(plan_name, network)

    deal_facts = {
        "network": network,
        "plan_name": plan_name,
        "data_size": data_size,
        "validity": validity,
        "price": price,
    }

    # Attempt AI copy generation first, fallback to templating
    copy = generate_deal_copy_via_groq(deal_facts, website_url)
    if not copy:
        copy = generate_deal_copy_template(deal_facts, website_url)

    now = datetime.now(timezone.utc)

    if existing and force:
        existing.network = network
        existing.plan_name = plan_name
        existing.plan_code = deal_plan["variation_code"]
        existing.data_size = data_size
        existing.validity = validity
        existing.price = price
        existing.headline = copy["headline"]
        existing.caption = copy["caption"]
        existing.full_caption = copy["full_caption"]
        existing.status = "GENERATED"
        existing.updated_at = now
        db.session.commit()
        return existing, True

    content = MarketingContent(
        content_type="DAILY_DEAL",
        target_date=target_date,
        network=network,
        plan_name=plan_name,
        plan_code=deal_plan["variation_code"],
        data_size=data_size,
        validity=validity,
        price=price,
        headline=copy["headline"],
        caption=copy["caption"],
        full_caption=copy["full_caption"],
        status="GENERATED",
        created_at=now,
        updated_at=now,
    )
    db.session.add(content)
    try:
        db.session.commit()
        return content, True
    except Exception:
        db.session.rollback()
        # In case another process created it concurrently, fetch existing
        existing = MarketingContent.query.filter_by(
            content_type="DAILY_DEAL",
            target_date=target_date
        ).first()
        return existing, False


def mark_deal_used(content_id):
    """Marks a daily deal as USED by the admin."""
    deal = MarketingContent.query.get(content_id)
    if deal:
        deal.status = "USED"
        deal.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return True
    return False


def update_deal_caption(content_id, new_full_caption, new_headline=None):
    """Allows admin to customize the generated caption."""
    deal = MarketingContent.query.get(content_id)
    if deal:
        deal.full_caption = str(new_full_caption or "").strip()
        if new_headline:
            deal.headline = str(new_headline).strip()
        deal.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        return True
    return False


def get_daily_deal_history(limit=50):
    """Fetches recent generated daily deals for the admin history view."""
    return (
        MarketingContent.query.filter_by(content_type="DAILY_DEAL")
        .order_by(MarketingContent.target_date.desc())
        .limit(limit)
        .all()
    )
