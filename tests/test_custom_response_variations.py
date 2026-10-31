"""Question variations in the Custom response box: exact, per source, never over an order."""
import pytest
from fastapi.testclient import TestClient

from camellia import app as app_module, instagram_routing
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


def test_admin_dashboard_has_a_box_on_every_custom_editor():
    html = app_module.get_admin_dashboard().body.decode("utf-8")
    for box in ("igCustomResponseVariations", "igStoryCustomResponseVariations", "igMediaCustomResponseVariations"):
        assert f'id="{box}"' in html
