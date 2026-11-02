"""
Category buttons: when the bot cannot tell the source, it asks once which
category the customer means, instead of guessing or going straight to staff.

Off until CATEGORY_BUTTONS_MODE is "test" (test accounts only) or "all". A tap is
just its button text arriving as a message, so it is matched against the options
this customer was offered. Any answer that cannot be sent in full goes to staff.

Question buttons follow the same switch. Attached to a mapped reply or a category answer
that ends in text, buttons such as Price offer the next question. A tap is swapped for the
full question before the bot reads it, so it is answered exactly like the same question typed.
"""

import json
import re
from typing import Any, Dict, List, Optional

from camellia import answer_slots, approved_qa, client_qa, instagram_chatbot_control, menu_images
from camellia.config import config
from camellia.database import db
from camellia.monitoring import get_logger

log = get_logger("camellia.category_buttons")

# Draft: needs the client's approval before CATEGORY_BUTTONS_MODE is set to "all".
QUESTION = "Thanks for messaging Camellia! Which are you interested in?"
QUESTIONS = {"price": ("Price", "How much does it cost?"), "details": ("Tell me more", "Tell me more"),
             "booking": ("How to book", "How to book?"), "date": ("Date and venue", "When is it?"),
             "location": ("Location", "Where is it?")}
MAX_BUTTONS = 13  # Instagram's limit for quick replies.
NAME_LIMIT = 20  # Instagram's limit for a quick reply title.
_KEYWORDS = {
    "booking": re.compile(r"\b(?:book|booking|reserve|reservation|how\s+to\s+order|place\s+an?\s+order)\b", re.I),
    "date": re.compile(r"\b(?:when\s+is|what\s+date|what\s+time|next\s+date|venue)\b", re.I),
    "location": re.compile(r"\b(?:deliver|delivery|shipping|address|location|where\s+(?:is|are))\b", re.I),
}


def enabled(sender_id: str) -> bool:
    mode = config.CATEGORY_BUTTONS_MODE
    if mode == "all":
        return True
    if mode != "test":
        return False
    return any(str(account.get("sender_id")) == str(sender_id)
               for account in instagram_chatbot_control.get_state().get("test_accounts", []))


def slot_for(message: str) -> Optional[str]:
    """The question's type by keywords, so this path needs no AI."""
    asked = {*approved_qa._asked_roles(message), approved_qa.local_wording_role(message)}
    return next((slot for slot in ("price", "details") if slot in asked),
                next((slot for slot, words in _KEYWORDS.items() if words.search(message or "")), None))


def options() -> List[Dict[str, str]]:
    """Active categories with a button name and a usable Details answer."""
    found = []
    for topic in db.list_instagram_topics(include_inactive=False):
        name = str(topic.get("button_name") or "").strip()
        answer = answer_slots.effective({"topic_id": topic["topic_id"]}, "details") if name else None
        if answer and not answer["expired"]:
            found.append({"title": name, "topic_id": topic["topic_id"]})
    return found[:MAX_BUTTONS]


def blocks_for(topic_id: str, slot: str) -> Optional[List[Dict[str, Any]]]:
    """The answer as ordered blocks, or None if any part of it cannot be sent."""
    answer = answer_slots.effective({"topic_id": topic_id}, slot)
    if not answer or answer["expired"]:
        return None
    if answer["kind"] == "CUSTOM":
        url = menu_images.current_url(str(answer["menu_id"])) if answer.get("menu_id") else None
        if answer.get("menu_id") and not url:
            return None
        return [{"kind": "TEXT", "text": answer["text"]}] + ([{"kind": "IMAGE", "asset_key": answer["menu_id"], "urls": [url]}] if url else [])
    match = client_qa.resolve_effective_approved_qa_by_id(answer["qa_id"])
    if not match:
        return None
    blocks: List[Dict[str, Any]] = []
    for block in match.entry.blocks:
        if block.kind == "TEXT":
            if client_qa.HOME_TABLE_VIDEO_ANCHOR in block.value:
                blocks.extend(client_qa.build_home_table_reel_blocks(block.value))
            else:
                blocks.append({"kind": "TEXT", "text": block.value})
            continue
        key = block.value.split(":", 1)[-1]
        url = menu_images.current_url(key)
        if block.kind == "REEL" or not url:
            return None  # The bot's main path resolves Reels; a part-sent answer is worse than staff.
        blocks.append({"kind": block.kind, "asset_key": block.value, "urls": [url]})
    return blocks or None


def _record_gap(topic_id: str, slot: str, message: str) -> None:
    """Puts a missing answer on the Needs answers page."""
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO answer_slot_shadow (topic_id, user_message, slot, outcome, detail) VALUES (?, ?, ?, 'empty', 'category_button')",
            (topic_id, message, slot),
        )
        conn.commit()


def tap(sender_id: str, facts: Dict[str, Any], message: str) -> Optional[List[Dict[str, Any]]]:
    """The answer for a tapped button, or None when this message is not a usable tap."""
    pending = facts.get("category_question") or {}
    topic_id = (pending.get("options") or {}).get(str(message or "").strip().casefold())
    if not topic_id or not enabled(sender_id):
        return None
    facts.pop("category_question", None)
    facts["category_choice"] = topic_id
    slot = pending.get("slot") or "details"
    blocks = blocks_for(topic_id, slot)
    if not blocks:
        _record_gap(topic_id, slot, pending.get("message") or str(message))
    return blocks


def next_step(sender_id: str, facts: Dict[str, Any], message: str, ad_button: bool) -> Optional[Dict[str, Any]]:
    """What to do instead of a handoff: answer from the chosen category, or ask once."""
    if not enabled(sender_id):
        return None
    slot = slot_for(message)
    chosen = facts.get("category_choice")
    if chosen:
        if not slot:
            return None
        blocks = blocks_for(chosen, slot)
        if not blocks:
            _record_gap(chosen, slot, message)
            return None
        return {"blocks": blocks}
    if facts.get("category_question_asked") or not (ad_button or slot):
        return None
    found = options()
    return {"ask": found, "slot": slot, "message": message} if found else None


def remember_question(facts: Dict[str, Any], step: Dict[str, Any]) -> None:
    facts["category_question_asked"] = True
    facts["category_question"] = {"options": {item["title"].casefold(): item["topic_id"] for item in step["ask"]},
                                  "slot": step["slot"], "message": step["message"]}


def stored_buttons(owner_type: str, owner_id: str) -> Optional[List[Dict[str, str]]]:
    with db.get_connection() as conn:
        row = conn.execute("SELECT buttons_json FROM question_buttons WHERE owner_type=? AND owner_id=?",
                           (owner_type, str(owner_id))).fetchone()
    return json.loads(row["buttons_json"]) if row else None


def save_buttons(owner_type: str, owner_id: str, buttons: Any) -> None:
    if owner_type not in answer_slots.OWNERS or not isinstance(buttons, list):
        raise ValueError("Choose the question buttons to show.")
    clean, labels = [], set()
    for button in buttons:
        slot = button.get("slot") if isinstance(button, dict) else None
        label = " ".join(str(button.get("label") or "").split()) if isinstance(button, dict) else ""
        if slot not in QUESTIONS or not 1 <= len(label) <= NAME_LIMIT or label.casefold() in labels:
            raise ValueError(f"Each button needs its own name of up to {NAME_LIMIT} characters.")
        labels.add(label.casefold())
        clean.append({"slot": slot, "label": label})
    with db.get_connection() as conn:
        conn.execute(
            """INSERT INTO question_buttons (owner_type, owner_id, buttons_json) VALUES (?, ?, ?)
               ON CONFLICT(owner_type, owner_id) DO UPDATE SET buttons_json=excluded.buttons_json""",
            (owner_type, str(owner_id), json.dumps(clean)),
        )
        conn.commit()


def _mapped_answer_exists(route: Dict[str, Any], slot: str) -> bool:
    """The bot answers a mapped post from its ticked answers, so only those count there."""
    topic = db.get_instagram_topic(route["topic_id"]) if route.get("topic_id") else None
    ids = list(route.get("approved_qa_ids") or []) + json.loads((topic or {}).get("default_approved_qa_ids") or "[]")
    live = [key for key in ids if approved_qa.get(key)]
    roles = approved_qa.mapped_settings(approved_qa.overlays())
    if any(roles.get(key, (None,))[0] == slot for key in live):
        return True
    return slot == "details" and bool(route.get("custom_info_questions_enabled") and route.get("source_description"))


def _offer(sender_id: str, facts: Dict[str, Any], buttons: Optional[List[Dict[str, str]]], has_answer) -> Optional[List[Dict[str, str]]]:
    shown = [button for button in buttons or [] if has_answer(button["slot"])]
    if not shown or not enabled(sender_id):
        return None
    facts["question_buttons"] = {button["label"].casefold(): QUESTIONS[button["slot"]][1] for button in shown}
    return [{"content_type": "text", "title": button["label"], "payload": "question:" + button["slot"]} for button in shown]


def after_mapped_reply(sender_id: str, facts: Dict[str, Any], route: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    """Buttons under the first reply from this source, once per customer and source."""
    routing_id = route.get("routing_id")
    if not routing_id or facts.get("question_buttons_shown") == routing_id:
        return None
    buttons = stored_buttons("route", routing_id)
    if buttons is None and route.get("topic_id"):
        buttons = stored_buttons("topic", route["topic_id"])
    replies = _offer(sender_id, facts, buttons, lambda slot: _mapped_answer_exists(route, slot))
    if replies:
        facts["question_buttons_shown"] = routing_id
    return replies


def after_category_answer(sender_id: str, facts: Dict[str, Any], topic_id: str) -> Optional[List[Dict[str, str]]]:
    return _offer(sender_id, facts, stored_buttons("topic", topic_id), lambda slot: bool(blocks_for(topic_id, slot)))


def question_for_tap(facts: Dict[str, Any], message: str) -> str:
    """The full question behind a tapped button; any other message is left as it is."""
    offered = facts.pop("question_buttons", None) or {}
    return offered.get(str(message or "").strip().casefold(), message)


def preview(topic_id: str, slot: str) -> Dict[str, Any]:
    """What a customer asking this would get from this category, for the Admin preview."""
    blocks = blocks_for(topic_id, slot)
    answer = answer_slots.effective({"topic_id": topic_id}, slot)
    if blocks:
        state = "ok"
    else:
        state = "expired" if answer and answer["expired"] else "unsendable" if answer else "empty"
    shown = [button["label"] for button in stored_buttons("topic", topic_id) or [] if blocks_for(topic_id, button["slot"])]
    return {"state": state, "blocks": blocks or [], "buttons": shown if blocks else []}


def quick_replies(found: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [{"content_type": "text", "title": item["title"], "payload": "category:" + item["topic_id"]} for item in found]
