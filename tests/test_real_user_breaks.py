"""Five ways real customers and staff break Camellia, driven through the real webhook and Admin API."""
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from camellia import ad_gate, admin_shared_mapping, app as app_module, approved_qa, system_replies
from camellia.config import config
from camellia.instagram_routing import decode_route
from camellia.session_memory import session_memory

from test_followup_manual_sequences import assets, auth, store  # noqa: F401
from test_shared_media_mapping import NOTE, _attach, _client, _mapped

ATTACHMENT = [{"type": "image", "payload": {"url": "https://example.test/photo.jpg"}}]
_mid = [0]


def message(sender, text=None, **fields):
    _mid[0] += 1
    body = {"mid": f"mid-{_mid[0]}", **fields}
    if text is not None:
        body["text"] = text
    return {"sender": {"id": sender}, "recipient": {"id": "biz"},
            "timestamp": 1760000000000 + _mid[0], "message": body}


@pytest.fixture
def web(store, assets, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "INSTAGRAM_ACCOUNT_ID", "")
    monkeypatch.setattr(config, "REQUIRE_WEBHOOK_SIGNATURE", False)
    sent = []
    monkeypatch.setattr(app_module, "send_instagram_reply",
                        lambda recipient, text, **_kwargs: sent.append(text) or {"message_id": "x", "status": "sent"})
    client = TestClient(app_module.app)

    def post(*events):
        response = client.post("/webhook/instagram", json={
            "object": "instagram", "entry": [{"id": "biz", "messaging": list(events)}]})
        assert response.status_code == 200, response.text

    post.sent = sent
    return post


def _handed_off(store, sender):  # noqa: F811
    return store.get_active_handoff("instagram", sender) is not None


def _on_custom_post(store, sender):  # noqa: F811
    _item, route = _mapped(_client(), store)
    facts = session_memory.get_facts(sender, "instagram")
    facts["source_route"] = decode_route(store.get_instagram_source_route(route["routing_id"]))


def _ordering(web, sender):
    web(message(sender, "I want to order 1 box of baked sandesh"))
    web.sent.clear()
    return session_memory.get_facts(sender, "instagram")


# 1. A reaction, not a question. A handoff mutes the customer until staff resume.

@pytest.mark.parametrize("text", ["❤️", "😍😍😍", "🙂", "😂", "🙏", "👍", "Ok 👍", "hmm", "?", "..."])
def test_a_reaction_gets_a_friendly_line_and_keeps_the_bot_on(web, store, text):  # noqa: F811
    web(message("reactor", text))

    assert web.sent == [system_replies.text("acknowledgement")]
    assert not _handed_off(store, "reactor")


@pytest.mark.parametrize("text", ["😡", "👎", "😭😭", "no 😡"])
def test_an_unhappy_emoji_still_reaches_a_person(web, store, text):  # noqa: F811
    web(message("unhappy", text))

    assert _handed_off(store, "unhappy")


def test_a_reaction_during_an_order_keeps_the_order(web, store):  # noqa: F811
    facts = _ordering(web, "reactor")

    web(message("reactor", "❤️"))

    assert not _handed_off(store, "reactor")
    assert facts["order_intake_draft"]["item"]


@pytest.mark.parametrize("flag", ["cart", "awaiting_menu_choice", "awaiting_product_price", "pending_clarification"])
@pytest.mark.parametrize("text", ["ok", "👍", "❤️", "?"])
def test_a_reaction_to_an_open_question_stays_with_the_bot(web, store, flag, text):  # noqa: F811
    session_memory.get_facts("reactor", "instagram")[flag] = [{"name": "Box"}] if flag == "cart" else True

    web(message("reactor", text))

    assert not _handed_off(store, "reactor")


# 2. Bengali and Hindi on a mapped post. "price koto" worked; "দাম কত" muted them.

@pytest.mark.parametrize("text", [
    "দাম কত?", "দাম কত", "কত টাকা", "কত পড়বে", "কত পড়বে", "कीमत क्या है", "दाम कितना है", "कितना है",
    "मूल्य क्या है", "dam koto", "kitna hai", "আরও জানতে চাই", "और जानकारी",
])
def test_a_mapped_post_answers_in_bengali_and_hindi(web, store, text):  # noqa: F811
    _on_custom_post(store, "local")

    web(message("local", text))

    assert web.sent == [NOTE]
    assert not _handed_off(store, "local")


@pytest.mark.parametrize("text", ["ডেলিভারি চার্জ কত টাকা", "डिलीवरी कितना है", "कितना समय लगेगा", "छूट कितनी है"])
def test_questions_for_a_person_stay_with_a_person_in_any_script(web, store, text):  # noqa: F811
    _on_custom_post(store, "local")

    web(message("local", text))

    assert web.sent != [NOTE]


@pytest.mark.parametrize("text,roles", [
    ("দাম কত?", ["price"]), ("कीमत क्या है", ["price"]), ("কোথায় হবে", ["location"]), ("कहाँ है", ["location"]),
    ("কবে", ["date"]), ("कब है", ["date"]), ("কত পিস", []), ("बादाम", []), ("कबाब", []),
])
def test_a_checklist_reads_the_question_type_in_any_script(text, roles):
    assert approved_qa._asked_roles(text) == roles


# 3. Changing course mid-order. Answering the bot's own question must not end the order.

@pytest.mark.parametrize("text,slot", [
    ("tomorrow 5pm", "Tomorrow 5Pm"), ("Saturday evening", "Saturday Evening"),
    ("sunday 11:30 am", "Sunday 11:30 Am"), ("5 pm", "5 Pm"),
])
def test_a_delivery_time_is_an_order_detail(web, store, text, slot):  # noqa: F811
    facts = _ordering(web, "buyer")

    web(message("buyer", text))

    assert not _handed_off(store, "buyer")
    assert facts["order_intake_draft"]["delivery_slot"] == slot


@pytest.mark.parametrize("text", ["my name is Riya", "name is Riya", "wait", "no wait", "hold on", "one sec", "just a minute"])
def test_a_pause_or_a_name_does_not_end_the_order(web, store, text):  # noqa: F811
    facts = _ordering(web, "buyer")

    web(message("buyer", text))

    assert len(web.sent) == 1 and not _handed_off(store, "buyer")
    assert facts["order_intake_draft"]["item"]


def test_a_price_question_mid_order_keeps_the_draft(web, store):  # noqa: F811
    facts = _ordering(web, "buyer")

    web(message("buyer", "sondesh price"))
    web(message("buyer", "2 boxes"))

    assert not _handed_off(store, "buyer")
    assert facts["order_intake_draft"]["quantity"] == "2 boxes"


# 4. Rapid, repeated and edited messages.

def test_meta_retrying_a_message_answers_once(web):
    event = message("repeat", "Hi")

    web(event)
    web(event)

    assert len(web.sent) == 1


@pytest.mark.parametrize("first", ["message", "edit"])
def test_meta_edit_notice_for_a_new_message_answers_once(web, monkeypatch, first):
    event = message("repeat", "Hi")
    edit = {"timestamp": 1760009999999, "message_edit": {"mid": event["message"]["mid"], "num_edit": 0}}
    monkeypatch.setattr(ad_gate, "fetch_message_details_fallback", lambda _mid: ("repeat", "Hi"))

    web(*([event, edit] if first == "message" else [edit, event]))

    assert len(web.sent) == 1


def test_a_real_edit_is_read_as_a_new_message(web):
    event = message("repeat", "Hi")
    web(event)

    web({"sender": {"id": "repeat"}, "timestamp": 1760009999999,
         "message_edit": {"mid": event["message"]["mid"], "num_edit": 1, "text": "thanks"}})

    assert web.sent[-1] == system_replies.text("thanks")


def test_rapid_messages_are_answered_in_the_order_sent(web):
    events = [message("rapid", text) for text in ("Hi", "thanks", "bye")]

    threads = [threading.Thread(target=web, args=(event,)) for event in events]
    for thread in threads:
        thread.start()
        thread.join()

    assert web.sent == [system_replies.text(reply) for reply in ("greeting", "thanks", "goodbye")]


def test_rapid_photos_open_one_handoff_and_later_text_stays_silent(web, store):  # noqa: F811
    web(message("album", attachments=ATTACHMENT), message("album", attachments=ATTACHMENT), message("album", "did you get this?"))

    assert len(web.sent) == 1
    assert _handed_off(store, "album")


def test_media_instagram_cannot_show_us_still_reaches_a_person(web, store):  # noqa: F811
    web(message("gif", is_unsupported=True))

    assert len(web.sent) == 1
    assert _handed_off(store, "gif")


def test_unsending_or_reacting_needs_no_answer(web, store):  # noqa: F811
    web(message("quiet", is_deleted=True))
    web({"sender": {"id": "quiet"}, "timestamp": 1, "reaction": {"mid": "m", "action": "react", "reaction": "love"}})

    assert web.sent == []
    assert not _handed_off(store, "quiet")


# 5. Staff: Select all on hundreds of posts, and two people in one mapping.

def _posts(store, like, count):  # noqa: F811
    base = store.get_instagram_media(like)
    with store.get_connection() as conn:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(instagram_media)") if row["name"] != "id"]
        for number in range(count):
            row = {**{key: base[key] for key in columns}, "instagram_media_id": f"bulk{number}", "shortcode": f"BULK{number}",
                   "permalink": f"https://www.instagram.com/p/BULK{number}/", "custom_route_id": None, "route_mode": "DEFAULT"}
            conn.execute(f"INSERT INTO instagram_media ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                         tuple(row.values()))
        conn.commit()


def test_select_all_on_hundreds_of_posts_is_one_write(store, assets, monkeypatch):  # noqa: F811
    client = _client()
    item, route = _mapped(client, store)
    _posts(store, item["id"], 300)
    monkeypatch.setattr(store, "update_instagram_media_route",
                        lambda *args, **kwargs: pytest.fail("one write per post locks the database hundreds of times"))

    result = _attach(client, routing_id=route["routing_id"], all_matching=True)

    assert result == {"attached": 300, "already_attached": 0, "skipped_mapped": 0}
    assert store.count_attached_media(route["routing_id"]) == 300


def test_select_all_leaves_customers_replies_running(store, assets, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "REQUIRE_WEBHOOK_SIGNATURE", False)
    with TestClient(app_module.app) as client:
        _item, route = _mapped(client, store)
        finished = []
        monkeypatch.setattr(admin_shared_mapping, "attach",
                            lambda *args, **kwargs: time.sleep(1.5) or finished.append("attach") or {})
        worker = threading.Thread(target=client.post, args=("/admin/instagram-media/attach-mapping",), kwargs={
            "headers": auth(), "json": {"routing_id": route["routing_id"], "all_matching": True}})
        worker.start()
        time.sleep(0.4)

        response = client.post("/webhook/instagram", json={"object": "other"})

        assert response.status_code == 200 and not finished
        worker.join()


def test_two_staff_running_select_all_cannot_take_each_others_posts(store, assets):  # noqa: F811
    client = _client()
    item, first = _mapped(client, store)
    _other, second = _mapped(client, store, "reel", "OTHERMAPPING")
    _posts(store, item["id"], 3)
    ids = [row["id"] for row in store.list_instagram_media(page_size=100, search="bulk")[0]]
    store.attach_instagram_media(ids, first["routing_id"], first.get("topic_id"))

    # The second person's list was read before the first person's save landed.
    assert store.attach_instagram_media(ids, second["routing_id"], second.get("topic_id")) == 0

    assert store.count_attached_media(first["routing_id"]) == 3


def _save(client, item, expected, note, topic):
    return client.put(f"/admin/instagram-media/{item['id']}/mapping", headers=auth(), json={
        "topic_id": topic, "response_mode": "CUSTOM", "source_description": note, "menu_mode": "NONE",
        **({"expected_updated_at": expected} if expected is not None else {})})


def test_a_second_editor_cannot_silently_overwrite_the_first(store, assets):  # noqa: F811
    client = _client()
    item, route = _mapped(client, store)
    opened = route["updated_at"]
    assert _save(client, item, opened, "First person's note", route["topic_id"]).status_code == 200

    stale = _save(client, item, opened, "Second person's note", route["topic_id"])

    assert stale.status_code == 409 and "reopen" in stale.json()["detail"].lower()
    assert store.get_instagram_source_route(route["routing_id"])["source_description"] == "First person's note"


def test_a_save_from_the_latest_copy_goes_through(store, assets):  # noqa: F811
    client = _client()
    item, route = _mapped(client, store)

    first = _save(client, item, route["updated_at"], "Note one", route["topic_id"])
    second = _save(client, item, first.json()["route"]["updated_at"], "Note two", route["topic_id"])
    without_token = _save(client, item, None, "Note three", route["topic_id"])

    assert first.status_code == second.status_code == without_token.status_code == 200


def test_the_mapping_editor_sends_the_copy_it_opened():
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert 'id="igMediaOpenedAt"' in source and "expected_updated_at: document.getElementById('igMediaOpenedAt')" in source
