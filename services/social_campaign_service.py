"""
Social Media Campaign Studio Service for WAJ VTU.
Generates platform-tailored promotional copy with automatic UTM tracking tags
and 1-click sharing links for WhatsApp, Twitter/X, Instagram, and Facebook.
"""
import urllib.parse
from datetime import datetime


def build_utm_url(base_url: str, source: str, medium: str = "social", campaign: str = "promo") -> str:
    """Appends standard UTM tracking parameters to the website URL."""
    base = (base_url or "https://wajvtu.com.ng").rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base

    params = {
        "utm_source": source,
        "utm_medium": medium,
        "utm_campaign": campaign,
    }
    sep = "&" if "?" in base else "?"
    return f"{base}/{sep}{urllib.parse.urlencode(params)}"


def generate_social_pack(
    campaign_type: str = "DAILY_DEAL",
    deal: dict = None,
    custom_data: dict = None,
    base_url: str = "https://wajvtu.com.ng",
    phone: str = "08102314725",
) -> dict:
    """
    Generates tailored promotional content for:
      - WhatsApp Status (punchy, high urgency)
      - WhatsApp Broadcast/Groups (detailed benefits & instructions)
      - Twitter/X (character counted <= 280, viral hooks & hashtags)
      - Instagram (rich caption + structured hashtag cloud + link-in-bio prompt)
      - Facebook (community post format)
    """
    campaign_key = (campaign_type or "DAILY_DEAL").upper()
    now_str = datetime.now().strftime("%d %b %Y")

    # Resolve core campaign variables
    if campaign_key == "DAILY_DEAL" and deal:
        network = deal.get("network", "MTN")
        data_size = deal.get("data_size") or deal.get("plan_name", "2.0 GB")
        try:
            price_val = float(deal.get("price", 0))
            price_str = f"₦{price_val:,.0f}"
        except (ValueError, TypeError):
            price_str = f"₦{deal.get('price', '')}"
        validity = deal.get("validity", "30 Days")
        tagline = f"{network} {data_size} for just {price_str}!"
        campaign_slug = f"daily_deal_{network.lower()}_{data_size.replace(' ', '').lower()}"
    elif campaign_key == "WEEKEND_PROMO":
        network = deal.get("network", "MTN") if deal else "MTN & ALL NETWORKS"
        data_size = "Super Cheap Weekend Gigabytes"
        price_str = "Lowest Rates"
        validity = "Instant Delivery"
        tagline = "TGIF! Weekend Data Mega Rush is LIVE on WAJ VTU!"
        campaign_slug = "weekend_mega_promo"
    elif campaign_key == "REFERRAL_EARN":
        network = "ALL NETWORKS"
        data_size = "Earn Passive Income"
        price_str = "₦0 Investment"
        validity = "Lifetime Cashback"
        tagline = "Turn your phone into a money-maker with WAJ VTU Referral Program!"
        campaign_slug = "referral_earn_cash"
    elif campaign_key == "PAYDAY_FLASH":
        network = "ALL NETWORKS"
        data_size = "Payday Data Bonanza"
        price_str = "Wholesale Rates"
        validity = "30 Days"
        tagline = "Salary landed? Stack up on your month's high-speed data at distributor prices!"
        campaign_slug = "payday_bonanza"
    else:  # CUSTOM
        custom = custom_data or {}
        network = custom.get("network", "ALL NETWORKS")
        data_size = custom.get("headline", "Special VTU Offer")
        price_str = custom.get("price", "Best Rates")
        validity = custom.get("validity", "Instant")
        tagline = custom.get("body", "Get cheap data, airtime, and utility top-ups 24/7 on WAJ VTU.")
        campaign_slug = "custom_promo"

    # Tracked landing links for each platform
    wa_status_url = build_utm_url(base_url, source="whatsapp_status", medium="status", campaign=campaign_slug)
    wa_group_url = build_utm_url(base_url, source="whatsapp_group", medium="broadcast", campaign=campaign_slug)
    twitter_url = build_utm_url(base_url, source="twitter", medium="social", campaign=campaign_slug)
    ig_url = build_utm_url(base_url, source="instagram", medium="social", campaign=campaign_slug)
    fb_url = build_utm_url(base_url, source="facebook", medium="social", campaign=campaign_slug)

    # -------------------------------------------------------------
    # 1. WhatsApp Status Copy (Snappy, high curiosity, emoji hooks)
    # -------------------------------------------------------------
    if campaign_key == "DAILY_DEAL":
        wa_status_text = (
            f"🔥 *TODAY'S SPECIAL DATA OFFER!* 🔥\n\n"
            f"⚡ *{network} {data_size}* for just *{price_str}*!\n"
            f"⏳ Valid for *{validity}* (Works on all devices 4G/5G)\n\n"
            f"✅ 10-Second Auto Delivery\n"
            f"✅ No downtime • 100% Guaranteed\n\n"
            f"👉 Order instantly on website:\n"
            f"{wa_status_url}\n\n"
            f"💬 Or reply to this status to buy now!"
        )
    elif campaign_key == "WEEKEND_PROMO":
        wa_status_text = (
            f"🎉 *WEEKEND DATA RUSH IS LIVE!* 🎉\n\n"
            f"Don't let 'Data exhausted' ruin your weekend movies, football & TikTok vibes! 🍿⚽\n\n"
            f"📶 *MTN • Airtel • Glo • 9mobile*\n"
            f"⚡ Instant top-up within seconds!\n\n"
            f"👉 Top up your line now:\n"
            f"{wa_status_url}\n\n"
            f"📲 WhatsApp Bot: {phone}"
        )
    elif campaign_key == "REFERRAL_EARN":
        wa_status_text = (
            f"💸 *WANT TO MAKE EXTRA CASH ON YOUR PHONE?* 💸\n\n"
            f"Did you know you earn real money whenever your friends top up data or airtime on WAJ VTU?\n\n"
            f"1️⃣ Copy your referral link from your dashboard\n"
            f"2️⃣ Share with friends & family\n"
            f"3️⃣ Withdraw earnings directly to your bank!\n\n"
            f"👉 Start earning today:\n"
            f"{wa_status_url}"
        )
    else:
        wa_status_text = (
            f"🚨 *{tagline}* 🚨\n\n"
            f"Fast, reliable data & airtime delivered straight to your line in 10 seconds.\n\n"
            f"👉 Tap to recharge now:\n"
            f"{wa_status_url}"
        )

    # -------------------------------------------------------------
    # 2. WhatsApp Group / Broadcast Copy (Comprehensive, reassuring)
    # -------------------------------------------------------------
    if campaign_key == "DAILY_DEAL":
        wa_group_text = (
            f"📢 *SPECIAL BROADCAST: TODAY'S DATA DEAL* 📢\n\n"
            f"Hello everyone! Here is today's exclusive wholesale data price for our smart users:\n\n"
            f"🔹 *Network:* {network}\n"
            f"🔹 *Plan:* {data_size}\n"
            f"🔹 *Price:* {price_str} only\n"
            f"🔹 *Validity:* {validity}\n"
            f"🔹 *Delivery:* Automated (Under 10 Seconds)\n\n"
            f"💡 *Why WAJ VTU?*\n"
            f"✔️ 24/7 Instant Auto-dispense (no waiting for admin approval)\n"
            f"✔️ Monnify & Paystack instant bank funding\n"
            f"✔️ 100% network uptime & reliable support\n\n"
            f"👉 Click here to buy directly now:\n"
            f"{wa_group_url}\n\n"
            f"📞 Need support or have questions? Chat us on {phone}."
        )
    else:
        wa_group_text = (
            f"📢 *UPDATE FROM WAJ VTU* 📢\n\n"
            f"*{tagline}*\n\n"
            f"Stop overpaying for internet data and airtime when you can get wholesale rates directly on your phone!\n\n"
            f"🚀 *Available Services:*\n"
            f"• Cheap SME & Corporate Gifting Data (MTN, Airtel, Glo, 9mobile)\n"
            f"• Discounted Airtime Top-up (up to 3% OFF)\n"
            f"• Electricity Bill Payments (Instant Token)\n"
            f"• Cable TV Subscriptions (DStv, GOtv, Startimes)\n\n"
            f"👉 Top up now:\n"
            f"{wa_group_url}\n\n"
            f"📞 WhatsApp Bot & Support: {phone}"
        )

    # -------------------------------------------------------------
    # 3. Twitter / X Copy (Strictly <= 280 characters with tags)
    # -------------------------------------------------------------
    if campaign_key == "DAILY_DEAL":
        tw_text = (
            f"🔥 Daily Data Deal!\n"
            f"Get {network} {data_size} for {price_str} ({validity}) on WAJ VTU.\n"
            f"⚡ 10-second instant auto delivery.\n\n"
            f"Order now 👇\n"
            f"{twitter_url}\n\n"
            f"#WAJVTU #CheapData #MTNData"
        )
    elif campaign_key == "WEEKEND_PROMO":
        tw_text = (
            f"TGIF! 🍿 Don't let your data run out this weekend.\n\n"
            f"Get ultra-cheap data on MTN, Airtel, Glo & 9mobile with instant auto-delivery on WAJ VTU.\n\n"
            f"Top up here 👇\n"
            f"{twitter_url}\n\n"
            f"#WAJVTU #TGIF #CheapData"
        )
    elif campaign_key == "REFERRAL_EARN":
        tw_text = (
            f"Earn money from your phone 💰\n\n"
            f"Refer friends to WAJ VTU & earn instant wallet cashback on every data & airtime purchase they make.\n\n"
            f"Start earning 👇\n"
            f"{twitter_url}\n\n"
            f"#WAJVTU #PassiveIncome"
        )
    elif campaign_key == "PAYDAY_FLASH":
        tw_text = (
            f"Payday Data Bonanza! 💼\n\n"
            f"Stock up on your monthly high-speed internet data at wholesale distributor rates on WAJ VTU.\n\n"
            f"Get yours now 👇\n"
            f"{twitter_url}\n\n"
            f"#WAJVTU #PaydayDeal #CheapData"
        )
    else:
        headline = (tagline[:90] + "...") if len(tagline) > 90 else tagline
        tw_text = (
            f"⚡ {headline}\n\n"
            f"Fast, automated data & airtime top-up at discount rates.\n\n"
            f"Order on WAJ VTU 👇\n"
            f"{twitter_url}\n\n"
            f"#WAJVTU #CheapData"
        )

    # -------------------------------------------------------------
    # 4. Instagram Copy (Bio prompt, line breaks, full hashtag cloud)
    # -------------------------------------------------------------
    ig_hashtags = (
        "#wajvtu #cheapdata #mtndata #airteldata #glodata #9mobile #vtuapp "
        "#nigeriastudents #datapackage #cheapestdata #datanaija #nigeriabusiness "
        "#instantrecharge #dataplug #lagosbusiness #abuja #ibadan #studentplug"
    )

    if campaign_key == "DAILY_DEAL":
        ig_text = (
            f"🚨 SPECIAL DEAL OF THE DAY 🚨\n.\n"
            f"Never run out of internet! Get your {network} {data_size} today for only {price_str} ({validity}).\n.\n"
            f"⚡ 10-Second Automated Delivery\n"
            f"📶 4G/5G High Speed for all devices\n"
            f"🔒 Safe, secure & reliable\n.\n"
            f"👉 TAP THE LINK IN OUR BIO TO ORDER NOW:\n"
            f"{ig_url}\n.\n"
            f"Tag a friend who always asks for hotspot! 😂👇\n.\n"
            f"{ig_hashtags}"
        )
    else:
        ig_text = (
            f"✨ {tagline.upper()} ✨\n.\n"
            f"Why pay full price for internet data when WAJ VTU gives you the cheapest distributor rates in Nigeria?\n.\n"
            f"⚡ MTN • AIRTEL • GLO • 9MOBILE\n"
            f"⚡ Instant funding via Monnify & Paystack\n"
            f"⚡ 24/7 automated instant delivery\n.\n"
            f"👉 CLICK THE LINK IN BIO TO TOP UP:\n"
            f"{ig_url}\n.\n"
            f"{ig_hashtags}"
        )

    # -------------------------------------------------------------
    # 5. Facebook Copy (Engaging story / community post)
    # -------------------------------------------------------------
    if campaign_key == "DAILY_DEAL":
        fb_text = (
            f"🔥 TODAY'S EXCLUSIVE DATA DEAL ON WAJ VTU! 🔥\n\n"
            f"Smart data users, don't miss out on today's special offer:\n\n"
            f"📦 Plan: {network} {data_size}\n"
            f"💰 Price: {price_str} only\n"
            f"⏳ Validity: {validity}\n"
            f"⚡ Speed: Full 4G/5G LTE\n\n"
            f"Instant automated delivery straight to your phone in under 10 seconds. No delays, no stories!\n\n"
            f"👉 Order directly here: {fb_url}\n\n"
            f"Customer Care & WhatsApp Bot: {phone}"
        )
    else:
        fb_text = (
            f"📢 Stop overpaying for data and airtime! 📢\n\n"
            f"{tagline}\n\n"
            f"WAJ VTU is your trusted platform for instant, automated data and bill payments:\n"
            f"✔️ Cheap MTN, Airtel, Glo & 9mobile data plans\n"
            f"✔️ Discounted Airtime\n"
            f"✔️ Instant Electricity token\n"
            f"✔️ Cable TV subscriptions\n\n"
            f"👉 Click to recharge instantly: {fb_url}\n\n"
            f"WhatsApp Support: {phone}"
        )

    # 1-Click URLs
    wa_status_share_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(wa_status_text)}"
    wa_group_share_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(wa_group_text)}"
    tw_share_url = f"https://x.com/intent/post?text={urllib.parse.quote(tw_text)}"
    fb_share_url = f"https://www.facebook.com/sharer/sharer.php?u={urllib.parse.quote(fb_url)}"

    # LinkedIn UTM URL
    li_url = build_utm_url(base_url, source="linkedin", medium="social", campaign=campaign_slug)

    # -------------------------------------------------------------
    # 6. LinkedIn Copy (Professional, slightly formal, story-driven)
    #    LinkedIn allows up to 3,000 characters. No link-in-bio
    #    restriction — the URL can go inline in the post text.
    # -------------------------------------------------------------
    if campaign_key == "DAILY_DEAL":
        li_text = (
            f"💡 Today's Data Deal for Nigerians — {now_str}\n\n"
            f"If you're looking for a reliable, affordable way to stay connected, here's today's offer on WAJ VTU:\n\n"
            f"📦 {network} {data_size}\n"
            f"💰 Price: {price_str} only\n"
            f"⏳ Validity: {validity}\n"
            f"⚡ Delivery: Automated in under 10 seconds\n\n"
            f"WAJ VTU is a fully automated Nigerian VTU platform offering wholesale data, airtime, "
            f"electricity tokens, and cable TV subscriptions at the best rates — 24/7, no waiting.\n\n"
            f"🔗 Order here: {li_url}\n\n"
            f"#WAJVTUNigeria #CheapData #NigeriaInternet #VTU #{network}Data"
        )
    elif campaign_key == "WEEKEND_PROMO":
        li_text = (
            f"🌟 Weekend Data Offer — Don't Get Cut Off!\n\n"
            f"As we head into the weekend, here's a reminder that WAJ VTU offers the cheapest data "
            f"bundles across MTN, Airtel, Glo, and 9mobile — with instant automated delivery.\n\n"
            f"No delays. No admin approval. Just top up and browse.\n\n"
            f"🔗 Top up now: {li_url}\n\n"
            f"#WAJVTUNigeria #CheapData #WeekendVibes"
        )
    elif campaign_key == "REFERRAL_EARN":
        li_text = (
            f"💼 Passive Income Opportunity for Nigerians\n\n"
            f"Did you know you can earn real cash just by referring friends and family to WAJ VTU?\n\n"
            f"Here's how it works:\n"
            f"1️⃣ Share your unique referral link\n"
            f"2️⃣ Your referred contacts sign up and make a purchase\n"
            f"3️⃣ You earn instant wallet cashback — withdrawable to any Nigerian bank\n\n"
            f"No investment required. No risk. Pure passive income from your phone.\n\n"
            f"🔗 Get your referral link: {li_url}\n\n"
            f"#PassiveIncome #NigeriaHustle #WAJVTUNigeria"
        )
    elif campaign_key == "PAYDAY_FLASH":
        li_text = (
            f"💼 Payday Data Bonanza — Stack Up Your Data for the Month!\n\n"
            f"Salary dropped? Here's the smart move: buy your month's high-speed data at wholesale "
            f"distributor rates on WAJ VTU before prices change.\n\n"
            f"✅ MTN, Airtel, Glo, 9mobile — all networks supported\n"
            f"✅ SME, Corporate Gifting & Direct bundles available\n"
            f"✅ Automated instant delivery — 24/7\n\n"
            f"🔗 Top up now: {li_url}\n\n"
            f"#PaydayDeal #WAJVTUNigeria #CheapDataNigeria"
        )
    else:
        li_text = (
            f"📣 {tagline}\n\n"
            f"WAJ VTU is a trusted Nigerian VTU platform delivering data, airtime, electricity tokens, "
            f"and cable TV subscriptions instantly at the best wholesale rates.\n\n"
            f"🔗 Visit us: {li_url}\n\n"
            f"#WAJVTUNigeria #CheapData #VTU"
        )

    return {
        "campaign_type": campaign_key,
        "campaign_slug": campaign_slug,
        "details": {
            "network": network,
            "data_size": data_size,
            "price": price_str,
            "validity": validity,
            "date": now_str,
        },
        "whatsapp_status": {
            "text": wa_status_text,
            "share_url": wa_status_share_url,
            "tracked_url": wa_status_url,
        },
        "whatsapp_group": {
            "text": wa_group_text,
            "share_url": wa_group_share_url,
            "tracked_url": wa_group_url,
        },
        "twitter": {
            "text": tw_text,
            "char_count": len(tw_text),
            "share_url": tw_share_url,
            "tracked_url": twitter_url,
        },
        "instagram": {
            "text": ig_text,
            "hashtags": ig_hashtags,
            "tracked_url": ig_url,
        },
        "facebook": {
            "text": fb_text,
            "share_url": fb_share_url,
            "tracked_url": fb_url,
        },
        "linkedin": {
            "text": li_text,
            "tracked_url": li_url,
        },
    }
