"""A Category picked on an Approved mapping links it to buttons; replies must not change."""
import pytest

from camellia import instagram_routing, responder as responder_module
from camellia.instagram_routing import decode_route

from test_ad_mapped_source_context import ad_customer, _mapped_source  # noqa: F401

QUESTIONS = ["Hi", "How much does it cost?", "Tell me more", "Tell me more about this", "price?",
             "Do you deliver to Noida?", "How do I pay?", "menu please", "what is this", "minimum order?"]
ANSWERS = ["sondesh_price_client", "sondesh_details_client", "general_payment_client"]


def _replies(ad_customer, route):  # noqa: F811
    sender, facts, _db = ad_customer
    start, results = dict(facts), []
    for question in QUESTIONS:
        facts.clear()
        facts.update({**start, "source_route": route})
        results.append(responder_module.responder.match_and_respond(question, user_id=sender, channel="instagram"))
    facts.clear()
    facts.update(start)
    return results


def test_a_category_on_an_approved_mapping_changes_no_reply(ad_customer):  # noqa: F811
    route = decode_route(_mapped_source(ad_customer, "post", "120246593316020541", response_mode="APPROVED_QA",
                                        approved_qa_id=ANSWERS[0], approved_qa_ids=ANSWERS))
    route["menu_mode"] = "NONE"
    assert route["topic_id"]
    instagram_routing.update_topic(route["topic_id"], default_approved_qa_ids=["general_price"])
    assert _replies(ad_customer, route) == _replies(ad_customer, {**route, "topic_id": None})
