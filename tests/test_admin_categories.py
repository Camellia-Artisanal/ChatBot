"""The Categories page shows all four lists; each list keeps its own delete rules."""
import pytest
from fastapi.testclient import TestClient

from camellia import app as app_module, approved_qa, instagram_routing, menu_catalogue
from camellia.admin_categories import HTML, SCRIPT
from camellia.config import config
from test_ad_mapped_source_context import ad_customer  # noqa: F401


@pytest.fixture
def admin(ad_customer):
    return TestClient(app_module.app), {"x-admin-key": config.ADMIN_API_KEY}


def _categories(admin):
    client, headers = admin
    response = client.get("/admin/categories", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _route(db, kind, topic, code):
    url = f"https://www.instagram.com/{'reel' if kind == 'reel' else 'p'}/{code}/"
    return db.save_instagram_source_route(instagram_routing.validate_route_payload({
        "source_type": kind, "url": url, "shortcode": code, "media_id": f"media-{code}",
        "topic_id": topic["topic_id"], "source_description": "Test source.",
        "response_mode": "CUSTOM", "active": True,
    }))


def test_page_needs_admin_auth(ad_customer):
    assert TestClient(app_module.app).get("/admin/categories").status_code in (401, 403)


def test_all_four_lists_are_returned_with_their_usage(admin, ad_customer):
    db = ad_customer[2]
    topic = instagram_routing.create_topic("Kolkata Bento")
    _route(db, "post", topic, "POSTONE")
    _route(db, "reel", topic, "REELONE")
    menu_catalogue.create_category("Festive")
    approved_qa.save_context("Kolkata story telling sessions", "test")

    data = _categories(admin)

    row = next(item for item in data["topics"] if item["topic_id"] == topic["topic_id"])
    assert row["usage"] == {"post": 1, "reel": 1}
    assert next(item for item in data["menu_categories"] if item["name"] == "Festive")["menus"] == 0
    assert any(item["name"] == "Sondesh" and item["is_system"] for item in data["menu_topics"])
    assert {key["value"] for key in data["routing_keys"]} == set(menu_catalogue.CUSTOMER_TOPICS)
    custom = next(item for item in data["qa_categories"] if item["value"] == "kolkata_story_telling_sessions")
    assert custom == {"value": "kolkata_story_telling_sessions", "label": "Kolkata story telling sessions",
                      "editable": True, "answers": 0, "mappings": 0}
    assert not next(item for item in data["qa_categories"] if item["value"] == "general")["editable"]


def test_savoury_menu_is_shown_as_protected(admin):
    savoury = [item for item in _categories(admin)["menu_categories"] if item["name"] == "Savoury Menu"]
    assert savoury and savoury[0]["protected"]


def test_a_topic_in_use_cannot_be_deleted_without_a_replacement(admin, ad_customer):
    client, headers = admin
    db = ad_customer[2]
    old = instagram_routing.create_topic("Old Topic")
    new = instagram_routing.create_topic("New Topic")
    route = _route(db, "post", old, "POSTTWO")

    refused = client.request("DELETE", f"/admin/instagram-topics/{old['topic_id']}", headers=headers, json={})
    assert refused.status_code == 409

    moved = client.request("DELETE", f"/admin/instagram-topics/{old['topic_id']}", headers=headers,
                           json={"replacement_topic_id": new["topic_id"]})
    assert moved.status_code == 200, moved.text
    assert db.get_instagram_source_route(route["routing_id"])["topic_id"] == new["topic_id"]
    assert old["topic_id"] not in {item["topic_id"] for item in _categories(admin)["topics"]}


def test_a_menu_category_with_menus_is_not_deleted(admin, ad_customer):
    client, headers = admin
    db = ad_customer[2]
    category = menu_catalogue.create_category("Festive")
    with db.get_connection() as conn:
        conn.execute("INSERT INTO menu_catalogue (menu_id,name,category) VALUES ('festive_box','Festive Box','festive')")
        conn.commit()
    assert next(item for item in _categories(admin)["menu_categories"] if item["name"] == "Festive")["menus"] == 1
    response = client.delete(f"/admin/menu-categories/{category['category_id']}", headers=headers)
    assert response.status_code == 409
    assert "Move those menus" in response.json()["detail"]


def test_page_offers_no_delete_for_built_in_lists():
    # Deleting a built-in customer topic would stop "send sondesh menu" working.
    assert "topic.is_system ? [] : [['Delete'" in SCRIPT
    assert "category.protected ? [] :" in SCRIPT
    assert "category.editable ? [[" in SCRIPT


def test_admin_page_has_the_tab_and_keeps_the_main_nav_order(admin):
    client, _headers = admin
    page = client.get("/admin").text
    sidebar = page.split('<aside class="admin-sidebar"', 1)[1].split('</aside>', 1)[0]
    assert '<span class="nav-label">Categories</span>' in sidebar
    assert sidebar.index("'tab-handoffs'") < sidebar.index("'tab-categories'")
    assert "'tab-categories': 'categories'" in page
    assert "handoffs: 'tab-handoffs', categories: 'tab-categories'" in page
    assert "categories: 'tab-categories', legacy:" in page  # A refresh stays on the page.
    assert "if (group === 'categories') { loadCategories(); }" in page
    assert HTML.strip() in page and "async function catDeleteTopic" in page
