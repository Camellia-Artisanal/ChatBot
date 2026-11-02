"""Answer slots: approved or custom answers per question type, in shadow only."""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from camellia import answer_slots, app as app_module, approved_qa, instagram_routing
from camellia.ai_responder import ai_responder
from camellia.config import config

from test_ad_mapped_source_context import ad_customer, _process  # noqa: F401
from test_admin_categories import _route, admin  # noqa: F401
from test_custom_mapping_topic_answers import _ask, _custom_route  # noqa: F401

FUTURE, PAST = "2099-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"
BASE = "/admin/categories/slots"


@pytest.fixture
def slots(ad_customer, monkeypatch):
    monkeypatch.setattr(answer_slots, "db", ad_customer[2])
    return ad_customer[2]


def _topic(name="Slot Topic"):
    return instagram_routing.create_topic(name)["topic_id"]


def _custom(text="Rs 500 a box", **extra):
    return {"kind": "CUSTOM", "custom_text": text, **extra}


def _groq(payload, ok=True):
    return patch.object(ai_responder, "_query_groq", return_value=(json.dumps(payload), ok, "x"))


def test_custom_price_needs_a_future_end_date(slots):
    topic = _topic()
    with pytest.raises(ValueError, match="end date"):
        answer_slots.save("topic", topic, "price", _custom())
    with pytest.raises(ValueError, match="future"):
        answer_slots.save("topic", topic, "price", _custom(valid_until=PAST))
    answer_slots.save("topic", topic, "price", _custom(valid_until=FUTURE))
    answer_slots.save("topic", topic, "details", _custom())
    assert [item["answer"] is not None for item in answer_slots.list_slots("topic", topic)][:2] == [True, True]


@pytest.mark.parametrize("body", [{}, {"kind": "APPROVED_QA", "qa_id": "nope"}, {"kind": "CUSTOM", "custom_text": ""},
                                  {"kind": "CUSTOM", "custom_text": "x", "menu_id": "missing"}])
def test_bad_slot_answers_are_refused(slots, body):
    with pytest.raises(ValueError):
        answer_slots.save("topic", _topic(), "details", body)


def test_post_override_beats_category_and_category_beats_tagged(slots):
    topic = _topic()
    instagram_routing.update_topic(topic, default_approved_qa_ids=["sondesh_price_client"])
    route = {"routing_id": 77, "topic_id": topic}
    assert answer_slots.effective(route, "price")["source"] == "tagged"
    answer_slots.save("topic", topic, "price", _custom("Category", valid_until=FUTURE))
    assert answer_slots.effective(route, "price")["text"] == "Category"
    answer_slots.save("route", 77, "price", _custom("Post", valid_until=FUTURE))
    assert answer_slots.effective(route, "price")["source"] == "post"
    answer_slots.clear("route", 77, "price")
    assert answer_slots.effective(route, "price")["source"] == "category"


def test_an_expired_custom_answer_counts_as_expired(slots):
    topic = _topic()
    with slots.get_connection() as conn:
        conn.execute("INSERT INTO answer_slots (owner_type, owner_id, slot, kind, custom_text, valid_until) "
                     "VALUES ('topic', ?, 'price', 'CUSTOM', 'Old', ?)", (topic, PAST))
        conn.commit()
    assert answer_slots.effective({"topic_id": topic}, "price")["expired"] is True


@pytest.mark.parametrize("result, answer, expected", [
    ({"ok": False}, None, "failed"),
    ({"ok": True, "slot": None}, None, "other"),
    ({"ok": True, "slot": "price", "specific": True, "confidence": 1}, {"expired": False}, "specific"),
    ({"ok": True, "slot": "price", "specific": False, "confidence": 0.5}, {"expired": False}, "low_confidence"),
    ({"ok": True, "slot": "price", "specific": False, "confidence": 1}, None, "empty"),
    ({"ok": True, "slot": "price", "specific": False, "confidence": 1}, {"expired": True}, "expired"),
    ({"ok": True, "slot": "price", "specific": False, "confidence": 1}, {"expired": False}, "would_send"),
])
def test_outcomes(result, answer, expected):
    assert answer_slots.outcome_for(result, answer) == expected


@pytest.mark.parametrize("raw, ok", [('{"slot":"price","specific":false,"confidence":0.9}', True), ("not json", False), ("[]", False)])
def test_classify_reads_or_fails_safely(raw, ok):
    with patch.object(ai_responder, "_query_groq", return_value=(raw, True, "x")):
        assert answer_slots.classify("dam koto")["ok"] is ok


def test_classify_treats_an_unknown_type_as_other():
    with _groq({"slot": "weather", "specific": False, "confidence": 1}):
        assert answer_slots.classify("x")["slot"] is None


def test_shadow_feeds_needs_answers_until_the_gap_is_filled(slots):
    topic = _topic("Sondesh Gap")
    with _groq({"slot": "price", "specific": False, "confidence": 0.95}):
        answer_slots.SlotShadow()._run("dam koto", {"routing_id": 5, "topic_id": topic})
        answer_slots.SlotShadow()._run("rate kya hai", {"routing_id": 5, "topic_id": topic})
    group = answer_slots.needs_answers()[0]
    assert (group["topic_name"], group["label"], group["count"], group["state"]) == ("Sondesh Gap", "Price", 2, "empty")
    assert {item["message"] for item in group["examples"]} == {"dam koto", "rate kya hai"}
    answer_slots.save("topic", topic, "price", _custom(valid_until=FUTURE))
    assert answer_slots.needs_answers() == []
    assert answer_slots.shadow_report()["outcomes"] == {"empty": 2}


def test_a_failed_check_is_recorded_and_never_raises(slots):
    with patch.object(ai_responder, "_query_groq", return_value=("", False, "groq_http_500")):
        answer_slots.SlotShadow()._run("x", {"routing_id": 5, "topic_id": _topic()})
    assert answer_slots.shadow_report()["outcomes"] == {"failed": 1}


def test_save_to_approved_points_the_slot_at_the_new_answer(slots):
    topic = _topic("Saved Topic")
    answer_slots.save("topic", topic, "details", _custom("Handmade daily."))
    qa_id = answer_slots.save_to_approved("topic", topic, "details", "tester")
    assert approved_qa.get(qa_id).answer == "Handmade daily."
    assert answer_slots.effective({"topic_id": topic}, "details")["kind"] == "APPROVED_QA"
    with pytest.raises(ValueError):
        answer_slots.save_to_approved("topic", topic, "details", "tester")


def test_a_handoff_is_checked_and_its_reply_is_unchanged(ad_customer, monkeypatch):
    monkeypatch.setattr(config, "VARIATION_SHADOW_ENABLED", True)
    sender, _facts, db = ad_customer
    _custom_route(ad_customer)
    with patch.object(answer_slots.slot_shadow, "record_shadow") as record, \
            patch("camellia.variation_shadow.variation_shadow.record_shadow"):
        _ask(ad_customer, "Do you ship to the moon on Sunday night?")
    assert record.call_count == 1
    assert record.call_args.args[0] == "Do you ship to the moon on Sunday night?"
    assert "handoff" in str(db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]["template_id"])


def test_a_human_request_is_not_checked(ad_customer, monkeypatch):
    monkeypatch.setattr(config, "VARIATION_SHADOW_ENABLED", True)
    _custom_route(ad_customer)
    with patch.object(answer_slots.slot_shadow, "record_shadow") as record:
        _ask(ad_customer, "Can I talk to a human?")
    assert not record.called


def test_shadow_is_off_with_its_flag(monkeypatch):
    monkeypatch.setattr(config, "VARIATION_SHADOW_ENABLED", False)
    shadow = answer_slots.SlotShadow()
    with patch.object(shadow, "_run") as run:
        shadow.record_shadow("x", {"topic_id": "t"})
    assert not run.called


def test_slot_routes(admin, slots):  # noqa: F811
    client, headers = admin
    topic = _topic("Route Topic")
    assert client.put(f"{BASE}/topic/{topic}/price", headers=headers, json=_custom()).status_code == 400
    assert client.put(f"{BASE}/topic/{topic}/details", headers=headers, json=_custom("Fresh")).status_code == 200
    rows = client.get(f"{BASE}/topic/{topic}", headers=headers).json()["slots"]
    assert next(row for row in rows if row["slot"] == "details")["answer"]["text"] == "Fresh"
    assert client.get(f"{BASE}/nope/{topic}", headers=headers).status_code == 400
    assert client.delete(f"{BASE}/topic/{topic}/details", headers=headers).status_code == 200
    assert client.get("/admin/categories/needs-answers", headers=headers).json()["groups"] == []
    assert client.get("/admin/answer-slot-shadow", headers=headers).json()["outcomes"] == {}


def test_add_as_phrase_saves_on_a_custom_mapping_only(admin, slots):  # noqa: F811
    client, headers = admin
    topic = instagram_routing.create_topic("Phrase Topic")
    saved = _route(slots, "reel", topic, "PHRASEREEL")
    path = "/admin/categories/needs-answers/phrase"
    response = client.post(path, headers=headers, json={"routing_id": saved["routing_id"], "phrase": "Box ka rate?"})
    assert response.status_code == 200, response.text
    stored = instagram_routing.decode_route(slots.get_instagram_source_route(saved["routing_id"]))
    assert stored["custom_response_variations"] == ["Box ka rate?"]
    assert client.post(path, headers=headers, json={"routing_id": 99999, "phrase": "x"}).status_code == 404


def test_add_as_phrase_refuses_a_bad_mapping_id(admin):  # noqa: F811
    client, headers = admin
    response = client.post("/admin/categories/needs-answers/phrase", headers=headers, json={"routing_id": "x", "phrase": "y"})
    assert response.status_code == 404


def test_old_shadow_rows_are_removed(slots):
    with slots.get_connection() as conn:
        conn.execute("INSERT INTO answer_slot_shadow (user_message, outcome, timestamp) VALUES ('old', 'empty', datetime('now', '-91 days'))")
        conn.commit()
    with patch.object(ai_responder, "_query_groq", return_value=("", False, "x")):
        answer_slots.SlotShadow()._run("new", {"routing_id": 5, "topic_id": _topic()})
    with slots.get_connection() as conn:
        assert [row[0] for row in conn.execute("SELECT user_message FROM answer_slot_shadow")] == ["new"]


def test_slot_routes_need_admin():
    assert TestClient(app_module.app).get("/admin/categories/needs-answers").status_code in (401, 403)



def _gap(topic, message="iye sthaan pawa jabe"):
    with _groq({"slot": "location", "specific": False, "confidence": 0.9}):
        answer_slots.SlotShadow()._run(message, {"routing_id": 5, "topic_id": topic})


def test_a_dismissed_gap_stays_away_until_a_customer_asks_again(slots):
    topic = _topic("Dismiss Gap")
    _gap(topic)
    answer_slots.dismiss_gap(topic, "location")
    assert answer_slots.needs_answers() == []
    with answer_slots.db.get_connection() as conn:
        conn.execute("UPDATE answer_slot_dismissed SET dismissed_at=datetime('now', '-1 hour')")
        conn.execute("UPDATE answer_slot_shadow SET timestamp=datetime('now', '-2 hours')")
        conn.commit()
    _gap(topic, "again please")
    group = answer_slots.needs_answers()[0]
    assert group["count"] == 1 and group["examples"][0]["message"] == "again please"


def test_own_questions_are_answered_classified_and_removed(slots):
    topic, other = _topic("Own A"), _topic("Own B")
    slot = answer_slots.add_own_question(topic, "  Are seats   available? ")
    assert answer_slots.own_questions(topic) == {slot: "Are seats available?"} and answer_slots.own_questions(other) == {}
    answer_slots.save("topic", topic, slot, _custom())
    row = next(item for item in answer_slots.list_slots("topic", topic) if item["slot"] == slot)
    assert row["own"] and row["answer"]["text"] == "Rs 500 a box"
    assert not any(item.get("own") for item in answer_slots.list_slots("topic", other))
    with pytest.raises(ValueError):
        answer_slots.save("topic", other, slot, _custom())
    with _groq({"slot": slot, "specific": False, "confidence": 0.9}):
        assert answer_slots.classify("sthaan pawa jabe", answer_slots.own_questions(topic))["slot"] == slot
    with _groq({"slot": slot, "specific": False, "confidence": 0.9}):
        assert answer_slots.classify("sthaan pawa jabe")["slot"] is None
    answer_slots.remove_own_question(topic, slot)
    assert answer_slots.own_questions(topic) == {} and answer_slots.effective({"topic_id": topic}, slot) is None


@pytest.mark.parametrize("label", ["", "x" * 61, "Price", "price"])
def test_bad_own_questions_are_refused(slots, label):
    with pytest.raises(ValueError):
        answer_slots.add_own_question(_topic("Own Bad"), label)


def test_own_question_and_dismiss_routes(admin, slots):  # noqa: F811
    client, headers = admin
    topic = _topic("Own Route")
    made = client.post(f"/admin/categories/own-questions/{topic}", headers=headers, json={"label": "Parking?"})
    assert made.status_code == 200
    assert client.post("/admin/categories/own-questions/missing", headers=headers, json={"label": "x"}).status_code == 404
    assert client.post("/admin/categories/own-questions/" + topic, headers=headers, json={"label": ""}).status_code == 400
    assert client.delete(f"/admin/categories/own-questions/{topic}/{made.json()['slot']}", headers=headers).status_code == 200
    assert client.post("/admin/categories/needs-answers/dismiss", headers=headers, json={"topic_id": topic}).status_code == 400
    assert client.post("/admin/categories/needs-answers/dismiss", headers=headers, json={"topic_id": topic, "slot": "price"}).status_code == 200


def test_a_wrongly_typed_question_can_be_moved_to_the_right_one(slots):
    topic = _topic("Move Gap")
    seats = answer_slots.add_own_question(topic, "Are seats available?")
    _gap(topic)
    assert {item["slot"] for item in answer_slots.needs_answers()[0]["choices"]} >= {"price", seats}
    answer_slots.move_gap(topic, "location", "iye sthaan pawa jabe", seats)
    group = answer_slots.needs_answers()[0]
    assert (group["slot"], group["label"]) == (seats, "Are seats available?")
    answer_slots.save("topic", topic, seats, _custom())
    assert answer_slots.needs_answers() == []


def test_a_question_cannot_move_to_an_unknown_type(slots):
    topic = _topic("Move Bad")
    _gap(topic)
    with pytest.raises(ValueError):
        answer_slots.move_gap(topic, "location", "iye sthaan pawa jabe", "nope")
    assert answer_slots.needs_answers()[0]["slot"] == "location"


def test_move_route(admin, slots):  # noqa: F811
    client, headers = admin
    topic = _topic("Move Route")
    _gap(topic)
    body = {"topic_id": topic, "slot": "location", "message": "iye sthaan pawa jabe"}
    assert client.post("/admin/categories/needs-answers/move", headers=headers, json={**body, "to": "x"}).status_code == 400
    assert client.post("/admin/categories/needs-answers/move", headers=headers, json={**body, "to": "booking"}).status_code == 200
    assert answer_slots.needs_answers()[0]["slot"] == "booking"
