"""Built-in conversation replies the client can edit (Approved Q&A → System Q&A).

23 Oct: "Hi", "Thanks", "OK", "Bye" and the two clarifying questions were
hard-coded. Each now reads an Admin-edited copy from the versioned
response-template overrides (the same store as the handoff message), falling
back to the wording below, which is what customers got before.
"""
from typing import Dict, List

from camellia.database import db
from camellia.monitoring import get_logger

log = get_logger("camellia.system_replies")

OVERRIDE_PREFIX = "system_reply."
MAX_LENGTH = 1000

# id: (label, what the customer says, default reply)
REPLIES: Dict[str, tuple] = {
    "greeting": ("Greeting", "Hi / Hello / Hii / Good morning",
                 "Hi! Thanks for reaching out to Camellia.artisanal 😊 How can I help you today?"),
    "thanks": ("Thank you", "Thanks / Thank you / Thank you so much",
               "You're most welcome! 😊"),
    "acknowledgement": ("OK / Sure", "Ok / Sure / Great / Got it / 👍",
                        "Got it! 😊 How can I help you?"),
    "goodbye": ("Bye", "Bye / Goodbye / See you / Take care",
                "Thank you for connecting with Camellia.artisanal. Have a lovely day!"),
    "price_clarify": ("Which product's price?", "Price? / How much? (no product named)",
                      "Would you like prices for Sondesh or Bengali Meals?"),
    "tell_me_more_clarify": ("Tell me more (unknown post)", "Tell me more — after sharing a post we can't identify",
                             "Of course — what would you like to know more about?"),
}


def text(reply_id: str) -> str:
    """The Admin-edited reply, or the built-in default."""
    default = REPLIES[reply_id][2]
    try:
        override = db.get_response_template_override(OVERRIDE_PREFIX + reply_id) or {}
    except Exception:
        log.exception("Could not read the %s reply override; using the default.", reply_id)
        return default
    if str(override.get("status") or "ACTIVE").upper() != "ACTIVE":
        return default
    return str(override.get("response") or "").strip() or default


def listing() -> List[dict]:
    rows = []
    for reply_id, (label, trigger, default) in REPLIES.items():
        current = text(reply_id)
        rows.append({"id": reply_id, "label": label, "trigger": trigger, "default": default,
                     "text": current, "edited": current != default})
    return rows
