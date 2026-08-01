from typing import Dict, Any, Tuple, Optional
from database import db

class AdGate:
    """Instagram Ad-Referral Hard Gate Processor.
    
    Per PRD Requirement #4:
    - Auto-replies MUST only engage for ad-originated inquiries (referral.source == "ADS").
    - Non-ad organic DMs are strictly IGNORED by the bot (left for manual human handling).
    """

    @staticmethod
    def inspect_and_gate_message(messaging_event: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """
        Inspects an incoming Instagram messaging event.
        
        Returns:
            (is_ad_qualified: bool, sender_id: str, message_text: str, ad_id: Optional[str])
        """
        sender_id = messaging_event.get("sender", {}).get("id")
        if not sender_id:
            return False, None, None, None

        # Extract text if present
        message_obj = messaging_event.get("message", {})
        postback_obj = messaging_event.get("postback", {})
        message_text = message_obj.get("text") or postback_obj.get("title") or ""

        # Extract referral from message level or postback level
        referral = message_obj.get("referral") or postback_obj.get("referral") or messaging_event.get("referral")
        
        ad_id = None
        is_direct_ad_referral = False

        if referral and isinstance(referral, dict):
            source = str(referral.get("source", "")).upper()
            if source == "ADS":
                is_direct_ad_referral = True
                ad_id = referral.get("ad_id")

        # Check existing session status in database
        already_qualified = db.is_ad_qualified(sender_id, "instagram")

        if is_direct_ad_referral:
            # Tag session as ad_qualified in DB
            db.set_ad_qualified(sender_id, "instagram", is_ad_qualified=True, ad_id=ad_id)
            return True, sender_id, message_text, ad_id
        elif already_qualified:
            # User was previously qualified via Ad in this session
            return True, sender_id, message_text, None
        else:
            # Organic non-ad message: Do NOT auto-reply
            return False, sender_id, message_text, None

ad_gate = AdGate()
