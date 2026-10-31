"""Customers word a question endlessly, so its type picks the ticked answer.

31 Oct: "How much is the cost per head" handed off on an event ad although the
answer was saved as "how much per person". Listing every wording cannot work; on
a mapped post/ad the type of question picks the one answer ticked for that type.
"""
import uuid

import pytest

from camellia import approved_qa, instagram_ads as ads, instagram_routing
from camellia.ad_gate import EntryPoint
from camellia.session_memory import session_memory
from test_ad_mapped_source_context import ad_customer, _mapped_source, _process  # noqa: F401
from test_approved_qa_custom_contexts import _publish

AD = "111222333"
CONTEXT = "kolkata_bhoot_chaturdashi"
PRICE = "PER HEAD ANSWER"
PRICE_WORDINGS = (
    "How much is the cost per head", "How much does it cost", "What is the price of this", "how much per person",
    "cost per head?", "per head cost", "per person cost", "price per person", "What's the charge per person",
    "how much per plate", "per plate price", "how much for one person", "price for 2 people", "how much for two",
    "what is the entry fee", "ticket price", "how much is the ticket", "what are the charges", "rate kya hai",
    "kitna hai", "kitne ka hai", "koto dam", "dam koto", "price?", "how much will it be for 4 people",
    "cost for couple", "is there a cover charge", "what is the contribution", "pp cost",
)
NOT_PRICE = (
    "any discount on the price?", "delivery charges?", "how much time does it take", "how much is delivery to Delhi",
    "can you do a price for a party of 30", "catering cost", "is there a refund if I cancel", "sondesh price",
    "how many people can come", "i have a complaint about the price",
)


def _reply(ad_customer, text):
    _sender, facts, db = ad_customer
    sender = "type-" + uuid.uuid4().hex
    session_memory.clear_session(sender, "instagram")
    send = _process((sender, facts, db), text, EntryPoint(source="ADS", ad_id=AD, from_ad_click=True))
    return send.call_args_list[0].args[1] if send.call_args_list else ""


def _answer(text=PRICE, question="how much per person", **extra):
    if CONTEXT not in {option["value"] for option in approved_qa.context_options()}:
        approved_qa.save_context("Kolkata Bhoot Chaturdashi", "test")
    return _publish_with(question, text, extra)


def _publish_with(question, text, extra):
    if not extra:
        return _publish(question, text, CONTEXT)
    body = dict(question=question, answer=text, context=CONTEXT, aliases=[], **extra)
    draft = approved_qa.preview(body)
    return approved_qa.publish({**body, "qa_id": draft["qa_id"], "preview_token": draft["preview_token"]}, "test")


def _ticked(ad_customer, *ids):
    return _mapped_source(ad_customer, "post", AD, response_mode="APPROVED_QA", approved_qa_ids=list(ids),
                          source_context=CONTEXT)


def _custom(ad_customer, *ids):
    route = _mapped_source(ad_customer, "post", AD)
    instagram_routing.update_topic(route["topic_id"], default_approved_qa_ids=list(ids))
    return route


@pytest.mark.parametrize("mapping", (_ticked, _custom))
def test_every_price_wording_gets_the_one_price_answer(ad_customer, mapping):
    mapping(ad_customer, _answer()["qa_id"])
    missed = [text for text in PRICE_WORDINGS if PRICE not in _reply(ad_customer, text)]
    assert not missed


@pytest.mark.parametrize("mapping", (_ticked, _custom))
def test_questions_that_only_mention_a_price_never_get_it(ad_customer, mapping):
    mapping(ad_customer, _answer()["qa_id"])
    leaked = [text for text in NOT_PRICE if PRICE in _reply(ad_customer, text)]
    assert not leaked


def test_two_price_answers_on_one_mapping_go_to_a_person(ad_customer):
    first, second = _answer(), _answer("SECOND PRICE", "how much for couples")
    assert first["qa_id"] != second["qa_id"]
    _ticked(ad_customer, first["qa_id"], second["qa_id"])
    reply = _reply(ad_customer, "How much is the cost per head")
    assert PRICE not in reply and "SECOND PRICE" not in reply


def test_a_custom_mappings_note_still_answers_details(ad_customer):
    details = _answer("TOPIC DETAILS", "tell me about the night", mapped_role="details")
    _custom(ad_customer, details["qa_id"])
    reply = _reply(ad_customer, "Tell me more")
    assert "TOPIC DETAILS" not in reply and "description" in reply


@pytest.mark.parametrize("mapping", (_ticked, _custom))
@pytest.mark.parametrize("role,wordings", (
    ("booking", ("How can I book", "can i register", "how to do the registration")),
    ("location", ("what is the venue", "where is this happening", "kothay hobe")),
    ("date", ("what date is this on", "when is this happening", "what are the timings", "kobe hobe")),
))
def test_booking_location_and_date_questions_find_their_answer(ad_customer, mapping, role, wordings):
    mapping(ad_customer, _answer("ROLE ANSWER", "event " + role, mapped_role=role)["qa_id"])
    missed = [text for text in wordings if "ROLE ANSWER" not in _reply(ad_customer, text)]
    assert not missed


def test_order_and_opening_hour_rules_still_come_first(ad_customer):
    # Other rules own these turns, so they stay with a person rather than the answer.
    _ticked(ad_customer, _answer("ROLE ANSWER", "event booking", mapped_role="booking")["qa_id"])
    for text in ("How do I reserve a place", "are slots open"):
        assert "ROLE ANSWER" not in _reply(ad_customer, text)


def test_no_ad_customers_never_get_a_category_answer(ad_customer):
    answer = _answer()
    for text in ("How much is the cost per head", "kitna hai", "koto dam"):
        found = approved_qa.match(text, {})
        assert found is None or found.entry.qa_id != answer["qa_id"]


def _reason(ad_customer, route):
    db = ad_customer[2]
    decoded = instagram_routing.decode_route(db.get_instagram_source_route(route["routing_id"]))
    from camellia.instagram_source_qa import route_contract
    decoded = route_contract(db, decoded)
    return ads.price_gap_reason(db, decoded, ads.price_answer_status(db, decoded))


def test_ads_page_says_why_a_price_question_goes_to_the_manager(ad_customer):
    db = ad_customer[2]
    assert ads.price_gap_reason(db, None, "NONE") == "This ad has no mapping."
    assert ads.price_gap_reason(db, {"active": True}, "APPROVED") is None
    assert "no default answers" in _reason(ad_customer, _custom(ad_customer))
