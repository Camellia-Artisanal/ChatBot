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


def test_price_question_without_its_ad_gets_the_general_price_answer(ad_customer):
    # 2026-10-17: "How much does it cost?" arrived from an ad with no ad ID and
    # was handed to the manager. Unknown product -> general approved answer.
    sender, facts, db = ad_customer
    message = "How much does it cost?"
    from camellia.ad_gate import describe_entry_point
    entry = describe_entry_point({"sender": {"id": sender}, "message": {"text": message}})
    _process(ad_customer, message, entry)
    assert facts["client_qa_id"] == "general_price"
    assert db.get_active_handoff("instagram", sender) is None


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


@pytest.mark.parametrize("mapped", [False, True])
def test_more_info_button_gets_the_general_answer_not_the_manager(ad_customer, mapped):
    # 18 Oct 07:35: a new ad's post was unmapped and the button text was handed
    # off; it still was after the client mapped the post to the general bundle.
    if mapped:
        _mapped_source(ad_customer, "post", AD, topic_name="General", response_mode="APPROVED_QA",
                       approved_qa_ids=["general_price", "general_details", "price_is_high"],
                       source_context="general")
    sender, facts, db = ad_customer
    _ask(ad_customer, "Hello, can I get more info on this?")
    assert facts["client_qa_id"] == "general_details"
    assert db.get_active_handoff("instagram", sender) is None


def test_every_handoff_says_only_the_clients_handoff_text(ad_customer):
    # 18 Oct 16:59: "How much does a box cost?" arrived without its ad and was
    # handed off with different wording ("Let me have the team confirm").
    from camellia import handoff
    from camellia.ad_gate import describe_entry_point
    sender, _facts, db = ad_customer
    text = "Thank you for your question. Our Manager of Kitchens will get in touch with you here shortly to help."
    db.save_response_template_override(handoff.HANDOFF_MESSAGE_OVERRIDE_ID, text, "test")
    message = "How much does a box cost?"
    _process(ad_customer, message, describe_entry_point({"sender": {"id": sender}, "message": {"text": message}}))
    assert db.get_active_handoff("instagram", sender) is not None
    assert db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]["bot_response"] == text


@pytest.mark.parametrize("message", ["Hello, can I get more info on this?", "Can you tell me more about this?"])
def test_more_info_on_a_sondesh_checklist_gets_the_sondesh_details(ad_customer, message):
    # 18 Oct 17:10: the post's checklist held only sondesh answers, so Meta's
    # button (which names no product) found nothing and went to the manager.
    _mapped_source(ad_customer, "post", AD, topic_name="Sondesh", response_mode="APPROVED_QA",
                   approved_qa_ids=["sondesh_price_client", "sondesh_details_client", "price_is_high"],
                   source_context="sondesh")
    sender, facts, db = ad_customer
    _ask(ad_customer, message)
    assert facts["client_qa_id"] == "sondesh_details_client"
    assert db.get_active_handoff("instagram", sender) is None
