import re
from typing import Dict, Any, Optional

PRICING_TABLE = {
    "aamar_mishti": {
        "classic": { "250g": 350, "500g": 800, "1kg": 1500 },
        "rose": { "250g": 350, "500g": 800, "1kg": 1500 },
        "mango": { "250g": 450, "500g": 1000, "1kg": 1800 },
        "kesar": { "250g": 450, "500g": 1000, "1kg": 1800 },
        "nolen gur": { "250g": 450, "500g": 1100, "1kg": 2000 },
        "jolbhora": { "250g": 600, "500g": 1400, "1kg": 2500 },
    },
    "signature": {
        "classic": { "250g": 500, "500g": 1100, "1kg": 2000 },
        "rose": { "250g": 500, "500g": 1100, "1kg": 2000 },
        "mango": { "250g": 550, "500g": 1200, "1kg": 2200 },
        "kesar": { "250g": 550, "500g": 1200, "1kg": 2200 },
        "vanilla": { "250g": 550, "500g": 1200, "1kg": 2200 },
        "cinnamon": { "250g": 550, "500g": 1200, "1kg": 2200 },
        "nolen gur": { "250g": 600, "500g": 1300, "1kg": 2500 },
        "jolbhora": { "250g": 700, "500g": 1600, "1kg": 2900 },
        "mahua": { "250g": 700, "500g": 1600, "1kg": 2900 },
    }
}

class OrderCalculator:
    """Parses order intent from chat text and generates itemized price breakdowns."""

    @staticmethod
    def format_currency(amount: int) -> str:
        """Formats INR integer amounts to formatted string (e.g. ₹1,200)."""
        return f"₹{amount:,}"

    def parse_and_calculate(self, text: str) -> Optional[Dict[str, Any]]:
        text_lower = text.lower()

        # Determine collection
        collection = "signature" if "signature" in text_lower else "aamar_mishti"
        collection_title = "Signature A2 Collection" if collection == "signature" else "Aamar Mishti Collection"

        # Determine pack size
        size = None
        if "1kg" in text_lower or "1 kg" in text_lower or "1000g" in text_lower or "36 pcs" in text_lower:
            size = "1kg"
        elif "500g" in text_lower or "500 g" in text_lower or "18 pcs" in text_lower:
            size = "500g"
        elif "250g" in text_lower or "250 g" in text_lower or "9 pcs" in text_lower:
            size = "250g"
        else:
            size = "250g"  # Default box size

        # Determine flavour
        flavour = "nolen gur"
        for fl in ["jolbhora", "mahua", "mango", "kesar", "rose", "vanilla", "cinnamon", "classic", "nolen gur"]:
            if fl in text_lower:
                flavour = fl
                break

        # Lookup price
        coll_prices = PRICING_TABLE.get(collection, PRICING_TABLE["aamar_mishti"])
        flavor_prices = coll_prices.get(flavour, coll_prices["nolen gur"])
        unit_price = flavor_prices.get(size, 450)

        # Quantity detection
        qty = 1
        qty_match = re.search(r'\b(\d+)\s*(?:box|boxes|pack|packs|pcs)\b', text_lower)
        if qty_match:
            try:
                qty = max(1, int(qty_match.group(1)))
            except ValueError:
                qty = 1

        total_item_price = unit_price * qty

        receipt = (
            f"💐 *CAMELLIA ARTISANAL — ORDER DRAFT RECEIPT*\n"
            f"─────────────────────────────\n"
            f"• *Collection:* {collection_title}\n"
            f"• *Flavour:* {flavour.title()}\n"
            f"• *Pack Size:* {size} ({qty} box{'es' if qty > 1 else ''})\n"
            f"• *Unit Price:* ₹{unit_price}\n"
            f"• *Subtotal:* ₹{total_item_price}\n"
            f"• *Packaging:* ₹0 (Plastic-Free Cornstarch Box)\n"
            f"• *Courier (Porter):* Actuals at dispatch (from Sushant Arcade, Gurgaon)\n"
            f"─────────────────────────────\n"
            f"💰 *Total Estimated Item Price:* ₹{total_item_price}\n\n"
            f"📞 *Next Steps:* Our Manager of Kitchens, Mr. Guru Dayal Singh (+91 87965 51990), "
            f"will verify your delivery address & share the UPI payment link via WhatsApp!"
        )

        return {
            "collection": collection,
            "flavour": flavour,
            "size": size,
            "quantity": qty,
            "unit_price": unit_price,
            "total_price": total_item_price,
            "formatted_receipt": receipt
        }

order_calculator = OrderCalculator()
