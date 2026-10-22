"""Settings that make the bot hand customers to the manager without anyone noticing.

23 Oct: a live scan found 40 such settings (expired weekly menu, mappings that
tick expired answers, Custom notes promising a menu that is not set). None
showed anywhere the client looks. These checks back the admin dashboard's
"Setup check" card and the /health warnings.
"""
import json
from typing import Any, Dict, List

from camellia import approved_qa
from camellia.monitoring import get_logger

log = get_logger("camellia.setup_check")


def _label(route: Dict[str, Any]) -> str:
    where = route.get("canonical_url") or route.get("media_id") or route.get("ad_id") or ""
    topic = route.get("routing_topic") or route.get("source_type") or "mapping"
    return f"#{route.get('routing_id')} {topic} {where}".strip()


def _ids(route: Dict[str, Any]) -> List[str]:
    value = route.get("approved_qa_ids")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = []
    return [str(item) for item in (value or [])]


def check(database) -> List[Dict[str, Any]]:
    """One entry per problem kind: what happens, where to fix it, which items."""
    problems: List[Dict[str, Any]] = []

    def add(kind, message, fix, items):
        if items:
            problems.append({"kind": kind, "count": len(items), "message": message, "fix": fix, "items": items[:25]})

    try:
        from camellia.admin_menu_review import _weekly_view
        weekly = _weekly_view()
        if not weekly.get("current"):
            add("weekly_menu", "No current weekly menu: price questions from customers with no ad go to the manager.",
                "Menu → Weekly menu: upload and publish this week's menu.", [f"Today is {weekly.get('today')}"])
    except Exception:
        log.exception("Setup check: weekly menu state unavailable.")

    try:
        records = approved_qa.listing()["records"]
    except Exception:
        log.exception("Setup check: Approved Q&A unavailable.")
        return problems
    status = {record["qa_id"]: record["status"] for record in records}
    question = {record["qa_id"]: record["question"] for record in records}
    # Ticked answers live in the bundle tables; read routes the way the admin list does.
    from camellia.instagram_routing import decode_route
    from camellia.instagram_source_qa import route_contract as source_contract
    from camellia.instagram_story_qa import route_contract as story_contract
    routes = [source_contract(database, story_contract(database, decode_route(r)))
              for r in database.list_instagram_source_routes() if r.get("active") and not r.get("archived_at")]

    dead, empty, menu = [], [], []
    for route in routes:
        ids = _ids(route)
        if str(route.get("response_mode") or "CUSTOM").upper() == "APPROVED_QA":
            gone = [i for i in ids if status.get(i) != "ACTIVE"]
            if gone:
                dead.append(f"{_label(route)}: {', '.join(question.get(i, i) + ' (' + status.get(i, 'missing').lower() + ')' for i in gone)}")
            if not ids:
                empty.append(_label(route))
        elif ("menu" in str(route.get("source_description") or "").lower()
              and str(route.get("menu_mode") or "INHERIT").upper() == "NONE"):
            menu.append(_label(route))
    add("dead_answers", "Mappings tick answers that have expired or been archived, so those questions go to the manager.",
        "Approved Q&A: renew the answer's dates, or untick it on these mappings.", dead)
    add("no_answers", "Mappings set to Approved Q&A with nothing ticked.",
        "Posts & Reels / Stories: tick the answers each post should give.", empty)
    add("menu_promised", "Custom replies say \"menu below\" but no menu is selected, so customers get the text only.",
        "Posts & Reels: pick a menu on these mappings, or switch them to Approved Q&A.", menu)

    expiring = [f"{r['question']} (until {r.get('valid_until')})" for r in records if r.get("expiring_soon")]
    add("answers_expiring", "Approved answers expire soon; mappings that tick them will start handing off.",
        "Approved Q&A → Expiring Soon: extend or replace them.", expiring)
    return problems
