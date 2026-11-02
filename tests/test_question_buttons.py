"""Question buttons: a tap reads as the full question, and only answered ones show."""
from unittest.mock import patch

import pytest

from camellia import answer_slots, category_buttons as buttons, instagram_routing
from camellia.config import config

from test_ad_mapped_source_context import ad_customer, _process  # noqa: F401
from test_admin_categories import admin  # noqa: F401

PRICE = {"slot": "price", "label": "Price"}
MORE = {"slot": "details", "label": "Tell me more"}


@pytest.fixture
def store(ad_customer, monkeypatch):  # noqa: F811
    db = ad_customer[2]
    for module in (answer_slots, buttons):
        monkeypatch.setattr(module, "db", db)
    monkeypatch.setattr(config, "CATEGORY_BUTTONS_MODE", "all")
    return db


def _topic(*answers):
    topic = instagram_routing.create_topic("Button Topic")["topic_id"]
    instagram_routing.update_topic(topic, default_approved_qa_ids=list(answers))
    return topic


@pytest.mark.parametrize("body", [[{"slot": "nope", "label": "x"}], [{"slot": "price", "label": ""}],
                                  [{"slot": "price", "label": "x" * 21}], [PRICE, {"slot": "details", "label": "price"}], "x"])
def test_bad_buttons_are_refused(store, body):
    with pytest.raises(ValueError):
        buttons.save_buttons("topic", "t", body)


def test_only_questions_with_an_answer_are_offered(store):
    topic = _topic("sondesh_price_client")
    buttons.save_buttons("topic", topic, [PRICE, MORE])
    facts = {}
    replies = buttons.after_mapped_reply("s", facts, {"routing_id": 1, "topic_id": topic})
    assert [item["title"] for item in replies] == ["Price"]
    assert facts["question_buttons"] == {"price": "How much does it cost?"}


def test_a_mapping_overrides_its_category(store):
    topic = _topic("sondesh_price_client")
    buttons.save_buttons("topic", topic, [PRICE])
    buttons.save_buttons("route", 2, [{"slot": "price", "label": "Rate"}])
    assert buttons.after_mapped_reply("s", {}, {"routing_id": 2, "topic_id": topic})[0]["title"] == "Rate"


def test_they_show_once_per_customer_and_source(store):
    topic = _topic("sondesh_price_client")
    buttons.save_buttons("topic", topic, [PRICE])
    facts, route = {}, {"routing_id": 3, "topic_id": topic}
    assert buttons.after_mapped_reply("s", facts, route)
    assert buttons.after_mapped_reply("s", facts, route) is None
    assert buttons.after_mapped_reply("s", facts, {**route, "routing_id": 4})


def test_off_and_empty_show_nothing(store, monkeypatch):
    topic = _topic("sondesh_price_client")
    assert buttons.after_mapped_reply("s", {}, {"routing_id": 5, "topic_id": topic}) is None
    buttons.save_buttons("topic", topic, [PRICE])
    monkeypatch.setattr(config, "CATEGORY_BUTTONS_MODE", "off")
    assert buttons.after_mapped_reply("s", {}, {"routing_id": 5, "topic_id": topic}) is None


def test_a_tap_becomes_the_full_question_and_typing_is_untouched():
    facts = {"question_buttons": {"rate": "How much does it cost?"}}
    assert buttons.question_for_tap(facts, " Rate ") == "How much does it cost?"
    assert "question_buttons" not in facts
    assert buttons.question_for_tap({"question_buttons": {"rate": "x"}}, "something else") == "something else"


def test_a_custom_label_answers_like_the_typed_question(store, ad_customer):  # noqa: F811
    sender, facts, _db = ad_customer
    facts["question_buttons"] = {"rate": "How do I place an order?"}
    with patch("camellia.app.responder.match_and_respond", return_value=("Sure", True, "x", None)) as respond, \
            patch("camellia.app._send_or_queue_instagram_reply"):
        _process_typed(sender, "Rate")
    assert respond.call_args.args[0] == "How do I place an order?"


def _process_typed(sender, text):
    from camellia import app as app_module
    from camellia.ad_gate import EntryPoint
    app_module._process_instagram_event(sender, text, None, EntryPoint(source="typed"))


def _mapped_reply(ad_customer, image=None):  # noqa: F811
    from test_custom_mapping_topic_answers import _custom_route
    from camellia.instagram_routing import decode_route
    sender, facts, _db = ad_customer
    route = decode_route(_custom_route(ad_customer))
    instagram_routing.update_topic(route["topic_id"], default_approved_qa_ids=["sondesh_price_client"])
    buttons.save_buttons("topic", route["topic_id"], [PRICE])
    facts["source_route"] = route
    sent = []
    with patch("camellia.app.responder.match_and_respond", return_value=("Hello", True, "x", image)), \
            patch("camellia.app._send_or_queue_instagram_reply", side_effect=lambda r, text, **k: sent.append((text, k.get("quick_replies")))):
        _process_typed(sender, "Hi")
    return sent


def test_the_buttons_come_on_the_mapped_reply_with_no_extra_message(store, ad_customer):  # noqa: F811
    sent = _mapped_reply(ad_customer)
    assert len(sent) == 1 and sent[0][0] == "Hello" and sent[0][1][0]["title"] == "Price"


def test_a_reply_ending_in_an_image_keeps_its_buttons_for_later(store, ad_customer):  # noqa: F811
    assert _mapped_reply(ad_customer, image="https://x/y.jpg")[0][1] is None
    assert "question_buttons_shown" not in ad_customer[1]


def test_blocks_put_the_buttons_only_on_a_final_text():
    from camellia import app as app_module
    blocks = [{"kind": "TEXT", "text": "One"}, {"kind": "TEXT", "text": "Two"}]
    with patch("camellia.app._send_or_queue_instagram_reply") as send, patch("camellia.app.session_memory.get_facts", return_value={}):
        app_module.send_instagram_response_blocks("s", blocks, quick_replies=[{"title": "Price"}])
    assert "quick_replies" not in send.call_args_list[0].kwargs
    assert send.call_args_list[1].kwargs["quick_replies"] == [{"title": "Price"}]


def test_health_shows_the_button_switch(admin, store):  # noqa: F811
    assert admin[0].get("/health").json()["category_buttons_mode"] == "all"


def test_button_routes(admin, store):  # noqa: F811
    client, headers = admin
    path = "/admin/categories/question-buttons/topic/t1"
    assert client.get(path, headers=headers).json()["buttons"] is None
    assert client.put(path, headers=headers, json={"buttons": [PRICE]}).status_code == 200
    assert client.get(path, headers=headers).json()["buttons"] == [PRICE]
    assert client.put(path, headers=headers, json={"buttons": [{"slot": "x", "label": "y"}]}).status_code == 400
    assert client.get("/admin/categories/question-buttons/nope/1", headers=headers).status_code == 400


def _preview_topic():
    topic = _topic()
    answer_slots.save("topic", topic, "details", {"kind": "CUSTOM", "custom_text": "Made fresh."})
    buttons.save_buttons("topic", topic, [MORE, PRICE])
    return topic


def test_preview_shows_the_answer_and_only_buttons_that_have_one(store):
    data = buttons.preview(_preview_topic(), "details")
    assert data["state"] == "ok" and data["blocks"] == [{"kind": "TEXT", "text": "Made fresh."}]
    assert data["buttons"] == ["Tell me more"]


def test_preview_says_when_a_question_goes_to_staff(store):
    assert buttons.preview(_preview_topic(), "price") == {"state": "empty", "blocks": [], "buttons": []}


def test_preview_route(admin, store):  # noqa: F811
    client, headers = admin
    topic = _preview_topic()
    assert client.get(f"/admin/categories/preview/{topic}/details", headers=headers).json()["state"] == "ok"
    assert client.get("/admin/categories/preview/missing/details", headers=headers).status_code == 404
