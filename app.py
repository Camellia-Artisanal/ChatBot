import hmac
import hashlib
import os
import httpx
from fastapi import FastAPI, Request, Response, Query, HTTPException, status
from fastapi.responses import PlainTextResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from typing import Dict, Any, Optional

from config import config
from database import db
from ad_gate import ad_gate
from responder import responder
from whatsapp_adapter import whatsapp_adapter
from escalator import escalator
from sheet_sync import sheet_sync

app = FastAPI(
    title="Camellia Artisanal — Instagram & WhatsApp Auto-Response Bot",
    description="Ad-Gated Instagram DM Bot + WhatsApp Business API with Deterministic Q&A Matching & Human Escalation",
    version="1.0.0"
)

# Mount local project directory to serve menu photo JPEGs at /images
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/images", StaticFiles(directory=BASE_DIR), name="images")

# --------------------------------------------------------------------------
# Root / Health Check
# --------------------------------------------------------------------------
@app.get("/")
def read_root():
    return {
        "client": "camellia.artisanal",
        "service": "Ad-Gated Auto-Response Bot",
        "status": "running",
        "analytics": db.get_analytics()
    }

# --------------------------------------------------------------------------
# Instagram Webhook Endpoints
# --------------------------------------------------------------------------
@app.get("/webhook/instagram", response_class=PlainTextResponse)
def verify_instagram_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge")
):
    """Meta Webhook verification endpoint for Instagram."""
    if hub_mode == "subscribe" and hub_verify_token == config.META_VERIFY_TOKEN:
        print("[INSTAGRAM WEBHOOK] Verification successful.")
        return hub_challenge or ""
    print("[INSTAGRAM WEBHOOK] Verification failed. Invalid token.")
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook/instagram")
async def handle_instagram_webhook(request: Request):
    """
    Handles Instagram DM Webhooks.
    Applies Ad-Gate: Responds ONLY to Ad-originated messages (referral.source == 'ADS').
    """
    payload = await request.json()
    
    if payload.get("object") != "instagram":
        return {"status": "ignored", "reason": "Not an instagram object"}

    results = []
    for entry in payload.get("entry", []):
        for messaging_event in entry.get("messaging", []):
            is_ad_qualified, sender_id, message_text, ad_id = ad_gate.inspect_and_gate_message(messaging_event)
            
            if not is_ad_qualified:
                # Hard Gate: Ignore organic messages per PRD Requirement #4
                print(f"[AD-GATE IGNORE] Sender {sender_id} is non-ad organic DM. Sitting for manual reply.")
                results.append({"sender_id": sender_id, "action": "ignored_organic_dm"})
                continue

            # Match Q&A response
            response_text, was_matched, template_id, image_url = responder.match_and_respond(message_text)
            
            # Log exchange to DB
            db.log_conversation(sender_id, "instagram", message_text, response_text, was_matched, template_id)

            # If unmatched, escalate to human contact
            if not was_matched:
                escalator.handle_escalation(
                    user_id=sender_id,
                    channel="instagram",
                    user_message=message_text,
                    reason="Unmatched Instagram Query / Sensitive Request",
                    context_data={"ad_id": ad_id, "sender_id": sender_id}
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

def send_instagram_reply(recipient_id: str, text: str, image_url: Optional[str] = None) -> Dict[str, Any]:
    """Sends text or image message via Meta Graph API to Instagram user."""
    if not config.WHATSAPP_ACCESS_TOKEN or not config.INSTAGRAM_PAGE_ID:
        safe_text = text[:60].encode("ascii", "backslashreplace").decode("ascii")
        msg_type = f"IMAGE ({image_url})" if image_url else "TEXT"
        print(f"[MOCK IG OUTBOUND] [{msg_type}] To: {recipient_id} | Message: {safe_text}...")
        return {"mock": True, "status": "sent", "recipient": recipient_id, "type": msg_type}

    url = f"https://graph.facebook.com/v19.0/me/messages"
    headers = {
        "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    if image_url:
        payload = {
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": "image",
                    "payload": {"url": image_url, "is_reusable": True}
                }
            }
        }
    else:
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text}
        }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            
            # If sending an image with caption, send text message follow-up on Instagram
            if image_url and text:
                client.post(url, headers=headers, json={"recipient": {"id": recipient_id}, "message": {"text": text}})
                
            return resp.json()
    except Exception as e:
        print(f"Failed to send IG message: {e}")
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
        print("[WHATSAPP WEBHOOK] Verification successful.")
        return hub_challenge or ""
    print("[WHATSAPP WEBHOOK] Verification failed. Invalid token.")
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook/whatsapp")
async def handle_whatsapp_webhook(request: Request):
    """Handles WhatsApp Business Webhook events."""
    payload = await request.json()
    parsed = whatsapp_adapter.parse_webhook_message(payload)
    if not parsed:
        return {"status": "no_message_event"}

    sender_phone, msg_id, message_text = parsed
    result = whatsapp_adapter.process_incoming_message(sender_phone, message_text)
    return result

# --------------------------------------------------------------------------
# Admin & Self-Service Endpoints
# --------------------------------------------------------------------------
@app.get("/admin/escalations")
def get_escalations_list(limit: int = 50, status: Optional[str] = None):
    """Returns recorded human escalations for client dashboard/records."""
    return {
        "count": len(db.get_escalations(limit, status)),
        "escalations": db.get_escalations(limit, status)
    }

@app.get("/admin/analytics")
def get_analytics():
    """Returns match rate vs escalation analytics summary."""
    return db.get_analytics()

@app.post("/admin/sync-menu")
def sync_menu(csv_url: Optional[str] = None):
    """Triggers live menu reload from Google Sheet CSV or local files."""
    if csv_url:
        result = sheet_sync.sync_from_csv_url(csv_url)
    else:
        result = sheet_sync.sync_local_reload()
    return result

# --------------------------------------------------------------------------
# Interactive Web Chat Tester Endpoint (/chat)
# --------------------------------------------------------------------------
from fastapi.responses import HTMLResponse

@app.post("/api/test-chat")
async def api_test_chat(request: Request):
    """Endpoint for interactive chat testing."""
    payload = await request.json()
    user_message = payload.get("message", "")
    channel = payload.get("channel", "instagram_ad") # 'instagram_ad', 'instagram_organic', 'whatsapp'

    if channel == "instagram_organic":
        # Simulate organic DM blocked by Ad-Gate
        return {
            "status": "blocked",
            "is_ad_qualified": False,
            "response": "🛑 [AD-GATE BLOCKED]: This is an organic (non-ad) DM. The bot does not auto-reply to organic DMs. Message sits in Inbox for human reply.",
            "was_matched": False,
            "image_url": None
        }

    # Simulate Instagram Ad or WhatsApp
    if channel == "instagram_ad":
        db.set_ad_qualified("web_tester", "instagram", is_ad_qualified=True)
        ch_name = "instagram"
    else:
        ch_name = "whatsapp"

    response_text, was_matched, template_id, image_url = responder.match_and_respond(user_message)
    db.log_conversation("web_tester", ch_name, user_message, response_text, was_matched, template_id)

    if not was_matched:
        escalator.handle_escalation("web_tester", ch_name, user_message, "Web Tester Unmatched", {})

    return {
        "status": "processed",
        "is_ad_qualified": True,
        "response": response_text,
        "was_matched": was_matched,
        "template_id": template_id,
        "image_url": image_url
    }

@app.get("/chat", response_class=HTMLResponse)
def get_web_chat():
    """Renders a sleek browser-based chat window to ask the bot questions directly."""
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Camellia Artisanal — Bot Tester</title>
        <style>
            * { box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
            body { background: #f0f2f5; margin: 0; padding: 20px; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
            .chat-container { width: 100%; max-width: 480px; background: white; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.12); display: flex; flex-direction: column; height: 90vh; overflow: hidden; }
            .header { background: #8B1E3F; color: white; padding: 16px 20px; display: flex; flex-direction: column; gap: 8px; }
            .header h2 { margin: 0; font-size: 1.2rem; }
            .header p { margin: 0; font-size: 0.85rem; opacity: 0.9; }
            .channel-select { margin-top: 6px; padding: 6px 10px; border-radius: 6px; border: none; font-size: 0.85rem; background: #fff; color: #333; }
            .messages { flex: 1; padding: 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; background: #faf9f6; }
            .message { max-width: 90%; padding: 10px 14px; border-radius: 12px; font-size: 0.92rem; line-height: 1.4; white-space: pre-wrap; word-wrap: break-word; }
            .user-msg { align-self: flex-end; background: #8B1E3F; color: white; border-bottom-right-radius: 2px; }
            .bot-msg { align-self: flex-start; background: #e9ecef; color: #212529; border-bottom-left-radius: 2px; }
            .menu-card-img { width: 100%; border-radius: 8px; margin-bottom: 8px; border: 1px solid #ddd; display: block; object-fit: contain; }
            .meta-tag { font-size: 0.72rem; color: #6c757d; margin-top: 4px; display: block; }
            .input-area { padding: 12px 16px; background: white; border-top: 1px solid #e9ecef; display: flex; gap: 8px; }
            input[type="text"] { flex: 1; padding: 10px 14px; border: 1px solid #ced4da; border-radius: 24px; outline: none; font-size: 0.95rem; }
            input[type="text"]:focus { border-color: #8B1E3F; }
            button { background: #8B1E3F; color: white; border: none; border-radius: 24px; padding: 10px 20px; font-weight: 600; cursor: pointer; }
            button:hover { background: #6b1630; }
            .suggestions { padding: 8px 16px; background: #f8f9fa; border-top: 1px solid #eee; display: flex; gap: 6px; overflow-x: auto; white-space: nowrap; }
            .chip { background: #e9ecef; font-size: 0.78rem; padding: 4px 10px; border-radius: 12px; cursor: pointer; color: #495057; }
            .chip:hover { background: #dee2e6; }
        </style>
    </head>
    <body>
        <div class="chat-container">
            <div class="header">
                <h2>🌸 Camellia Artisanal Bot Tester</h2>
                <p>Bengal-Rooted Artisanal Sweets & Food</p>
                <select id="channelSelect" class="channel-select">
                    <option value="instagram_ad">Instagram DM (Ad-Originated) ✅ Auto-Reply Enabled</option>
                    <option value="instagram_organic">Instagram DM (Organic) 🛑 Auto-Reply Blocked</option>
                    <option value="whatsapp">WhatsApp Business ✅ Auto-Reply Enabled</option>
                </select>
            </div>
            
            <div class="messages" id="messages">
                <div class="message bot-msg">
                    Hi! 👋 Ask me anything about our Sondesh flavours, prices, weekly menu card, or delivery options!
                </div>
            </div>

            <div class="suggestions">
                <span class="chip" onclick="quickSend('Show me the veg menu this week')">Veg Menu Card</span>
                <span class="chip" onclick="quickSend('What non-veg dishes do you have?')">Non-Veg Menu Card</span>
                <span class="chip" onclick="quickSend('How much is Sondesh?')">Sondesh Price</span>
                <span class="chip" onclick="quickSend('Do you deliver to Gurgaon?')">Delivery</span>
                <span class="chip" onclick="quickSend('I want a refund')">Test Escalation</span>
            </div>

            <div class="input-area">
                <input type="text" id="userInput" placeholder="Ask a question..." onkeydown="if(event.key==='Enter') sendMessage()">
                <button onclick="sendMessage()">Send</button>
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
                        body: JSON.stringify({ message: text, channel: channel })
                    });
                    const data = await resp.json();
                    
                    let metaText = data.was_matched ? `Matched: ${data.template_id}` : `Escalated to Human`;
                    if (channel === 'instagram_organic') metaText = 'Ad-Gate Filtered';
                    
                    appendMessage(data.response, 'bot-msg', metaText, data.image_url);
                } catch (err) {
                    appendMessage('Error connecting to server.', 'bot-msg');
                }
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
                    const img = document.createElement('img');
                    img.src = imageUrl;
                    img.className = 'menu-card-img';
                    img.alt = 'Weekly Menu Card';
                    div.appendChild(img);
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

