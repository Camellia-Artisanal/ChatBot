from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import camellia.app as app_module
import camellia.assisted_resolution as assisted
from camellia.ad_gate import describe_entry_point
from camellia.config import config
from camellia.database import Database

from test_ad_mapped_source_context import ad_customer, _process  # noqa: F401


class _ApprovedRag:
    def retrieve(self, message, *, persist_candidate=True):
        assert persist_candidate is False
        return SimpleNamespace(
            proposed_response="Jolbhora is available in our approved menu.",
            source_refs=["qa_templates.json#jolbhora"],
            source_chunks=["Jolbhora is available in our approved menu."],
            confidence_score=0.9,
            proposed_intent="jolbhora",
        )

    def grounding_report(self, answer, chunks):
        return True, "grounded"


@pytest.fixture
def isolated_assisted(monkeypatch, tmp_path):
    database = Database(str(tmp_path / "assisted.db"))
    monkeypatch.setattr(assisted, "db", database)
    monkeypatch.setattr(assisted, "rag_fallback_engine", _ApprovedRag())
    monkeypatch.setattr(config, "LLM_ASSISTED_RESOLUTION_ENABLED", True)
    return database


def test_assisted_resolution_is_disabled_by_default_and_whatsapp_is_blocked(monkeypatch):
    monkeypatch.setattr(config, "LLM_ASSISTED_RESOLUTION_ENABLED", False)
    assert assisted.create_suggestion(user_id="u", channel="instagram", customer_message="hello") is None
    monkeypatch.setattr(config, "LLM_ASSISTED_RESOLUTION_ENABLED", True)
    assert assisted.create_suggestion(user_id="u", channel="whatsapp", customer_message="hello") is None
    assert assisted.is_whatsapp_allowed("whatsapp") is False


def test_grounded_instagram_suggestion_is_deduplicated(isolated_assisted):
    first = assisted.create_suggestion(user_id="u", channel="instagram", customer_message="Tell me about Jolbhora")
    second = assisted.create_suggestion(user_id="u", channel="instagram", customer_message="Tell me about Jolbhora")
    assert first["id"] == second["id"]
    assert first["triage_category"] == "AUTO_RESOLVE"
    assert first["grounding_status"] == "Grounded"
    assert first["status"] == "PENDING_REVIEW"


def test_risky_request_remains_human_gated(isolated_assisted):
    item = assisted.create_suggestion(
        user_id="u2", channel="instagram",
        customer_message="I need 50 boxes for my wedding in Delhi",
    )
    assert item["triage_category"] == "AI_SUGGESTED"
    assert "event_date" in item["risk_flags"]


def test_authenticated_approval_is_the_only_send_path(monkeypatch, tmp_path):
    database = Database(str(tmp_path / "api.db"))
    monkeypatch.setattr(app_module, "db", database)
    monkeypatch.setattr(config, "ADMIN_API_KEY", "assisted-test-api-key-long-enough")
    sent = []
    # The production sender accepts correlation/audit metadata as keyword-only
    # arguments.  Keep this test focused on the approval gate while allowing
    # those observability fields to pass through the mock.
    monkeypatch.setattr(
        app_module,
        "send_instagram_reply",
        lambda user_id, text, image_url=None, **kwargs: sent.append((user_id, text)),
    )
    suggestion_id = database.create_assisted_suggestion(
        user_id="u3", channel="instagram", customer_message="What is Jolbhora?",
        triage_category="AUTO_RESOLVE", suggested_answer="Jolbhora is a sweet.",
        original_suggested_answer="Jolbhora is a sweet.", grounding_status="Grounded",
    )
    client = TestClient(app_module.app)
    assert client.post(f"/admin/assisted-resolutions/{suggestion_id}/approve", json={}).status_code in {401, 403}
    response = client.post(
        f"/admin/assisted-resolutions/{suggestion_id}/approve",
        headers={"x-admin-key": "assisted-test-api-key-long-enough"}, json={},
    )
    assert response.status_code == 200
    assert sent == [("u3", "Jolbhora is a sweet.")]
    assert database.get_assisted_suggestion(suggestion_id)["status"] == "APPROVED"


UNANSWERED = "Do you ship to Antarctica?"


def _instagram_message(ad_customer, monkeypatch, message=UNANSWERED):  # noqa: F811
    monkeypatch.setattr(config, "LLM_ASSISTED_RESOLUTION_ENABLED", True)
    monkeypatch.setattr(config, "LLM_AUTO_RESOLVE_SIMULATION_ENABLED", True)
    entry = describe_entry_point({"sender": {"id": ad_customer[0]}, "message": {"text": message}})
    return _process(ad_customer, message, entry)


def test_an_untouched_order_draft_does_not_block_a_suggestion(ad_customer, monkeypatch):  # noqa: F811
    with patch.object(app_module, "create_suggestion", return_value={"id": 1}) as suggest,             patch.object(app_module, "simulate_auto_resolve") as simulate:
        _instagram_message(ad_customer, monkeypatch)
    assert suggest.call_args.kwargs["active_protected_flow"] is False
    simulate.assert_called_once_with({"id": 1}, protected_order_flow=False, source_routed=False)


@pytest.mark.parametrize("order", [
    {"cart": [{"name": "Bento", "quantity": 2}]},
    {"order_intake_draft": {"status": "COLLECTING"}},
    {"order_draft": {"flavour": "mango", "collection": "signature", "size": None, "quantity": 1}},
    {"order_draft": {"flavour": None, "collection": None, "size": "500g", "quantity": 2}},
])
def test_a_real_order_gets_no_suggestion(ad_customer, monkeypatch, order):  # noqa: F811
    ad_customer[1].update(order)
    with patch.object(app_module, "create_suggestion") as suggest:
        _instagram_message(ad_customer, monkeypatch)
    suggest.assert_not_called()


def test_one_product_mention_is_not_an_order(ad_customer, monkeypatch):  # noqa: F811
    ad_customer[1]["order_draft"]["flavour"] = "mango"
    with patch.object(app_module, "create_suggestion", return_value=None) as suggest:
        _instagram_message(ad_customer, monkeypatch)
    suggest.assert_called_once()


def test_a_failed_suggestion_never_costs_the_customer_a_reply(ad_customer, monkeypatch):  # noqa: F811
    with patch.object(app_module, "create_suggestion", side_effect=RuntimeError("db down")):
        send = _instagram_message(ad_customer, monkeypatch)
    assert send.called
