import json
import httpx
from typing import Tuple, Dict, Any
from config import config

class OllamaResponder:
    """
    Ollama Local LLM Fast & Accurate RAG Engine.
    
    Provides context-grounded answers based strictly on menu.json + qa_templates.json.
    Enforces strict refusal ('OUT_OF_CONTEXT') for out-of-syllabus or unrelated queries,
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
            print(f"[OllamaResponder] Error loading {path}: {e}")
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
        Queries Ollama with strict RAG context grounding and active session memory context.
        
        Returns:
            (response_text: str, is_context_grounded: bool, reason: str)
        """
        if not config.OLLAMA_ENABLED:
            return "", False, "ollama_disabled"

        context_str = self._build_context_string()
        user_ctx_block = f"\nACTIVE CUSTOMER PROFILE:\n{user_context}\n" if user_context else ""

        system_prompt = (
            "You are the customer support AI for Camellia Artisanal, a boutique bakery & kitchen in Delhi NCR.\n\n"
            "STRICT GROUNDING RULES:\n"
            "1. Answer using the facts present in the CONTEXT and ACTIVE CUSTOMER PROFILE provided below.\n"
            "2. If the customer asks about their own name or saved preferences present in ACTIVE CUSTOMER PROFILE, answer accurately.\n"
            "3. Be concise, polite, helpful, and clear. Maintain a friendly brand tone.\n"
            "4. CRITICAL OUT-OF-CONTEXT RULE: If the user's message cannot be answered using the provided CONTEXT or ACTIVE CUSTOMER PROFILE "
            "(e.g., questions about non-menu items, general knowledge, coding, politics, recipes, or custom unpriced orders), "
            "you MUST reply with ONLY the exact string: OUT_OF_CONTEXT\n"
            "Do NOT provide general knowledge or answer questions outside the syllabus under any circumstances.\n\n"
            f"--- CONTEXT ---\n{context_str}\n"
            f"{user_ctx_block}"
        )

        messages = [{"role": "system", "content": system_prompt}]

        # Append recent history turns if provided
        if history_turns:
            for turn in history_turns[-4:]: # include up to last 4 messages (2 pairs)
                messages.append({"role": turn["role"], "content": turn["content"]})

        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": config.OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.1,  # Low temp for deterministic adherence to context
                "num_predict": 256
            }
        }

        url = f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/chat"

        try:
            with httpx.Client(timeout=config.OLLAMA_TIMEOUT) as client:
                resp = client.post(url, json=payload)
                if resp.status_code != 200:
                    print(f"[OllamaResponder] HTTP Error {resp.status_code}: {resp.text}")
                    return "", False, f"ollama_http_{resp.status_code}"

                data = resp.json()
                assistant_text = data.get("message", {}).get("content", "").strip()

                # Check if Ollama flagged the query as OUT_OF_CONTEXT
                if "OUT_OF_CONTEXT" in assistant_text.upper() or not assistant_text:
                    fallback = self.qa_data.get("escalation_triggers", {}).get(
                        "fallback_response",
                        "Thanks for reaching out! I can only help with questions about Camellia Artisanal menu, prices, and delivery. Let me connect you with our team. 🙏"
                    )
                    return fallback, False, "out_of_context"

                return assistant_text, True, "ollama_rag"

        except Exception as e:
            print(f"[OllamaResponder] Exception querying Ollama ({config.OLLAMA_MODEL} at {url}): {e}")
            fallback = self.qa_data.get("escalation_triggers", {}).get(
                "fallback_response",
                "Thanks for reaching out! Let me connect you with our team — they'll get back to you shortly. 🙏"
            )
            return fallback, False, f"ollama_exception_{type(e).__name__}"

ollama_responder = OllamaResponder()
