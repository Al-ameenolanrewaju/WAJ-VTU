"""
WhatsApp Status & Social Media Promo Poster Generator for WAJ VTU.
Generates ultra-crisp, branded 1080x1920 (9:16 portrait) and 1080x1080 (1:1 square)
graphical posters ready for 1-click sharing on WhatsApp Status, Instagram Stories,
Facebook, and Twitter.
"""
from io import BytesIO
import os
from decimal import Decimal
from PIL import Image, ImageDraw, ImageFont

# Network brand styling palette
NETWORK_THEMES = {
    "MTN": {
        "primary": "#FFCC00",
        "primary_dark": "#DDAA00",
        "text_color": "#09090b",
        "glow": (255, 204, 0, 40),
        "tag": "MTN SME & DIRECT DATA",
    },
    "GLO": {
        "primary": "#16a34a",
        "primary_dark": "#15803d",
        "text_color": "#ffffff",
        "glow": (22, 163, 74, 40),
        "tag": "GLO 4G HIGH-SPEED DATA",
    },
    "AIRTEL": {
        "primary": "#dc2626",
        "primary_dark": "#b91c1c",
        "text_color": "#ffffff",
        "glow": (220, 38, 38, 40),
        "tag": "AIRTEL FAST CG DATA",
    },
    "9MOBILE": {
        "primary": "#047857",
        "primary_dark": "#065f46",
        "text_color": "#ffffff",
        "glow": (4, 120, 87, 40),
        "tag": "9MOBILE DIRECT DATA",
    },
}

DEFAULT_THEME = {
    "primary": "#FFC400",
    "primary_dark": "#E5B000",
    "text_color": "#09090b",
    "glow": (255, 196, 0, 40),
    "tag": "NATIONWIDE DATA BUNDLE",
}


def _get_font(size):
    """Load default PIL font scaled or fallback safely."""
    try:
        for font_name in ["arialbd.ttf", "arial.ttf", "segoeui.ttf", "DejaVuSans-Bold.ttf"]:
            try:
                return ImageFont.truetype(font_name, size)
            except Exception:
                continue
    except Exception:
        pass
    return ImageFont.load_default(size=size)


def generate_status_poster(network, plan_name, price, validity="30 Days", website_url="wajvtu.com.ng", phone="0810 231 4725", format="story"):
    """
    Generates a high-impact social flyer.
    format='story'  -> 1080x1920 (9:16 portrait) for WhatsApp Status & Instagram Stories
    format='square' -> 1080x1080 (1:1 square) for Instagram Feed, Twitter, and Facebook
    Returns BytesIO object containing the PNG image.
    """
    if str(format).lower() == "square":
        return generate_square_poster(network, plan_name, price, validity, website_url, phone)

    width = 1080
    height = 1920

    # 1. Background
    poster = Image.new("RGBA", (width, height), (9, 9, 11, 255))
    draw = ImageDraw.Draw(poster)

    net_key = str(network or "MTN").strip().upper()
    theme = NETWORK_THEMES.get(net_key, DEFAULT_THEME)

    # 2. Decorative arcs / glow
    draw.ellipse((-200, -200, 500, 500), fill=(255, 196, 0, 18))
    draw.ellipse((width - 400, height - 500, width + 300, height + 400), fill=theme["glow"])

    # Outer border accent
    draw.rectangle((24, 24, width - 24, height - 24), outline="#27272a", width=3)
    draw.rectangle((32, 32, width - 32, height - 32), outline=theme["primary"], width=2)

    # 3. Logo
    logo_path = os.path.join("static", "images", "waj-vtu-logo.jpeg")
    y_cursor = 100
    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.resize((140, 140))
            mask = Image.new("L", (140, 140), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 140, 140), fill=255)
            poster.paste(logo, (int((width - 140) / 2), y_cursor), mask)
            y_cursor += 160
        except Exception:
            pass

    # Brand Title: "WAJ VTU"
    brand_font = _get_font(52)
    draw.text((width / 2, y_cursor), "WAJ VTU", fill="#ffffff", font=brand_font, anchor="mm")
    y_cursor += 50

    tag_font = _get_font(26)
    draw.text((width / 2, y_cursor), "⚡ FAST • CHEAP • INSTANT DELIVERY ⚡", fill="#FFC400", font=tag_font, anchor="mm")
    y_cursor += 90

    # 4. Top Announcement Pill Badge: "🔥 DEAL OF THE DAY"
    pill_w = 460
    pill_h = 70
    pill_x1 = int((width - pill_w) / 2)
    pill_y1 = y_cursor
    pill_x2 = pill_x1 + pill_w
    pill_y2 = pill_y1 + pill_h
    draw.rounded_rectangle((pill_x1, pill_y1, pill_x2, pill_y2), radius=35, fill="#1c1917", outline="#FFC400", width=3)
    pill_font = _get_font(32)
    draw.text((width / 2, y_cursor + 35), "🔥 DEAL OF THE DAY", fill="#FFC400", font=pill_font, anchor="mm")
    y_cursor += 140

    # 5. Main Network Card
    card_margin = 80
    card_w = width - (card_margin * 2)
    card_h = 860
    card_x1 = card_margin
    card_y1 = y_cursor
    card_x2 = card_x1 + card_w
    card_y2 = card_y1 + card_h

    draw.rounded_rectangle((card_x1, card_y1, card_x2, card_y2), radius=28, fill="#18181b", outline="#27272a", width=3)

    # Network badge header inside card
    badge_w = 340
    badge_h = 75
    badge_x = int(width / 2)
    badge_y = card_y1 + 75
    draw.rounded_rectangle(
        (badge_x - int(badge_w / 2), badge_y - int(badge_h / 2), badge_x + int(badge_w / 2), badge_y + int(badge_h / 2)),
        radius=16,
        fill=theme["primary"]
    )
    badge_font = _get_font(38)
    draw.text((badge_x, badge_y), net_key, fill=theme["text_color"], font=badge_font, anchor="mm")

    # Network subtag
    subtag_font = _get_font(24)
    draw.text((width / 2, card_y1 + 145), theme["tag"], fill="#a1a1aa", font=subtag_font, anchor="mm")

    # Plan Size Text (e.g. "5GB")
    plan_font = _get_font(130)
    clean_plan = str(plan_name or "DATA").strip()
    draw.text((width / 2, card_y1 + 290), clean_plan, fill="#ffffff", font=plan_font, anchor="mm")

    # Validity badge
    val_font = _get_font(32)
    draw.text((width / 2, card_y1 + 400), f"⏳ Valid for {validity}", fill="#93c5fd", font=val_font, anchor="mm")

    # Price Box
    price_box_w = card_w - 120
    price_box_h = 160
    pbox_x1 = int((width - price_box_w) / 2)
    pbox_y1 = card_y1 + 480
    pbox_x2 = pbox_x1 + price_box_w
    pbox_y2 = pbox_y1 + price_box_h

    draw.rounded_rectangle(
        (pbox_x1, pbox_y1, pbox_x2, pbox_y2),
        radius=20,
        fill="#09090b",
        outline=theme["primary"],
        width=3
    )

    try:
        price_num = float(price or 0)
        price_str = f"₦{price_num:,.0f}"
    except (ValueError, TypeError):
        price_str = f"₦{price}"

    draw.text((width / 2, pbox_y1 + 45), "SPECIAL DISCOUNT PRICE", fill="#71717a", font=_get_font(22), anchor="mm")
    draw.text((width / 2, pbox_y1 + 105), price_str, fill="#22c55e", font=_get_font(72), anchor="mm")

    # Features list
    feats_y = card_y1 + 685
    feat_font = _get_font(26)
    draw.text((width / 2, feats_y), "✓ Instant Delivery within 10 Seconds", fill="#e4e4e7", font=feat_font, anchor="mm")
    draw.text((width / 2, feats_y + 45), "✓ Works on All Android, iPhone, Laptops & MiFi", fill="#e4e4e7", font=feat_font, anchor="mm")
    draw.text((width / 2, feats_y + 90), "✓ 24/7 Automated Wallet Funding", fill="#e4e4e7", font=feat_font, anchor="mm")

    y_cursor = card_y2 + 60

    # 6. Bottom Call to Action Section
    cta_box_w = width - 140
    cta_box_h = 170
    cta_x1 = int((width - cta_box_w) / 2)
    cta_y1 = y_cursor
    cta_x2 = cta_x1 + cta_box_w
    cta_y2 = cta_y1 + cta_box_h

    draw.rounded_rectangle((cta_x1, cta_y1, cta_x2, cta_y2), radius=22, fill="#FFC400")
    draw.text((width / 2, cta_y1 + 55), "👉 ORDER NOW ON WEBSITE", fill="#09090b", font=_get_font(42), anchor="mm")
    clean_site = website_url.replace("https://", "").replace("http://", "").rstrip("/")
    draw.text((width / 2, cta_y1 + 115), clean_site, fill="#18181b", font=_get_font(34), anchor="mm")

    # Footer contact info
    footer_y = cta_y2 + 70
    footer_font = _get_font(28)
    draw.text((width / 2, footer_y), f"💬 WhatsApp Bot & Customer Care: {phone}", fill="#ffffff", font=footer_font, anchor="mm")
    draw.text((width / 2, footer_y + 45), "WAJ VTU — Nigeria's Most Reliable Telecoms Platform", fill="#71717a", font=_get_font(22), anchor="mm")

    rgb_poster = poster.convert("RGB")
    buf = BytesIO()
    rgb_poster.save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf


def generate_square_poster(network, plan_name, price, validity="30 Days", website_url="wajvtu.com.ng", phone="0810 231 4725"):
    """
    Generates a 1080x1080 (1:1 square) social feed flyer (Instagram Feed, Facebook, Twitter/X).
    Returns BytesIO object containing the PNG image.
    """
    width = 1080
    height = 1080

    poster = Image.new("RGBA", (width, height), (9, 9, 11, 255))
    draw = ImageDraw.Draw(poster)

    net_key = str(network or "MTN").strip().upper()
    theme = NETWORK_THEMES.get(net_key, DEFAULT_THEME)

    # Decorative glow
    draw.ellipse((-150, -150, 400, 400), fill=(255, 196, 0, 20))
    draw.ellipse((width - 350, height - 350, width + 200, height + 200), fill=theme["glow"])

    # Double border
    draw.rectangle((20, 20, width - 20, height - 20), outline="#27272a", width=3)
    draw.rectangle((28, 28, width - 28, height - 28), outline=theme["primary"], width=2)

    # Top Brand Header
    logo_path = os.path.join("static", "images", "waj-vtu-logo.jpeg")
    x_header = 70
    y_header = 65
    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA").resize((70, 70))
            mask = Image.new("L", (70, 70), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 70, 70), fill=255)
            poster.paste(logo, (x_header, y_header), mask)
            x_header += 86
        except Exception:
            pass

    draw.text((x_header, y_header + 16), "WAJ VTU", fill="#ffffff", font=_get_font(34))
    draw.text((x_header, y_header + 48), "FAST • CHEAP • INSTANT", fill="#FFC400", font=_get_font(18))

    # Top right pill: "🔥 DEAL OF THE DAY"
    pill_w = 340
    pill_h = 52
    pill_x2 = width - 70
    pill_x1 = pill_x2 - pill_w
    draw.rounded_rectangle((pill_x1, y_header + 10, pill_x2, y_header + 10 + pill_h), radius=26, fill="#1c1917", outline="#FFC400", width=2)
    draw.text((pill_x1 + int(pill_w / 2), y_header + 10 + int(pill_h / 2)), "🔥 SPECIAL DATA DEAL", fill="#FFC400", font=_get_font(22), anchor="mm")

    # Center Hero Card (width 940, height 640)
    card_x1 = 70
    card_y1 = 175
    card_x2 = width - 70
    card_y2 = 815
    draw.rounded_rectangle((card_x1, card_y1, card_x2, card_y2), radius=24, fill="#18181b", outline="#27272a", width=3)

    # Network Pill inside card
    net_badge_w = 260
    net_badge_h = 58
    nb_x = int(width / 2)
    nb_y = card_y1 + 55
    draw.rounded_rectangle((nb_x - int(net_badge_w/2), nb_y - int(net_badge_h/2), nb_x + int(net_badge_w/2), nb_y + int(net_badge_h/2)), radius=14, fill=theme["primary"])
    draw.text((nb_x, nb_y), net_key, fill=theme["text_color"], font=_get_font(30), anchor="mm")

    # Big Plan Size (e.g. "5GB")
    draw.text((width / 2, card_y1 + 175), str(plan_name or "DATA").strip(), fill="#ffffff", font=_get_font(110), anchor="mm")
    draw.text((width / 2, card_y1 + 265), f"⏳ Valid for {validity} • 4G/5G All Devices", fill="#93c5fd", font=_get_font(26), anchor="mm")

    # Price Box inside card
    pbox_w = 800
    pbox_h = 135
    pbox_x1 = int((width - pbox_w) / 2)
    pbox_y1 = card_y1 + 325
    pbox_x2 = pbox_x1 + pbox_w
    pbox_y2 = pbox_y1 + pbox_h

    draw.rounded_rectangle((pbox_x1, pbox_y1, pbox_x2, pbox_y2), radius=18, fill="#09090b", outline=theme["primary"], width=3)

    try:
        price_num = float(price or 0)
        price_str = f"₦{price_num:,.0f}"
    except (ValueError, TypeError):
        price_str = f"₦{price}"

    draw.text((width / 2, pbox_y1 + 35), "EXCLUSIVE PROMO PRICE", fill="#71717a", font=_get_font(20), anchor="mm")
    draw.text((width / 2, pbox_y1 + 90), price_str, fill="#22c55e", font=_get_font(60), anchor="mm")

    # Bullets inside card
    draw.text((width / 2, card_y1 + 515), "⚡ 10-Second Auto Delivery  •  🔒 100% Reliable", fill="#e4e4e7", font=_get_font(24), anchor="mm")
    draw.text((width / 2, card_y1 + 565), "📶 Works on all phones, modems, and routers", fill="#a1a1aa", font=_get_font(21), anchor="mm")

    # Bottom CTA Box
    cta_x1 = 70
    cta_y1 = 845
    cta_x2 = width - 70
    cta_y2 = 985
    draw.rounded_rectangle((cta_x1, cta_y1, cta_x2, cta_y2), radius=20, fill="#FFC400")
    draw.text((width / 2, cta_y1 + 45), "👉 BUY NOW ON WEBSITE", fill="#09090b", font=_get_font(36), anchor="mm")
    clean_site = website_url.replace("https://", "").replace("http://", "").rstrip("/")
    draw.text((width / 2, cta_y1 + 95), clean_site, fill="#18181b", font=_get_font(28), anchor="mm")

    # Footer contact
    draw.text((width / 2, 1025), f"WhatsApp Bot & Care: {phone} • Instant Auto-Recharge", fill="#a1a1aa", font=_get_font(20), anchor="mm")

    rgb_poster = poster.convert("RGB")
    buf = BytesIO()
    rgb_poster.save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf
