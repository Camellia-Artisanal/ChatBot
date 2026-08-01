import httpx
from typing import Dict, Any, Tuple, Optional
from config import config
from responder import responder
from database import db
from escalator import escalator

class WhatsAppAdapter:
    """
    Adapter for Meta WhatsApp Business Cloud API.
    
    Per PRD Requirement #2:
    - Same bot logic/session reachable via WhatsApp Business.
    - Deterministic responses and escalation on WhatsApp.
    """

    def __init__(self):
        self.access_token = config.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = config.WHATSAPP_PHONE_NUMBER_ID
        self.api_version = config.WHATSAPP_API_VERSION
        self.api_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"

    def parse_webhook_message(self, payload: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
        """
        Parses Meta WhatsApp Business API webhook payload.
        
        Returns:
            (sender_phone: str, message_id: str, message_text: str) or None
        """
        try:
            entry = payload.get("entry", [])[0]
            changes = entry.get("changes", [])[0]
            value = changes.get("value", {})
            messages = value.get("messages", [])
            if not messages:
                return None
            
            msg = messages[0]
            sender_phone = msg.get("from")
            msg_id = msg.get("id")
            
            msg_text = ""
            if msg.get("type") == "text":
                msg_text = msg.get("text", {}).get("body", "")
            elif msg.get("type") == "interactive":
                interactive = msg.get("interactive", {})
                msg_text = (
                    interactive.get("button_reply", {}).get("title") or
                    interactive.get("list_reply", {}).get("title") or ""
                )
                
            return sender_phone, msg_id, msg_text
        except Exception as e:
            print(f"Error parsing WhatsApp webhook: {e}")
            return None

    def process_incoming_message(self, sender_phone: str, message_text: str) -> Dict[str, Any]:
        """Processes an incoming WhatsApp message through responder & escalator."""
        # Match response
        response_text, was_matched, template_id, image_url = responder.match_and_respond(message_text)
        
        # Log to DB
        db.log_conversation(sender_phone, "whatsapp", message_text, response_text, was_matched, template_id)
        
        # Handle escalation if unmatched
        if not was_matched:
            escalator.handle_escalation(
                user_id=sender_phone,
                channel="whatsapp",
                user_message=message_text,
                reason="Unmatched WhatsApp Query / Sensitive Request",
                context_data={"phone": sender_phone}
            )

        # Send response via WhatsApp API (or mock if no token configured)
        send_result = self.send_message(sender_phone, response_text, image_url=image_url)
        
        return {
            "status": "processed",
            "sender_phone": sender_phone,
            "was_matched": was_matched,
            "template_id": template_id,
            "response_text": response_text,
            "image_url": image_url,
            "send_result": send_result
        }

    def send_message(self, recipient_phone: str, text_body: str, image_url: Optional[str] = None) -> Dict[str, Any]:
        """Sends a text or image message using Meta WhatsApp Cloud API."""
        if not self.access_token or not self.phone_number_id:
            # Development / Mock Mode
            safe_body = text_body[:60].encode("ascii", "backslashreplace").decode("ascii")
            msg_type = f"IMAGE ({image_url})" if image_url else "TEXT"
            print(f"[MOCK WHATSAPP OUTBOUND] [{msg_type}] To: {recipient_phone} | Body: {safe_body}...")
            return {"mock": True, "status": "sent", "recipient": recipient_phone, "type": msg_type}

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        if image_url:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient_phone,
                "type": "image",
                "image": {"link": image_url, "caption": text_body}
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient_phone,
                "type": "text",
                "text": {"preview_url": False, "body": text_body}
            }

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(self.api_url, headers=headers, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            print(f"Failed to send WhatsApp message to {recipient_phone}: {e}")
            return {"error": str(e), "status": "failed"}

whatsapp_adapter = WhatsAppAdapter()
