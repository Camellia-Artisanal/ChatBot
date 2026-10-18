"""'On mapped posts/ads, use for' (18 Oct).

"How much does it cost?" belongs to General price everywhere, so a Kolkata
answer could not claim it. Each answer now names the customer questions it
answers where it is ticked on a post/reel/ad: General price on a general
post, Kolkata price on a Kolkata post, General price with no ad at all.
"""
import pytest
from fastapi.testclient import TestClient

from camellia import app as app_module, approved_qa, instagram_story_qa

from test_approved_qa_contexts import store  # noqa: F401


def _publish(body):
    draft = approved_qa.preview(body)
    return approved_qa.publish({**body, "qa_id": draft["qa_id"], "preview_token": draft["preview_token"]}, "test")


def _kolkata(**extra):
    approved_qa.save_context("Kolkata Bengali Home Table", "test")
    return _publish(dict(question="What is the price of the Kolkata lunch?", answer="KOLKATA PRICE",
                         context="kolkata_bengali_home_table", aliases=[], **extra))


def _ask(message, ids, context="kolkata_bengali_home_table"):
    found = approved_qa.match(message, {"last_topic": context}, allowed_qa_ids=set(ids), product_fallback=True)
    return found.entry.qa_id if found else None


def test_price_role_answers_its_questions_only_where_ticked(store):
    kolkata = _kolkata(mapped_role="price", mapped_questions=["How much does it cost?", "Kitna hai?"])
    assert kolkata["mapped_role"] == "price" and "Kitna hai?" in kolkata["mapped_questions"]
    assert _ask("How much does it cost?", [kolkata["qa_id"]]) == kolkata["qa_id"]
    assert _ask("Kitna hai?", [kolkata["qa_id"]]) == kolkata["qa_id"]
    # No ad: the general answer, never Kolkata.
    assert approved_qa.match("How much does it cost?", {}).entry.qa_id == "general_price"
    outside = approved_qa.match("Kitna hai?", {})
    assert outside is None or outside.entry.qa_id != kolkata["qa_id"]


def test_choosing_a_use_fills_the_usual_questions(store):
    roles = {role["value"]: role for role in approved_qa.listing()["mapped_roles"]}
    assert "How much does it cost?" in roles["price"]["questions"]
    assert "Hello, can I get more info on this?" in roles["details"]["questions"]
    assert {"booking", "location", "date"} <= set(roles)
    record = next(r for r in approved_qa.listing()["records"] if r["qa_id"] == "general_price")
    assert record["mapped_role"] == "price" and "How much does it cost?" in record["mapped_questions"]


def test_mapping_save_refuses_two_answers_claiming_one_question(store):
    kolkata = _kolkata(mapped_role="price")
    with pytest.raises(ValueError, match='is used by both'):
        instagram_story_qa.validate("kolkata_bengali_home_table", [kolkata["qa_id"], "general_price"], approved_qa.overlays())
    # Removing the question from one answer clears the clash.
    _publish(dict(qa_id=kolkata["qa_id"], mapped_role="price", mapped_questions=["Kitna hai?"]))
    instagram_story_qa.validate("kolkata_bengali_home_table", [kolkata["qa_id"], "general_price"], approved_qa.overlays())


def test_nothing_extra_claims_no_questions(store):
    kolkata = _kolkata(mapped_role="none", mapped_questions=[])
    assert kolkata["mapped_role"] is None and kolkata["mapped_questions"] == []
    assert _ask("Kitna hai?", [kolkata["qa_id"]]) is None


def test_preview_table_endpoint(store, monkeypatch):
    from camellia.config import config
    monkeypatch.setattr(config, "ADMIN_API_KEY", "preview-admin")
    kolkata = _kolkata(mapped_role="price")
    response = TestClient(app_module.app).post("/admin/instagram-routes/answer-preview", headers={"x-admin-key": "preview-admin"},
                                               json={"approved_qa_ids": [kolkata["qa_id"]], "context": "kolkata_bengali_home_table"})
    rows = {row["question"]: row["qa_id"] for row in response.json()["rows"]}
    assert rows["How much does it cost?"] == kolkata["qa_id"] and rows["Where is it?"] is None
    clash = TestClient(app_module.app).post("/admin/instagram-routes/answer-preview", headers={"x-admin-key": "preview-admin"},
                                            json={"approved_qa_ids": [kolkata["qa_id"], "general_price"], "context": "general"}).json()
    assert clash["collisions"] and clash["collisions"][0]["question"] == "How much does it cost?"


def test_editor_and_mapping_screens_carry_the_controls():
    from camellia.admin_approved_qa import SCRIPT
    from camellia.admin_source_response import SCRIPT as MAPPING
    assert "On mapped posts/ads, use for" in SCRIPT and "mapped_questions:" in SCRIPT and "function aqRoleChanged" in SCRIPT
    assert "What customers on this post will get" in MAPPING and "/admin/instagram-routes/answer-preview" in MAPPING


@pytest.mark.parametrize("message", ["What are the costs of preparing a full Bengali meal",
                                     "How much for catering 30 people?", "Cost of hosting a party?"])
def test_custom_catering_quote_goes_to_the_manager(store, message):
    # 18 Oct, asenc911 on the YOU HOST ad got the general price list.
    general = {"general_price", "general_details", "price_is_high"}
    assert _ask(message, general, context="savoury") is None
    assert approved_qa.match(message, {}) is None
    assert _ask("How much does it cost?", general, context="savoury") == "general_price"
    assert approved_qa.match("How much does it cost?", {}).entry.qa_id == "general_price"


def test_ads_card_price_check_uses_the_checklists_use_for(store, monkeypatch):
    # 18 Oct: Kolkata ads answered "How much does it cost?" with the Kolkata
    # answer, but the Ads card and /health said it went to the manager.
    from camellia import instagram_ads
    kolkata = _kolkata(mapped_role="price")
    route = {"routing_id": 1, "active": True, "response_mode": "APPROVED_QA", "qa_bundle": True,
             "source_context": "kolkata_bengali_home_table", "approved_qa_ids": [kolkata["qa_id"]]}
    monkeypatch.setattr("camellia.instagram_source_qa.effective",
                        lambda database, bundle: (approved_qa.overlays(), {kolkata["qa_id"]}))
    assert instagram_ads.price_answer_status(store, route) == "APPROVED"
