import os
import httpx
from flask import Flask, request, jsonify, render_template_string, send_from_directory
from typing import Dict, Any, Optional

from config import config
from database import db
from ad_gate import ad_gate
from responder import responder
from whatsapp_adapter import whatsapp_adapter
from escalator import escalator
from sheet_sync import sheet_sync
from session_memory import session_memory

app = Flask(__name__)

# Serve static menu images
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.route("/images/<path:filename>")
def serve_images(filename):
    return send_from_directory(BASE_DIR, filename)

@app.route("/", methods=["GET"])
def read_root():
    return jsonify({
        "client": "camellia.artisanal",
        "framework": "Flask",
        "service": "Ad-Gated Auto-Response Bot",
        "status": "running",
        "analytics": db.get_analytics()
    })

# --------------------------------------------------------------------------
# Instagram Webhook Endpoints
# --------------------------------------------------------------------------
@app.route("/webhook/instagram", methods=["GET"])
def verify_instagram_webhook():
    """Meta Webhook verification endpoint for Instagram."""
    hub_mode = request.args.get("hub.mode")
    hub_verify_token = request.args.get("hub.verify_token")
    hub_challenge = request.args.get("hub.challenge")

    if (hub_mode in ("subscribe", None)) and hub_verify_token == config.META_VERIFY_TOKEN:
        print(f"[INSTAGRAM WEBHOOK - FLASK] Verification successful. Returning challenge: {hub_challenge}")
        return hub_challenge or "", 200, {"Content-Type": "text/plain"}
    
    print(f"[INSTAGRAM WEBHOOK - FLASK] Verification failed. Token: {hub_verify_token}")
    return "Forbidden", 403, {"Content-Type": "text/plain"}

@app.route("/webhook/instagram", methods=["POST"])
def handle_instagram_webhook():
    """Handles Instagram DM Webhooks with Ad-Gate."""
    payload = request.get_json(force=True, silent=True) or {}
    if payload.get("object") != "instagram":
        return jsonify({"status": "ignored", "reason": "Not an instagram object"})

    results = []
    for entry in payload.get("entry", []):
        for messaging_event in entry.get("messaging", []):
            is_ad_qualified, sender_id, message_text, ad_id = ad_gate.inspect_and_gate_message(messaging_event)
            
            if not is_ad_qualified:
                print(f"[AD-GATE IGNORE] Sender {sender_id} is non-ad organic DM. Sitting for manual reply.")
                results.append({"sender_id": sender_id, "action": "ignored_organic_dm"})
                continue

            response_text, was_matched, template_id, image_url = responder.match_and_respond(message_text, user_id=sender_id, channel="instagram")
            db.log_conversation(sender_id, "instagram", message_text, response_text, was_matched, template_id)

            if not was_matched:
                escalator.handle_escalation(
                    user_id=sender_id,
                    channel="instagram",
                    user_message=message_text,
                    reason="Unmatched Instagram Query / Sensitive Request",
                    context_data={"ad_id": ad_id, "sender_id": sender_id}
                )

            send_status = send_instagram_reply(sender_id, response_text, image_url=image_url)
            results.append({
                "sender_id": sender_id,
                "action": "auto_replied" if was_matched else "escalated",
                "template_id": template_id,
                "response_preview": response_text[:60],
                "image_url": image_url,
                "send_status": send_status
            })

    return jsonify({"status": "processed", "results": results})

def send_instagram_reply(recipient_id: str, text: str, image_url=None) -> Dict[str, Any]:
    images = [image_url] if isinstance(image_url, str) else list(image_url or [])

    if not config.WHATSAPP_ACCESS_TOKEN or not config.INSTAGRAM_PAGE_ID:
        safe_text = text[:60].encode("ascii", "backslashreplace").decode("ascii")
        msg_type = f"IMAGE x{len(images)} ({', '.join(images)})" if images else "TEXT"
        print(f"[MOCK IG OUTBOUND - FLASK] [{msg_type}] To: {recipient_id} | Message: {safe_text}...")
        return {"mock": True, "status": "sent", "recipient": recipient_id, "type": msg_type}

    url = f"https://graph.facebook.com/v19.0/me/messages"
    headers = {
        "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = None
            if text:
                resp = client.post(url, headers=headers, json={"recipient": {"id": recipient_id}, "message": {"text": text}})
                resp.raise_for_status()
            for img in images:
                resp = client.post(url, headers=headers, json={
                    "recipient": {"id": recipient_id},
                    "message": {"attachment": {"type": "image", "payload": {"url": img, "is_reusable": True}}}
                })
                resp.raise_for_status()
            return resp.json() if resp is not None else {"status": "no_content"}
    except Exception as e:
        print(f"Failed to send IG message: {e}")
        return {"error": str(e), "status": "failed"}

# --------------------------------------------------------------------------
# WhatsApp Webhook Endpoints
# --------------------------------------------------------------------------
@app.route("/webhook/whatsapp", methods=["GET"])
def verify_whatsapp_webhook():
    hub_mode = request.args.get("hub.mode")
    hub_verify_token = request.args.get("hub.verify_token")
    hub_challenge = request.args.get("hub.challenge")

    if hub_mode == "subscribe" and hub_verify_token == config.META_VERIFY_TOKEN:
        print("[WHATSAPP WEBHOOK - FLASK] Verification successful.")
        return hub_challenge or "", 200, {"Content-Type": "text/plain"}
    
    print("[WHATSAPP WEBHOOK - FLASK] Verification failed. Invalid token.")
    return "Verification token mismatch", 403, {"Content-Type": "text/plain"}

@app.route("/webhook/whatsapp", methods=["POST"])
def handle_whatsapp_webhook():
    payload = request.get_json(force=True, silent=True) or {}
    parsed = whatsapp_adapter.parse_webhook_message(payload)
    if not parsed:
        return jsonify({"status": "no_message_event"})

    sender_phone, msg_id, message_text = parsed
    result = whatsapp_adapter.process_incoming_message(sender_phone, message_text)
    return jsonify(result)

# --------------------------------------------------------------------------
# Admin Endpoints
# --------------------------------------------------------------------------
@app.route("/admin/escalations", methods=["GET"])
def get_escalations_list():
    limit = int(request.args.get("limit", 50))
    status_filter = request.args.get("status")
    escalations = db.get_escalations(limit, status_filter)
    return jsonify({"count": len(escalations), "escalations": escalations})

@app.route("/admin/analytics", methods=["GET"])
def get_analytics():
    return jsonify(db.get_analytics())

@app.route("/admin/sync-menu", methods=["POST"])
def sync_menu():
    data = request.get_json(force=True, silent=True) or {}
    csv_url = data.get("csv_url")
    if csv_url:
        result = sheet_sync.sync_from_csv_url(csv_url)
    else:
        result = sheet_sync.sync_local_reload()
    return jsonify(result)

# --------------------------------------------------------------------------
# Web Chat Tester (/chat)
# --------------------------------------------------------------------------
@app.route("/api/test-chat", methods=["POST"])
def api_test_chat():
    payload = request.get_json(force=True, silent=True) or {}
    user_message = payload.get("message", "")
    channel = payload.get("channel", "instagram_ad")
    user_id = payload.get("user_id", "web_tester")

    if channel == "instagram_organic":
        return jsonify({
            "status": "blocked",
            "is_ad_qualified": False,
            "response": "🛑 [AD-GATE BLOCKED]: Organic DM ignored.",
            "was_matched": False,
            "image_url": None,
            "active_memory": session_memory.get_session(user_id, "instagram")["facts"]
        })

    ch_name = "instagram" if channel == "instagram_ad" else "whatsapp"
    response_text, was_matched, template_id, image_url = responder.match_and_respond(user_message, user_id=user_id, channel=ch_name)
    db.log_conversation(user_id, ch_name, user_message, response_text, was_matched, template_id)

    if not was_matched:
        escalator.handle_escalation(user_id, ch_name, user_message, "Web Tester Unmatched", {})

    active_facts = session_memory.get_session(user_id, ch_name)["facts"]

    return jsonify({
        "status": "processed",
        "is_ad_qualified": True,
        "response": response_text,
        "was_matched": was_matched,
        "template_id": template_id,
        "image_url": image_url,
        "active_memory": active_facts
    })

@app.route("/api/reset-session", methods=["POST"])
def api_reset_session():
    payload = request.get_json(force=True, silent=True) or {}
    user_id = payload.get("user_id", "web_tester")
    channel = payload.get("channel", "instagram_ad")
    ch_name = "whatsapp" if channel == "whatsapp" else "instagram"
    session_memory.clear_session(user_id, ch_name)
    return jsonify({"status": "cleared", "user_id": user_id, "channel": ch_name})

@app.route("/chat", methods=["GET"])
@app.route("/demo", methods=["GET"])
@app.route("/test", methods=["GET"])
def get_web_chat():
    with open("app.py", "r", encoding="utf-8") as f:
        content = f.read()
    # Extract HTML block from app.py
    start = content.find('html_content = """') + len('html_content = """')
    end = content.find('"""\n    return HTMLResponse(content=html_content)')
    html_code = content[start:end]
    return render_template_string(html_code)

if __name__ == "__main__":
    print("🌸 Starting Camellia Artisanal Bot Server via Flask on port 5000...")
    app.run(host="0.0.0.0", port=5000, debug=True)
