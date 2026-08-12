import difflib
import json
import re
from typing import Dict, Any, Tuple, Optional
from config import config
from monitoring import get_logger
from gemini_responder import gemini_responder
from session_memory import session_memory

log = get_logger("camellia.responder")

class DeterministicResponder:
    """
    Hybrid Auto-Responder Engine for Camellia Artisanal.
    Combines 100% deterministic regex boundary matching, menu photo cards,
    conversational session memory, and Google Gemini Flash AI RAG fallback.
    """

    def __init__(self, qa_path: str = None):
        self.qa_path = qa_path or config.QA_TEMPLATES_PATH
        self.qa_data = self._load_json(self.qa_path)

    def _load_json(self, path: str) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error("Error loading %s: %s", path, e)
            return {}

    def reload_data(self):
        """Reload JSON files to support live menu updates."""
        self.qa_data = self._load_json(self.qa_path)
        gemini_responder.reload_data()

    def check_explicit_escalation_triggers(self, text: str) -> Optional[str]:
        """Checks if text contains explicit escalation keywords."""
        triggers = self.qa_data.get("escalation_triggers", {}).get("keywords", [])
        lowered = text.lower()
        for kw in triggers:
            if re.search(r'\b' + re.escape(kw.lower()) + r'\b', lowered):
                return f"Trigger keyword matched: '{kw}'"
        return None

    def match_and_respond(self, user_message: str, user_id: str = None, channel: str = None) -> Tuple[str, bool, Optional[str], Optional[Any]]:
        """
        Matches incoming message against qa_templates.json & menu.json.
        Updates session memory and falls back to Gemini Flash RAG.

        Returns:
            (response_text: str, was_matched: bool, template_id: Optional[str], image_url: str | List[str] | None)
        """
        if not user_message or not user_message.strip():
            fallback = self.qa_data.get("escalation_triggers", {}).get(
                "fallback_response", "Thanks for reaching out! Let me connect you with our team."
            )
            return fallback, False, None, None

        # 1. Update & Extract Session Memory Facts (Name)
        user_id = user_id or "default_user"
        channel = channel or "default_channel"
        facts = session_memory.extract_and_update_facts(user_id, channel, user_message)

        # 2. Check explicit escalation triggers first (refund, complaint, etc.)
        escalation_reason = self.check_explicit_escalation_triggers(user_message)
        if escalation_reason:
            fallback = self.qa_data.get("escalation_triggers", {}).get(
                "fallback_response", "Thanks for reaching out! Let me connect you with our team — they'll get back to you shortly. 🙏"
            )
            session_memory.add_turn(user_id, channel, user_message, fallback)
            return fallback, False, "explicit_escalation", None

        lowered_text = user_message.lower().strip()
        user_words = set(re.findall(r'\b\w+\b', lowered_text))
        
        STOP_WORDS = {"i", "am", "im", "my", "is", "are", "do", "you", "a", "an", "the", "to", "for", "of", "in", "on", "have", "has", "with", "and", "or", "we", "can", "your", "this", "what", "how", "when", "where", "why", "who", "which", "many", "much", "me", "show", "tell", "it", "more", "want", "send", "share"}
        
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
                
                # Check 1: Exact full word boundary regex match
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
                    else:
                        # Check 3: Fuzzy word similarity match
                        for u_word in substantive_user_words:
                            if len(u_word) < 4:
                                continue
                            for t_word in substantive_trigger_words:
                                if len(t_word) < 4:
                                    continue
                                ratio = difflib.SequenceMatcher(None, u_word, t_word).ratio()
                                if ratio >= 0.80:
                                    score += 3

            # Give non-greeting templates priority over generic greeting if substantive product keywords are present
            if tmpl_id != "greeting" and score > 0:
                score += 2

            if score > best_score:
                best_score = score
                best_template = tmpl

        # Require a minimum matching threshold (score >= 4) in hybrid/deterministic mode
        if best_template and best_score >= 4:
            tmpl_id = best_template.get("id")
            response_text, image_url = self._format_dynamic_response(
                tmpl_id, best_template.get("response", ""), user_id=user_id, channel=channel, user_message=user_message
            )
            
            # Dynamic Name Personalization
            if tmpl_id == "name_introduction":
                if facts.get("name"):
                    response_text = f"Nice to meet you, {facts['name']}! 👋 Welcome to Camellia Artisanal. Ask us about our Sondesh flavours & prices, weekly menu, or delivery — happy to help!"
                else:
                    response_text = "Nice to meet you! 👋 Welcome to Camellia Artisanal. Ask us about our Sondesh flavours & prices, weekly menu, or delivery — happy to help!"
            elif tmpl_id == "recall_name":
                if facts.get("name"):
                    response_text = f"Yes! Your name is {facts['name']}. 😊 How can I assist you today with our Sondesh or weekly menu?"
                else:
                    response_text = "You haven't told me your name yet! What should I call you?"

            session_memory.add_turn(user_id, channel, user_message, response_text)
            return response_text, True, tmpl_id, image_url

        # Fallback to Google Gemini Flash Cloud AI RAG Engine if enabled
        if config.GEMINI_ENABLED and config.GEMINI_API_KEY:
            context_summary = session_memory.get_context_summary(user_id, channel)
            history_turns = session_memory.get_session(user_id, channel)["turns"]

            gemini_response, is_grounded, reason = gemini_responder.generate_response(
                user_message,
                user_context=context_summary,
                history_turns=history_turns
            )
            if is_grounded and gemini_response:
                session_memory.add_turn(user_id, channel, user_message, gemini_response)
                return gemini_response, True, "gemini_rag", None
            elif reason == "out_of_context":
                return gemini_response, False, "gemini_out_of_context", None

        # No match found & AI RAG disabled/unreachable -> Escalate to human
        fallback = self.qa_data.get("escalation_triggers", {}).get(
            "fallback_response", "Thanks for reaching out! Let me connect you with our team for this — they'll get back to you shortly. 🙏"
        )
        session_memory.add_turn(user_id, channel, user_message, fallback)
        return fallback, False, None, None

    def _format_dynamic_response(
        self, template_id: str, default_response: str, user_id: str = None, channel: str = None, user_message: str = ""
    ) -> Tuple[str, Optional[Any]]:
        """Attaches the exact photo card(s) a template should send alongside its text, suppressing duplicates on follow-up questions."""
        img_till_thursday = "/images/Till%20Thursday.jpeg"
        img_from_friday = "/images/From%20Friday.jpeg"
        img_signature_sondesh = "/images/Signature%20Sondesh.jpeg"
        img_aamar_mishti = "/images/AAMAR%20MISHTI.jpeg"

        # Check session memory to see if photo cards were already delivered in this session
        session = session_memory.get_session(user_id, channel) if user_id and channel else None
        already_sent_cards = session["facts"].get("image_card_sent", False) if session else False

        # Allow photo re-delivery if customer explicitly asks for image/photo/card
        msg_lowered = user_message.lower() if user_message else ""
        explicit_image_req = any(kw in msg_lowered for kw in ["photo", "image", "picture", "card", "send image", "send photo"])

        import datetime, zoneinfo
        try:
            ist = zoneinfo.ZoneInfo("Asia/Kolkata")
        except Exception:
            # zoneinfo available Python 3.9+; fallback to fixed +5:30 offset
            ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        today_weekday = datetime.datetime.now(tz=ist).weekday()  # 0=Mon … 6=Sun
        current_weekly_img = img_from_friday if today_weekday in [4, 5, 6] else img_till_thursday

        target_images = None
        formatted_text = default_response

        # "I want to know more" -> both collection cards + this week's meal menu
        if template_id == "know_more":
            target_images = [img_aamar_mishti, img_signature_sondesh, current_weekly_img]

        # Sondesh photo card queries
        elif template_id in ["sondesh_price_general", "nolen_gur_aamar", "aamar_mishti_collection"]:
            target_images = [img_aamar_mishti, img_signature_sondesh]

        elif template_id == "signature_collection":
            target_images = [img_signature_sondesh]

        # Weekly menu & bento photo card queries
        elif template_id in ["weekly_menu_veg", "weekly_menu_nonveg", "combo_bento"]:
            target_images = current_weekly_img
            if today_weekday in [4, 5, 6]:
                menu_title = "From Friday (Weekend) Menu Card"
            else:
                menu_title = "Till Thursday (Weekday) Menu Card"
            formatted_text = f"🌸 *Camellia Artisanal — {menu_title}*\n\nHere is our fresh menu card! Delivery across Delhi NCR. Reply to place an order! 📦"

        if target_images:
            if already_sent_cards and not explicit_image_req:
                # Suppress repeat menu photo cards on follow-up questions for clean text-only pricing
                return formatted_text, None
            else:
                if session:
                    session["facts"]["image_card_sent"] = True
                return formatted_text, target_images

        return formatted_text, None

responder = DeterministicResponder()
