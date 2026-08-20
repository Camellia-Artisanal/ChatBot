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

    def __init__(self, qa_path: str = None, menu_path: str = None):
        self.qa_path = qa_path or config.QA_TEMPLATES_PATH
        self.menu_path = menu_path or config.MENU_JSON_PATH
        self.qa_data = self._load_json(self.qa_path)
        self.menu_data = self._load_json(self.menu_path)

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
        self.menu_data = self._load_json(self.menu_path)
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
            max_trigger_score = 0
            
            for trigger in triggers:
                trigger_lower = trigger.lower().strip()
                trigger_words = set(re.findall(r'\b\w+\b', trigger_lower))
                t_score = 0
                
                # Check 1: Exact full word boundary regex match
                pattern = r'\b' + re.escape(trigger_lower) + r'\b'
                if re.search(pattern, lowered_text):
                    t_score = len(trigger_words) * 10 + 10
                else:
                    # Check 2: Substantive (non-stopword) token intersection
                    substantive_trigger_words = trigger_words - STOP_WORDS
                    substantive_user_words = user_words - STOP_WORDS
                    matching_substantive = substantive_trigger_words.intersection(substantive_user_words)
                    
                    if matching_substantive:
                        coverage = len(matching_substantive) / max(len(substantive_trigger_words), 1)
                        t_score = len(matching_substantive) * 6 + int(coverage * 6)
                    else:
                        # Check 3: Fuzzy word similarity match (Tiered: len >= 3 allowed with stricter threshold)
                        fuzzy_matches = 0
                        matched_t_words = set()
                        for u_word in substantive_user_words:
                            if len(u_word) < 3:
                                continue
                            for t_word in substantive_trigger_words:
                                if len(t_word) < 3 or t_word in matched_t_words:
                                    continue
                                ratio = difflib.SequenceMatcher(None, u_word, t_word).ratio()
                                # 3-letter words require stricter similarity (>= 0.83), 4+ letter words use 0.80
                                threshold = 0.83 if min(len(u_word), len(t_word)) == 3 else 0.80
                                if ratio >= threshold:
                                    fuzzy_matches += 1
                                    matched_t_words.add(t_word)
                                    break
                        if fuzzy_matches > 0:
                            coverage = fuzzy_matches / max(len(substantive_trigger_words), 1)
                            t_score = fuzzy_matches * 5 + int(coverage * 5)

                if t_score > max_trigger_score:
                    max_trigger_score = t_score

            score = max_trigger_score
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

        if any(w in lowered for w in ["collection 1", "collection1", "1st collection", "first collection", "aamar mishti", "col 1", "option 1"]):
            return [img_aamar_mishti]
        elif any(w in lowered for w in ["collection 2", "collection2", "2nd collection", "second collection", "signature", "desi a2", "col 2", "option 2", "mahua"]):
            return [img_signature_sondesh]
        elif any(w in lowered for w in ["product", "all products", "what do you have", "what do you sell", "overview", "know more", "full menu", "both collections"]):
            return [img_aamar_mishti, img_signature_sondesh, current_weekly_img]
        elif any(w in lowered for w in ["sondesh", "mishti", "sweet", "sweets", "kanchagolla", "jolbhora", "nolen gur", "250g", "500g", "1kg", "rose", "classic", "mango", "kesar", "price"]):
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
        lowered_msg = (user_message or "").lower().strip()

        # "I want to know more" & Product Overview -> both collection cards + this week's meal menu
        if template_id in ["know_more", "product_overview"]:
            target_images = [img_aamar_mishti, img_signature_sondesh, current_weekly_img]

        # Collection 1 specific queries — send ONLY Collection 1 photo card
        elif template_id in ["aamar_mishti_collection", "nolen_gur_aamar"]:
            target_images = [img_aamar_mishti]

        # Collection 2 specific queries — send ONLY Collection 2 photo card
        elif template_id in ["signature_collection", "mahua_signature_special"]:
            target_images = [img_signature_sondesh]

        # Classic & Rose Sondesh — dedicated formatting based on query specificity
        elif template_id == "sondesh_classic_rose":
            has_rose = "rose" in lowered_msg
            has_classic = "classic" in lowered_msg
            has_col1 = any(w in lowered_msg for w in ["collection 1", "collection1", "aamar mishti", "1st collection", "first collection", "col 1", "option 1"])
            has_col2 = any(w in lowered_msg for w in ["collection 2", "collection2", "signature", "desi a2", "2nd collection", "second collection", "col 2", "option 2"])

            if has_col1 and not has_col2:
                target_images = [img_aamar_mishti]
                if has_rose and not has_classic:
                    formatted_text = "*Collection 1 — Aamar Mishti (Rose Sondesh)* 🌸\nFresh artisanal chhana infused with fragrant organic rose petals, made with pure dairy milk in our kitchen:\n\n```\n+-----------------------+-------+-------+-------+\n| Pack Size             | 250g  | 500g  |  1kg  |\n+-----------------------+-------+-------+-------+\n| Piece Count           | 9 pcs | 18 pcs| 36 pcs|\n| Price (INR)           | ₹450  | ₹800  | ₹1500 |\n+-----------------------+-------+-------+-------+\n```\n\nWhich pack size (250g, 500g, or 1kg) would you like to order?"
                elif has_classic and not has_rose:
                    formatted_text = "*Collection 1 — Aamar Mishti (Classic Sondesh)* 🌸\nFresh artisanal chhana and delicate aromatic cardamom, made with pure dairy milk in our kitchen:\n\n```\n+-----------------------+-------+-------+-------+\n| Pack Size             | 250g  | 500g  |  1kg  |\n+-----------------------+-------+-------+-------+\n| Piece Count           | 9 pcs | 18 pcs| 36 pcs|\n| Price (INR)           | ₹450  | ₹800  | ₹1500 |\n+-----------------------+-------+-------+-------+\n```\n\nWhich pack size (250g, 500g, or 1kg) would you like to order?"
                else:
                    formatted_text = "*Collection 1 — Aamar Mishti (Classic & Rose Sondesh)* 🌸\nMade with pure dairy milk in our kitchen:\n\n```\n+-----------------------+-------+-------+-------+\n| Item / Flavour        | 250g  | 500g  |  1kg  |\n+-----------------------+-------+-------+-------+\n| Classic / Rose        | ₹450  | ₹800  | ₹1500 |\n+-----------------------+-------+-------+-------+\n* Box count: 9 pcs (250g) | 18 pcs (500g) | 36 pcs (1kg)\n```\n\nWhich flavour and pack size would you like to order?"

            elif has_col2 and not has_col1:
                target_images = [img_signature_sondesh]
                if has_rose and not has_classic:
                    formatted_text = "*Collection 2 — Signature A2 (Rose Sondesh)* 🌸\n100% handcrafted with pure desi A2 cow milk and fragrant organic rose petals:\n\n```\n+-----------------------+-------+-------+-------+\n| Pack Size             | 250g  | 500g  |  1kg  |\n+-----------------------+-------+-------+-------+\n| Piece Count           | 9 pcs | 18 pcs| 36 pcs|\n| Price (INR)           | ₹600  | ₹1100 | ₹2000 |\n+-----------------------+-------+-------+-------+\n```\n\nWhich pack size (250g, 500g, or 1kg) would you like to order?"
                elif has_classic and not has_rose:
                    formatted_text = "*Collection 2 — Signature A2 (Classic Sondesh)* 🌸\n100% handcrafted with pure desi A2 cow milk and delicate aromatic cardamom:\n\n```\n+-----------------------+-------+-------+-------+\n| Pack Size             | 250g  | 500g  |  1kg  |\n+-----------------------+-------+-------+-------+\n| Piece Count           | 9 pcs | 18 pcs| 36 pcs|\n| Price (INR)           | ₹600  | ₹1100 | ₹2000 |\n+-----------------------+-------+-------+-------+\n```\n\nWhich pack size (250g, 500g, or 1kg) would you like to order?"
                else:
                    formatted_text = "*Collection 2 — Signature A2 (Classic & Rose Sondesh)* 🌸\n100% handcrafted with pure desi A2 cow milk:\n\n```\n+-----------------------+-------+-------+-------+\n| Item / Flavour        | 250g  | 500g  |  1kg  |\n+-----------------------+-------+-------+-------+\n| Classic / Rose        | ₹600  | ₹1100 | ₹2000 |\n+-----------------------+-------+-------+-------+\n* Box count: 9 pcs (250g) | 18 pcs (500g) | 36 pcs (1kg)\n```\n\nWhich flavour and pack size would you like to order?"

            elif has_rose and not has_classic:
                target_images = [img_aamar_mishti, img_signature_sondesh]
                formatted_text = "Our handcrafted *Rose Sondesh* 🌸 is made with fresh artisanal chhana infused with fragrant organic rose petals. Available in both collections:\n\n*Rose Sondesh Price Breakdown:*\n```\n+-------------------------------------+-------+-------+-------+\n| Collection                          | 250g  | 500g  |  1kg  |\n+-------------------------------------+-------+-------+-------+\n| Collection 1 — Aamar Mishti (Dairy) | ₹450  | ₹800  | ₹1500 |\n| Collection 2 — Signature (Desi A2)  | ₹600  | ₹1100 | ₹2000 |\n+-------------------------------------+-------+-------+-------+\n* Box count: 9 pcs (250g) | 18 pcs (500g) | 36 pcs (1kg)\n```\n\n• *Collection 1 (Aamar Mishti)*: Daily indulgence made with pure dairy milk in our kitchen.\n• *Collection 2 (Signature A2)*: 100% hand-kneaded with unadulterated desi A2 cow milk — exceptionally rich & melt-in-the-mouth.\n\nWhich collection and pack size would you like to order (250g, 500g, or 1kg)?"

            elif has_classic and not has_rose:
                target_images = [img_aamar_mishti, img_signature_sondesh]
                formatted_text = "Our traditional *Classic Sondesh* 🌸 is made with fresh artisanal chhana and delicate aromatic cardamom. Available in both collections:\n\n*Classic Sondesh Price Breakdown:*\n```\n+-------------------------------------+-------+-------+-------+\n| Collection                          | 250g  | 500g  |  1kg  |\n+-------------------------------------+-------+-------+-------+\n| Collection 1 — Aamar Mishti (Dairy) | ₹450  | ₹800  | ₹1500 |\n| Collection 2 — Signature (Desi A2)  | ₹600  | ₹1100 | ₹2000 |\n+-------------------------------------+-------+-------+-------+\n* Box count: 9 pcs (250g) | 18 pcs (500g) | 36 pcs (1kg)\n```\n\n• *Collection 1 (Aamar Mishti)*: Daily indulgence made with pure dairy milk in our kitchen.\n• *Collection 2 (Signature A2)*: 100% hand-kneaded with unadulterated desi A2 cow milk — exceptionally rich & melt-in-the-mouth.\n\nWhich collection and pack size would you like to order (250g, 500g, or 1kg)?"

            else:
                target_images = [img_aamar_mishti, img_signature_sondesh]

        # Other Sondesh price / flavour queries
        elif template_id in [
            "sondesh_price_general",
            "sondesh_mango_kesar", "sondesh_nolen_gur",
            "jolbhora", "kanchagolla", "pack_size_250g", "pack_size_500g", "pack_size_1kg"
        ]:
            if any(w in lowered_msg for w in ["collection 1", "collection1", "aamar mishti", "1st collection", "first collection", "col 1", "option 1"]):
                target_images = [img_aamar_mishti]
            elif any(w in lowered_msg for w in ["collection 2", "collection2", "signature", "desi a2", "2nd collection", "second collection", "col 2", "option 2"]):
                target_images = [img_signature_sondesh]
            else:
                target_images = [img_aamar_mishti, img_signature_sondesh]

        # Weekly menu & bento photo card queries
        elif template_id in ["weekly_menu_veg", "weekly_menu_nonveg", "combo_bento"]:
            target_images = current_weekly_img
            weekly_menus = self.menu_data.get("weekly_menus", [])
            # Select appropriate rotation based on day of week if multiple rotations exist
            if today_weekday in [4, 5, 6] and len(weekly_menus) > 1:
                current_week = weekly_menus[-1]
            elif weekly_menus:
                current_week = weekly_menus[0]
            else:
                current_week = {}

            week_name = current_week.get("week_of", "Current Rotation")
            service_window = current_week.get("service_window", "Friday Lunch – Thursday Dinner")

            if template_id == "weekly_menu_veg":
                veg_list = current_week.get("vegetarian", [])
                if veg_list:
                    lines = [
                        f"🌸 *Camellia Artisanal — This Week's Vegetarian Menu* 🌿",
                        f"_{week_name} ({service_window})_\n"
                    ]
                    for itm in veg_list:
                        name = itm.get("item", "")
                        desc = itm.get("description", "")
                        qty = itm.get("quantity", "")
                        price = itm.get("price_inr", "")
                        desc_str = f" • _{desc}_" if desc else ""
                        qty_str = f" ({qty})" if qty else ""
                        lines.append(f"• *{name}*{qty_str} — *₹{price}*{desc_str}")
                    
                    lines.append("\n📦 *Packaging*: 100% plastic-free, eco-friendly cornstarch boxes.")
                    lines.append("🚚 *Delivery*: Across Delhi NCR from Sushant Arcade, Gurgaon (Courier on actuals).")
                    lines.append("\nWhich vegetarian dishes would you like to order? 🙏")
                    formatted_text = "\n".join(lines)
                else:
                    formatted_text = default_response

            elif template_id == "weekly_menu_nonveg":
                nonveg_list = current_week.get("non_vegetarian", [])
                if nonveg_list:
                    lines = [
                        f"🌸 *Camellia Artisanal — This Week's Non-Vegetarian Menu* 🍗",
                        f"_{week_name} ({service_window})_\n"
                    ]
                    for itm in nonveg_list:
                        name = itm.get("item", "")
                        desc = itm.get("description", "")
                        qty = itm.get("quantity", "")
                        price = itm.get("price_inr", "")
                        desc_str = f" • _{desc}_" if desc else ""
                        qty_str = f" ({qty})" if qty else ""
                        lines.append(f"• *{name}*{qty_str} — *₹{price}*{desc_str}")
                    
                    lines.append("\n📦 *Packaging*: 100% plastic-free, eco-friendly cornstarch boxes.")
                    lines.append("🚚 *Delivery*: Across Delhi NCR from Sushant Arcade, Gurgaon (Courier on actuals).")
                    lines.append("\nWhich non-vegetarian dishes would you like to order? 🙏")
                    formatted_text = "\n".join(lines)
                else:
                    formatted_text = default_response

            elif template_id == "combo_bento":
                bento_list = current_week.get("bengali_bento", []) or current_week.get("combos", [])
                if bento_list:
                    lines = [
                        f"🌸 *Camellia Artisanal — Bengali Bento Meals & Combos* 🍱",
                        f"_{week_name} ({service_window})_\n"
                    ]
                    for b in bento_list:
                        name = b.get("name", "")
                        price = b.get("price_inr", "")
                        includes = ", ".join(b.get("includes", []))
                        inc_str = f" (Includes: {includes})" if includes else ""
                        lines.append(f"• *{name}* — *₹{price}*{inc_str}")
                    
                    lines.append("\n📦 *Packaging*: 100% plastic-free, eco-friendly cornstarch boxes.")
                    lines.append("🚚 *Delivery*: Across Delhi NCR from Sushant Arcade, Gurgaon.")
                    lines.append("\nWhich Bento meal would you like to order? 🌸")
                    formatted_text = "\n".join(lines)
                else:
                    formatted_text = default_response

        if target_images:
            return formatted_text, target_images

        return formatted_text, None

    def respond(self, user_message: str, user_id: str = None, channel: str = None) -> BotResponse:
        """Convenience wrapper returning a structured BotResponse object with .text attribute."""
        text, was_matched, tmpl_id, image_url = self.match_and_respond(user_message, user_id, channel)
        return BotResponse(text=text, was_matched=was_matched, template_id=tmpl_id, image_url=image_url)

responder = DeterministicResponder()
