"""Question variations in the Custom response box: exact, per source, never over an order."""
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from camellia import app as app_module, instagram_routing
from camellia.ai_responder import ai_responder
from camellia.config import config
from camellia.instagram_routing import decode_route, matches_custom_response_variation, validate_route_payload

from test_ad_mapped_source_context import ad_customer, _process  # noqa: F401
from test_custom_mapping_topic_answers import AD, NOTE, _ask, _custom_route
from test_followup_manual_sequences import assets, auth, store  # noqa: F401

PHRASE = "Is the box recyclable?"
URL = "https://www.instagram.com/p/VARIATION1/"


def _with_variations(ad_customer, phrases):
    route = decode_route(_custom_route(ad_customer))
    route["custom_response_variations"] = phrases
    return ad_customer[2].save_instagram_source_route(route, int(route["routing_id"]))


def _last(ad_customer):
    sender, _facts, db = ad_customer
    return db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]


@pytest.mark.parametrize("message", [PHRASE, "  is the BOX recyclable  ", "Is the box, recyclable!!"])
def test_a_listed_phrase_sends_the_custom_response_every_time(ad_customer, message):
    _with_variations(ad_customer, [PHRASE])
    sender, facts, db = ad_customer
    for _ in range(2):
        send = _ask(ad_customer, message)
        stored = _last(ad_customer)
        assert stored["bot_response"] == NOTE
        assert stored["template_id"] == "source_description_variation"
    assert facts["last_route_source"] == "SOURCE_DESCRIPTION_VARIATION"
    assert db.get_active_handoff("instagram", sender) is None
    assert any(call.kwargs.get("image_url") for call in send.call_args_list)


def test_a_phrase_that_only_resembles_one_is_not_answered(ad_customer):
    _with_variations(ad_customer, [PHRASE])
    _ask(ad_customer, "Is the box recyclable in Delhi?")
    assert _last(ad_customer)["template_id"] != "source_description_variation"


def test_human_help_beats_a_listed_phrase(ad_customer):
    _with_variations(ad_customer, ["Can I talk to a human?"])
    _ask(ad_customer, "Can I talk to a human?")
    assert _last(ad_customer)["template_id"] != "source_description_variation"


def test_an_order_in_progress_beats_a_listed_phrase(ad_customer):
    _with_variations(ad_customer, [PHRASE])
    _sender, facts, _db = ad_customer
    facts["cart"] = [{"name": "Bento", "quantity": 2}]
    _ask(ad_customer, PHRASE)
    assert _last(ad_customer)["template_id"] != "source_description_variation"
    assert facts["cart"] == [{"name": "Bento", "quantity": 2}]


@pytest.mark.parametrize("phrase, message", [("দাম কত?", "দাম কত"), ("कितने का है?", "कितने का है"), ("Dam koto?", "dam koto")])
def test_phrases_match_in_any_script(phrase, message):
    clean = validate_route_payload({"url": URL, "routing_topic": "T", "source_description": NOTE,
                                    "custom_response_variations": [phrase]})
    assert clean["custom_response_variations"] == [phrase]
    assert matches_custom_response_variation(clean, message)
    assert not matches_custom_response_variation(clean, "500")


def test_validation_limits_and_duplicates():
    base = {"url": URL, "routing_topic": "T", "source_description": NOTE}
    clean = validate_route_payload({**base, "custom_response_variations": ["Price?", " price ", "PRICE!"]})
    assert clean["custom_response_variations"] == ["Price?"]
    for bad in ("Price?", [1], ["?!"], ["x" * 181], [f"q{i}" for i in range(31)]):
        with pytest.raises(ValueError):
            validate_route_payload({**base, "custom_response_variations": bad})
    with pytest.raises(ValueError):
        validate_route_payload({**base, "source_description": "", "custom_response_variations": ["Price?"]})


def test_media_save_keeps_stored_phrases_unless_the_box_is_sent(store, assets):  # noqa: F811
    client = TestClient(app_module.app)
    topic = store.create_instagram_topic("variation-unit", "Variation topic", "", None, [], [])
    item = client.post("/admin/instagram-media/manual-link", headers=auth(), json={"url": URL}).json()["item"]
    payload = {"topic_id": topic["topic_id"], "response_mode": "CUSTOM", "source_description": NOTE,
               "menu_mode": "NONE"}
    path = f"/admin/instagram-media/{item['id']}/mapping"

    saved = client.put(path, headers=auth(), json={**payload, "custom_response_variations": ["Price?"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["route"]["custom_response_variations"] == ["Price?"]
    assert client.put(path, headers=auth(), json=payload).json()["route"]["custom_response_variations"] == ["Price?"]
    assert client.put(path, headers=auth(), json={**payload, "custom_response_variations": []}
                      ).json()["route"]["custom_response_variations"] == []
    reloaded = client.get(f"/admin/instagram-media/{item['id']}", headers=auth()).json()["item"]["mapping_route"]
    assert reloaded["custom_response_variations"] == []


def _suggest(body, groq_reply=None):
    outcome = groq_reply if groq_reply is not None else ("", False, "groq_http_500")
    with patch.object(ai_responder, "get_active_provider", return_value="groq"), \
            patch.object(ai_responder, "_query_groq", return_value=outcome) as groq:
        response = TestClient(app_module.app).post(
            "/admin/approved-qa/variation-suggestions", headers=auth(), json=body)
    return response, groq


def test_suggestions_keep_only_new_english_phrases(store):  # noqa: F811
    phrases = ["Price?", "price!!", "How much?", "How’s the box?", "দাম কত?", "x" * 181, 5]
    response, groq = _suggest({"reply": NOTE, "existing": ["PRICE"]},
                              (json.dumps({"phrases": phrases}), True, "groq_rag"))
    assert response.status_code == 200, response.text
    assert response.json() == {"suggestions": ["How much?", "How's the box?"]}
    assert NOTE in groq.call_args.args[0] and "PRICE" in groq.call_args.args[0]


def test_suggesting_never_saves_a_phrase(ad_customer):
    route = decode_route(_custom_route(ad_customer))
    _suggest({"reply": NOTE}, ('{"phrases":["How much?"]}', True, "groq_rag"))
    stored = ad_customer[2].get_instagram_source_route(int(route["routing_id"]))
    assert decode_route(stored)["custom_response_variations"] == []


@pytest.mark.parametrize("outcome", [("", False, "groq_http_500"), ("not json", True, "groq_rag"), ("[]", True, "groq_rag")])
def test_a_failed_suggestion_says_so(store, outcome):  # noqa: F811
    response, _groq = _suggest({"reply": NOTE}, outcome)
    assert response.status_code == 503
    assert "by hand" in response.json()["detail"]


def test_a_bad_json_generation_does_not_pause_customer_replies(monkeypatch):
    response = MagicMock(status_code=400)
    response.json.return_value = {"error": {"code": "json_validate_failed"}}
    monkeypatch.setattr(ai_responder, "_groq_cooldown_until", 0.0)
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    with patch("camellia.ai_responder.httpx.Client") as client:
        client.return_value.__enter__.return_value.post.return_value = response
        _text, ok, _reason = ai_responder._query_groq(
            "x", model=config.GROQ_MODEL, response_format={"type": "json_object"})
    assert not ok
    assert ai_responder._groq_cooldown_until == 0.0


@pytest.mark.parametrize("body", [{"reply": ""}, {"reply": NOTE, "existing": "Price?"}, {"reply": "x" * 4001}])
def test_a_bad_suggestion_request_never_reaches_the_model(store, body):  # noqa: F811
    response, groq = _suggest(body)
    assert response.status_code == 400
    groq.assert_not_called()


def test_admin_dashboard_has_a_box_on_every_custom_editor():
    html = app_module.get_admin_dashboard().body.decode("utf-8")
    for box in ("igCustomResponseVariations", "igStoryCustomResponseVariations", "igMediaCustomResponseVariations"):
        assert f'id="{box}"' in html
