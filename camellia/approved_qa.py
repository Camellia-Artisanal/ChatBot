"""Explicit Admin approval overlay. Runtime reads are resilient; writes fail closed."""
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import uuid
from dataclasses import replace
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from camellia import client_qa

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger(__name__)
_snapshots = {}
_lock = threading.RLock()
_preview_secret = secrets.token_bytes(32)
EDIT_FIELDS = {"question", "answer", "context", "aliases", "valid_from", "valid_until", "blocks", "hidden_aliases",
               "mapped_role", "mapped_questions"}
CANONICAL_CONTEXTS = {
    "general": "General", "savoury_deliveries": "Savoury Deliveries",
    "sondesh": "Sondesh", "bengali_home_table": "Bengali Home Table",
}


def context_options(rows=None):
    """Exact stored values, including retained versions, remain editable."""
    rows = overlays(strict=True) if rows is None else rows
    values = {entry.context for entry in client_qa.REGISTRY}
    values.update(row["context"] for row in rows.values() if isinstance(row.get("context"), str))
    with _database().get_connection() as conn:
        for row in conn.execute("SELECT snapshot_json FROM approved_qa_versions"):
            snapshot = json.loads(row["snapshot_json"])
            if isinstance(snapshot.get("context"), str):
                values.add(snapshot["context"])
        custom = {row["value"]: row["label"] for row in conn.execute("SELECT value, label FROM approved_qa_contexts ORDER BY label")}
    return [{"value": key, "label": label, "legacy": False} for key, label in CANONICAL_CONTEXTS.items()] + [
        {"value": key, "label": label, "legacy": False, "custom": True} for key, label in custom.items()
    ] + [
        {"value": value, "label": (value or "Empty context") + " (existing)", "legacy": True}
        for value in sorted(values - CANONICAL_CONTEXTS.keys() - custom.keys())
    ]


def context_usage(conn, value):
    """Answers and mappings still filed under a context; deleting it would strand them."""
    answers = sum(1 for entry in registry(_read(conn), active_only=False) if entry.context == value)
    mappings = sum(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE context=?", (value,)).fetchone()[0]
                   for table in ("instagram_source_qa_bundles", "instagram_story_qa_bundles"))
    return answers, mappings


def save_context(label, actor, value=None):
    """Add a client category, or rename one; built-in categories stay fixed."""
    label = " ".join(str(label or "").split())
    if not 2 <= len(label) <= 60:
        raise ValueError("Name the category in 2 to 60 characters")
    now = now_utc().isoformat()
    with _lock, _database().get_connection() as conn:
        if value is None:
            value = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
            taken = {option["value"] for option in context_options()} | set(CANONICAL_CONTEXTS)
            if not value or value in taken or any(option["label"].casefold() == label.casefold() for option in context_options()):
                raise ValueError("A category with this name already exists")
            conn.execute("INSERT INTO approved_qa_contexts VALUES (?,?,?,?,?,?)", (value, label, now, now, actor, actor))
        else:
            if conn.execute("UPDATE approved_qa_contexts SET label=?, updated_at=?, updated_by=? WHERE value=?",
                            (label, now, actor, value)).rowcount != 1:
                raise ValueError("Only categories added here can be renamed")
        conn.commit()
    return {"value": value, "label": label}


def delete_context(value):
    with _lock, _database().get_connection() as conn:
        if not conn.execute("SELECT 1 FROM approved_qa_contexts WHERE value=?", (value,)).fetchone():
            raise ValueError("Only categories added here can be deleted")
        answers, mappings = context_usage(conn, value)
        if answers or mappings:
            raise ValueError(f"This category is still used by {answers} answer(s) and {mappings} mapping(s). Move them to another category first.")
        conn.execute("DELETE FROM approved_qa_contexts WHERE value=?", (value,))
        conn.commit()
    return {"deleted": value}


def now_utc():
    return datetime.now(timezone.utc)


def _database():
    from camellia.database import db
    return db


def _read(conn):
    return {row["qa_id"]: dict(row) for row in conn.execute("SELECT * FROM approved_qa_overrides ORDER BY qa_id")}


def overlays(*, strict=False):
    database = _database()
    with _lock:
        try:
            with database.get_connection() as conn:
                result = _read(conn)
            _snapshots[database] = result
            return result
        except Exception:
            if strict:
                raise
            log.exception("Approved Q&A overlay unavailable; retaining last known good snapshot or static source")
            return _snapshots.get(database, {})


def parse_date(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("Validity must be an ISO date/time in Asia/Kolkata")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid validity date/time") from exc
    # datetime-local from the Admin form explicitly represents IST.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(timezone.utc)


def status(row, at=None):
    at = at or now_utc()
    if row.get("status") == "ARCHIVED":
        return "ARCHIVED"
    start, end = parse_date(row.get("valid_from")), parse_date(row.get("valid_until"))
    if start and at < start:
        return "SCHEDULED"
    if end and at > end:
        return "EXPIRED"
    return "ACTIVE"


def _edited_blocks(base, answer):
    """Keep read-only media slots in order while editing only the text copy."""
    old = "\n\n".join(block.value for block in base.blocks if block.kind == "TEXT")
    opcodes = SequenceMatcher(None, old, answer, autojunk=False).get_opcodes()
    def boundary(offset):
        for tag, a, b, c, d in opcodes:
            if a <= offset <= b:
                if tag == "equal":
                    return c + offset - a
                return c if b == a else c + round((offset - a) * (d - c) / (b - a))
        return len(answer)
    blocks, offset, previous = [], 0, 0
    texts = [block for block in base.blocks if block.kind == "TEXT"]
    seen = 0
    for block in base.blocks:
        if block.kind != "TEXT":
            blocks.append(block)
            continue
        offset += len(block.value)
        seen += 1
        end = len(answer) if seen == len(texts) else boundary(offset)
        blocks.append(client_qa.ResponseBlock("TEXT", answer[previous:end]))
        previous = end
        offset += 2
    return tuple(blocks) or (client_qa.ResponseBlock("TEXT", answer),)


def _entry(qa_id, row):
    entry = _base_entry(qa_id, row)
    sequence = json.loads(row.get("blocks_json") or "null") or []
    if sequence:
        # The answer box is sent first, then the blocks (18 Oct: the client put
        # the intro in the answer box and it was silently skipped).
        # "catalogue:<menu_id>" is resolved to the menu's current image at send time.
        lead = (client_qa.ResponseBlock("TEXT", entry.answer),) if (entry.answer or "").strip() else ()
        entry = replace(entry, blocks=lead + tuple(
            client_qa.ResponseBlock("TEXT", block["text"]) if block["type"] == "text"
            else client_qa.ResponseBlock("MENU", "catalogue:" + block["menu_id"])
            if block["type"] == "menu"
            else client_qa.ResponseBlock("REEL", block["media_id"]) for block in sequence))
    return entry


def hidden_aliases(rows):
    """Built-in question variations the client removed, normalised, per answer."""
    return {key: {client_qa._normalise_match_text(v) for v in json.loads(row.get("hidden_aliases_json") or "[]")}
            for key, row in rows.items() if row.get("hidden_aliases_json") not in (None, "", "[]")}


def _builtin_aliases(qa_id, rows):
    hidden = hidden_aliases(rows).get(qa_id, set())
    return [v for v in client_qa._PARAPHRASE_ALIASES.get(qa_id, ()) if client_qa._normalise_match_text(v) not in hidden]


def _base_entry(qa_id, row):
    base = client_qa.REGISTRY_BY_ID.get(qa_id)
    if base is None:
        return client_qa.ApprovedQA(qa_id, row["question"], row["answer"], row["context"] or "general",
                                  blocks=(client_qa.ResponseBlock("TEXT", row["answer"]),))
    changes = {key: row[key] for key in ("question", "answer", "context") if row.get(key) is not None}
    if changes.get("answer", base.answer) != base.answer:
        changes["blocks"] = _edited_blocks(base, changes["answer"])
    return replace(base, **changes) if changes else base


def registry(rows=None, *, active_only=True, at=None):
    rows = overlays() if rows is None else rows
    ids = list(client_qa.REGISTRY_BY_ID) + [key for key in rows if key not in client_qa.REGISTRY_BY_ID]
    return tuple(_entry(key, rows.get(key, {})) for key in ids
                 if not active_only or status(rows.get(key, {}), at) == "ACTIVE")


def get(qa_id):
    return next((entry for entry in registry() if entry.qa_id == qa_id), None)


_ROLES = {
    "price": {"general_price", "home_table_price", "sondesh_price_client", "subscription_price",
              "bento_price_client"},  # Chicken Kosha price names its dish; not a generic price answer.
    "details": {"general_details", "home_table_details", "sondesh_details_client",
                "subscription_details_client", "bento_details_client"},
}
# Ad customers' short questions (18 Oct): "Pricing?", "Fees?", "Info?", "Interested".
_ROLE_WORDING = {"price": re.compile(r"\b(?:price|prices|pricing|cost|costs|rate|rates|how\s+much|charges?|fees?)\b", re.I),
                 "details": re.compile(r"\b(?:tell\s+me|more\s+(?:about|info|information|details)|details?|info|information"
                                       r"|what\s+is\s+this(?:\s+about)?|(?:i\s+am\s+|i'?m\s+)?interested)\b", re.I)}

# What a customer is asking on a mapped post/ad (31 Oct: "How much is the cost per
# head" handed off because only the saved wording matched). Customers word these in
# endless ways, so the type of question picks the one answer ticked for that type.
# _ROLE_WORDING stays narrower: it also guesses a client answer's own type.
_ASKED_WORDING = {
    "price": re.compile(r"\b(?:price|prices|pricing|cost|costs|rate|rates|how\s+much|charges?|fees?"
                        r"|kitna|kitne|kitni|da+m|taka|contribution|koto\s+(?:porbe|lagbe|pore|lage))\b", re.I),
    "details": _ROLE_WORDING["details"],
    "booking": re.compile(r"\b(?:book|booking|bookings|reserve|reservation|reservations|register|registration"
                          r"|sign\s+up|slots?)\b", re.I),
    "location": re.compile(r"\b(?:where|location|venue|address|located|directions?|how\s+to\s+reach|kothay|kahan)\b", re.I),
    "date": re.compile(r"\b(?:when|date|dates|what\s+time|timings?|which\s+day|kobe|kab)\b", re.I),
}
# These mention a price or place but need a person or another answer, so they
# never pick a ticked answer by type.
_NOT_ASKED_RE = re.compile(
    r"\b(?:discounts?|offers?|coupons?|refunds?|cancel\w*|complain\w*|complaint|deliver\w*|ship\w*|courier|dispatch"
    r"|allerg\w*|shelf\s+life|how\s+long|how\s+much\s+(?:time|longer|sugar|spice|weight|quantity)"
    r"|(?:kitna|kitne|kitni)\s+(?:time|samay|der|din))\b", re.I)
# A Custom mapping's note answers details questions, so its Topic answers skip them.
CUSTOM_FALLBACK_ROLES = frozenset({"price", "booking", "location", "date"})


def _asked_roles(message):
    if _NOT_ASKED_RE.search(message or "") or client_qa.custom_quote_request(message):
        return []  # A catering/hosting quote is priced by the manager.
    asked = [name for name, wording in _ASKED_WORDING.items() if wording.search(message or "")]
    return ["price"] if set(asked) == {"price", "details"} else asked  # "Price details" asks for the price.


def _role(entry):
    """Price or details answer: built-ins by ID, client-added ones by their question."""
    for role, ids in _ROLES.items():
        if entry.qa_id in ids:
            return role
    if entry.qa_id in client_qa.REGISTRY_BY_ID:
        return None
    roles = [role for role, wording in _ROLE_WORDING.items() if wording.search(entry.question or "")]
    return roles[0] if len(roles) == 1 else None


# "On mapped posts/ads, use for" (18 Oct). An answer ticked on a post/reel/ad
# answers these customer questions there only; customers with no ad never see
# them, so "How much does it cost?" can mean General price, Gurgaon or Kolkata
# Home Table depending on the post. NULL settings fall back to these defaults.
MAPPED_ROLES = {"price": "Price questions", "details": "Details questions", "booking": "Booking",
                "location": "Location", "date": "Date & time"}
DEFAULT_MAPPED_QUESTIONS = {
    "price": ["How much does it cost?", "What is the price?", "What is the price per person?",
              "What is the price per box?", "What is the price of a box?", "What's the price?",
              "Price?", "Price please", "Rate?", "Cost?", "How much?", "How much is it?",
              "How much per person?", "Pricing?", "Price details", "Price list", "Charges?", "Fees?"],
    "details": ["Tell me more", "Tell me more about this", "Hello, can I get more info on this?", "Can I get more info?",
                "More info", "More information please", "More details", "Details please", "Details?",
                "Can you share details?", "Info?", "What is this?", "What is this about?", "Interested", "I am interested"],
    "booking": ["How to book?", "How do I book?", "How can I book?", "I want to book", "How to reserve a seat?"],
    "location": ["Where is it?", "Location?", "Address please", "Where are you located?", "How to reach?"],
    "date": ["When is it?", "What is the date?", "What time does it start?", "Next date?", "Is this available?"],
}
PREVIEW_QUESTIONS = ["How much does it cost?", "What is the price per person?", "What is the price per box?",
                     "What is the price of a box?", "Tell me more",
                     "Hello, can I get more info on this?", "How to book?", "Where is it?", "When is it?"]


def mapped_settings(rows):
    """qa_id -> (role or None, raw questions, normalised questions) for every answer."""
    result = {}
    for entry in registry(rows, active_only=False):
        row = rows.get(entry.qa_id, {})
        role = row.get("mapped_role")
        role = None if role == "none" else role if role else _role(entry)
        stored = row.get("mapped_questions_json")
        questions = json.loads(stored) if stored is not None else list(DEFAULT_MAPPED_QUESTIONS.get(role, []))
        result[entry.qa_id] = (role, questions, {client_qa._normalise_match_text(q) for q in questions})
    return result


def mapped_collisions(qa_ids, rows=None):
    """Customer questions two ticked answers both claim on one post/ad."""
    rows = overlays() if rows is None else rows
    settings, names = mapped_settings(rows), {entry.qa_id: entry.question for entry in registry(rows, active_only=False)}
    owners = {}
    for key in dict.fromkeys(qa_ids):
        mine = {client_qa._normalise_match_text(q): q for q in settings.get(key, (None, [], set()))[1]}
        for normal, question in mine.items():
            owners.setdefault(normal, (question, []))[1].append(names.get(key, key))
    explicit = {names.get(key, key) for key in qa_ids if (rows.get(key) or {}).get("mapped_role")}
    # Only a use the client set can clash at save time; two guessed ones simply
    # never answer (the matcher refuses ambiguity), as before.
    return [(question, keys) for question, keys in owners.values() if len(keys) > 1 and explicit & set(keys)]


def preview_mapping(qa_ids, context):
    """What common ad questions get from this checklist (matcher only)."""
    rows = overlays()
    facts = {"last_topic": {"bengali_home_table": "home_table", "savoury_deliveries": "savoury"}.get(context, context)}
    facts["home_table_context"] = facts["last_topic"] == "home_table"
    out = []
    for question in PREVIEW_QUESTIONS:
        found = match(question, facts, rows=rows, allowed_qa_ids=set(qa_ids), product_fallback=True)
        out.append({"question": question, "answer": found.entry.question if found else None,
                    "qa_id": found.entry.qa_id if found else None})
    return {"rows": out, "collisions": [{"question": q, "answers": keys} for q, keys in mapped_collisions(qa_ids, rows)]}


def match(message, facts=None, *, rows=None, allowed_qa_ids=None, product_fallback=False, fallback_roles=None):
    rows = overlays() if rows is None else rows
    active = {entry.qa_id: entry for entry in registry(rows)}
    if allowed_qa_ids is not None:
        active = {key: entry for key, entry in active.items() if key in allowed_qa_ids}
    text = client_qa._normalise_match_text(message)
    exact = set()
    if allowed_qa_ids is not None:
        # Reuse canonical questions/source aliases, with the same whole-turn
        # ownership guard. All matching selected IDs participate in ambiguity.
        for key, entry in active.items():
            words = [entry.question, *_builtin_aliases(key, rows)]
            if (any(client_qa._normalise_match_text(word) == text for word in words)
                    and client_qa.can_candidate_own_turn(message, key)):
                exact.add(key)
    for key, row in rows.items():
        if key not in active:
            continue
        words = json.loads(row["aliases_json"])
        if row.get("question") is not None and (allowed_qa_ids is not None or heading_matches_everywhere(active[key])):
            words = [row["question"], *words]
        if (any(client_qa._normalise_match_text(word) == text for word in words)
                and (allowed_qa_ids is None or client_qa.can_candidate_own_turn(message, key))):
            exact.add(key)
    original = client_qa._match_base(message, facts)
    if allowed_qa_ids is not None:
        independent = client_qa._match_base(message, {})
        if independent and independent.entry.qa_id in active and usable_everywhere(active[independent.entry.qa_id]):
            exact.add(independent.entry.qa_id)
    if original and original.entry.qa_id in active:
        # Existing QA-S1 semantic authority also participates in ambiguity checks.
        exact.add(original.entry.qa_id)
    if product_fallback and allowed_qa_ids is not None and not exact:
        # "Can I get more info on this?" / "How much does it cost?" name no
        # product, so they resolve to the general answer. A checklist holding
        # one product's answer means "this" is that product (18 Oct: a
        # sondesh-only mapping handed Meta's ad button to the manager).
        # fallback_roles limits this to those question types.
        settings = mapped_settings(rows)
        usable = lambda role: fallback_roles is None or role in fallback_roles
        claimed = [key for key in active if text in settings.get(key, (None, [], set()))[2]
                   and usable(settings[key][0])]
        if len(claimed) > 1:
            return None  # Two ticked answers claim this question; the mapping save refuses this.
        exact.update(claimed)
        asked = _asked_roles(message)
        for base in (() if exact else (original, client_qa._match_base(message, {}), None)):
            role = next((name for name, ids in _ROLES.items() if base and base.entry.qa_id in ids), None)
            if base is None and len(asked) == 1:
                role = asked[0]  # "Tell me more" matches no built-in answer on its own.
            same = [key for key in active if role and usable(role) and settings.get(key, (None,))[0] == role]
            if len(same) == 1:
                exact.add(same[0])
                break
    if len(exact) != 1:
        return None
    key = exact.pop()
    if text in hidden_aliases(rows).get(key, ()):
        return None  # The client removed this exact wording from this answer.
    entry = active[key]
    kind = "story_bundle" if allowed_qa_ids is not None else original.match_kind if original and original.entry.qa_id == key else "admin_exact"
    assets = original.asset_keys if original and original.entry.qa_id == key else entry.asset_keys
    return client_qa.ApprovedMatch(entry, kind, assets)


# 23 Oct: "whats the price" and "Can you tell me more about this" belong to
# Savoury, but they are also the answers for customers with no ad and are
# ticked on posts of every category. They stay usable everywhere, exactly as
# General answers are, whatever category they are filed under.
SHARED_ANSWER_IDS = frozenset({"general_price", "general_details"})


def usable_everywhere(entry) -> bool:
    return entry.context == "general" or entry.qa_id in SHARED_ANSWER_IDS


def heading_matches_everywhere(entry) -> bool:
    """31 Oct: a client-added answer outside General is filed under a heading
    such as "tell me more"; a customer typing that heading with no post/ad must
    not get one category's answer. Its variations still match everywhere, and
    on posts/ads where it is ticked the heading still counts."""
    return entry.qa_id in client_qa.REGISTRY_BY_ID or usable_everywhere(entry)


def _record(entry, row):
    derived = status(row)
    end = parse_date(row.get("valid_until"))
    return {"qa_id": entry.qa_id, "question": entry.question, "answer": entry.answer, "shared": entry.qa_id in SHARED_ANSWER_IDS,
            "context": entry.context, "aliases": json.loads(row.get("aliases_json", "[]")),
            "source_aliases": _builtin_aliases(entry.qa_id, {entry.qa_id: row}),
            "hidden_aliases": json.loads(row.get("hidden_aliases_json") or "[]"),
            "sequence": json.loads(row.get("blocks_json") or "null") or [],
            **dict(zip(("mapped_role", "mapped_questions"), mapped_settings({entry.qa_id: row}).get(entry.qa_id, (None, []))[:2])),
            "variation_count": len({client_qa._normalise_match_text(v) for v in [*_builtin_aliases(entry.qa_id, {entry.qa_id: row}), *json.loads(row.get("aliases_json", "[]"))]}),
            "asset_keys": list(entry.asset_keys), "blocks": [vars(block) for block in entry.blocks],
            "status": derived, "expiring_soon": bool(derived == "ACTIVE" and end and end <= now_utc() + timedelta(days=7)),
            "valid_from": row.get("valid_from"), "valid_until": row.get("valid_until"),
            "record_type": row.get("record_type", "BASE_OVERRIDE"),
            **{key: row.get(key) for key in ("created_at", "updated_at", "created_by", "updated_by")}}


def listing():
    rows = overlays(strict=True)
    records = [_record(entry, rows.get(entry.qa_id, {})) for entry in registry(rows, active_only=False)]
    counts = {key: sum(record["status"] == key for record in records) for key in ("ACTIVE", "SCHEDULED", "EXPIRED", "ARCHIVED")}
    counts["EXPIRING_SOON"] = sum(record["expiring_soon"] for record in records)
    return {"records": records, "counts": counts, "timezone": "Asia/Kolkata", "contexts": context_options(rows),
            "mapped_roles": [{"value": key, "label": label, "questions": DEFAULT_MAPPED_QUESTIONS[key]} for key, label in MAPPED_ROLES.items()]}


def detail(qa_id):
    record = next((row for row in listing()["records"] if row["qa_id"] == qa_id), None)
    if record is None:
        raise KeyError(qa_id)
    with _database().get_connection() as conn:
        versions = [dict(row) for row in conn.execute(
            "SELECT * FROM approved_qa_versions WHERE qa_id=? ORDER BY id DESC", (qa_id,))]
    record["versions"] = [{**row, "snapshot": json.loads(row["snapshot_json"]),
                           "effective_snapshot": _record(_entry(qa_id, json.loads(row["snapshot_json"])), json.loads(row["snapshot_json"]))}
                          for row in versions]
    return record


def _digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _candidate(body, rows):
    if not isinstance(body, dict) or set(body) - EDIT_FIELDS - {"qa_id", "preview_token", "test_message", "create"}:
        raise ValueError("Only question, answer, context, variations, reply sequence and validity may be edited")
    qa_id = body.get("qa_id") or "admin_" + uuid.uuid4().hex
    if not isinstance(qa_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", qa_id):
        raise ValueError("Invalid Q&A ID")
    if qa_id not in client_qa.REGISTRY_BY_ID and qa_id not in rows and not qa_id.startswith("admin_"):
        raise ValueError("New approved IDs must start with admin_")
    if body.get("create") and (qa_id in rows or qa_id in client_qa.REGISTRY_BY_ID):
        raise ValueError("Duplicate Q&A ID")
    existing = rows.get(qa_id, {})
    base = client_qa.REGISTRY_BY_ID.get(qa_id)
    result = {"qa_id": qa_id, "record_type": "BASE_OVERRIDE" if base else "ADMIN_ADDED",
              "status": existing.get("status", "ACTIVE"), "aliases_json": existing.get("aliases_json", "[]"),
              "question": existing.get("question"), "answer": existing.get("answer"), "context": existing.get("context"),
              "valid_from": existing.get("valid_from"), "valid_until": existing.get("valid_until"),
              "blocks_json": existing.get("blocks_json"), "hidden_aliases_json": existing.get("hidden_aliases_json") or "[]",
              "mapped_role": existing.get("mapped_role"), "mapped_questions_json": existing.get("mapped_questions_json")}
    for key in ("question", "answer", "context"):
        if key in body:
            value = body[key]
            if not isinstance(value, str) or (key != "context" and not value.strip()) or (key == "question" and not client_qa._normalise_match_text(value)):
                raise ValueError("Question and approved answer are required")
            if len(value) > (20000 if key == "answer" else 2000):
                raise ValueError("Approved field is too long")
            if key == "context" and value not in {option["value"] for option in context_options(rows)}:
                raise ValueError("Choose an allowed Approved Q&A context")
            # Sending unchanged base copy must not manufacture an override.
            result[key] = None if base and value == getattr(base, key) else value
    if not base and (not result["question"] or not result["answer"]):
        raise ValueError("Question and approved answer are required")
    if "aliases" in body:
        aliases = body["aliases"]
        if not isinstance(aliases, list) or len(aliases) > 100 or any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in aliases):
            raise ValueError("Question variations must be a list of nonempty questions")
        normal = [client_qa._normalise_match_text(v) for v in aliases]
        if not all(normal) or len(set(normal)) != len(normal):
            raise ValueError("Duplicate question variations")
        result["aliases_json"] = json.dumps(aliases, ensure_ascii=False)
    if "blocks" in body:
        from camellia import custom_response_sequence
        blocks = custom_response_sequence.validate(body["blocks"])
        result["blocks_json"] = json.dumps(blocks, ensure_ascii=False) if blocks else None
    if "mapped_role" in body:
        role = body["mapped_role"]
        if role not in (None, "none", *MAPPED_ROLES):
            raise ValueError("Choose what this answer is used for on mapped posts/ads")
        result["mapped_role"] = role
    if "mapped_questions" in body:
        questions = body["mapped_questions"]
        if not isinstance(questions, list) or len(questions) > 60 or any(not isinstance(q, str) or not q.strip() or len(q) > 200 for q in questions):
            raise ValueError("Mapped post/ad questions must be a list of up to 60 short questions")
        normal = [client_qa._normalise_match_text(q) for q in questions]
        if not all(normal) or len(set(normal)) != len(normal):
            raise ValueError("Duplicate mapped post/ad questions")
        result["mapped_questions_json"] = json.dumps([q.strip() for q in questions], ensure_ascii=False)
    if "hidden_aliases" in body:
        builtin = {client_qa._normalise_match_text(v): v for v in client_qa._PARAPHRASE_ALIASES.get(qa_id, ())}
        hidden = body["hidden_aliases"]
        if not isinstance(hidden, list) or any(not isinstance(v, str) or client_qa._normalise_match_text(v) not in builtin for v in hidden):
            raise ValueError("Only built-in question variations can be removed")
        result["hidden_aliases_json"] = json.dumps(sorted({builtin[client_qa._normalise_match_text(v)] for v in hidden}), ensure_ascii=False)
    for key in ("valid_from", "valid_until"):
        if key in body:
            date = parse_date(body[key])
            result[key] = date.isoformat() if date else None
    start, end = parse_date(result["valid_from"]), parse_date(result["valid_until"])
    if start and end and end < start:
        raise ValueError("Valid Until must be on or after Valid From")
    if base and result["answer"] is not None:
        if any(marker in result["answer"] for marker in client_qa._PLACEHOLDER_ASSETS):
            raise ValueError("Existing asset references are read-only")
        if base.context == "home_table" and client_qa.HOME_TABLE_VIDEO_ANCHOR in base.answer and client_qa.HOME_TABLE_VIDEO_ANCHOR not in result["answer"]:
            raise ValueError("Preserve the Home Table Reel anchor")
    if not base and re_search_asset(result["answer"]):
        raise ValueError("New approved Q&A must be text-only")
    return result


def re_search_asset(answer):
    return bool(re.search(r"\[.*(?:IMAGE|ASSET|VIDEO).*\]", answer, re.I))


def _conflicts(candidate, rows):
    entry = _entry(candidate["qa_id"], candidate)
    words = [entry.question, *json.loads(candidate["aliases_json"])]
    others = {e.qa_id: e for e in registry(rows) if e.qa_id != entry.qa_id}
    for word_index, word in enumerate(words):
        if word_index == 0 and not heading_matches_everywhere(entry):
            continue  # A category answer's heading is a label, not customer wording.
        normalized = client_qa._normalise_match_text(word)
        for key, other in others.items():
            other_words = ([other.question] if heading_matches_everywhere(other) else []) + json.loads(
                rows.get(key, {}).get("aliases_json", "[]"))
            if any(client_qa._normalise_match_text(v) == normalized for v in other_words):
                # Immutable source has a few intentionally context-dependent duplicate primary questions.
                if word_index == 0 and entry.qa_id in client_qa.REGISTRY_BY_ID and candidate.get("question") is None:
                    continue
                raise ValueError(f'This wording already matches: {key} ("{word}")')
        original = client_qa._match_base(word)
        if original and original.entry.qa_id != entry.qa_id and original.entry.qa_id in others:
            if word_index == 0 and entry.qa_id in client_qa.REGISTRY_BY_ID and candidate.get("question") is None:
                continue
            raise ValueError(f'This wording already matches: {original.entry.qa_id} ("{word}")')


def _token(candidate, rows):
    data = {"candidate": candidate, "registry": _digest(rows), "expires": int(now_utc().timestamp()) + 900}
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8").hex()
    signature = hmac.new(_preview_secret, encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def preview(body):
    rows = overlays(strict=True)
    candidate = _candidate(body, rows)
    _conflicts(candidate, rows)
    proposed = {**rows, candidate["qa_id"]: candidate}
    message = body.get("test_message") or _entry(candidate["qa_id"], candidate).question
    tested = match(message, rows=proposed)
    return {"qa_id": candidate["qa_id"], "preview_token": _token(candidate, rows),
            "current": _record(_entry(candidate["qa_id"], rows.get(candidate["qa_id"], {})), rows.get(candidate["qa_id"], {}))
            if candidate["qa_id"] in rows or candidate["qa_id"] in client_qa.REGISTRY_BY_ID else None,
            "proposed": _record(_entry(candidate["qa_id"], candidate), candidate),
            "test": {"matched_id": tested.entry.qa_id if tested else None,
                     "answer": tested.entry.answer if tested else None,
                     "assets": list(tested.asset_keys) if tested else [],
                     "status": status(candidate)}}


def test(message):
    rows = overlays(strict=True)
    result = match(message, rows=rows)
    original = client_qa._match_base(message)
    key = result.entry.qa_id if result else original.entry.qa_id if original else None
    return {"matched_id": result.entry.qa_id if result else None,
            "status": status(rows.get(key, {})) if key else "NO_MATCH",
            "answer": result.entry.answer if result else None, "assets": list(result.asset_keys) if result else []}


def _save(conn, candidate, rows, actor, reason):
    now = now_utc().isoformat()
    key = candidate["qa_id"]
    old = rows.get(key)
    if old is None and key in client_qa.REGISTRY_BY_ID:
        old = {"qa_id": key, "record_type": "BASE_OVERRIDE", "question": None, "answer": None,
               "context": None, "aliases_json": "[]", "status": "ACTIVE", "valid_from": None, "valid_until": None}
    for snapshot, label in ((old, "Before: " + reason),):
        if snapshot is not None:
            conn.execute("INSERT INTO approved_qa_versions (qa_id,snapshot_json,changed_at,changed_by,change_reason) VALUES (?,?,?,?,?)",
                         (key, json.dumps(snapshot, ensure_ascii=False), now, actor, label))
    candidate = {**candidate, "created_at": (old or {}).get("created_at") or now,
                 "created_by": (old or {}).get("created_by") or actor, "updated_at": now, "updated_by": actor}
    columns = ("qa_id", "record_type", "question", "answer", "context", "aliases_json", "status", "valid_from", "valid_until", "created_at", "updated_at", "created_by", "updated_by", "blocks_json", "hidden_aliases_json", "mapped_role", "mapped_questions_json")
    conn.execute("INSERT INTO approved_qa_overrides (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ") ON CONFLICT(qa_id) DO UPDATE SET " + ",".join(key + "=excluded." + key for key in columns if key != "qa_id"),
                 tuple(candidate[key] for key in columns))
    conn.execute("INSERT INTO approved_qa_versions (qa_id,snapshot_json,changed_at,changed_by,change_reason) VALUES (?,?,?,?,?)",
                 (key, json.dumps(candidate, ensure_ascii=False), now, actor, reason))


def publish(body, actor):
    try:
        encoded, signature = body.get("preview_token", "").split(".")
        if not hmac.compare_digest(signature, hmac.new(_preview_secret, encoded.encode(), hashlib.sha256).hexdigest()):
            raise ValueError()
        approval = json.loads(bytes.fromhex(encoded))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Preview / Test is required before Publish") from None
    with _lock, _database().get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = _read(conn)
        candidate = _candidate(body, rows)
        if approval["expires"] < now_utc().timestamp() or approval["registry"] != _digest(rows) or approval["candidate"] != candidate:
            raise ValueError("The draft or approved registry changed. Preview / Test again before Publish")
        _conflicts(candidate, rows)
        _save(conn, candidate, rows, actor, "Published approved Q&A")
    return detail(candidate["qa_id"])


def archive_or_restore(qa_id, actor, *, restore=False, version_id=None):
    with _lock, _database().get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = _read(conn)
        if qa_id not in rows and qa_id not in client_qa.REGISTRY_BY_ID:
            raise KeyError(qa_id)
        candidate = _candidate({"qa_id": qa_id}, rows)
        if version_id is not None:
            version = conn.execute("SELECT snapshot_json FROM approved_qa_versions WHERE qa_id=? AND id=?", (qa_id, version_id)).fetchone()
            if not version:
                raise KeyError(version_id)
            snapshot = json.loads(version[0])
            defaults = {"hidden_aliases_json": "[]"}  # Versions saved before 18 Oct lack it.
            candidate.update({key: snapshot.get(key, defaults.get(key)) for key in candidate if key not in {"qa_id", "record_type"}})
        candidate["status"] = "ACTIVE" if restore else "ARCHIVED"
        if restore:
            _conflicts(candidate, rows)
        _save(conn, candidate, rows, actor, "Restored version" if version_id is not None else "Restored" if restore else "Archived")
    return detail(qa_id)


# Built-in rules beyond the variation list must not answer a removed wording either.
client_qa._hidden_aliases = lambda: hidden_aliases(_snapshots.get(_database(), {}))
