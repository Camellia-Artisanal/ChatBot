"""Meaning match in shadow: records what it would send and never changes a reply."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from camellia import app as app_module, variation_shadow as module
from camellia.ai_responder import ai_responder
from camellia.config import config

from test_ad_mapped_source_context import ad_customer, _process  # noqa: F401
from test_custom_mapping_topic_answers import _ask, _custom_route  # noqa: F401
from test_custom_response_variations import _with_variations
from test_followup_manual_sequences import auth, store  # noqa: F401

PHRASES = ["Is the box recyclable?", "How much per box?"]
ROUTE = {"routing_id": 7, "source_description": "A note.", "custom_response_variations": PHRASES}


def _groq(text, ok=True, reason="groq_rag"):
    return patch.object(ai_responder, "_query_groq", return_value=(text, ok, reason))


@pytest.mark.parametrize("text, phrase, confidence", [
    ('{"phrase":2,"confidence":0.9}', PHRASES[1], 0.9),
    ('{"phrase":0,"confidence":0.9}', None, 0.9),
    ('{"phrase":9,"confidence":0.9}', None, 0.9),
])
def test_match_reads_the_chosen_phrase(text, phrase, confidence):
    with _groq(text):
        result = module.match("box ka rate", PHRASES, "A note.")
    assert (result["phrase"], result["confidence"]) == (phrase, confidence)


@pytest.mark.parametrize("outcome", [("", False, "groq_http_500"), ("not json", True, "x"), ("[]", True, "x")])
def test_a_failed_check_is_no_match(outcome):
    with patch.object(ai_responder, "_query_groq", return_value=outcome):
        assert module.match("x", PHRASES, "A note.")["ok"] is False


def test_records_would_send_only_above_the_threshold(store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(module, "db", store)
    shadow = module.VariationShadow()
    with _groq('{"phrase":2,"confidence":0.9}'):
        shadow._run("box ka rate", PHRASES, "A note.", 7)
    with _groq('{"phrase":2,"confidence":0.5}'):
        shadow._run("box?", PHRASES, "A note.", 7)
    report = store.variation_shadow_report()
    assert (report["checked"], report["would_send"], report["low_confidence"]) == (2, 1, 1)
    assert report["rows"][0]["user_message"] == "box ka rate"


def test_a_failed_check_is_recorded_as_failed(store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(module, "db", store)
    with _groq("", False, "groq_http_500"):
        module.VariationShadow()._run("x", PHRASES, "A note.", 7)
    report = store.variation_shadow_report()
    assert (report["checked"], report["failed"], report["would_send"]) == (1, 1, 0)


def test_off_by_default_and_needs_phrases(monkeypatch):
    shadow = module.VariationShadow()
    monkeypatch.setattr(config, "VARIATION_SHADOW_ENABLED", False)
    with patch.object(shadow, "_run") as run:
        shadow.record_shadow("x", ROUTE)
        assert not run.called
    monkeypatch.setattr(config, "VARIATION_SHADOW_ENABLED", True)
    with patch.object(shadow, "_run") as run:
        shadow.record_shadow("x", {**ROUTE, "custom_response_variations": []})
        assert not run.called


def test_a_handoff_is_checked_and_its_reply_is_unchanged(ad_customer, monkeypatch):
    monkeypatch.setattr(config, "VARIATION_SHADOW_ENABLED", True)
    _with_variations(ad_customer, PHRASES)
    sender, _facts, db = ad_customer
    with patch.object(module.variation_shadow, "record_shadow") as record:
        _ask(ad_customer, "Do you ship to the moon on Sunday night?")
    stored = db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]
    assert record.call_count == 1
    assert record.call_args.args[0] == "Do you ship to the moon on Sunday night?"
    assert stored["template_id"] != "source_description_variation"


def test_an_exact_phrase_and_a_human_request_are_not_checked(ad_customer, monkeypatch):
    monkeypatch.setattr(config, "VARIATION_SHADOW_ENABLED", True)
    _with_variations(ad_customer, PHRASES)
    with patch.object(module.variation_shadow, "record_shadow") as record:
        _ask(ad_customer, PHRASES[0])
        _ask(ad_customer, "Can I talk to a human?")
    assert not record.called


def test_report_needs_admin_and_explains_when_off(store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "VARIATION_SHADOW_ENABLED", False)
    client = TestClient(app_module.app)
    assert client.get("/admin/variation-shadow").status_code in {401, 403}
    assert "off" in client.get("/admin/variation-shadow", headers=auth()).json()["note"]
