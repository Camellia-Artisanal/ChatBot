"""
Answer slots: one answer per question type, set on a master category with a
per-post override. Each slot holds an Approved Q&A link or a Custom response.

For now slots run in shadow, like variation_shadow: a message heading for a
handoff is read in the background for its question type, and the result only
goes to answer_slot_shadow, which also feeds the Needs answers page. Nothing in
the reply path reads a slot, so a customer never sees one.
"""

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from camellia import approved_qa
from camellia.ai_responder import ai_responder
from camellia.config import config
from camellia.database import db
from camellia.monitoring import get_logger

log = get_logger("camellia.answer_slots")

SLOTS = {"price": "Price", "details": "Details", "booking": "Booking",
         "date": "Date and venue", "location": "Delivery or location"}
OWNERS = ("topic", "route")
END_DATE_REQUIRED = ("price", "date")
WOULD_SEND_CONFIDENCE = 0.85
OWN_LIMIT = 10
KEEP_DAYS = 90  # These rows hold customer wording, so they do not outlive the review.
_SHADOW_WORKERS = 2
_SHADOW_QUEUE_LIMIT = 64


def own_questions(topic_id: Optional[str]) -> Dict[str, str]:
    """Question types staff added to one category, by slot."""
    if not topic_id:
        return {}
    with db.get_connection() as conn:
        return {row["slot"]: row["label"] for row in conn.execute(
            "SELECT slot, label FROM answer_own_questions WHERE topic_id=? ORDER BY label", (str(topic_id),))}


def add_own_question(topic_id: str, label: Any) -> str:
    label = " ".join(str(label or "").split())
    own = own_questions(topic_id)
    if not 1 <= len(label) <= 60:
        raise ValueError("Write the question in 1 to 60 characters.")
    if label.casefold() in {name.casefold() for name in [*SLOTS.values(), *own.values()]}:
        raise ValueError("This category already has that question.")
    if len(own) >= OWN_LIMIT:
        raise ValueError(f"A category can hold {OWN_LIMIT} of its own questions.")
    slot = "own_" + uuid.uuid4().hex[:10]
    with db.get_connection() as conn:
        conn.execute("INSERT INTO answer_own_questions (topic_id, slot, label) VALUES (?, ?, ?)", (str(topic_id), slot, label))
        conn.commit()
    return slot


def remove_own_question(topic_id: str, slot: str) -> None:
    with db.get_connection() as conn:
        conn.execute("DELETE FROM answer_own_questions WHERE topic_id=? AND slot=?", (str(topic_id), slot))
        conn.execute("DELETE FROM answer_slots WHERE owner_type='topic' AND owner_id=? AND slot=?", (str(topic_id), slot))
        conn.commit()


def labels(topic_id: Optional[str]) -> Dict[str, str]:
    return {**SLOTS, **own_questions(topic_id)}


def _row(owner_type: str, owner_id: str, slot: str) -> Optional[Dict[str, Any]]:
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM answer_slots WHERE owner_type=? AND owner_id=? AND slot=?",
                           (owner_type, str(owner_id), slot)).fetchone()
    return dict(row) if row else None


def _past(value: Optional[str]) -> bool:
    end = approved_qa.parse_date(value) if value else None
    return bool(end and datetime.now(timezone.utc) > end)


def _answer(row: Dict[str, Any], source: str) -> Dict[str, Any]:
    if row["kind"] == "APPROVED_QA":
        entry = approved_qa.get(row["qa_id"])
        return {"source": source, "kind": "APPROVED_QA", "qa_id": row["qa_id"], "expired": entry is None,
                "text": entry.answer if entry else "", "question": entry.question if entry else row["qa_id"]}
    return {"source": source, "kind": "CUSTOM", "text": row["custom_text"], "menu_id": row["menu_id"],
            "valid_until": row["valid_until"], "expired": _past(row["valid_until"])}


def _tagged_answer(topic: Dict[str, Any], slot: str) -> Optional[Dict[str, Any]]:
    """An answer the Topic already ticks and tags with this question type."""
    ids = json.loads(topic.get("default_approved_qa_ids") or "[]")
    roles = approved_qa.mapped_settings(approved_qa.overlays())
    for qa_id in ids:
        if roles.get(qa_id, (None,))[0] == slot:
            return _answer({"kind": "APPROVED_QA", "qa_id": qa_id}, "tagged")
    return None


def effective(route: Dict[str, Any], slot: str) -> Optional[Dict[str, Any]]:
    """The more specific setting wins: this post, then its category, then a tagged answer."""
    if route.get("routing_id"):
        own = _row("route", route["routing_id"], slot)
        if own:
            return _answer(own, "post")
    topic = db.get_instagram_topic(route["topic_id"]) if route.get("topic_id") else None
    if not topic:
        return None
    shared = _row("topic", topic["topic_id"], slot)
    return _answer(shared, "category") if shared else _tagged_answer(topic, slot)


def list_slots(owner_type: str, owner_id: str) -> List[Dict[str, Any]]:
    return [{"slot": key, "label": label,
             "answer": _answer(row, "post" if owner_type == "route" else "category") if (row := _row(owner_type, owner_id, key)) else None}
            | ({"own": True} if key not in SLOTS else {})
            for key, label in (labels(owner_id) if owner_type == "topic" else SLOTS).items()]


def save(owner_type: str, owner_id: str, slot: str, body: Dict[str, Any]) -> None:
    if owner_type not in OWNERS or slot not in (labels(owner_id) if owner_type == "topic" else SLOTS):
        raise ValueError("Unknown answer slot.")
    kind = body.get("kind")
    qa_id = custom_text = menu_id = valid_until = None
    if kind == "APPROVED_QA":
        qa_id = str(body.get("qa_id") or "")
        if not approved_qa.get(qa_id):
            raise ValueError("Choose a current Approved Q&A answer.")
    elif kind == "CUSTOM":
        custom_text = str(body.get("custom_text") or "").strip()
        menu_id = body.get("menu_id") or None
        valid_until = body.get("valid_until") or None
        if not 1 <= len(custom_text) <= 2000:
            raise ValueError("Write the custom answer in 1 to 2000 characters.")
        if menu_id and not db.get_menu_catalogue_entry(menu_id):
            raise ValueError("Choose a menu from the list.")
        if slot in END_DATE_REQUIRED and not valid_until:
            raise ValueError(f"A custom {SLOTS[slot]} answer needs an end date, so it cannot go stale.")
        if valid_until and (approved_qa.parse_date(valid_until) is None or _past(valid_until)):
            raise ValueError("Choose an end date in the future.")
    else:
        raise ValueError("Choose Approved Q&A or Custom response.")
    with db.get_connection() as conn:
        conn.execute(
            """INSERT INTO answer_slots (owner_type, owner_id, slot, kind, qa_id, custom_text, menu_id, valid_until, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(owner_type, owner_id, slot) DO UPDATE SET kind=excluded.kind, qa_id=excluded.qa_id,
               custom_text=excluded.custom_text, menu_id=excluded.menu_id, valid_until=excluded.valid_until,
               updated_at=CURRENT_TIMESTAMP""",
            (owner_type, str(owner_id), slot, kind, qa_id, custom_text, menu_id, valid_until),
        )
        conn.commit()


def clear(owner_type: str, owner_id: str, slot: str) -> None:
    with db.get_connection() as conn:
        conn.execute("DELETE FROM answer_slots WHERE owner_type=? AND owner_id=? AND slot=?",
                     (owner_type, str(owner_id), slot))
        conn.commit()


def save_to_approved(owner_type: str, owner_id: str, slot: str, actor: str) -> str:
    """Copies a working Custom answer into Approved Q&A, then points the slot at it."""
    row = _row(owner_type, owner_id, slot)
    if not row or row["kind"] != "CUSTOM":
        raise ValueError("Only a Custom answer can be saved to Approved Q&A.")
    topic_id = owner_id if owner_type == "topic" else (db.get_instagram_source_route(int(owner_id)) or {}).get("topic_id")
    topic = db.get_instagram_topic(topic_id) if topic_id else None
    body = {"create": True, "question": f"{labels(topic_id).get(slot, 'Answer')}: {(topic or {}).get('name') or 'custom answer'}",
            "answer": row["custom_text"], "context": (topic or {}).get("qa_context") or "general",
            "valid_until": row["valid_until"], "mapped_role": slot if slot in SLOTS else "none",
            "blocks": [{"type": "menu", "menu_id": row["menu_id"]}] if row["menu_id"] else []}
    token = approved_qa.preview(body)
    saved = approved_qa.publish({**body, "qa_id": token["qa_id"], "preview_token": token["preview_token"]}, actor)
    save(owner_type, owner_id, slot, {"kind": "APPROVED_QA", "qa_id": saved["qa_id"]})
    return saved["qa_id"]


def classify(message: str, own: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The question's type in any language, and whether it names a specific case."""
    prompt = (
        "A customer sent this Instagram message to a food brand.\n"
        'Return exactly one JSON object: {"slot":"' + "|".join([*SLOTS, *(own or {}), "other"]) + '",'
        '"specific":true|false,"confidence":<0 to 1>}.\n\n'
        "RULES:\n"
        "- price: cost, rate or fees. details: what this is, or asking for more information.\n"
        "- booking: how to book, order or pay. date: when or where an event is held.\n"
        "- location: delivery areas, address or how to reach. other: anything else.\n"
        + "".join(f"- {key}: {json.dumps(label, ensure_ascii=False)}\n" for key, label in (own or {}).items()) +
        "- specific is true if the message names a particular place, date, quantity or custom request.\n"
        "- The message may be English, Hindi, Bengali or a mix, in any script.\n\n"
        f"MESSAGE:\n{message}\n"
    )
    raw, ok, reason = ai_responder._query_groq(
        prompt, model=config.GROQ_ROUTER_MODEL, response_format={"type": "json_object"},
        max_tokens=150, reasoning_effort="low",
    )
    if not ok:
        return {"ok": False, "reason": reason}
    try:
        parsed = json.loads(raw)
        slot = str(parsed.get("slot") or "other")
        return {"ok": True, "slot": slot if slot in SLOTS or slot in (own or {}) else None, "specific": parsed.get("specific") is True,
                "confidence": min(max(float(parsed.get("confidence") or 0), 0.0), 1.0)}
    except (AttributeError, TypeError, ValueError):
        return {"ok": False, "reason": "invalid_json"}


def outcome_for(result: Dict[str, Any], answer: Optional[Dict[str, Any]]) -> str:
    if not result.get("ok"):
        return "failed"
    if not result.get("slot"):
        return "other"
    if result["specific"]:
        return "specific"
    if result["confidence"] < WOULD_SEND_CONFIDENCE:
        return "low_confidence"
    if not answer:
        return "empty"
    return "expired" if answer["expired"] else "would_send"


class SlotShadow:
    def __init__(self):
        self._pool: Optional[ThreadPoolExecutor] = None
        self._pending = 0
        self._lock = threading.Lock()

    def record_shadow(self, message: str, route: Dict[str, Any]) -> None:
        """Queues the check and returns at once. Never raises: the reply is already decided."""
        if not config.VARIATION_SHADOW_ENABLED or not route.get("topic_id"):
            return
        try:
            with self._lock:
                if self._pending >= _SHADOW_QUEUE_LIMIT:
                    log.warning("Answer slot shadow queue is full; skipping this message.")
                    return
                self._pending += 1
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(max_workers=_SHADOW_WORKERS, thread_name_prefix="slot-shadow")
            self._pool.submit(self._run, message, dict(route))
        except Exception as e:
            with self._lock:
                self._pending = max(0, self._pending - 1)
            log.warning("Could not queue an answer slot check: %s", e)

    def _run(self, message: str, route: Dict[str, Any]) -> None:
        started = time.perf_counter()
        try:
            result = classify(message, own_questions(route.get("topic_id")))
            answer = effective(route, result["slot"]) if result.get("slot") else None
            with db.get_connection() as conn:
                conn.execute(
                    """INSERT INTO answer_slot_shadow
                       (routing_id, topic_id, user_message, slot, confidence, specific, outcome, answer_source, detail, latency_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (route.get("routing_id"), route.get("topic_id"), message, result.get("slot"), result.get("confidence"),
                     int(bool(result.get("specific"))), outcome_for(result, answer), (answer or {}).get("source"),
                     result.get("reason", "ok"), round((time.perf_counter() - started) * 1000, 1)),
                )
                conn.execute("DELETE FROM answer_slot_shadow WHERE timestamp < datetime('now', ?)", (f"-{KEEP_DAYS} days",))
                conn.commit()
        except Exception as e:
            log.warning("Answer slot check failed: %s", e)
        finally:
            with self._lock:
                self._pending = max(0, self._pending - 1)


slot_shadow = SlotShadow()


def shadow_report(days: int = 7, limit: int = 100) -> Dict[str, Any]:
    since = f"-{int(days)} days"
    with db.get_connection() as conn:
        counts = {row["outcome"]: row["n"] for row in conn.execute(
            "SELECT outcome, COUNT(*) AS n FROM answer_slot_shadow WHERE timestamp >= datetime('now', ?) GROUP BY outcome",
            (since,))}
        rows = conn.execute(
            """SELECT routing_id, topic_id, user_message, slot, confidence, specific, outcome, answer_source, detail, timestamp
               FROM answer_slot_shadow WHERE timestamp >= datetime('now', ?) ORDER BY id DESC LIMIT ?""",
            (since, int(limit)),
        ).fetchall()
    return {"days": int(days), "outcomes": counts, "rows": [dict(row) for row in rows],
            "shadow_enabled": config.VARIATION_SHADOW_ENABLED}


def move_gap(topic_id: str, slot: str, message: str, to: str) -> None:
    """Refiles a waiting question the AI typed wrongly. The AI itself does not learn from this."""
    if to not in labels(topic_id):
        raise ValueError("Choose a question from the list.")
    with db.get_connection() as conn:
        conn.execute(
            """UPDATE answer_slot_shadow SET slot=?, detail='moved_by_staff'
               WHERE topic_id=? AND slot=? AND user_message=? AND outcome IN ('empty', 'expired')""",
            (to, str(topic_id), slot, message),
        )
        conn.commit()


def dismiss_gap(topic_id: str, slot: str) -> None:
    """Sets this gap aside. A customer asking again brings it back."""
    with db.get_connection() as conn:
        conn.execute(
            """INSERT INTO answer_slot_dismissed (topic_id, slot) VALUES (?, ?)
               ON CONFLICT(topic_id, slot) DO UPDATE SET dismissed_at=CURRENT_TIMESTAMP""", (str(topic_id), str(slot)))
        conn.commit()


def needs_answers(days: int = 14) -> List[Dict[str, Any]]:
    """Questions with no usable answer, grouped by category and type. Fixed gaps drop off."""
    since = f"-{int(days)} days"
    with db.get_connection() as conn:
        rows = [dict(row) for row in conn.execute(
            """SELECT s.topic_id, t.name AS topic_name, s.slot, s.routing_id, s.user_message, s.timestamp
               FROM answer_slot_shadow s JOIN instagram_topics t ON t.topic_id = s.topic_id
               LEFT JOIN answer_slot_dismissed d ON d.topic_id = s.topic_id AND d.slot = s.slot
               WHERE s.outcome IN ('empty', 'expired') AND s.timestamp >= datetime('now', ?)
                 AND (d.dismissed_at IS NULL OR s.timestamp > d.dismissed_at)
               ORDER BY s.id DESC""",
            (since,),
        )]
    groups: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        answer = effective({"routing_id": row["routing_id"], "topic_id": row["topic_id"]}, row["slot"])
        if answer and not answer["expired"]:
            continue
        group = groups.setdefault((row["topic_id"], row["slot"]), {
            "topic_id": row["topic_id"], "topic_name": row["topic_name"], "slot": row["slot"],
            "label": labels(row["topic_id"]).get(row["slot"], "Other"),
            "choices": [{"slot": key, "label": label} for key, label in labels(row["topic_id"]).items()], "state": "expired" if answer else "empty", "count": 0, "examples": [],
        })
        group["count"] += 1
        if len(group["examples"]) < 5 and row["user_message"] not in {item["message"] for item in group["examples"]}:
            group["examples"].append({"message": row["user_message"], "routing_id": row["routing_id"]})
    return sorted(groups.values(), key=lambda item: -item["count"])
