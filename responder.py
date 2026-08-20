import difflib
import json
import re
from dataclasses import dataclass
from typing import Dict, Any, Tuple, Optional, List
from config import config
from monitoring import get_logger
from gemini_responder import gemini_responder
from session_memory import session_memory

from order_calculator import order_calculator

log = get_logger("camellia.responder")

@dataclass
class BotResponse:
    text: str
    was_matched: bool = True
    template_id: Optional[str] = None
    image_url: Optional[Any] = None

    def __str__(self):
        return self.text

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

    def reload_templates(self):
        """Alias for reload_data."""
        self.reload_data()

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

        # 1. Update & Extract Session Memory Facts (Name, Order Draft)
        user_id = user_id or "default_user"
        channel = channel or "default_channel"
        facts = session_memory.extract_and_update_facts(user_id, channel, user_message)

        # Check if user just provided their name
        if facts.get("just_introduced_name"):
            facts["just_introduced_name"] = False
            user_name = facts.get("name")
            if user_name:
                response_text = f"Nice to meet you, {user_name}! 👋 Welcome to Camellia Artisanal. Ask us about our Sondesh flavours & prices, weekly menu, or delivery — happy to help!"
            else:
                response_text = "Nice to meet you! 👋 Welcome to Camellia Artisanal. Ask us about our Sondesh flavours & prices, weekly menu, or delivery — happy to help!"
            session_memory.add_turn(user_id, channel, user_message, response_text)
            return response_text, True, "name_introduction", None

        # 2. Check explicit escalation triggers first (refund, complaint, etc.)
        escalation_reason = self.check_explicit_escalation_triggers(user_message)
        if escalation_reason:
            fallback = self.qa_data.get("escalation_triggers", {}).get(
                "fallback_response", "Thanks for reaching out! Let me connect you with our team — they'll get back to you shortly. 🙏"
            )
            session_memory.add_turn(user_id, channel, user_message, fallback)
            return fallback, False, "explicit_escalation", None

        lowered_text = user_message.lower().strip()

        # 2.5 Check if accumulated order_draft slots form a complete order receipt
        order_draft = facts.get("order_draft", {})
        existing_cart = facts.get("cart", [])
        is_general_menu_query = any(phrase in lowered_text for phrase in [
            "full menu", "send menu", "price list", "show menu", "what is your menu",
            "how much is sondesh", "sondesh price", "how much sondesh", "sondesh cost", "cost of sondesh", "price of sondesh",
            "tell me about your product", "about your product", "about your products", "what products", "your products", "product", "products",
            "how can you assist", "how can you help", "what can you do", "help me", "assist me"
        ])
        if not is_general_menu_query:
            calc_result = order_calculator.parse_and_calculate(user_message, existing_draft=order_draft, existing_cart=existing_cart)
            if calc_result and calc_result.get("formatted_receipt"):
                if calc_result.get("items"):
                    facts["cart"] = calc_result["items"]
                response_text = calc_result["formatted_receipt"]
                session_memory.add_turn(user_id, channel, user_message, response_text)
                return response_text, True, "order_draft_receipt", None

        user_words = set(re.findall(r'\b\w+\b', lowered_text))
        
        STOP_WORDS = {
            "i", "am", "im", "my", "is", "are", "do", "you", "u", "r", "a", "an", "the", "to", "for",
            "of", "in", "on", "have", "has", "with", "and", "or", "we", "can", "your", "this",
            "what", "how", "when", "where", "why", "who", "which", "many", "much", "me",
            "show", "tell", "it", "more", "want", "send", "share", "about", "some", "any",
            "please", "pls", "give", "know", "let", "sell", "use"
        }
        
        # Check Self-Healing Learned Knowledge Base (Saved from Staff Escalation Resolutions)
        try:
            from database import db
            learned_items = db.get_learned_knowledge(limit=100)
            for item in learned_items:
                pattern = item.get("question_pattern", "").lower().strip()
                ans = item.get("verified_answer", "").strip()
                if not pattern or not ans:
                    continue
                p_words = set(re.findall(r'\b\w+\b', pattern))
                sub_p_words = p_words - STOP_WORDS
                sub_u_words = user_words - STOP_WORDS
                if pattern in lowered_text or (sub_p_words and sub_p_words.issubset(sub_u_words)):
                    session_memory.add_turn(user_id, channel, user_message, ans)
                    return ans, True, f"learned_kb_{item.get('id')}", None
        except Exception as e:
            log.warning("Learned knowledge check error: %s", e)

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
                        # Check 3: Fuzzy word similarity match (Tiered: len >= 3 allowed with stricter threshold)
                        for u_word in substantive_user_words:
                            if len(u_word) < 3:
                                continue
                            for t_word in substantive_trigger_words:
                                if len(t_word) < 3:
                                    continue
                                ratio = difflib.SequenceMatcher(None, u_word, t_word).ratio()
                                # 3-letter words require stricter similarity (>= 0.83), 4+ letter words use 0.80
                                threshold = 0.83 if min(len(u_word), len(t_word)) == 3 else 0.80
                                if ratio >= threshold:
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
            
            # Dynamic Name Personalization & Order Draft reset on order confirmation
            if tmpl_id == "name_introduction":
                facts["awaiting_name"] = False
                if facts.get("name"):
                    response_text = f"Nice to meet you, {facts['name']}! 👋 Welcome to Camellia Artisanal. Ask us about our Sondesh flavours & prices, weekly menu, or delivery — happy to help!"
                else:
                    response_text = "Nice to meet you! 👋 Welcome to Camellia Artisanal. Ask us about our Sondesh flavours & prices, weekly menu, or delivery — happy to help!"
            elif tmpl_id == "recall_name":
                if facts.get("name"):
                    response_text = f"Yes! Your name is {facts['name']}. 😊 How can I assist you today with our Sondesh or weekly menu?"
                    facts["awaiting_name"] = False
                else:
                    response_text = "You haven't told me your name yet! What should I call you?"
                    facts["awaiting_name"] = True
            elif tmpl_id == "order_next_steps":
                session_memory.clear_order_draft(user_id, channel)
            elif config.is_ai_enabled:
                context_summary = session_memory.get_context_summary(user_id, channel)
                history_turns = session_memory.get_session(user_id, channel)["turns"]
                refined_text, used_ai = gemini_responder.refine_template_response(
                    template_id=tmpl_id,
                    template_text=response_text,
                    user_message=user_message,
                    user_context=context_summary,
                    history_turns=history_turns
                )
                if used_ai and refined_text:
                    response_text = refined_text

            session_memory.add_turn(user_id, channel, user_message, response_text)
            return response_text, True, tmpl_id, image_url

        # Fallback to Cloud AI RAG Engine (OpenAI / Gemini) if enabled
        if config.is_ai_enabled:
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

    def _detect_relevant_images(self, user_message: str) -> Optional[Any]:
        """Attaches relevant menu photo cards when user inquires about products or menus in AI-First mode."""
        lowered = user_message.lower().strip()
        img_till_thursday = "/images/Till%20Thursday.jpeg"
        img_from_friday = "/images/From%20Friday.jpeg"
        img_signature_sondesh = "/images/Signature%20Sondesh.jpeg"
        img_aamar_mishti = "/images/AAMAR%20MISHTI.jpeg"

        import datetime, zoneinfo
        try:
            ist = zoneinfo.ZoneInfo("Asia/Kolkata")
        except Exception:
            ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        today_weekday = datetime.datetime.now(tz=ist).weekday()
        current_weekly_img = img_from_friday if today_weekday in [4, 5, 6] else img_till_thursday

        if any(w in lowered for w in ["product", "all products", "what do you have", "what do you sell", "overview", "know more"]):
            return [img_aamar_mishti, img_signature_sondesh, current_weekly_img]
        elif any(w in lowered for w in ["sondesh", "mishti", "sweet", "sweets", "kanchagolla", "jolbhora", "nolen gur", "250g", "500g", "1kg"]):
            if "signature" in lowered or "mahua" in lowered:
                return [img_signature_sondesh]
            return [img_aamar_mishti, img_signature_sondesh]
        elif any(w in lowered for w in ["menu", "veg", "non veg", "non-veg", "bento", "combo", "meal", "meals", "lunch", "dinner"]):
            return current_weekly_img
        return None

    def _format_dynamic_response(
        self, template_id: str, default_response: str, user_id: str = None, channel: str = None, user_message: str = ""
    ) -> Tuple[str, Optional[Any]]:
        """Attaches the exact photo card(s) a template should send alongside its text whenever requested."""
        img_till_thursday = "/images/Till%20Thursday.jpeg"
        img_from_friday = "/images/From%20Friday.jpeg"
        img_signature_sondesh = "/images/Signature%20Sondesh.jpeg"
        img_aamar_mishti = "/images/AAMAR%20MISHTI.jpeg"

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

        # "I want to know more" & Product Overview -> both collection cards + this week's meal menu
        if template_id in ["know_more", "product_overview"]:
            target_images = [img_aamar_mishti, img_signature_sondesh, current_weekly_img]

        # Sondesh photo card queries
        elif template_id in [
            "sondesh_price_general", "nolen_gur_aamar", "aamar_mishti_collection",
            "sondesh_classic_rose", "sondesh_mango_kesar", "sondesh_nolen_gur",
            "jolbhora", "kanchagolla", "pack_size_250g", "pack_size_500g", "pack_size_1kg"
        ]:
            target_images = [img_aamar_mishti, img_signature_sondesh]

        elif template_id in ["signature_collection", "mahua_signature_special"]:
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
            return formatted_text, target_images

        return formatted_text, None

    def respond(self, user_message: str, user_id: str = None, channel: str = None) -> BotResponse:
        """Convenience wrapper returning a structured BotResponse object with .text attribute."""
        text, was_matched, tmpl_id, image_url = self.match_and_respond(user_message, user_id, channel)
        return BotResponse(text=text, was_matched=was_matched, template_id=tmpl_id, image_url=image_url)

responder = DeterministicResponder()
