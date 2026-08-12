import hmac
import hashlib
import json
import os
import httpx
from fastapi import FastAPI, Request, Response, Query, Header, HTTPException
from fastapi.responses import PlainTextResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from typing import Dict, Any, Optional

from config import config
from database import db
from ad_gate import ad_gate
from responder import responder
from whatsapp_adapter import whatsapp_adapter
from escalator import escalator
from sheet_sync import sheet_sync
from session_memory import session_memory
from monitoring import get_logger, delivery_monitor

log = get_logger("camellia.app")

app = FastAPI(
    title="FastAPI Webhook Application for Camellia Artisanal — Instagram & WhatsApp Auto-Response Bot",
# Ad-Gated Instagram DM Bot + WhatsApp Business API with Deterministic Q&A Matching & Gemini RAG AI & Human Escalation
    version="1.0.0"
)

# Serve menu photo JPEGs from the dedicated images/ subdirectory.
# Mounting BASE_DIR directly would expose .env, source files and the database.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/images", StaticFiles(directory=os.path.join(BASE_DIR, "images")), name="images")

def verify_meta_signature(raw_body: bytes, signature_header: Optional[str]) -> None:
    """
    Rejects webhook POSTs that Meta did not sign with our app secret.

    Skipped entirely when META_APP_SECRET is unset so local development and the
    test suite can post unsigned payloads. Always set it in production.
    """
    secret = (config.META_APP_SECRET or "").strip()
    if not secret:
        return

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()

    if not signature_header or not hmac.compare_digest(expected, signature_header):
        log.warning(
            "Rejected webhook payload signature mismatch!\n"
            "  Body length: %d bytes\n"
            "  Secret length: %d (starts with '%s...')\n"
            "  Received Header: %s\n"
            "  Expected HMAC:   %s",
            len(raw_body),
            len(secret),
            secret[:4] if len(secret) >= 4 else "***",
            signature_header,
            expected,
        )
        raise HTTPException(status_code=403, detail="Invalid payload signature")

def require_admin(provided_key: Optional[str]) -> None:
    """
    Guards /admin/* routes, which expose customer conversations and can
    overwrite the live menu. Skipped when ADMIN_API_KEY is unset.
    """
    if not config.ADMIN_API_KEY:
        return
    if not provided_key or not hmac.compare_digest(config.ADMIN_API_KEY, provided_key):
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key header")

def require_web_tester() -> None:
    """
    Guards the browser chat tester and its APIs.

    These write real conversation rows and fire real escalation alerts to the
    owner's phone, so on a public domain they are abusable by anyone who finds
    the URL. Keep ENABLE_WEB_TESTER false in production; it exists for local
    development, where a browser cannot attach an admin header on navigation.
    """
    if not config.ENABLE_WEB_TESTER:
        raise HTTPException(status_code=404, detail="Not found")

# --------------------------------------------------------------------------
# Root / Health Check
# --------------------------------------------------------------------------
@app.on_event("startup")
def purge_expired_on_startup():
    """Enforces the retention window promised on /privacy."""
    if not config.META_APP_SECRET:
        log.warning(
            "META_APP_SECRET is not set. Webhook signature verification is DISABLED — "
            "anyone can POST fake messages. Set it in .env before going live."
        )
    if not config.ADMIN_API_KEY:
        log.warning(
            "ADMIN_API_KEY is not set. /admin/* endpoints are open to the internet — "
            "customer conversations and the live menu are publicly readable. Set it in .env."
        )
    try:
        removed = db.purge_expired_data()
        if any(removed.values()):
            log.info(
                "Retention purge removed %s conversations, %s escalations, %s sessions "
                "older than %s days.",
                removed["conversations"], removed["escalations"], removed["sessions"],
                config.DATA_RETENTION_DAYS,
            )
    except Exception:
        log.exception("Retention purge failed on startup.")

@app.get("/")
def read_root():
    return {
        "client": "camellia.artisanal",
        "service": "Ad-Gated Auto-Response Bot",
        "status": "running",
    }

# --------------------------------------------------------------------------
# Instagram Webhook Endpoints
# --------------------------------------------------------------------------
@app.get("/webhook/instagram")
def verify_instagram_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge")
):
    """Meta Webhook verification endpoint for Instagram."""
    if (hub_mode in ("subscribe", None)) and hub_verify_token == config.META_VERIFY_TOKEN:
        log.info("Instagram webhook verification successful.")
        return Response(content=str(hub_challenge or ""), media_type="text/plain", status_code=200)
    log.warning("Instagram webhook verification failed (mode=%s).", hub_mode)
    return Response(content="Forbidden", media_type="text/plain", status_code=403)

@app.post("/webhook/instagram")
async def handle_instagram_webhook(request: Request):
    """
    Handles Instagram DM Webhooks.
    Applies Ad-Gate: Responds ONLY to Ad-originated messages (referral.source == 'ADS').
    """
    raw_body = await request.body()
    verify_meta_signature(raw_body, request.headers.get("X-Hub-Signature-256"))
    try:
        payload = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        log.warning("Received malformed JSON on Instagram webhook — ignoring.")
        return {"status": "ignored", "reason": "malformed_json"}
    
    if payload.get("object") != "instagram":
        return {"status": "ignored", "reason": "Not an instagram object"}

    log.info("Instagram Webhook Raw Payload:\n%s", json.dumps(payload, indent=2))

    results = []
    for entry in payload.get("entry", []):
        events = list(entry.get("messaging", []))
        if "changes" in entry:
            for change in entry.get("changes", []):
                val = change.get("value")
                if isinstance(val, dict):
                    events.append(val)

        for messaging_event in events:
            is_ad_qualified, sender_id, message_text, ad_id = ad_gate.inspect_and_gate_message(messaging_event)
            
            if not sender_id:
                log.info("Ignored Instagram event without sender ID (keys: %s).", list(messaging_event.keys()))
                results.append({"action": "ignored_no_sender"})
                continue

            if not message_text:
                log.info("Ignored Instagram event without text payload (sender: %s, keys: %s).", sender_id, list(messaging_event.keys()))
                results.append({"sender_id": sender_id, "action": "ignored_empty_text"})
                continue

            if not is_ad_qualified:
                # Hard Gate: Ignore organic messages per PRD Requirement #4
                log.info("Ad-gate: sender %s is a non-ad organic DM; left for manual reply.", sender_id)
                results.append({"sender_id": sender_id, "action": "ignored_organic_dm"})
                continue

            # Match Q&A response with session memory
            response_text, was_matched, template_id, image_url = responder.match_and_respond(message_text, user_id=sender_id, channel="instagram")
            
            # Log exchange to DB
            db.log_conversation(sender_id, "instagram", message_text, response_text, was_matched, template_id)

            # If unmatched OR order confirmation, escalate to human contact
            if not was_matched or template_id == "order_next_steps":
                escalator.handle_escalation(
                    user_id=sender_id,
                    channel="instagram",
                    user_message=message_text,
                    reason="Order Confirmation & Intake Request" if was_matched else "Unmatched Instagram Query / Sensitive Request",
                    context_data={"ad_id": ad_id, "sender_id": sender_id, "template_id": template_id}
                )

            # Dispatch outbound reply via Meta Graph API
            send_status = send_instagram_reply(sender_id, response_text, image_url=image_url)
            
            results.append({
                "sender_id": sender_id,
                "action": "auto_replied" if was_matched else "escalated",
                "template_id": template_id,
                "response_preview": response_text[:60],
                "image_url": image_url,
                "send_status": send_status
            })

    return {"status": "processed", "results": results}

def send_instagram_reply(recipient_id: str, text: str, image_url=None) -> Dict[str, Any]:
    """Sends text plus zero or more image cards via Meta Graph API to Instagram user."""
    images = [image_url] if isinstance(image_url, str) else list(image_url or [])

    if not config.INSTAGRAM_ACCESS_TOKEN or not config.INSTAGRAM_PAGE_ID:
        safe_text = text[:60].encode("ascii", "backslashreplace").decode("ascii")
        msg_type = f"IMAGE x{len(images)} ({', '.join(images)})" if images else "TEXT"
        log.info("[MOCK IG OUTBOUND] [%s] To: %s | Message: %s...", msg_type, recipient_id, safe_text)
        return {"mock": True, "status": "sent", "recipient": recipient_id, "type": msg_type}

    page_id = config.INSTAGRAM_PAGE_ID or "me"
    url = f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/{page_id}/messages"
    headers = {
        "Authorization": f"Bearer {config.INSTAGRAM_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            last = None
            if text:
                last = client.post(url, headers=headers, json={"recipient": {"id": recipient_id}, "message": {"text": text}})
                last.raise_for_status()

            for img in images:
                last = client.post(url, headers=headers, json={
                    "recipient": {"id": recipient_id},
                    "message": {"attachment": {"type": "image", "payload": {"url": img, "is_reusable": True}}}
                })
                last.raise_for_status()

            delivery_monitor.record_success("instagram")
            return last.json() if last is not None else {"status": "no_content"}
    except Exception as e:
        delivery_monitor.record_failure("instagram", str(e))
        if hasattr(e, "response") and getattr(e, "response", None) is not None:
            log.error("Instagram Meta API Error Body: %s", e.response.text)
        return {"error": str(e), "status": "failed"}

# --------------------------------------------------------------------------
# WhatsApp Webhook Endpoints
# --------------------------------------------------------------------------
@app.get("/webhook/whatsapp", response_class=PlainTextResponse)
def verify_whatsapp_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge")
):
    """Meta Webhook verification endpoint for WhatsApp Business API."""
    if hub_mode == "subscribe" and hub_verify_token == config.META_VERIFY_TOKEN:
        log.info("WhatsApp webhook verification successful.")
        return hub_challenge or ""
    log.warning("WhatsApp webhook verification failed: invalid token.")
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook/whatsapp")
async def handle_whatsapp_webhook(request: Request):
    """Handles WhatsApp Business Webhook events."""
    raw_body = await request.body()
    verify_meta_signature(raw_body, request.headers.get("X-Hub-Signature-256"))
    try:
        payload = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        log.warning("Received malformed JSON on WhatsApp webhook — ignoring.")
        return {"status": "ignored", "reason": "malformed_json"}
    parsed = whatsapp_adapter.parse_webhook_message(payload)
    if not parsed:
        return {"status": "no_message_event"}

    sender_phone, msg_id, message_text = parsed
    result = whatsapp_adapter.process_incoming_message(sender_phone, message_text)
    return result

# --------------------------------------------------------------------------
# Legal Pages (required by Meta before an app can go Live, and by the
# DPDP Act 2023 because we store customer names and message history)
# --------------------------------------------------------------------------

LEGAL_PAGE_CSS = """
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           max-width: 760px; margin: 0 auto; padding: 40px 24px 80px; line-height: 1.65;
           color: #2c2420; background: #fdfaf6; }
    h1 { font-size: 28px; margin-bottom: 4px; }
    h2 { font-size: 19px; margin-top: 34px; border-bottom: 1px solid #e8ded2; padding-bottom: 6px; }
    .updated { color: #8a7c6d; font-size: 14px; margin-top: 0; }
    a { color: #8c5a2b; }
    ul { padding-left: 22px; }
    li { margin-bottom: 6px; }
    .box { background: #f5ede2; border-left: 3px solid #8c5a2b; padding: 14px 18px; margin: 22px 0; }
    footer { margin-top: 48px; font-size: 13px; color: #8a7c6d; }
"""


@app.get("/privacy", response_class=HTMLResponse)
def privacy_policy():
    """Privacy policy. Meta requires this URL before the app can go Live."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Privacy Policy — {config.BUSINESS_LEGAL_NAME}</title>
<style>{LEGAL_PAGE_CSS}</style></head><body>

<h1>Privacy Policy</h1>
<p class="updated">{config.BUSINESS_LEGAL_NAME} &middot; Last updated 5 August 2026</p>

<p>This policy explains what happens to your information when you message
{config.BUSINESS_LEGAL_NAME} on Instagram or WhatsApp and our automated assistant replies.
We have tried to write it in plain language rather than legal boilerplate.</p>

<h2>Who is responsible for your data</h2>
<p>{config.BUSINESS_LEGAL_NAME} is the data fiduciary for the information described here.
For any privacy question or request, contact
<a href="mailto:{config.PRIVACY_CONTACT_EMAIL}">{config.PRIVACY_CONTACT_EMAIL}</a>
or call +91 87965 51990.</p>

<h2>What we collect</h2>
<ul>
  <li><strong>Your messages to us</strong> and the assistant's replies.</li>
  <li><strong>Your Instagram or WhatsApp account identifier</strong>, as supplied by Meta.
      For WhatsApp this is your phone number.</li>
  <li><strong>Your first name</strong>, only if you tell it to us in the chat.</li>
  <li><strong>Whether you arrived via one of our advertisements</strong>, because our assistant
      only replies automatically to enquiries that started from an ad.</li>
</ul>

<div class="box">
  <strong>We do not collect payment card details, addresses, or health information
  through this chat.</strong> Orders are completed with a human being over a separate
  conversation or call.
</div>

<h2>Why we collect it</h2>
<ul>
  <li>To answer your questions about our menu, prices, and delivery.</li>
  <li>To pass your enquiry to a human colleague when the assistant cannot help,
      so that the person picking it up can see what you already asked.</li>
  <li>To count how many questions the assistant answered correctly, so we can improve it.
      This is a simple tally and is not linked back to you.</li>
</ul>

<h2>How long we keep it</h2>
<p>Conversations, enquiry records, and session information are
<strong>automatically deleted after {config.DATA_RETENTION_DAYS} days</strong>.
This deletion is enforced by the software itself, not by someone remembering to do it.</p>

<h2>Who we share it with</h2>
<p>We do not sell your information, and we do not share it with advertisers or data brokers.
Your messages travel through <strong>Meta Platforms</strong> (Instagram and WhatsApp), which
handles them under its own privacy policy — that is unavoidable for any business reachable on
those apps. Beyond Meta, your conversation stays on our own server. No third-party analytics
or advertising service receives it.</p>

<h2>Where it is stored</h2>
<p>On a private server rented by {config.BUSINESS_LEGAL_NAME}, in a database that is not
reachable from the public internet. Access is limited to the business owner and the technical
administrator. Traffic to and from this service is encrypted with HTTPS.</p>

<h2>Your rights</h2>
<p>Under India's Digital Personal Data Protection Act, 2023, you may:</p>
<ul>
  <li>Ask what information we hold about you.</li>
  <li>Ask us to correct anything that is wrong.</li>
  <li><strong>Ask us to delete everything we hold about you</strong>, at any time and without
      giving a reason.</li>
  <li>Withdraw your consent — simply stop messaging us, and the record is deleted within
      {config.DATA_RETENTION_DAYS} days regardless.</li>
  <li>Raise a grievance with us first, and thereafter with the Data Protection Board of India.</li>
</ul>
<p>Email <a href="mailto:{config.PRIVACY_CONTACT_EMAIL}">{config.PRIVACY_CONTACT_EMAIL}</a>
with the phone number or handle you messaged us from. We will action deletion requests
within 30 days.</p>

<h2>Children</h2>
<p>This service is intended for adults placing food orders. We do not knowingly keep
information about anyone under 18.</p>

<h2>Changes to this policy</h2>
<p>If we change how we handle your information, we will update this page and change the date
at the top.</p>

<footer><a href="/terms">Terms of Service</a></footer>
</body></html>"""


@app.get("/terms", response_class=HTMLResponse)
def terms_of_service():
    """Terms of service for the automated chat assistant."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Terms of Service — {config.BUSINESS_LEGAL_NAME}</title>
<style>{LEGAL_PAGE_CSS}</style></head><body>

<h1>Terms of Service</h1>
<p class="updated">{config.BUSINESS_LEGAL_NAME} &middot; Last updated 5 August 2026</p>

<p>These terms cover the automated chat assistant that replies to messages sent to
{config.BUSINESS_LEGAL_NAME} on Instagram and WhatsApp.</p>

<h2>You are talking to software</h2>
<p>Replies are generated automatically. When the assistant is not confident about your
question, it stops and passes you to a human colleague rather than guessing.</p>

<h2>Prices and availability</h2>
<p>Prices quoted in chat are indicative and may change with ingredient costs and seasonal
availability. <strong>An order is confirmed only when a member of our team confirms it
directly with you.</strong> Nothing the assistant says forms a binding contract by itself.
Delivery charges are billed on actuals.</p>

<h2>Delivery</h2>
<p>We deliver within Delhi NCR only, dispatched from Sushant Lok, Gurgaon. Our kitchen
operates 10:00 AM to 7:30 PM daily. Because our sweets are made fresh without preservatives,
we cannot ship them outside our delivery area.</p>

<h2>Food information</h2>
<p>Our sweets are dairy-based and are prepared in a kitchen that handles milk, nuts, and
wheat. <strong>If you have a food allergy, speak to a human colleague before ordering</strong> —
please do not rely on the automated assistant for allergy guidance.</p>

<h2>Fair use</h2>
<p>Please do not use this service to send unlawful, abusive, or automated bulk messages.
We may stop responding to any account that does.</p>

<h2>Availability</h2>
<p>We aim to keep the assistant running at all times but cannot guarantee uninterrupted
service. If it is unavailable, please call +91 87965 51990.</p>

<h2>Governing law</h2>
<p>These terms are governed by the laws of India, with jurisdiction in the courts of Gurugram,
Haryana.</p>

<h2>Contact</h2>
<p>{config.BUSINESS_LEGAL_NAME} &middot;
<a href="mailto:{config.PRIVACY_CONTACT_EMAIL}">{config.PRIVACY_CONTACT_EMAIL}</a>
&middot; +91 87965 51990</p>

<footer><a href="/privacy">Privacy Policy</a></footer>
</body></html>"""


# --------------------------------------------------------------------------
# Admin & Self-Service Endpoints
# --------------------------------------------------------------------------
@app.get("/health")
def health_check():
    """Deep health check. Point an uptime monitor at this, not at /."""
    outbound = delivery_monitor.snapshot()
    status_code = 200 if outbound["healthy"] else 503
    return Response(
        content=json.dumps({
            "status": "healthy" if outbound["healthy"] else "degraded",
            "outbound_delivery": outbound,
            "retention_days": config.DATA_RETENTION_DAYS,
        }),
        media_type="application/json",
        status_code=status_code,
    )

@app.delete("/admin/user-data/{user_id}")
def delete_user_data(user_id: str, x_admin_key: Optional[str] = Header(None)):
    """Erases everything held about one customer, for DPDP deletion requests."""
    require_admin(x_admin_key)
    removed = db.delete_user_data(user_id)
    session_memory.clear_session(user_id, "instagram")
    session_memory.clear_session(user_id, "whatsapp")
    log.info("Erased all stored data for user %s: %s", user_id, removed)
    return {"status": "deleted", "user_id": user_id, "removed": removed}

@app.get("/admin/escalations")
def get_escalations_list(limit: int = 50, status: Optional[str] = None, x_admin_key: Optional[str] = Header(None)):
    """Returns recorded human escalations for client dashboard/records."""
    require_admin(x_admin_key)
    escalations = db.get_escalations(limit, status)
    return {"count": len(escalations), "escalations": escalations}

@app.get("/admin/analytics")
def get_analytics(x_admin_key: Optional[str] = Header(None)):
    """Returns match rate vs escalation analytics summary."""
    require_admin(x_admin_key)
    return db.get_analytics()

@app.post("/admin/sync-menu")
def sync_menu(csv_url: Optional[str] = None, x_admin_key: Optional[str] = Header(None)):
    """Triggers live menu reload from Google Sheet CSV or local files."""
    require_admin(x_admin_key)
    if csv_url:
        result = sheet_sync.sync_from_csv_url(csv_url)
    else:
        result = sheet_sync.sync_local_reload()
    return result

@app.post("/admin/update-menu")
async def update_menu(request: Request, x_admin_key: Optional[str] = Header(None)):
    """Dynamically updates the latest weekly menu rotation block in menu.json and hot-reloads the responder."""
    require_admin(x_admin_key)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    menu_path = config.MENU_JSON_PATH
    try:
        with open(menu_path, "r", encoding="utf-8") as f:
            menu_data = json.load(f)

        if "weekly_menus" in menu_data and menu_data["weekly_menus"]:
            current_week = menu_data["weekly_menus"][-1]
            if "week" in body:
                current_week["week"] = body["week"]
            if "vegetarian" in body and isinstance(body["vegetarian"], list):
                current_week["vegetarian"] = body["vegetarian"]
            if "non_vegetarian" in body and isinstance(body["non_vegetarian"], list):
                current_week["non_vegetarian"] = body["non_vegetarian"]

            with open(menu_path, "w", encoding="utf-8") as f:
                json.dump(menu_data, f, indent=2, ensure_ascii=False)

            responder.reload_data()
            return {"status": "success", "message": "Weekly menu updated and responder hot-reloaded successfully."}
        else:
            return {"status": "failed", "reason": "No weekly_menus block found in menu.json"}
    except Exception as e:
        log.error("Failed to update menu: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

# --------------------------------------------------------------------------
# Interactive Web Chat Tester Endpoint (/chat)
# --------------------------------------------------------------------------

@app.post("/api/test-chat")
async def api_test_chat(request: Request):
    """Endpoint for interactive chat testing with Session Memory support."""
    require_web_tester()
    payload = await request.json()
    user_message = payload.get("message", "")
    channel = payload.get("channel", "instagram_ad")
    # Always use a fixed tester ID — caller must not be able to supply a real
    # customer's ID and read or poison their session memory.
    user_id = "web_tester"

    if channel == "instagram_organic":
        # Simulate organic DM blocked by Ad-Gate
        return {
            "status": "blocked",
            "is_ad_qualified": False,
            "response": "🛑 [AD-GATE BLOCKED]: This is an organic (non-ad) DM. The bot does not auto-reply to organic DMs. Message sits in Inbox for human reply.",
            "was_matched": False,
            "image_url": None,
            "active_memory": session_memory.get_session(user_id, "instagram")["facts"]
        }

    # Simulate Instagram Ad or WhatsApp
    if channel == "instagram_ad":
        db.set_ad_qualified(user_id, "instagram", is_ad_qualified=True)
        ch_name = "instagram"
    else:
        ch_name = "whatsapp"

    response_text, was_matched, template_id, image_url = responder.match_and_respond(user_message, user_id=user_id, channel=ch_name)
    db.log_conversation(user_id, ch_name, user_message, response_text, was_matched, template_id)

    if not was_matched:
        escalator.handle_escalation(user_id, ch_name, user_message, "Web Tester Unmatched", {})

    active_facts = session_memory.get_session(user_id, ch_name)["facts"]

    return {
        "status": "processed",
        "is_ad_qualified": True,
        "response": response_text,
        "was_matched": was_matched,
        "template_id": template_id,
        "image_url": image_url,
        "active_memory": active_facts
    }

@app.post("/api/reset-session")
async def api_reset_session(request: Request):
    """Resets session memory for specified user and channel."""
    require_web_tester()
    payload = await request.json()
    user_id = payload.get("user_id", "web_tester")
    channel = payload.get("channel", "instagram_ad")
    ch_name = "whatsapp" if channel == "whatsapp" else "instagram"
    session_memory.clear_session(user_id, ch_name)
    return {"status": "cleared", "user_id": user_id, "channel": ch_name}

@app.get("/chat", response_class=HTMLResponse)
@app.get("/demo", response_class=HTMLResponse)
@app.get("/test", response_class=HTMLResponse)
def get_web_chat():
    """Renders a sleek browser-based chat window to ask the bot questions directly with Memory controls."""
    require_web_tester()
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Camellia Artisanal — Bot & Memory Tester</title>
        <style>
            * { box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
            body { background: #f0f2f5; margin: 0; padding: 20px; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
            .chat-container { width: 100%; max-width: 520px; background: white; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.12); display: flex; flex-direction: column; height: 92vh; overflow: hidden; }
            .header { background: #8B1E3F; color: white; padding: 16px 20px; display: flex; flex-direction: column; gap: 6px; }
            .header-top { display: flex; justify-content: space-between; align-items: center; }
            .header h2 { margin: 0; font-size: 1.2rem; }
            .reset-btn { background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.4); color: white; font-size: 0.75rem; padding: 4px 10px; border-radius: 12px; cursor: pointer; }
            .reset-btn:hover { background: rgba(255,255,255,0.35); }
            .header p { margin: 0; font-size: 0.85rem; opacity: 0.9; }
            .channel-select { margin-top: 4px; padding: 6px 10px; border-radius: 6px; border: none; font-size: 0.85rem; background: #fff; color: #333; }
            .memory-badge { background: #6b1630; color: #f8f9fa; padding: 6px 12px; font-size: 0.78rem; font-weight: 500; border-bottom: 1px solid #541124; display: flex; align-items: center; justify-content: space-between; }
            .messages { flex: 1; padding: 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; background: #faf9f6; }
            .message { max-width: 90%; padding: 10px 14px; border-radius: 12px; font-size: 0.92rem; line-height: 1.4; white-space: pre-wrap; word-wrap: break-word; }
            .user-msg { align-self: flex-end; background: #8B1E3F; color: white; border-bottom-right-radius: 2px; }
            .bot-msg { align-self: flex-start; background: #e9ecef; color: #212529; border-bottom-left-radius: 2px; }
            .menu-card-img { width: 100%; border-radius: 8px; margin-bottom: 8px; border: 1px solid #ddd; display: block; object-fit: contain; }
            .meta-tag { font-size: 0.72rem; color: #6c757d; margin-top: 4px; display: block; }
            .input-area { padding: 12px 16px; background: white; border-top: 1px solid #e9ecef; display: flex; gap: 8px; }
            input[type="text"] { flex: 1; padding: 10px 14px; border: 1px solid #ced4da; border-radius: 24px; outline: none; font-size: 0.95rem; }
            input[type="text"]:focus { border-color: #8B1E3F; }
            button.send-btn { background: #8B1E3F; color: white; border: none; border-radius: 24px; padding: 10px 20px; font-weight: 600; cursor: pointer; }
            button.send-btn:hover { background: #6b1630; }
            .suggestions { padding: 8px 16px; background: #f8f9fa; border-top: 1px solid #eee; display: flex; gap: 6px; overflow-x: auto; white-space: nowrap; }
            .chip { background: #e9ecef; font-size: 0.78rem; padding: 4px 10px; border-radius: 12px; cursor: pointer; color: #495057; }
            .chip:hover { background: #dee2e6; }
            .chip.memory-chip { background: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd; }
            .chip.memory-chip:hover { background: #bae6fd; }
        </style>
    </head>
    <body>
        <div class="chat-container">
            <div class="header">
                <div class="header-top">
                    <h2>🌸 Camellia Artisanal Bot Tester</h2>
                    <button class="reset-btn" onclick="resetSession()">🧹 Clear Memory</button>
                </div>
                <p>Bengal-Rooted Artisanal Sweets & Food</p>
                <select id="channelSelect" class="channel-select" onchange="resetSession()">
                    <option value="instagram_ad">Instagram DM (Ad-Originated) ✅ Auto-Reply Enabled</option>
                    <option value="instagram_organic">Instagram DM (Organic) 🛑 Auto-Reply Blocked</option>
                    <option value="whatsapp">WhatsApp Business ✅ Auto-Reply Enabled</option>
                </select>
            </div>
            
            <div class="memory-badge" id="memoryBadge">
                <span>👤 Active Session Memory: None recorded</span>
            </div>

            <div class="messages" id="messages">
                <div class="message bot-msg">
                    Hi! 👋 Ask me anything about our Sondesh flavours, prices, weekly menu card, or delivery options! Try telling me your name (e.g. "My name is Shoaib").
                </div>
            </div>

            <div class="suggestions">
                <span class="chip memory-chip" onclick="quickSend('My name is Shoaib')">1. Set Name</span>
                <span class="chip memory-chip" onclick="quickSend('What is my name?')">2. Recall Name</span>
                <span class="chip" onclick="quickSend('Show me the veg menu this week')">Veg Menu Card</span>
                <span class="chip" onclick="quickSend('How much is Sondesh?')">Sondesh Price</span>
                <span class="chip" onclick="quickSend('Who is the President of US?')">Out-of-Context Refusal</span>
            </div>

            <div class="input-area">
                <input type="text" id="userInput" placeholder="Ask a question..." onkeydown="if(event.key==='Enter') sendMessage()">
                <button class="send-btn" onclick="sendMessage()">Send</button>
            </div>
        </div>

        <script>
            async function sendMessage() {
                const input = document.getElementById('userInput');
                const text = input.value.trim();
                if (!text) return;

                const channel = document.getElementById('channelSelect').value;
                appendMessage(text, 'user-msg');
                input.value = '';

                try {
                    const resp = await fetch('/api/test-chat', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ message: text, channel: channel, user_id: 'web_tester' })
                    });
                    const data = await resp.json();
                    
                    let metaText = data.was_matched ? `Matched: ${data.template_id}` : `Escalated to Human`;
                    if (channel === 'instagram_organic') metaText = 'Ad-Gate Filtered';
                    
                    appendMessage(data.response, 'bot-msg', metaText, data.image_url);
                    updateMemoryBadge(data.active_memory);
                } catch (err) {
                    appendMessage('Error connecting to server.', 'bot-msg');
                }
            }

            async function resetSession() {
                const channel = document.getElementById('channelSelect').value;
                try {
                    await fetch('/api/reset-session', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ channel: channel, user_id: 'web_tester' })
                    });
                    updateMemoryBadge({ name: null });
                    appendMessage("🧹 Session memory reset for active channel.", "bot-msg", "System Event");
                } catch (err) {
                    console.error("Failed to reset session:", err);
                }
            }

            function updateMemoryBadge(memory) {
                const badge = document.getElementById('memoryBadge');
                if (!memory) return;
                const nameStr = memory.name ? `Name: <b>${memory.name}</b>` : 'Name: None';
                badge.innerHTML = `<span>👤 Session Memory: ${nameStr}</span>`;
            }

            function quickSend(text) {
                document.getElementById('userInput').value = text;
                sendMessage();
            }

            function appendMessage(text, className, meta = '', imageUrl = null) {
                const box = document.getElementById('messages');
                const div = document.createElement('div');
                div.className = 'message ' + className;
                
                if (imageUrl) {
                    const urls = Array.isArray(imageUrl) ? imageUrl : [imageUrl];
                    urls.forEach(u => {
                        const img = document.createElement('img');
                        img.src = u;
                        img.className = 'menu-card-img';
                        img.alt = 'Menu Card';
                        div.appendChild(img);
                    });
                }

                const textNode = document.createTextNode(text);
                div.appendChild(textNode);
                
                if (meta) {
                    const span = document.createElement('span');
                    span.className = 'meta-tag';
                    span.innerText = meta;
                    div.appendChild(span);
                }
                
                box.appendChild(div);
                box.scrollTop = box.scrollHeight;
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=5000, reload=True)

