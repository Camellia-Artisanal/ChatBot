"""Client-added categories, and generic questions answered from them (18 Oct).

The client wants a Kolkata Bengali Home Table kept apart from the Gurgaon
one. Customers ask "Tell me more" / "How much does it cost?", never with the
city, so the ad's checklist (one category) decides which answer they get.
"""
import pytest
from fastapi.testclient import TestClient

from camellia import approved_qa, app as app_module
from camellia.database import Database

from test_approved_qa_contexts import store  # noqa: F401

HEADERS = {"x-admin-key": "contexts-admin"}


def _publish(question, answer, context):
    body = dict(question=question, answer=answer, context=context, aliases=[])
    draft = approved_qa.preview(body)
    return approved_qa.publish({**body, "qa_id": draft["qa_id"], "preview_token": draft["preview_token"]}, "test")


def test_add_rename_and_delete_a_category(store, monkeypatch):
    from camellia.config import config
    monkeypatch.setattr(config, "ADMIN_API_KEY", "contexts-admin")
    client = TestClient(app_module.app)
    added = client.post("/admin/approved-qa/contexts", headers=HEADERS, json={"label": "Kolkata Bengali Home Table"}).json()
    assert added == {"value": "kolkata_bengali_home_table", "label": "Kolkata Bengali Home Table"}
    assert client.post("/admin/approved-qa/contexts", headers=HEADERS, json={"label": "kolkata bengali home table"}).status_code == 409
    renamed = client.patch("/admin/approved-qa/contexts/kolkata_bengali_home_table", headers=HEADERS, json={"label": "Kolkata Home Table"})
    assert renamed.status_code == 200
    options = {o["value"]: o for o in client.get("/admin/approved-qa", headers=HEADERS).json()["contexts"]}
    assert options["kolkata_bengali_home_table"]["label"] == "Kolkata Home Table" and options["kolkata_bengali_home_table"]["custom"]
    # Built-in categories carry bot behaviour and stay fixed.
    assert client.patch("/admin/approved-qa/contexts/sondesh", headers=HEADERS, json={"label": "X"}).status_code == 409
    assert client.delete("/admin/approved-qa/contexts/sondesh", headers=HEADERS).status_code == 409
    # A category still holding answers cannot be deleted.
    _publish("What is the price of the Kolkata lunch?", "KOLKATA PRICE", "kolkata_bengali_home_table")
    refused = client.delete("/admin/approved-qa/contexts/kolkata_bengali_home_table", headers=HEADERS)
    assert refused.status_code == 409 and "1 answer" in refused.json()["detail"]


def test_generic_questions_get_the_checklists_kolkata_answers(store):
    approved_qa.save_context("Kolkata Bengali Home Table", "test")
    price = _publish("What is the price of the Kolkata lunch?", "KOLKATA PRICE", "kolkata_bengali_home_table")
    details = _publish("Tell me more about the Kolkata Bengali Home Table", "KOLKATA DETAILS", "kolkata_bengali_home_table")
    allowed = {price["qa_id"], details["qa_id"], "general_payment_client"}
    facts = {"last_topic": "kolkata_bengali_home_table"}
    ask = lambda m: (approved_qa.match(m, facts, allowed_qa_ids=allowed, product_fallback=True) or type("N", (), {"entry": type("E", (), {"qa_id": None})})).entry.qa_id
    for message in ("Tell me more", "Can you tell me more about this?", "Hello, can I get more info on this?"):
        assert ask(message) == details["qa_id"], message
    for message in ("How much does it cost?", "What is the price?", "What is the price per person?",
                    "What is the price per box?", "What is the price of a box?"):
        assert ask(message) == price["qa_id"], message
    assert ask("How do I pay?") == "general_payment_client"
    assert ask("Is it vegetarian?") is None
    # Without an ad the Kolkata price never reaches other customers.
    assert approved_qa.match("How much does it cost?", {}).entry.qa_id == "general_price"


def test_manage_button_is_on_the_category_field():
    from camellia.admin_approved_qa import HTML, SCRIPT
    assert 'onclick="aqManageContexts()">Manage</button>' in SCRIPT and 'id="aqContextDialog"' in HTML


@pytest.mark.parametrize("message,role", [
    ("Pricing?", "price"), ("Price details", "price"), ("Price list", "price"), ("Charges?", "price"), ("Fees?", "price"),
    ("Info?", "details"), ("Information", "details"), ("What is this?", "details"), ("What is this about?", "details"),
    ("Interested", "details"), ("I am interested", "details"), ("I'm interested", "details"),
])
def test_short_ad_questions_use_the_checklists_answers_only_inside_a_mapping(store, message, role):
    # 18 Oct: these short ad replies went to the manager. Adding them as
    # variations would collide or leak Kolkata answers to customers with no ad.
    approved_qa.save_context("Kolkata Bengali Home Table", "test")
    price = _publish("What is the price of the Kolkata lunch?", "KOLKATA PRICE", "kolkata_bengali_home_table")
    details = _publish("Tell me more about the Kolkata Bengali Home Table", "KOLKATA DETAILS", "kolkata_bengali_home_table")
    allowed = {price["qa_id"], details["qa_id"]}
    found = approved_qa.match(message, {"last_topic": "kolkata_bengali_home_table"}, allowed_qa_ids=allowed, product_fallback=True)
    assert found.entry.qa_id == (price if role == "price" else details)["qa_id"]
    outside = approved_qa.match(message, {})
    assert outside is None or outside.entry.qa_id not in allowed


def test_category_heading_is_a_label_not_a_global_match(store):
    # 31 Oct: a Kolkata story-telling answer headed "tell me more" answered
    # every bare "tell me more", whatever the customer was asking about.
    approved_qa.save_context("Kolkata story telling sessions", "test")
    story = _publish("tell me more", "STORY SESSION DETAILS", "kolkata_story_telling_sessions")
    outside = approved_qa.match("tell me more", {})
    assert outside is None or outside.entry.qa_id != story["qa_id"]
    assert approved_qa.test("tell me more")["matched_id"] != story["qa_id"]
    # On a post/ad where it is ticked, the heading and the usual details questions still answer.
    allowed = {story["qa_id"]}
    for message in ("tell me more", "Tell me more", "Hello, can I get more info on this?"):
        found = approved_qa.match(message, {}, allowed_qa_ids=allowed, product_fallback=True)
        assert found and found.entry.qa_id == story["qa_id"], message
    # Variations the client adds still match everywhere.
    _publish_edit = approved_qa.preview({"qa_id": story["qa_id"], "aliases": ["Kali pujo story telling details"]})
    approved_qa.publish({"qa_id": story["qa_id"], "aliases": ["Kali pujo story telling details"],
                         "preview_token": _publish_edit["preview_token"]}, "test")
    assert approved_qa.match("Kali pujo story telling details", {}).entry.qa_id == story["qa_id"]
    # Another category can reuse the same heading.
    approved_qa.save_context("Gurgaon story telling sessions", "test")
    other = _publish("tell me more", "GURGAON STORY DETAILS", "gurgaon_story_telling_sessions")
    assert other["question"] == "tell me more"


def test_general_heading_still_matches_everywhere(store):
    general = _publish("Do you run story sessions?", "GENERAL STORY ANSWER", "general")
    assert approved_qa.match("Do you run story sessions?", {}).entry.qa_id == general["qa_id"]
    # And a category heading cannot be published as wording that already belongs to it.
    approved_qa.save_context("Kolkata story telling sessions", "test")
    with pytest.raises(ValueError, match="already matches"):
        approved_qa.preview(dict(question="Story heading", answer="X", context="kolkata_story_telling_sessions",
                                 aliases=["Do you run story sessions?"]))
