"""shubhojitdasgupta, 19 Oct: a Sondesh ad customer asked for the menu and got none.

The post's Custom note promised "menu below" with no menu set; "Can I see the
menu please" repeated the note, and in a Sondesh chat it asked "which asset?".
"""
import uuid

import pytest

from camellia.instagram_routing import decode_route
from camellia.ad_gate import EntryPoint
from camellia.responder import responder
from camellia.session_memory import session_memory

from test_ad_mapped_source_context import ad_customer, _ad_entry, _mapped_source, _process  # noqa: F401

AD = "120246633060420540"


def _images(send):
    return [call.kwargs.get("image_url") or (call.args[2] if len(call.args) > 2 else None) for call in send.call_args_list]


@pytest.mark.parametrize("mode", ["CUSTOM", "APPROVED_QA"])
def test_menu_request_on_a_post_with_no_menu_sends_the_menu_cards(ad_customer, mode):
    sender, facts, db = ad_customer
    ids = ["sondesh_price_client", "sondesh_details_client", "sondesh_delivery_time"] if mode == "APPROVED_QA" else None
    saved = _mapped_source(ad_customer, "post", AD, topic_name="sondesh delivery delhi NCR", response_mode=mode, approved_qa_ids=ids)
    route = decode_route(db.get_instagram_source_route(saved["routing_id"]))
    route.update(menu_mode="NONE", menu_id=None, source_description="Sharing the menu for aamar collection below")
    db.save_instagram_source_route(route, routing_id=saved["routing_id"])
    _process(ad_customer, "Hello, can I get more info on this?", _ad_entry(sender, AD, "Hello, can I get more info on this?"))
    send = _process(ad_customer, "Can i see the menu please", EntryPoint(source="typed"))
    assert any(_images(send)), "no menu image was sent"
    assert not db.get_active_handoff("instagram", sender)


@pytest.mark.parametrize("message", [
    "send sondesh menu", "sondesh menu please", "sweets menu", "mishti menu pls", "show me sondesh menu please",
])
def test_sondesh_menu_requests_send_both_collections(message):
    uid = "menu_" + uuid.uuid4().hex[:8]
    session_memory.clear_session(uid, "instagram")
    _text, _ok, template, images = responder.match_and_respond(message, user_id=uid, channel="instagram", referral_source="ADS")
    assert template != "asset_request_clarify" and images and len(images) == 2


def test_menu_after_a_sondesh_answer_sends_the_menus_not_which_asset():
    uid = "menu_" + uuid.uuid4().hex[:8]
    session_memory.clear_session(uid, "instagram")
    responder.match_and_respond("Tell me more about sondesh", user_id=uid, channel="instagram", referral_source="ADS")
    _text, _ok, template, images = responder.match_and_respond("Can i see the menu please", user_id=uid, channel="instagram", referral_source="ADS")
    assert template != "asset_request_clarify" and images


def test_other_asset_requests_still_resolve():
    uid = "menu_" + uuid.uuid4().hex[:8]
    session_memory.clear_session(uid, "instagram")
    _text, _ok, template, images = responder.match_and_respond("send me the subscription chart", user_id=uid, channel="instagram", referral_source="ADS")
    assert template == "asset_request_subscription_chart" and images
