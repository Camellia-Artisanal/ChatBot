import json
import re
from typing import Dict, Any, Tuple, Optional
from config import config

class DeterministicResponder:
    """
    Deterministic Keyword & Q&A Match Engine.
    
    Per PRD Requirement #5 & #2 (Point 5 & 4b):
    - Instant, predefined, relevant answers ONLY.
    - Zero LLM generation / hallucination.
    - Uses menu.json + qa_templates.json as single source of truth.
    - Escalates unrecognized or out-of-syllabus queries.
    """

    def __init__(self, menu_path: str = None, qa_path: str = None):
        self.menu_path = menu_path or config.MENU_JSON_PATH
        self.qa_path = qa_path or config.QA_TEMPLATES_PATH
        self.menu_data = self._load_json(self.menu_path)
        self.qa_data = self._load_json(self.qa_path)

    def _load_json(self, path: str) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {path}: {e}")
            return {}

    def reload_data(self):
        """Hot-reload JSON data files (e.g. after Google Sheet sync)."""
        self.menu_data = self._load_json(self.menu_path)
        self.qa_data = self._load_json(self.qa_path)

    def check_explicit_escalation_triggers(self, text: str) -> Optional[str]:
        """Check if message matches sensitive keywords that MUST escalate to human."""
        lowered = text.lower()
        
        sensitive_keywords = [
            "refund", "complaint", "allergy", "allergic", "custom order",
            "bulk order", "discount", "negotiate", "pay link", "gpay", "upi",
            "bank transfer", "poison", "hair in food", "rotten"
        ]
        for kw in sensitive_keywords:
            if kw in lowered:
                return f"Sensitive keyword matched: '{kw}'"
        return None

    def match_and_respond(self, user_message: str) -> Tuple[str, bool, Optional[str], Optional[str]]:
        """
        Matches incoming message against qa_templates.json & menu.json.
        
        Returns:
            (response_text: str, was_matched: bool, template_id: Optional[str], image_url: Optional[str])
        """
        if not user_message or not user_message.strip():
            fallback = self.qa_data.get("escalation_triggers", {}).get(
                "fallback_response", "Thanks for reaching out! Let me connect you with our team."
            )
            return fallback, False, None, None

        # Check explicit escalation triggers first
        escalation_reason = self.check_explicit_escalation_triggers(user_message)
        if escalation_reason:
            fallback = self.qa_data.get("escalation_triggers", {}).get(
                "fallback_response", "Thanks for reaching out! Let me connect you with our team — they'll get back to you shortly. 🙏"
            )
            return fallback, False, "explicit_escalation", None

        lowered_text = user_message.lower().strip()
        user_words = set(re.findall(r'\b\w+\b', lowered_text))
        
        # Stop words that should NOT count towards generic partial matches
        STOP_WORDS = {"do", "you", "is", "are", "a", "an", "the", "to", "for", "of", "in", "on", "have", "with", "and", "or", "we", "can", "your", "my", "this", "what", "me", "show", "tell", "it"}
        
        templates = self.qa_data.get("templates", [])
        
        best_template = None
        best_score = 0

        for tmpl in templates:
            tmpl_id = tmpl.get("id")
            triggers = tmpl.get("triggers", [])
            score = 0
            
            for trigger in triggers:
                trigger_lower = trigger.lower().strip()
                trigger_words = set(re.findall(r'\b\w+\b', trigger_lower))
                
                # Check 1: Exact full word boundary regex match (e.g. \bsondesh price\b or \bhi\b)
                pattern = r'\b' + re.escape(trigger_lower) + r'\b'
                if re.search(pattern, lowered_text):
                    score += len(trigger_words) * 5 + 5
                else:
                    # Check 2: Substantive (non-stopword) token intersection
                    substantive_trigger_words = trigger_words - STOP_WORDS
                    substantive_user_words = user_words - STOP_WORDS
                    matching_substantive = substantive_trigger_words.intersection(substantive_user_words)
                    
                    if matching_substantive:
                        score += len(matching_substantive) * 4

            # Give non-greeting templates priority over generic greeting if substantive product keywords are present
            if tmpl_id != "greeting" and score > 0:
                score += 2

            if score > best_score:
                best_score = score
                best_template = tmpl

        # Require a minimum matching threshold (score >= 4)
        if best_template and best_score >= 4:
            tmpl_id = best_template.get("id")
            response_text, image_url = self._format_dynamic_response(tmpl_id, best_template.get("response", ""))
            return response_text, True, tmpl_id, image_url

        # No match found -> Escalate
        fallback = self.qa_data.get("escalation_triggers", {}).get(
            "fallback_response", "Thanks for reaching out! Let me connect you with our team for this — they'll get back to you shortly. 🙏"
        )
        return fallback, False, None, None

    def _format_dynamic_response(self, template_id: str, default_response: str) -> Tuple[str, Optional[str]]:
        """Dynamically populates responses for weekly menus and attaches exact photo cards."""
        weekly_menus = self.menu_data.get("weekly_menus", [])
        if not weekly_menus:
            return default_response, None

        current_week = weekly_menus[-1]
        week_of = current_week.get("week_of", "")
        
        # Exact image cards from project directory
        img_till_thursday = "/images/Till%20Thursday.jpeg"
        img_from_friday = "/images/From%20Friday.jpeg"
        img_signature_sondesh = "/images/Signature%20Sondesh.jpeg"
        img_aamar_mishti = "/images/AAMAR%20MISHTI.jpeg"

        # Sondesh photo card queries
        if template_id == "sondesh_price_general":
            caption = "🌸 *Camellia Artisanal Sondesh Collection*\n\nHere is our Sondesh menu card! Delivery across Delhi NCR. Reply to place an order or customize! 📦"
            return caption, img_signature_sondesh

        # Weekly menu & bento photo card queries
        if template_id in ["weekly_menu_veg", "weekly_menu_nonveg", "combo_bento"]:
            import datetime
            today_weekday = datetime.datetime.now().weekday() # 0=Mon, 4=Fri, 5=Sat, 6=Sun
            
            # Select menu photo card: From Friday vs Till Thursday
            if today_weekday in [4, 5, 6]:
                selected_img = img_from_friday
                menu_title = "From Friday (Weekend) Menu Card"
            else:
                selected_img = img_till_thursday
                menu_title = "Till Thursday (Weekday) Menu Card"

            caption = f"🌸 *Camellia Artisanal — {menu_title}*\n\nHere is our fresh menu card! Delivery across Delhi NCR. Reply to place an order! 📦"
            return caption, selected_img

        return default_response, None

responder = DeterministicResponder()
