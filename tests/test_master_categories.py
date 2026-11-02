"""Master categories: one name links the lists, and no link changes a customer reply."""
import pytest
from fastapi.testclient import TestClient

from camellia import admin_categories, app as app_module, approved_qa, instagram_routing, menu_catalogue
from camellia.config import config
from camellia.instagram_routing import route_for_inbound

from test_ad_mapped_source_context import ad_customer  # noqa: F401
from test_admin_categories import _categories, _route, admin  # noqa: F401

MASTER = "/admin/categories/master"


def _row(admin, topic_id):  # noqa: F811
    return next(item for item in _categories(admin)["topics"] if item["topic_id"] == topic_id)


def _add(admin, **body):  # noqa: F811
    client, headers = admin
    response = client.post(MASTER, headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()["topic_id"]


def _links(admin, topic_id, **body):  # noqa: F811
    client, headers = admin
    return client.put(f"{MASTER}/{topic_id}/links", headers=headers, json=body)


def test_one_name_creates_the_menu_and_qa_categories(admin):  # noqa: F811
    row = _row(admin, _add(admin, name="Sondesh Delivery Mumbai"))
    data = _categories(admin)
    assert next(item for item in data["menu_categories"]
                if item["category_id"] == row["menu_category_id"])["name"] == "Sondesh Delivery Mumbai"
    assert next(item for item in data["qa_categories"]
                if item["value"] == row["qa_context"])["label"] == "Sondesh Delivery Mumbai"
    assert row["customer_topic_id"] is None


def test_an_existing_name_is_linked_not_duplicated(admin):  # noqa: F811
    menu = menu_catalogue.create_category("Festive Boxes")
    qa = approved_qa.save_context("Festive Boxes", "test")
    row = _row(admin, _add(admin, name="festive boxes"))
    assert (row["menu_category_id"], row["qa_context"]) == (menu["category_id"], qa["value"])
    assert sum(item["name"].casefold() == "festive boxes" for item in _categories(admin)["menu_categories"]) == 1


def test_a_duplicate_master_name_is_refused(admin):  # noqa: F811
    client, headers = admin
    _add(admin, name="Bento Delhi")
    assert client.post(MASTER, headers=headers, json={"name": "bento delhi"}).status_code == 409
    assert client.post(MASTER, headers=headers, json={"name": "  "}).status_code == 400


def test_suggestions_prefer_the_fuller_category_and_save_nothing(admin):  # noqa: F811
    approved_qa.save_context("Kolkata Sondesh", "test")
    menu = menu_catalogue.create_category("Sondesh Kolkata")
    topic = instagram_routing.create_topic("Kolkata sondesh delivery")
    row = _row(admin, topic["topic_id"])
    assert row["suggested_qa_context"] == "kolkata_sondesh"
    assert row["suggested_menu_category_id"] == menu["category_id"]
    assert row["suggested_name"] == "Kolkata Sondesh Delivery"
    assert row["qa_context"] is None and row["menu_category_id"] is None


def test_best_match_breaks_a_tie_by_content_and_needs_a_shared_word():
    options = [("empty", "Bengali Home Table", 0), ("full", "Kolkata Bengali Home Table", 3), ("none", "Event", 9)]
    assert admin_categories._best_match("Bengali Home Table", options) == "full"
    assert admin_categories._best_match("Subscription", options) is None


def test_capitals_only_keep_wording_and_acronyms():
    assert admin_categories._tidy_name("sondesh delivery delhi NCR") == "Sondesh Delivery Delhi NCR"
    assert admin_categories._tidy_name("Experience kolkata") == "Experience Kolkata"


def test_links_can_be_saved_corrected_and_cleared(admin):  # noqa: F811
    topic = instagram_routing.create_topic("Home Table Pune")["topic_id"]
    menu = menu_catalogue.create_category("Pune Dinners")
    assert _links(admin, topic, menu_category_id=menu["category_id"], qa_context="general",
                  customer_topic_id="SONDESH").status_code == 200
    row = _row(admin, topic)
    assert (row["menu_category_id"], row["qa_context"], row["customer_topic_id"]) == (menu["category_id"], "general", "SONDESH")
    assert _links(admin, topic).status_code == 200
    assert _row(admin, topic)["qa_context"] is None


@pytest.mark.parametrize("body", [{"menu_category_id": 99999}, {"qa_context": "nope"}, {"customer_topic_id": "NOPE"}])
def test_links_must_come_from_the_lists(admin, body):  # noqa: F811
    topic = instagram_routing.create_topic("Bad Links")["topic_id"]
    assert _links(admin, topic, **body).status_code == 400


def test_a_rename_reaches_only_lists_with_the_same_name(admin):  # noqa: F811
    client, headers = admin
    same = _add(admin, name="Bento Noida")
    response = client.patch(f"{MASTER}/{same}", headers=headers, json={"name": "Bento Greater Noida"})
    assert response.json()["kept_names"] == []
    data = _categories(admin)
    assert any(item["name"] == "Bento Greater Noida" for item in data["menu_categories"])
    assert any(item["label"] == "Bento Greater Noida" for item in data["qa_categories"])

    different = instagram_routing.create_topic("Bengali Home Table Two")["topic_id"]
    qa = approved_qa.save_context("Kolkata Bengali Home Table Two", "test")
    _links(admin, different, qa_context=qa["value"])
    response = client.patch(f"{MASTER}/{different}", headers=headers, json={"name": "Home Table Two"})
    assert response.json()["kept_names"] == ["Kolkata Bengali Home Table Two"]
    assert any(item["label"] == "Kolkata Bengali Home Table Two" for item in _categories(admin)["qa_categories"])


def test_a_built_in_list_is_never_renamed(admin):  # noqa: F811
    client, headers = admin
    topic = instagram_routing.create_topic("Sondesh")["topic_id"]
    _links(admin, topic, qa_context="sondesh")
    response = client.patch(f"{MASTER}/{topic}", headers=headers, json={"name": "Sondesh Delhi"})
    assert response.json()["kept_names"] == ["Sondesh"]


def test_copy_brings_only_what_was_kept(admin):  # noqa: F811
    source = instagram_routing.create_topic("Sondesh Delhi Source")["topic_id"]
    instagram_routing.update_topic(source, default_approved_qa_ids=["sondesh_price_client"])
    assert [item["qa_id"] for item in _row(admin, source)["default_answers"]] == ["sondesh_price_client"]
    kept = _add(admin, name="Sondesh Copy", default_approved_qa_ids=["sondesh_price_client"])
    blank = _add(admin, name="Sondesh Blank")
    assert [item["qa_id"] for item in _row(admin, kept)["default_answers"]] == ["sondesh_price_client"]
    assert _row(admin, blank)["default_answers"] == []


def test_turning_off_keeps_existing_mappings_answering(admin, ad_customer):  # noqa: F811
    client, headers = admin
    topic = instagram_routing.create_topic("Turned Off")
    route = _route(ad_customer[2], "reel", topic, "OFFREEL")
    assert client.patch(f"/admin/instagram-topics/{topic['topic_id']}", headers=headers,
                        json={"active": False}).status_code == 200
    found, _method = route_for_inbound(ad_id=None, media_id=None, shortcode="OFFREEL", source_type="reel")
    assert found["routing_id"] == route["routing_id"]


def test_links_do_not_change_what_a_mapping_returns(admin, ad_customer):  # noqa: F811
    topic = instagram_routing.create_topic("Linked Later")
    _route(ad_customer[2], "reel", topic, "LINKREEL")
    before, _method = route_for_inbound(ad_id=None, media_id=None, shortcode="LINKREEL", source_type="reel")
    _links(admin, topic["topic_id"], qa_context="sondesh")
    after, _method = route_for_inbound(ad_id=None, media_id=None, shortcode="LINKREEL", source_type="reel")
    assert {key: value for key, value in after.items() if key != "updated_at"} == \
        {key: value for key, value in before.items() if key != "updated_at"}


def test_master_routes_need_admin():
    client = TestClient(app_module.app)
    assert client.post(MASTER, json={"name": "x"}).status_code in (401, 403)
    assert config.ADMIN_API_KEY is not None


def test_the_page_keeps_other_lists_under_advanced():
    assert '<details id="catAdvanced">' in admin_categories.HTML
    assert admin_categories.HTML.index("Master categories") < admin_categories.HTML.index('id="catAdvanced"')
    assert admin_categories.HTML.index('id="catAdvanced"') < admin_categories.HTML.index('id="catMenus"')



def test_setup_steps_say_what_is_done(monkeypatch):
    from camellia import answer_slots, category_buttons
    monkeypatch.setattr(answer_slots, "effective", lambda route, slot: {"kind": "CUSTOM"} if slot == "details" else None)
    monkeypatch.setattr(category_buttons, "stored_buttons", lambda owner, key: [{"slot": "price", "label": "Price"}])
    steps = {step["key"]: step for step in admin_categories._setup_steps({"topic_id": "t", "qa_context": "x", "button_name": "Sondesh"})}
    assert all(step["done"] for step in steps.values())
    assert steps["answers"]["status"].endswith("Details") and steps["buttons"]["status"] == "Price"


def test_a_button_name_with_no_details_answer_is_not_done(monkeypatch):
    from camellia import answer_slots, category_buttons
    monkeypatch.setattr(answer_slots, "effective", lambda route, slot: None)
    monkeypatch.setattr(category_buttons, "stored_buttons", lambda owner, key: None)
    steps = {step["key"]: step for step in admin_categories._setup_steps({"topic_id": "t", "button_name": "Sondesh"})}
    assert not any(step["done"] for step in steps.values())
    assert steps["name"]["status"] == "Needs a Details answer first"


def test_a_category_opens_in_a_panel_on_its_first_unfinished_tab():
    script = admin_categories.SCRIPT
    assert "function catOpenPanel(topicId, tab)" in script and "find(key => !topic.setup.find" in script
    assert [name in script for name in ("'Answers'", "'Buttons'", "'Button name'", "'Links'")] == [True] * 4
    assert 'id="catPanel"' in admin_categories.HTML


def test_answers_are_one_line_rows_and_small_fields_save_themselves():
    script = admin_categories.SCRIPT
    assert "Pick an approved answer" in script and "Write your own" in script and "[data-slot-editor]" in script
    assert "catToast(async () =>" in script and "Save buttons" not in script
    assert "catSoon(() => catSaveLinks(topic, {button_name" in script
