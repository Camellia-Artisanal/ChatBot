"""A Custom mapping (the client's note only) must not hand every question to the manager.

On 2026-10-17 customers replying "How much does it cost?" to a sondesh ad got the
manager handoff: the ad's Reel was mapped with only a welcome note. Two rules:
- the mapping's Topic can name default approved answers, sent word for word;
- otherwise the note itself is sent once instead of the handoff.
"""
import pytest

from camellia import client_qa, instagram_routing
from camellia.instagram_routing import InstagramTopicError

from test_ad_mapped_source_context import ad_customer, _ad_entry, _mapped_source, _process  # noqa: F401

AD = "120246593316020540"
NOTE = "Operator-authored post description."


def _custom_route(ad_customer, topic_answers=None):
    route = _mapped_source(ad_customer, "post", AD, topic_name="Sondesh Delivery Delhi NCR")
    if topic_answers is not None:
        instagram_routing.update_topic(route["topic_id"], default_approved_qa_ids=topic_answers)
    return route


def _ask(ad_customer, message):
    sender = ad_customer[0]
    return _process(ad_customer, message, _ad_entry(sender, AD, message))


def test_topic_answers_let_a_custom_mapping_answer_price_word_for_word(eligible_weekly_asset, ad_customer):
    _custom_route(ad_customer, ["sondesh_price_client", "sondesh_details_client"])
    sender, facts, db = ad_customer
    db.save_menu_image_draft("weekly_vegetarian", "/uploads/test-reviewed-current-weekly.jpeg", "fixture")
    db.publish_menu_image("weekly_vegetarian")
    _ask(ad_customer, "How much does it cost?")
    assert facts["client_qa_id"] == "sondesh_price_client"
    assert facts["topic_default_qa_match"] == "matched"
    assert db.get_active_handoff("instagram", sender) is None
    stored = db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]
    assert stored["bot_response"] == client_qa.effective_entry("sondesh_price_client").answer


def test_without_topic_answers_the_note_is_sent_once_instead_of_the_manager(ad_customer):
    _custom_route(ad_customer)
    sender, facts, db = ad_customer
    send = _ask(ad_customer, "How much does it cost?")
    assert db.get_active_handoff("instagram", sender) is None
    stored = db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]
    assert stored["bot_response"] == NOTE and stored["template_id"] == "source_description"
    # The mapping's selected menu follows the note.
    assert any(call.kwargs.get("image_url") for call in send.call_args_list)
    # A second unanswered question goes to the manager as before.
    _ask(ad_customer, "Is the box recyclable?")
    assert db.get_active_handoff("instagram", sender) is not None


@pytest.mark.parametrize("topic_answers", [None, ["sondesh_price_client", "sondesh_details_client"]])
def test_meta_more_info_button_gets_the_note(ad_customer, topic_answers):
    # 15 customers in 30 days tapped this ad button and were all handed off.
    _custom_route(ad_customer, topic_answers)
    sender, _facts, db = ad_customer
    _ask(ad_customer, "Hello, can I get more info on this?")
    assert db.get_active_handoff("instagram", sender) is None
    assert db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]["bot_response"] == NOTE


def test_explicit_human_request_still_reaches_the_manager(ad_customer):
    _custom_route(ad_customer)
    sender, _facts, db = ad_customer
    _ask(ad_customer, "I want to talk to a human")
    assert db.get_active_handoff("instagram", sender) is not None


def test_topic_answers_must_be_live_approved_qa(ad_customer):
    route = _custom_route(ad_customer)
    with pytest.raises(InstagramTopicError, match="Approved Q&A"):
        instagram_routing.update_topic(route["topic_id"], default_approved_qa_ids=["not_a_real_answer"])
    updated = instagram_routing.update_topic(route["topic_id"], default_approved_qa_ids=["sondesh_price_client"])
    assert instagram_routing.decode_topic(updated)["default_approved_qa_ids"] == ["sondesh_price_client"]
