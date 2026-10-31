"""Verified native Instagram media shares for Camellia-owned Reels.

Verified 2 October 2026: @camellia.artisanal owned /me/media collection,
six pages, unique exact permalinks; Reel #1 MEDIA_SHARE accepted and its
native card confirmed by the allowed tester. Additional Reel IDs are admitted
only from the current owner-scoped media inventory. No caption or image inference.
"""
from camellia.config import config


VERIFIED_ACCOUNT_ID = "17841455615744812"
VERIFIED_MEDIA = {
    "https://www.instagram.com/reel/DdJhXQMT4VZ/": "18073790945717461",
    "https://www.instagram.com/reel/Dcd5bbEzbCo/": "18191102770349496",
    "https://www.instagram.com/reel/DcLZpuQzCKE/": "18158439172491979",
    "https://www.instagram.com/reel/DcQYp72zf8D/": "17908289592288134",
}


def native_sharing_enabled():
    return (config.instagram_media_owner_id() == VERIFIED_ACCOUNT_ID
            and config.INSTAGRAM_EXPECTED_USERNAME == "camellia.artisanal"
            and config.instagram_auth_mode() == "instagram_login")


def _legacy_native_sharing_enabled():
    """Keep the pre-inventory exact Reel allowlist on its original account guard."""
    return (str(config.INSTAGRAM_ACCOUNT_ID or "").strip() == VERIFIED_ACCOUNT_ID
            and config.INSTAGRAM_EXPECTED_USERNAME == "camellia.artisanal"
            and config.instagram_auth_mode() == "instagram_login")


def verified_media_id(url):
    """Never reuse this account's verified capability for another account."""
    known = VERIFIED_MEDIA.get(url)
    if known and _legacy_native_sharing_enabled():
        return known
    if not native_sharing_enabled():
        return None
    from camellia.database import db
    item = db.get_instagram_media_by_permalink(str(url or ""))
    if not item:
        return None
    return verified_media_id_for_item(item)


def verified_media_id_for_item(item):
    """Return a native share ID only for a synced, current Camellia-owned Reel."""
    if not native_sharing_enabled():
        return None
    owner = config.instagram_media_owner_id()
    if (not item or str(item.get("owner_instagram_account_id") or "") != owner
            or str(item.get("inventory_state") or "").upper() != "CURRENT"
            or str(item.get("media_type") or "").upper() != "VIDEO"
            or "/reel/" not in str(item.get("permalink") or "").lower()
            or not db_sync_is_current(owner)):
        return None
    media_id = str(item.get("instagram_media_id") or "").strip()
    return media_id if media_id and not media_id.startswith("manual:") else None


def db_sync_is_current(owner):
    from camellia.database import db
    return db.has_successful_instagram_media_inventory_sync(owner)


def synced_reel(media_id):
    """Resolve a picker selection again at preview, publish, and send time."""
    from camellia.database import db
    item = db.get_instagram_media_by_meta_id(str(media_id or ""))
    if not item or verified_media_id_for_item(item) != str(media_id or ""):
        return None
    return item


def explicitly_unavailable(response):
    """Fallback only on an explicit native rejection, never network uncertainty."""
    if response is None or response.status_code not in {400, 403}:
        return False
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        return False
    message = str(error.get("message", "")).lower()
    return error.get("code") in {10, 100, 200} and any(
        term in message for term in ("media_share", "media share", "media sharing", "attachment type")
    ) and any(term in message for term in (
        "not supported", "unsupported", "not available", "permission", "not owned", "own media",
    ))
