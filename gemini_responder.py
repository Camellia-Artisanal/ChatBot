import json
import httpx
from typing import Tuple, Dict, Any
from config import config
from monitoring import get_logger

log = get_logger("camellia.gemini")

class GeminiResponder:
    """
    Google Gemini 1.5 Flash Cloud RAG Engine.
    
    Provides fast, context-grounded customer support answers based strictly on
    menu.json + qa_templates.json.
    Enforces strict refusal ('OUT_OF_CONTEXT') for out-of-syllabus or unlisted queries,
    triggering human escalation.
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
            log.error("Error loading %s: %s", path, e)
            return {}

    def reload_data(self):
        """Reload JSON context data."""
        self.menu_data = self._load_json(self.menu_path)
        self.qa_data = self._load_json(self.qa_path)

    def _build_context_string(self) -> str:
        """Assembles a compact, high-density context string from menu.json and qa_templates.json."""
        context_parts = []
        
        # Business Details & Coverage
        context_parts.append("BUSINESS NAME: Camellia Artisanal")
        context_parts.append("TAGLINE: Bengal-Rooted Artisanal Sweets & Food")
        context_parts.append("DELIVERY AREA: Delhi NCR (Delhi, Gurgaon, Noida, Ghaziabad, Faridabad). We do NOT deliver outside Delhi NCR.")
        context_parts.append("ORDER METHOD: Direct message reply or WhatsApp message to our team.")

        # QA Syllabus / Templates
        templates = self.qa_data.get("templates", [])
        if templates:
            context_parts.append("\n--- FREQUENTLY ASKED QUESTIONS & POLICIES ---")
            for tmpl in templates:
                title = tmpl.get("id", "").replace("_", " ").title()
                resp = tmpl.get("response", "")
                context_parts.append(f"• {title}: {resp}")

        # Menu Details
        if self.menu_data:
            context_parts.append("\n--- MENU & PRODUCTS ---")
            collections = self.menu_data.get("collections", {})
            if collections:
                context_parts.append(f"Sondesh Collections, Flavours & Pricing: {json.dumps(collections, ensure_ascii=False)}")

            weekly = self.menu_data.get("weekly_menus", [])
            if weekly:
                context_parts.append(f"Weekly Menus & Bento Combos: {json.dumps(weekly, ensure_ascii=False)}")

            delivery = self.menu_data.get("delivery", {})
            if delivery:
                context_parts.append(f"Delivery & Packaging: {json.dumps(delivery, ensure_ascii=False)}")

        return "\n".join(context_parts)

    def generate_response(self, user_message: str, user_context: str = None, history_turns: list = None) -> Tuple[str, bool, str]:
        """
        Queries Google Gemini API with strict RAG context grounding and active session memory.
        
        Returns:
            (response_text: str, is_context_grounded: bool, reason: str)
        """
        if not config.GEMINI_ENABLED or not config.GEMINI_API_KEY:
            return "", False, "gemini_disabled_or_no_key"

        context_str = self._build_context_string()
        user_ctx_block = f"\nACTIVE CUSTOMER PROFILE:\n{user_context}\n" if user_context else ""

        system_prompt = (
            "You are the customer support AI for Camellia Artisanal, a boutique bakery & kitchen in Delhi NCR.\n\n"
            "STRICT GROUNDING RULES:\n"
            "1. Answer using ONLY the facts present in the CONTEXT and ACTIVE CUSTOMER PROFILE provided below.\n"
            "2. If the customer asks about their own name or saved preferences present in ACTIVE CUSTOMER PROFILE, answer accurately.\n"
            "3. Be concise, polite, helpful, and clear. Maintain a friendly brand tone.\n"
            "4. CRITICAL OUT-OF-CONTEXT RULE: If the user's message cannot be answered using the provided CONTEXT or ACTIVE CUSTOMER PROFILE "
            "(e.g., questions about non-menu items, general knowledge, coding, politics, recipes, or custom unpriced orders), "
            "you MUST reply with ONLY the exact string: OUT_OF_CONTEXT\n"
            "Do NOT provide general knowledge or answer questions outside the syllabus under any circumstances.\n\n"
            f"--- CONTEXT ---\n{context_str}\n"
            f"{user_ctx_block}"
        )

        prompt_text = f"{system_prompt}\n\nCustomer Message: {user_message}"
        if history_turns:
            history_str = "\n".join([f"{t['role'].capitalize()}: {t['content']}" for t in history_turns[-4:]])
            prompt_text = f"{system_prompt}\n\nRecent Chat History:\n{history_str}\n\nCustomer Message: {user_message}"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"

        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt_text}]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1024
            }
        }

        try:
            with httpx.Client(timeout=config.GEMINI_TIMEOUT) as client:
                resp = client.post(url, json=payload)
                if resp.status_code != 200:
                    log.warning("Gemini HTTP %s: %s", resp.status_code, resp.text[:200])
                    return "", False, f"gemini_http_{resp.status_code}"

                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return "", False, "gemini_no_candidates"

                finish_reason = candidates[0].get("finishReason", "")
                assistant_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()

                # Check if Gemini flagged the query as OUT_OF_CONTEXT
                if "OUT_OF_CONTEXT" in assistant_text.upper() or not assistant_text:
                    fallback = self.qa_data.get("escalation_triggers", {}).get(
                        "fallback_response",
                        "Thanks for reaching out! I can only help with questions about Camellia Artisanal menu, prices, and delivery. Let me connect you with our team. 🙏"
                    )
                    return fallback, False, "out_of_context"

                return assistant_text, True, "gemini_rag"

        except Exception as e:
            log.warning("Gemini API call failed (%s): %s", config.GEMINI_MODEL, e)
            fallback = self.qa_data.get("escalation_triggers", {}).get(
                "fallback_response",
                "Thanks for reaching out! Let me connect you with our team — they'll get back to you shortly. 🙏"
            )
            return fallback, False, f"gemini_exception_{type(e).__name__}"

    def refine_template_response(
        self,
        template_id: str,
        template_text: str,
        user_message: str,
        user_context: str = None,
        history_turns: list = None
    ) -> Tuple[str, bool]:
        """
        Uses Gemini AI to think through and format a natural, personalized response
        based on a matched QA template and active customer context.
        Falls back to template_text if Gemini is disabled, offline, or times out.
        """
        if not config.GEMINI_ENABLED or not config.GEMINI_API_KEY:
            return template_text, False

        context_str = self._build_context_string()
        user_ctx_block = f"\nACTIVE CUSTOMER PROFILE:\n{user_context}\n" if user_context else ""

        system_prompt = (
            "You are the customer support AI for Camellia Artisanal, a boutique bakery & kitchen in Delhi NCR.\n\n"
            f"MATCHED SUPPORT TEMPLATE ({template_id}):\n{template_text}\n\n"
            "INSTRUCTIONS:\n"
            "1. Answer the customer's message using the MATCHED SUPPORT TEMPLATE and the CONTEXT/CUSTOMER PROFILE.\n"
            "2. Keep all prices, product details, collection details, and policy facts 100% accurate to the template and menu.\n"
            "3. Personalize the tone warmly (addressing customer by name if known) and answer any specific nuance in their message.\n"
            "4. Keep the output clear, concise, and structured with bullet points where appropriate.\n"
            "5. Ensure the response is fully completed and never truncated.\n"
            "6. Do NOT invent prices or dishes not in the context.\n\n"
            f"--- CONTEXT ---\n{context_str}\n"
            f"{user_ctx_block}"
        )

        prompt_text = f"{system_prompt}\n\nCustomer Message: {user_message}"
        if history_turns:
            history_str = "\n".join([f"{t['role'].capitalize()}: {t['content']}" for t in history_turns[-4:]])
            prompt_text = f"{system_prompt}\n\nRecent Chat History:\n{history_str}\n\nCustomer Message: {user_message}"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"

        payload = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1024
            }
        }

        try:
            with httpx.Client(timeout=config.GEMINI_TIMEOUT) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        finish_reason = candidates[0].get("finishReason", "")
                        if finish_reason == "MAX_TOKENS":
                            log.warning("Gemini refinement hit MAX_TOKENS for %s. Falling back to template.", template_id)
                            return template_text, False
                        assistant_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                        if assistant_text and "OUT_OF_CONTEXT" not in assistant_text.upper():
                            return assistant_text, True
        except Exception as e:
            log.warning("Gemini template refinement failed for %s: %s", template_id, e)

        return template_text, False

gemini_responder = GeminiResponder()
