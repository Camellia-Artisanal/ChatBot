import json
import httpx
from typing import Dict, Any, Optional
from config import config
from database import db

class Escalator:
    """
    Escalation & Human Handoff System.
    
    Per PRD Requirement #4b & Phase 3:
    - Answer only 'in syllabus' questions; else forward to human.
    - Escalation path is mandatory, not optional.
    - Logs escalation details in database for full client visibility.
    - Sends automated alert message to owner/human contact.
    """

    def __init__(self):
        self.human_phone = config.HUMAN_ESCALATION_PHONE
        self.human_email = config.HUMAN_ESCALATION_EMAIL

    def handle_escalation(
        self,
        user_id: str,
        channel: str,
        user_message: str,
        reason: str,
        context_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Records escalation in database and sends notification to human contact.
        """
        context = context_data or {}
        context["ad_qualified"] = db.is_ad_qualified(user_id, channel)
        
        # 1. Record escalation record in database
        escalation_id = db.record_escalation(
            user_id=user_id,
            channel=channel,
            user_message=user_message,
            reason=reason,
            context_data=context
        )

        # 2. Dispatch human alert notification
        alert_result = self.send_human_alert(escalation_id, user_id, channel, user_message, reason, context)

        return {
            "escalation_id": escalation_id,
            "status": "escalated",
            "alert_sent": alert_result.get("status") == "sent" or alert_result.get("mock") is True
        }

    def send_human_alert(
        self,
        escalation_id: int,
        user_id: str,
        channel: str,
        user_message: str,
        reason: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Sends WhatsApp/API alert notification to human team contact."""
        alert_body = (
            f"🚨 *NEW CAMELLIA BOT ESCALATION #{escalation_id}*\n\n"
            f"• *Channel:* {channel.upper()}\n"
            f"• *Customer ID/Phone:* {user_id}\n"
            f"• *Reason:* {reason}\n"
            f"• *Customer Message:* \"{user_message}\"\n\n"
            f"Please respond directly to customer on {channel.capitalize()}."
        )

        # In production, uses Meta WhatsApp API to alert the human phone number
        if config.WHATSAPP_ACCESS_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID and self.human_phone:
            url = f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
            headers = {
                "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": self.human_phone,
                "type": "text",
                "text": {"body": alert_body}
            }
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    return {"status": "sent", "api_response": resp.json()}
            except Exception as e:
                print(f"[ESCALATOR ALERT ERROR] Failed to send alert: {e}")
                return {"status": "failed", "error": str(e)}
        else:
            # Development / Mock notification
            safe_alert = alert_body.encode("ascii", "backslashreplace").decode("ascii")
            print(f"\n[HUMAN ESCALATION ALERT LOG]\n{safe_alert}\n")
            return {"mock": True, "status": "sent", "recipient": self.human_phone}

escalator = Escalator()
