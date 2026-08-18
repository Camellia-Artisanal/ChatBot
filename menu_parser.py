import re
import json
import httpx
from typing import Dict, Any, List
from config import config
from monitoring import get_logger
from sheet_sync import sheet_sync

log = get_logger("camellia.menu_parser")

NON_VEG_KEYWORDS = [
    "mutton", "chicken", "fish", "egg", "non-veg", "non veg", "prawn", 
    "mangsho", "kosha", "cutlet", "kalia", "chingri", "machh", "biryani"
]

class MenuParser:
    """
    Auto-extracts structured menu data (Items, Descriptions, Quantities, Prices, Categories)
    from raw unstructured text pasted by the user (menu cards, WhatsApp posts, flyers).
    Uses Gemini AI with regex fallback.
    """

    def parse_menu_text(self, raw_text: str) -> Dict[str, Any]:
        """Parses raw menu text into structured vegetarian and non-vegetarian item lists."""
        if not raw_text or not raw_text.strip():
            return {"vegetarian": [], "non_vegetarian": []}

        # Try Gemini AI extraction if enabled and key present
        if config.GEMINI_ENABLED and config.GEMINI_API_KEY:
            ai_result = self._parse_with_gemini(raw_text)
            if ai_result and (ai_result.get("vegetarian") or ai_result.get("non_vegetarian")):
                log.info("Successfully extracted menu using Gemini AI")
                return ai_result

        # Fallback to regex & heuristic extraction
        log.info("Using regex fallback for menu text parsing")
        return self._parse_with_regex(raw_text)

    def _parse_with_gemini(self, raw_text: str) -> Dict[str, Any]:
        """Uses Gemini API to convert unstructured menu card text into JSON."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
        
        prompt = (
            "You are a structured data extraction AI for a food & artisanal sweets bakery.\n"
            "Extract all menu items from the following raw menu text into a strict valid JSON object.\n\n"
            "JSON SCHEMA REQUIREMENT:\n"
            "{\n"
            '  "vegetarian": [\n'
            '    {"item": "Item Name", "description": "Short description", "price_inr": 450, "quantity": "250g"}\n'
            "  ],\n"
            '  "non_vegetarian": [\n'
            '    {"item": "Item Name", "description": "Short description", "price_inr": 650, "quantity": "1 box"}\n'
            "  ]\n"
            "}\n\n"
            "RULES:\n"
            "1. Extract exact price as integer (price_inr).\n"
            "2. Extract quantity or weight (e.g., '250g', '500g', '1kg', '2pcs', '1 box'). Default to '250g' or '1 box' if unstated.\n"
            "3. Categorize items with meat/poultry/fish/egg/mutton/chicken into 'non_vegetarian', and sweets/dairy/veg into 'vegetarian'.\n"
            "4. Return ONLY valid JSON block without markdown formatting.\n\n"
            f"RAW MENU TEXT:\n{raw_text}"
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1000}
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    # Strip any markdown backticks if returned
                    text = re.sub(r"^```json\s*", "", text)
                    text = re.sub(r"^```\s*", "", text)
                    text = re.sub(r"\s*```$", "", text)
                    data = json.loads(text)
                    if isinstance(data, dict):
                        return {
                            "vegetarian": data.get("vegetarian", []),
                            "non_vegetarian": data.get("non_vegetarian", [])
                        }
        except Exception as e:
            log.warning("Gemini menu parsing failed: %s", e)
        return {}

    def _parse_with_regex(self, raw_text: str) -> Dict[str, Any]:
        """Regex and heuristic parser for extracting items, prices, and quantities from text lines."""
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        veg_items = []
        nonveg_items = []

        current_category = "vegetarian"

        for line in lines:
            line_lower = line.lower()
            price_match = re.search(r'(?:₹|rs\.?|inr)?\s*(\d{2,5})\s*(?:/-)?', line_lower)

            # Pure Category headers (no price digits present in header line)
            if not price_match:
                if "non-veg" in line_lower or "non veg" in line_lower or "non vegetarian" in line_lower:
                    current_category = "non_vegetarian"
                elif "veg" in line_lower or "vegetarian" in line_lower or "sweets" in line_lower:
                    current_category = "vegetarian"
                continue
            
            price_val = int(price_match.group(1))

            # Extract quantity / weight
            qty_match = re.search(r'(\d+\s*(?:g|kg|pcs|pieces|box|pack))', line_lower)
            quantity_val = qty_match.group(1) if qty_match else "250g"

            # Extract clean item name (strip price and bullet points)
            clean_name = line
            clean_name = re.sub(r'[•\-*\d+\.]+\s*', '', clean_name)
            clean_name = re.sub(r'(?:₹|rs\.?|inr)?\s*\d{2,5}\s*(?:/-)?', '', clean_name, flags=re.IGNORECASE)
            clean_name = re.sub(r'\(\d+\s*(?:g|kg|pcs|box)\)', '', clean_name, flags=re.IGNORECASE)
            clean_name = clean_name.strip(" |:-,")

            if not clean_name:
                clean_name = "Artisanal Specialty"

            # Determine category
            is_nonveg = any(kw in clean_name.lower() for kw in NON_VEG_KEYWORDS) or current_category == "non_vegetarian"
            
            item_dict = {
                "item": clean_name.title(),
                "description": f"Freshly handcrafted ({quantity_val})",
                "price_inr": price_val,
                "quantity": quantity_val
            }

            if is_nonveg:
                nonveg_items.append(item_dict)
            else:
                veg_items.append(item_dict)

        return {"vegetarian": veg_items, "non_vegetarian": nonveg_items}

    def parse_and_update_menu(self, raw_text: str) -> Dict[str, Any]:
        """Parses raw text, updates menu.json, and hot-reloads the bot."""
        parsed = self.parse_menu_text(raw_text)
        veg_items = parsed.get("vegetarian", [])
        nonveg_items = parsed.get("non_vegetarian", [])

        total_extracted = len(veg_items) + len(nonveg_items)
        if total_extracted == 0:
            return {
                "status": "failed",
                "reason": "Could not extract any valid items with prices from the text.",
                "parsed": parsed
            }

        menu_path = config.MENU_JSON_PATH
        try:
            with open(menu_path, "r", encoding="utf-8") as f:
                menu_data = json.load(f)

            if "weekly_menus" in menu_data and menu_data["weekly_menus"]:
                current_week = menu_data["weekly_menus"][-1]
                if veg_items:
                    current_week["vegetarian"] = veg_items
                if nonveg_items:
                    current_week["non_vegetarian"] = nonveg_items

                with open(menu_path, "w", encoding="utf-8") as f:
                    json.dump(menu_data, f, indent=2, ensure_ascii=False)

                sheet_sync.sync_local_reload()
                return {
                    "status": "success",
                    "updated_items_count": total_extracted,
                    "vegetarian_count": len(veg_items),
                    "non_vegetarian_count": len(nonveg_items),
                    "parsed": parsed,
                    "message": f"Successfully extracted {total_extracted} items and updated menu.json."
                }
            else:
                return {"status": "failed", "reason": "No weekly_menus section found in menu.json"}
        except Exception as e:
            log.error("Failed to write to menu.json: %s", e)
            return {"status": "error", "error": str(e)}

    def parse_image_and_update_menu(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> Dict[str, Any]:
        """
        Multimodal OCR & Data Extraction:
        Reads menu card photo / flyer using Gemini Vision, converts to JSON, updates menu.json, and hot-reloads.
        """
        import base64
        if not image_bytes:
            return {"status": "failed", "reason": "No image data received."}

        if not config.GEMINI_ENABLED or not config.GEMINI_API_KEY:
            # Fallback for testing environments without live Gemini API key
            log.warning("Gemini Vision not configured. Returning simulated test parse.")
            return {
                "status": "warning",
                "reason": "GEMINI_API_KEY not configured for live image OCR. Upload received for testing.",
                "message": "Image received successfully. Configure GEMINI_API_KEY for live visual OCR extraction."
            }

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
        
        prompt = (
            "You are a structured data extraction AI for Camellia Artisanal bakery & kitchen.\n"
            "Analyze this menu card / flyer image and extract all dishes, bento boxes, curries, and sweets into a strict valid JSON object.\n\n"
            "JSON SCHEMA:\n"
            "{\n"
            '  "vegetarian": [\n'
            '    {"item": "Item Name", "description": "Short description", "price_inr": 450, "quantity": "250g"}\n'
            "  ],\n"
            '  "non_vegetarian": [\n'
            '    {"item": "Item Name", "description": "Short description", "price_inr": 650, "quantity": "1 box"}\n'
            "  ]\n"
            "}\n\n"
            "RULES:\n"
            "1. Extract exact price as integer (price_inr).\n"
            "2. Extract quantity or weight (e.g., '250g', '500g', '1 portion', '1 box').\n"
            "3. Categorize non-veg (meat/fish/prawn/chicken/egg/mutton) into 'non_vegetarian', and veg/sweets into 'vegetarian'.\n"
            "4. Return ONLY the valid JSON block without markdown backticks."
        )

        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_image
                        }
                    }
                ]
            }],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1200}
        }

        try:
            with httpx.Client(timeout=25.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    text = re.sub(r"^```json\s*", "", text)
                    text = re.sub(r"^```\s*", "", text)
                    text = re.sub(r"\s*```$", "", text)
                    data = json.loads(text)
                    
                    veg_items = data.get("vegetarian", [])
                    nonveg_items = data.get("non_vegetarian", [])
                    total = len(veg_items) + len(nonveg_items)

                    if total > 0:
                        menu_path = config.MENU_JSON_PATH
                        with open(menu_path, "r", encoding="utf-8") as f:
                            menu_data = json.load(f)

                        if "weekly_menus" in menu_data and menu_data["weekly_menus"]:
                            current_week = menu_data["weekly_menus"][-1]
                            if veg_items:
                                current_week["vegetarian"] = veg_items
                            if nonveg_items:
                                current_week["non_vegetarian"] = nonveg_items

                            with open(menu_path, "w", encoding="utf-8") as f:
                                json.dump(menu_data, f, indent=2, ensure_ascii=False)

                            sheet_sync.sync_local_reload()
                            return {
                                "status": "success",
                                "updated_items_count": total,
                                "vegetarian_count": len(veg_items),
                                "non_vegetarian_count": len(nonveg_items),
                                "parsed": data,
                                "message": f"Successfully extracted {total} items from menu image ({len(veg_items)} veg, {len(nonveg_items)} non-veg) and updated menu.json."
                            }
                else:
                    log.error("Gemini Vision HTTP %s: %s", resp.status_code, resp.text)
                    return {"status": "error", "error": f"Gemini Vision API error: HTTP {resp.status_code}"}
        except Exception as e:
            log.error("Failed to parse menu image: %s", e)
            return {"status": "error", "error": str(e)}

        return {"status": "failed", "reason": "No items could be extracted from image."}

menu_parser = MenuParser()

