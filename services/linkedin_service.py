"""
LinkedIn API Service for WAJ VTU.
Handles OAuth 2.0 token exchange and direct post publishing via LinkedIn UGC Posts API.

Flow:
  1. Admin visits /admin/marketing/linkedin/connect  → redirected to LinkedIn OAuth
  2. LinkedIn redirects back to /admin/marketing/linkedin/callback  → token stored in DB settings
  3. Admin clicks "Post to LinkedIn" in Social Studio  → POST /admin/marketing/linkedin/post
  4. We call ugcPosts API and optionally attach the promo flyer image

Scopes required:  r_liteprofile  w_member_social   (for personal profile)
                  r_organization_social  w_organization_social  (for a Company/Page - optional)

Developer Console: https://www.linkedin.com/developers/apps
"""
import os
import time
import logging
import requests
from urllib.parse import urlencode

logger = logging.getLogger("linkedin_service")

LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
LINKEDIN_REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "")  # e.g. https://yoursite.com/admin/marketing/linkedin/callback

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"
LINKEDIN_PROFILE_URL = "https://api.linkedin.com/v2/me"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_ASSETS_URL = "https://api.linkedin.com/v2/assets?action=registerUpload"

# Requested scopes — covers text + image posts on personal feed
LINKEDIN_SCOPES = ["openid", "profile", "w_member_social"]


def get_authorization_url(state: str) -> str:
    """Build the LinkedIn OAuth authorization URL to redirect the admin to."""
    params = {
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": LINKEDIN_REDIRECT_URI,
        "state": state,
        "scope": " ".join(LINKEDIN_SCOPES),
    }
    return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict:
    """
    Exchange the OAuth authorization code for an access token.
    Returns a dict with keys: access_token, expires_in, scope, token_type
    Returns None on failure.
    """
    if not LINKEDIN_CLIENT_ID or not LINKEDIN_CLIENT_SECRET:
        logger.error("LinkedIn credentials not configured (LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET missing)")
        return None

    try:
        response = requests.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": LINKEDIN_REDIRECT_URI,
                "client_id": LINKEDIN_CLIENT_ID,
                "client_secret": LINKEDIN_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.exception("Failed to exchange LinkedIn code for token: %s", exc)
        return None


def get_profile_urn(access_token: str, id_token: str = None) -> tuple[str | None, str | None]:
    """
    Fetch the authenticated LinkedIn member's URN (e.g. 'urn:li:person:ABCDE') and display name.
    1. First tries OpenID Connect /v2/userinfo (the modern LinkedIn standard for openid+profile scopes).
    2. Then tries decoding the JWT id_token if provided.
    3. Falls back to legacy /v2/me.
    """
    # 1. Try modern OpenID Connect /v2/userinfo
    try:
        resp = requests.get(
            LINKEDIN_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            sub = data.get("sub")
            name = data.get("name") or f"{data.get('given_name', '')} {data.get('family_name', '')}".strip()
            if sub:
                return f"urn:li:person:{sub}", name
    except Exception as exc:
        logger.debug("OpenID Connect userinfo request failed: %s", exc)

    # 2. Try parsing id_token JWT
    if id_token and "." in str(id_token):
        try:
            import base64
            import json as json_mod
            parts = str(id_token).split(".")
            if len(parts) >= 2:
                payload = parts[1]
                payload += "=" * (-len(payload) % 4)
                jwt_data = json_mod.loads(base64.b64decode(payload))
                sub = jwt_data.get("sub")
                name = jwt_data.get("name")
                if sub:
                    return f"urn:li:person:{sub}", name
        except Exception as jwt_err:
            logger.debug("Failed parsing id_token: %s", jwt_err)

    # 3. Fallback to legacy /v2/me
    try:
        resp = requests.get(
            LINKEDIN_PROFILE_URL,
            headers={"Authorization": f"Bearer {access_token}", "X-Restli-Protocol-Version": "2.0.0"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            member_id = data.get("id")
            if member_id:
                return f"urn:li:person:{member_id}", None
    except Exception as exc:
        logger.debug("Legacy /v2/me request failed: %s", exc)

    return None, None


def upload_image_to_linkedin(access_token: str, author_urn: str, image_bytes: bytes) -> str | None:
    """
    Registers an image upload with LinkedIn, uploads the image bytes, and returns the asset URN.
    Returns the asset URN on success, None on failure.
    """
    # Step 1: Register the upload
    try:
        register_payload = {
            "registerUploadRequest": {
                "owner": author_urn,
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "serviceRelationships": [
                    {"identifier": "urn:li:userGeneratedContent", "relationshipType": "OWNER"}
                ],
                "supportedUploadMechanism": ["SYNCHRONOUS_UPLOAD"],
            }
        }
        reg_resp = requests.post(
            LINKEDIN_ASSETS_URL,
            json=register_payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Restli-Protocol-Version": "2.0.0",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        reg_resp.raise_for_status()
        reg_data = reg_resp.json()
        upload_url = reg_data["value"]["uploadMechanism"][
            "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
        ]["uploadUrl"]
        asset_urn = reg_data["value"]["asset"]
    except (requests.RequestException, KeyError, TypeError) as exc:
        body = exc.response.text if hasattr(exc, "response") and exc.response is not None else ""
        logger.exception("Failed to register LinkedIn image upload: %s | response: %s", exc, body)
        return None

    # Step 2: Upload the actual bytes to the pre-signed upload URL
    try:
        # Pre-signed cloud storage URLs reject extra Authorization headers
        put_headers = {"Content-Type": "image/png"}
        if "api.linkedin.com" in upload_url:
            put_headers["Authorization"] = f"Bearer {access_token}"
        up_resp = requests.put(
            upload_url,
            data=image_bytes,
            headers=put_headers,
            timeout=30,
        )
        up_resp.raise_for_status()
        return asset_urn
    except requests.RequestException as exc:
        body = exc.response.text if hasattr(exc, "response") and exc.response is not None else ""
        logger.exception("Failed to upload image bytes to LinkedIn: %s | response: %s", exc, body)
        return None


def post_to_linkedin(
    access_token: str,
    author_urn: str,
    text: str,
    image_bytes: bytes = None,
) -> dict:
    """
    Publish a post on LinkedIn. If image_bytes is provided, attaches the promo flyer.

    Returns:
        {"status": "success", "post_id": "...", "url": "https://linkedin.com/..."}
        {"status": "error", "message": "..."}
    """
    if not access_token or not author_urn:
        return {"status": "error", "message": "Missing access token or author URN."}

    # Optionally upload the image first
    asset_urn = None
    if image_bytes:
        asset_urn = upload_image_to_linkedin(access_token, author_urn, image_bytes)
        if not asset_urn:
            # Degrade gracefully to text-only
            logger.warning("Image upload to LinkedIn failed — posting text only.")

    # Build UGC Post payload
    if asset_urn:
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "IMAGE",
                    "media": [
                        {
                            "status": "READY",
                            "media": asset_urn,
                            "title": {"text": "WAJ VTU Data Deal"},
                        }
                    ],
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
    else:
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }

    try:
        resp = requests.post(
            LINKEDIN_UGC_POSTS_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Restli-Protocol-Version": "2.0.0",
                "Content-Type": "application/json",
            },
            timeout=20,
        )
        resp.raise_for_status()
        # LinkedIn returns the post URN in the X-RestLi-Id header
        post_urn = resp.headers.get("X-RestLi-Id", "")
        post_id = post_urn.replace("urn:li:ugcPost:", "") if post_urn else ""
        post_url = f"https://www.linkedin.com/feed/update/{post_urn}/" if post_urn else "https://linkedin.com/feed"
        logger.info("Posted to LinkedIn successfully: %s", post_urn)
        return {"status": "success", "post_id": post_id, "post_urn": post_urn, "url": post_url}
    except requests.RequestException as exc:
        body = ""
        if hasattr(exc, "response") and exc.response is not None:
            try:
                body = exc.response.json()
            except Exception:
                body = exc.response.text
        logger.exception("Failed to post to LinkedIn: %s | body: %s", exc, body)
        return {"status": "error", "message": str(body) or str(exc)}


def is_configured() -> bool:
    """Returns True if LinkedIn credentials are set in environment."""
    return bool(LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET and LINKEDIN_REDIRECT_URI)


def is_token_valid(token_data: dict) -> bool:
    """
    Check if the stored token data is still valid (not expired).
    token_data should have 'access_token' and 'expires_at' (unix timestamp).
    """
    if not token_data or not token_data.get("access_token"):
        return False
    expires_at = token_data.get("expires_at", 0)
    # Treat as valid if more than 5 minutes remain
    return time.time() < (expires_at - 300)

