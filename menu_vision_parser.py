"""
Menu Vision Parser — Standalone Starter Script for Camellia Artisanal
----------------------------------------------------------------------
This script allows you to test automatically extracting weekly menu items, 
prices, and chef recommendations directly from a photo/image of a menu card.

Usage:
    python menu_vision_parser.py [path_to_image_file] [--update-json]

Examples:
    python menu_vision_parser.py "images/Till Thursday.jpeg"
    python menu_vision_parser.py "images/From Friday.jpeg" --update-json
"""

import os
import sys
import json
import base64
import argparse
import httpx
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

SYSTEM_VISION_PROMPT = """
You are an expert menu reader for Camellia Artisanal, a Bengal-rooted artisanal food kitchen in Delhi NCR.
Analyze the provided menu card image and extract all menu items, prices, and descriptions.

Output ONLY a valid JSON object matching this exact schema:
{
  "week_of": "e.g., 7th August - 13th August, 2026",
  "service_window": "Friday Lunch - Thursday Dinner",
  "highlight_teaser": "A warm, appetizing 1-sentence recommendation featuring 1-2 star dishes (e.g., 'Try our Kosha Mangsho & Basanti Pulao this week — customer favourite!')",
  "vegetarian": [
    {"item": "Item Name", "description": "Short description", "price_inr": 300, "quantity": "150g"}
  ],
  "non_vegetarian": [
    {"item": "Item Name", "description": "Short description", "price_inr": 400, "quantity": "150g"}
  ],
  "bengali_bento": [
    {"name": "Veg Bento", "price_inr": 400, "includes": ["Item 1", "Item 2"]}
  ]
}
Do not include markdown codeblocks (```json) in your response, return raw JSON text only.
"""

def encode_image_to_base64(image_path: str) -> tuple[str, str]:
    """Encodes local image to base64 and detects MIME type."""
    ext = os.path.splitext(image_path)[1].lower()
    mime_type = "image/jpeg"
    if ext in [".png"]:
        mime_type = "image/png"
    elif ext in [".webp"]:
        mime_type = "image/webp"

    with open(image_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
    return encoded_string, mime_type

def parse_menu_image_with_gemini(image_path: str) -> dict:
    """Uses Gemini 1.5 Flash Vision API to parse the menu image into structured JSON."""
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    base64_data, mime_type = encode_image_to_base64(image_path)

    if not GEMINI_API_KEY:
        print("\n⚠️ [NOTE]: GEMINI_API_KEY is not set in .env. Running in MOCK / DEMO MODE.")
        return get_mock_extracted_data(image_path)

    print(f"\n🔍 [AI VISION]: Parsing image '{image_path}' using Gemini 1.5 Flash Vision...")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": SYSTEM_VISION_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64_data
                        }
                    }
                ]
            }
        ]
    }

    headers = {"Content-Type": "application/json"}
    
    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        print(f"❌ API Error ({response.status_code}): {response.text}")
        return get_mock_extracted_data(image_path)

    res_json = response.json()
    try:
        raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Clean potential markdown fences
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        
        extracted_data = json.loads(raw_text.strip())
        return extracted_data
    except (KeyError, json.JSONDecodeError) as e:
        print(f"⚠️ Failed to parse JSON from AI response: {e}")
        return get_mock_extracted_data(image_path)

def get_mock_extracted_data(image_path: str) -> dict:
    """Fallback sample data to demonstrate script functionality when API key is offline."""
    filename = os.path.basename(image_path)
    return {
        "week_of": "7th August - 13th August, 2026",
        "service_window": "Friday Lunch - Thursday Dinner",
        "menu_source_image": filename,
        "highlight_teaser": "🌟 Try our Kosha Mangsho & Basanti Pulao this week — customer favourite!",
        "vegetarian": [
            {"item": "Chhoto Aloor Dom", "description": "Baby potatoes in spiced gravy", "price_inr": 300, "quantity": "150g"},
            {"item": "Basonti Polao", "description": "Fragrant yellow ghee rice", "price_inr": 300, "quantity": "250g"}
        ],
        "non_vegetarian": [
            {"item": "Mutton Kosha", "description": "Rich slow-cooked Bengali mutton", "price_inr": 600, "quantity": "200g"},
            {"item": "Bhetki Paturi", "description": "Kolkata bhetki in banana leaf", "price_inr": 700, "quantity": "150g"}
        ],
        "bengali_bento": [
            {"name": "Veg Bento", "price_inr": 400, "includes": ["6 Luchi", "Chholar Daal", "Chatni", "1 Sondesh"]},
            {"name": "Non-Veg Bento", "price_inr": 700, "includes": ["6 Luchi", "Mutton Kosha", "Chatni", "1 Sondesh"]}
        ]
    }

def update_menu_json(extracted_data: dict, menu_json_path: str = "menu.json") -> None:
    """Appends extracted menu data to menu.json."""
    if not os.path.exists(menu_json_path):
        print(f"❌ {menu_json_path} does not exist.")
        return

    with open(menu_json_path, "r", encoding="utf-8") as f:
        menu_data = json.load(f)

    if "weekly_menus" not in menu_data:
        menu_data["weekly_menus"] = []

    menu_data["weekly_menus"].append(extracted_data)

    with open(menu_json_path, "w", encoding="utf-8") as f:
        json.dump(menu_data, f, indent=2, ensure_ascii=False)

    print(f"✅ Successfully updated '{menu_json_path}' with new weekly menu data!")

def main():
    if sys.stdout.encoding.lower() != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Extract menu items and prices from a menu card photo using Vision AI.")
    parser.add_argument("image_path", nargs="?", default="Till Thursday.jpeg", help="Path to menu card photo")
    parser.add_argument("--update-json", action="store_true", help="Automatically append extracted menu to menu.json")

    args = parser.parse_args()

    print("==========================================================")
    print("[CAMELLIA ARTISANAL] - VISION AI MENU PARSER STARTER SCRIPT")
    print("==========================================================")

    data = parse_menu_image_with_gemini(args.image_path)

    print("\n----------------------------------------------------------")
    print("[EXTRACTED MENU DATA - JSON PREVIEW]:")
    print("----------------------------------------------------------")
    print(json.dumps(data, indent=2))

    print("\n----------------------------------------------------------")
    print("[GENERATED CHATBOT CAPTION TEASER]:")
    print("----------------------------------------------------------")
    teaser = data.get("highlight_teaser", "Check out our new weekly specials!")
    caption = f"*Camellia Artisanal - Weekly Menu*\n\n{teaser}\n\nReply with what you'd like to order!"
    print(caption)
    print("----------------------------------------------------------")

    if args.update_json:
        update_menu_json(data)
    else:
        print("\n[TIP]: Run with '--update-json' to save this menu into 'menu.json' automatically!")

if __name__ == "__main__":
    main()
