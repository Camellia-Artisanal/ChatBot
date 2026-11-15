"""A priced Custom reply answers every price question on its post (2026-11-15).

Customers asked "how much price per person" after already getting the post's reply,
and the once-only rule handed them to staff.
"""
import pytest

from camellia import instagram_routing

from test_ad_mapped_source_context import ad_customer, _ad_entry, _process  # noqa: F401
from test_custom_mapping_topic_answers import AD, _ask, _custom_route

PRICED = "Bengali home table, ₹650 per person. Book 2 days ahead."


def _note(ad_customer, text):
    route = instagram_routing.decode_route(_custom_route(ad_customer))
    route["source_description"] = text
    ad_customer[2].save_instagram_source_route(route, int(route["routing_id"]))


def _last(ad_customer):
    sender, _facts, db = ad_customer
    return db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]


@pytest.mark.parametrize("second", ["how much price per person", "rate per head?", "per person kitna"])
def test_a_priced_note_answers_every_price_question(ad_customer, second):
    _note(ad_customer, PRICED)
    _ask(ad_customer, "How much does it cost?")
    _ask(ad_customer, second)
    assert _last(ad_customer)["bot_response"] == PRICED
    assert ad_customer[2].get_active_handoff("instagram", ad_customer[0]) is None


@pytest.mark.parametrize("message", ["how much for 2 people", "price for 4 persons", "how much for 10 pax"])
def test_a_people_count_is_a_price_question(ad_customer, message):
    _note(ad_customer, PRICED)
    _ask(ad_customer, message)
    assert _last(ad_customer)["bot_response"] == PRICED
    assert ad_customer[2].get_active_handoff("instagram", ad_customer[0]) is None


def test_a_note_without_a_price_is_still_sent_once(ad_customer):
    _note(ad_customer, "Bengali home table every Sunday. Book 2 days ahead.")
    _ask(ad_customer, "How much does it cost?")
    _ask(ad_customer, "how much price per person")
    assert ad_customer[2].get_active_handoff("instagram", ad_customer[0]) is not None


def test_a_priced_note_does_not_answer_other_questions_twice(ad_customer):
    _note(ad_customer, PRICED)
    _ask(ad_customer, "How much does it cost?")
    _ask(ad_customer, "Is the box recyclable?")
    assert ad_customer[2].get_active_handoff("instagram", ad_customer[0]) is not None
