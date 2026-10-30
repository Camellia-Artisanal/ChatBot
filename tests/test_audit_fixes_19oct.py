"""Bugs found by the 19 Oct code audit, each pinned by its failing input."""
import uuid
from unittest.mock import patch

import pytest

from camellia import app as app_module
from camellia.handoff import is_human_help_request
from camellia.instagram_routing import decode_route, route_for_inbound
from camellia.responder import responder
from camellia.session_memory import session_memory

from test_ad_mapped_source_context import ad_customer, _ad_entry, _mapped_source, _process  # noqa: F401
from test_instagram_ads_inventory import item, route, store, sync  # noqa: F401
from test_ads_inherit_post_answers import _twins, OLD_TWIN, NEW_TWIN


def _reply(message):
    uid = "audit_" + uuid.uuid4().hex[:8]
    session_memory.clear_session(uid, "instagram")
    return responder.match_and_respond(message, user_id=uid, channel="instagram", referral_source="ADS")[2]


@pytest.mark.parametrize("message", [
    "Tell me more about Sondesh for someone special", "what is the price of sondesh for my staff",
    "is the sondesh box enough for 4 people?", "I want to gift sondesh to someone in Kolkata",
    "Can you get me the sondesh price", "how long to reach to Noida",
])
def test_product_questions_are_not_requests_for_a_person(message):
    assert not is_human_help_request(message)


@pytest.mark.parametrize("message", [
    "human", "I want to talk to a human", "can someone call me", "speak to someone please",
    "connect me to your team", "I need customer support", "get back to me", "can I talk to the manager",
])
def test_requests_for_a_person_still_hand_off(message):
    assert is_human_help_request(message)


def test_minimum_order_for_other_products_is_not_the_sondesh_rule():
    assert _reply("what is the minimum order for bento?") != "minimum_sondesh_quantity"
    assert _reply("minimum order quantity for chicken kosha?") != "minimum_sondesh_quantity"
    assert _reply("Why is the minimum order 9 pieces for sondesh?") == "minimum_sondesh_quantity"


def test_sondesh_shelf_life_is_not_the_dispatch_answer():
    assert _reply("How long does sondesh stay fresh?") != "sondesh_dispatch_time"
    assert _reply("how long will sondesh last") != "sondesh_dispatch_time"
    assert _reply("How long does sondesh delivery take?") == "sondesh_dispatch_time"


def test_not_ready_to_order_is_not_a_finalised_order():
    assert _reply("I'm not ready to order yet, just looking") != "order_next_steps"
    assert _reply("I am ready to order") == "order_next_steps"


def test_a_new_ad_does_not_inherit_the_previous_ads_title(ad_customer):
    sender, facts, db = ad_customer
    facts["ad_title"] = "Bengali Bento meal box"
    facts["ad_product"] = "bento"
    _mapped_source(ad_customer, "post", "999000111", topic_name="General",
                   response_mode="APPROVED_QA", approved_qa_id="general_price")
    _process(ad_customer, "How much does it cost?", _ad_entry(sender, "999000111", "How much does it cost?", title=""))
    stored = db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]
    assert facts.get("ad_product") is None
    assert facts.get("client_qa_id") == "general_price"
    assert not db.get_active_handoff("instagram", sender) and "handoff" not in stored["template_id"]


def test_always_forward_words_beat_the_custom_note(ad_customer):
    ad = "120246593316020540"
    _mapped_source(ad_customer, "post", ad, topic_name="Sondesh Delivery Delhi NCR")
    sender, facts, db = ad_customer
    message = "what is the price? I have a complaint about my last order"
    _process(ad_customer, message, _ad_entry(sender, ad, message))
    stored = db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]
    assert stored["template_id"] != "source_description"


class _Deferred:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))


def test_a_mapped_shared_post_running_late_is_not_handed_off(ad_customer):
    sender, facts, db = ad_customer
    mapped = _mapped_source(ad_customer, "post", None)
    pairing = db.start_post_pairing(mapped["routing_id"], 600, "admin")
    db.bind_post_pairing(pairing["id"], "dm-alias-1")
    db.enqueue_instagram_inbound_job("earlier-text", sender, "_process_instagram_event",
                                     {"args": [sender, "hi", None], "kwargs": {}})
    assert db.claim_instagram_inbound_jobs(limit=1)
    event = {"sender": {"id": sender}, "timestamp": 1700000000500,
             "message": {"mid": "share-mid", "attachments": [{"type": "ig_post", "payload": {"ig_post_media_id": "dm-alias-1"}}]}}
    app_module._dispatch_instagram_payload({"entry": [{"id": "acct", "messaging": [event]}]},
                                           app_module._DurableInstagramTasks(_Deferred()))
    with db.get_connection() as conn:
        conn.execute("UPDATE instagram_pending_media_events SET expires_at=0")
        conn.commit()
    with patch.object(app_module, "send_instagram_reply", return_value={"status": "sent", "message_id": "m"}), \
         patch.object(app_module.escalator, "handle_escalation"):
        app_module._finalize_expired_unmapped_posts()
    assert db.get_active_handoff("instagram", sender) is None


def test_a_disabled_mapping_is_not_inherited_by_twin_ads(store, monkeypatch):
    reel = _twins(store, monkeypatch)
    store.bind_instagram_ad(OLD_TWIN, reel["routing_id"])
    off = decode_route(store.get_instagram_source_route(reel["routing_id"]))
    off["active"] = False
    store.save_instagram_source_route(off, routing_id=reel["routing_id"])
    assert route_for_inbound(ad_id=NEW_TWIN, media_id=None)[0] is None


def test_admin_resolve_button_and_route_tab_survive_odd_input():
    source = open(app_module.__file__, encoding="utf-8").read()
    # Customer text with an apostrophe broke (or injected into) the inline handler.
    assert "openResolveModal(${e.id}, '${escapeHtml(e.user_message)}')" not in source
    assert "openResolveModal(${e.id}, this.dataset.userMsg)" in source
    # The Archived button was removed; the Active tab must not throw on it.
    assert "getElementById('igRouteTabArchivedBtn').classList" not in source


@pytest.mark.parametrize("answered", [True, False])
def test_an_unmapped_share_whose_question_was_answered_is_not_handed_off(ad_customer, answered):
    # 20 Oct, real_shagun0707: shared an unmapped post + "What is the price of a
    # box?"; the price was answered, then the share's timer handed off mid-reply.
    sender, facts, db = ad_customer
    event = {"sender": {"id": sender}, "timestamp": 1700000000500,
             "message": {"mid": "share-" + sender, "attachments": [{"type": "ig_post", "payload": {"ig_post_media_id": "unmapped-77"}}]}}
    app_module._dispatch_instagram_payload({"entry": [{"id": "acct", "messaging": [event]}]},
                                           app_module._DurableInstagramTasks(_Deferred()))
    if answered:
        db.log_conversation(sender, "instagram", "What is the price of a box?", "Sondesh price…", True, "sondesh_price_detailed")
    with db.get_connection() as conn:
        conn.execute("UPDATE instagram_pending_media_events SET expires_at=0")
        conn.commit()
    with patch.object(app_module, "send_instagram_reply", return_value={"status": "sent", "message_id": "m"}), \
         patch.object(app_module.escalator, "handle_escalation"):
        app_module._finalize_expired_unmapped_posts()
    assert bool(db.get_active_handoff("instagram", sender)) is (not answered)
