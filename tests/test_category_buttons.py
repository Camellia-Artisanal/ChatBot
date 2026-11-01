"""Category buttons: ask once, answer from the tapped category, never guess."""
from unittest.mock import patch

import pytest

from camellia import answer_slots, app as app_module, category_buttons, instagram_routing
from camellia.ad_gate import EntryPoint
from camellia.config import config

from test_ad_mapped_source_context import ad_customer, _process  # noqa: F401
from test_admin_categories import _categories, admin  # noqa: F401

AD_BUTTON = "Hello, can I get more info on this?"
FUTURE = "2099-01-01T00:00:00+00:00"


@pytest.fixture
def world(ad_customer, monkeypatch):  # noqa: F811
    db = ad_customer[2]
    for module in (answer_slots, category_buttons):
        monkeypatch.setattr(module, "db", db)
    monkeypatch.setattr(config, "CATEGORY_BUTTONS_MODE", "all")
    topic = instagram_routing.create_topic("Sondesh Delivery Delhi")["topic_id"]
    db.set_instagram_topic_links(topic, button_name="Sondesh Delhi")
    answer_slots.save("topic", topic, "details", {"kind": "CUSTOM", "custom_text": "Handmade daily."})
    answer_slots.save("topic", topic, "price", {"kind": "CUSTOM", "custom_text": "Rs 500 a box.", "valid_until": FUTURE})
    return ad_customer, topic


def _send(sender, text):
    """Runs one typed message and returns everything sent, handoffs included."""
    sent = []

    def record(recipient, body=None, **kwargs):
        sent.append((body, kwargs))
        return {"status": "sent", "message_id": "m"}

    with patch.object(app_module, "send_instagram_reply", side_effect=record), \
            patch.object(app_module, "_send_or_queue_instagram_reply", side_effect=record), \
            patch.object(app_module, "send_instagram_response_blocks",
                         side_effect=lambda r, blocks, **k: sent.append((blocks[0].get("text"), k))), \
            patch.object(app_module.escalator, "handle_escalation"):
        app_module._process_instagram_event(sender, text, None, EntryPoint(source="typed"))
    return sent


def test_off_by_default_changes_nothing(world, monkeypatch):
    (sender, _facts, db), _topic = world
    monkeypatch.setattr(config, "CATEGORY_BUTTONS_MODE", "off")
    sent = _send(sender, AD_BUTTON)
    assert all(category_buttons.QUESTION != body for body, _k in sent)
    assert db.get_active_handoff("instagram", sender) is not None


def test_an_ad_message_with_no_id_asks_instead_of_handing_off(world):
    (sender, _facts, db), _topic = world
    sent = _send(sender, AD_BUTTON)
    assert sent[0][0] == category_buttons.QUESTION
    assert sent[0][1]["quick_replies"][0]["title"] == "Sondesh Delhi"
    assert db.get_active_handoff("instagram", sender) is None


def test_a_tap_sends_details_and_later_questions_use_that_category(world):
    (sender, _facts, db), _topic = world
    _send(sender, AD_BUTTON)
    assert _send(sender, "Sondesh Delhi")[0][0] == "Handmade daily."
    assert _send(sender, "What is the price?")[0][0] == "Rs 500 a box."
    assert db.get_active_handoff("instagram", sender) is None


def test_a_tap_answers_the_original_question(world):
    (sender, _facts, _db), _topic = world
    with patch.object(category_buttons, "slot_for", return_value="price"):
        step = category_buttons.next_step(sender, {}, "dam koto", False)
    assert step["slot"] == "price"
    facts = {}
    category_buttons.remember_question(facts, step)
    assert category_buttons.tap(sender, facts, "sondesh delhi")[0]["text"] == "Rs 500 a box."


def test_it_asks_only_once_and_an_ignored_question_goes_to_staff(world):
    (sender, _facts, db), _topic = world
    _send(sender, AD_BUTTON)
    sent = _send(sender, "Something unrelated about my life")
    assert all(body != category_buttons.QUESTION for body, _k in sent)
    assert db.get_active_handoff("instagram", sender) is not None


def test_an_empty_answer_after_a_tap_goes_to_staff_and_needs_answers(world):
    (sender, facts, db), topic = world
    _send(sender, AD_BUTTON)
    assert _send(sender, "Sondesh Delhi")
    answer_slots.clear("topic", topic, "price")
    _send(sender, "What is the price?")
    assert db.get_active_handoff("instagram", sender) is not None
    assert [(item["topic_id"], item["slot"]) for item in answer_slots.needs_answers()] == [(topic, "price")]
    assert facts["category_choice"] == topic


def test_only_named_categories_with_details_become_buttons(world):
    (_sender, _facts, db), topic = world
    other = instagram_routing.create_topic("No Name Yet")["topic_id"]
    answer_slots.save("topic", other, "details", {"kind": "CUSTOM", "custom_text": "x"})
    no_answer = instagram_routing.create_topic("No Answer Yet")["topic_id"]
    db.set_instagram_topic_links(no_answer, button_name="No Answer")
    assert [item["topic_id"] for item in category_buttons.options()] == [topic]


def test_no_buttons_means_the_normal_handoff(world):
    (sender, _facts, db), topic = world
    db.set_instagram_topic_links(topic)
    _send(sender, AD_BUTTON)
    assert db.get_active_handoff("instagram", sender) is not None


def test_a_human_request_is_never_asked(world):
    (sender, _facts, db), _topic = world
    sent = _send(sender, "Can I talk to a human?")
    assert all(body != category_buttons.QUESTION for body, _k in sent)
    assert db.get_active_handoff("instagram", sender) is not None


def test_a_failed_question_falls_back_to_the_handoff(world):
    (sender, _facts, db), _topic = world
    with patch.object(app_module, "send_instagram_reply", return_value={"status": "failed"}), \
            patch.object(app_module, "_send_or_queue_instagram_reply"), \
            patch.object(app_module.escalator, "handle_escalation"):
        app_module._process_instagram_event(sender, AD_BUTTON, None, EntryPoint(source="typed"))
    assert db.get_active_handoff("instagram", sender) is not None


def test_test_mode_reaches_test_accounts_only(world, monkeypatch):
    (sender, _facts, _db), _topic = world
    monkeypatch.setattr(config, "CATEGORY_BUTTONS_MODE", "test")
    state = {"test_accounts": [{"sender_id": sender}]}
    with patch("camellia.category_buttons.instagram_chatbot_control.get_state", return_value=state):
        assert category_buttons.enabled(sender)
        assert not category_buttons.enabled("someone-else")


@pytest.mark.parametrize("message, slot", [("What is the price?", "price"), ("How to book?", "booking"),
                                           ("Do you deliver to Noida?", "location"), ("Hello there", None)])
def test_question_types_by_keyword(message, slot):
    assert category_buttons.slot_for(message) == slot


def test_button_names_are_limited(admin, world):  # noqa: F811
    client, headers = admin
    _ctx, topic = world
    path = f"/admin/categories/master/{topic}/links"
    assert client.put(path, headers=headers, json={"button_name": "x" * 21}).status_code == 400
    assert client.put(path, headers=headers, json={"button_name": "Sondesh"}).status_code == 200
    assert next(row for row in _categories(admin)["topics"] if row["topic_id"] == topic)["button_name"] == "Sondesh"


def test_quick_replies_reach_meta_in_the_message(monkeypatch):
    replies = category_buttons.quick_replies([{"title": "Sondesh", "topic_id": "t"}])
    assert replies == [{"content_type": "text", "title": "Sondesh", "payload": "category:t"}]

    class Response:
        status_code = 200
        headers = {}
        content = b'{"ok": true}'

        def json(self):
            return {"ok": True}

        def raise_for_status(self):
            return None

    class Client:
        bodies = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            self.bodies.append(kwargs.get("json"))
            return Response()

    monkeypatch.setattr(config, "INSTAGRAM_ACCESS_TOKEN", "IGAA_login_token")
    monkeypatch.setattr(app_module.httpx, "Client", lambda *args, **kwargs: Client())
    with patch.object(app_module.db, "create_instagram_outbound_audit", return_value=None), \
            patch.object(app_module.db, "finalize_instagram_outbound_audit"), \
            patch.object(app_module, "delivery_monitor"):
        app_module.send_instagram_reply("recipient-1", category_buttons.QUESTION, quick_replies=replies)
    assert Client.bodies[-1]["message"] == {"text": category_buttons.QUESTION, "quick_replies": replies}
