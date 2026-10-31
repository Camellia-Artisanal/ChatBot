"""One mapping on many Posts and Reels: edit once, reels and ads follow, nothing overwritten."""
import pytest
from fastapi.testclient import TestClient

from camellia import app as app_module, instagram_ads, instagram_routing
from camellia.config import config
from camellia.instagram_routing import decode_route, route_for_inbound

from test_followup_manual_sequences import assets, auth, store  # noqa: F401

NOTE = "Our sondesh box is 500."
ATTACH = "/admin/instagram-media/attach-mapping"


def _client():
    return TestClient(app_module.app)


def _media(client, kind, code):
    url = f"https://www.instagram.com/{'reel' if kind == 'reel' else 'p'}/{code}/"
    return client.post("/admin/instagram-media/manual-link", headers=auth(), json={"url": url}).json()["item"]


def _mapped(client, store, kind="post", code="SOURCE1"):  # noqa: F811
    topic = store.create_instagram_topic(f"shared-{code}", f"Shared {code}", "", None, [], [])
    item = _media(client, kind, code)
    response = client.put(f"/admin/instagram-media/{item['id']}/mapping", headers=auth(), json={
        "topic_id": topic["topic_id"], "response_mode": "CUSTOM", "source_description": NOTE, "menu_mode": "NONE"})
    assert response.status_code == 200, response.text
    return item, response.json()["route"]


def _attach(client, **body):
    response = client.post(ATTACH, headers=auth(), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_an_attached_reel_answers_with_the_shared_mapping(store, assets):  # noqa: F811
    client = _client()
    _source, route = _mapped(client, store)
    reel = _media(client, "reel", "MEMBER1")
    assert _attach(client, routing_id=route["routing_id"], item_ids=[reel["id"]])["attached"] == 1

    found, method = route_for_inbound(ad_id=None, media_id=None, shortcode="MEMBER1", source_type="reel")
    assert found["routing_id"] == route["routing_id"]
    assert method == "shared_media_url"
    assert client.get("/admin/instagram-media/shared-mapping/%d" % route["routing_id"], headers=auth()).json()["shared_count"] == 1


def test_editing_the_mapping_reaches_every_attached_post(store, assets):  # noqa: F811
    client = _client()
    _source, route = _mapped(client, store)
    reel = _media(client, "reel", "MEMBER2")
    _attach(client, routing_id=route["routing_id"], item_ids=[reel["id"]])
    saved = decode_route(store.get_instagram_source_route(route["routing_id"]))
    saved["custom_response_variations"] = ["Price?"]
    store.save_instagram_source_route(saved, route["routing_id"])

    found, _method = route_for_inbound(ad_id=None, media_id=None, shortcode="MEMBER2", source_type="reel")
    assert found["custom_response_variations"] == ["Price?"]


def test_an_ad_promoting_an_attached_post_uses_the_shared_mapping(store, assets):  # noqa: F811
    client = _client()
    _source, route = _mapped(client, store)
    post = _media(client, "post", "MEMBER3")
    _attach(client, routing_id=route["routing_id"], item_ids=[post["id"]])
    media_id = store.get_instagram_media(post["id"])["instagram_media_id"]

    routes = [decode_route(row) for row in store.list_instagram_source_routes()]
    by_id = {row["routing_id"]: row for row in routes}
    siblings = instagram_ads.creative_routes(store, {}, {}, by_id)
    inherited = instagram_ads.inherited_route({"effective_instagram_media_id": media_id}, routes, None, siblings)
    assert inherited["routing_id"] == route["routing_id"]


def test_a_post_with_its_own_mapping_is_skipped(store, assets):  # noqa: F811
    client = _client()
    _source, route = _mapped(client, store)
    other, other_route = _mapped(client, store, "reel", "OWN1")
    plain = _media(client, "post", "PLAIN1")

    result = _attach(client, routing_id=route["routing_id"], item_ids=[other["id"], plain["id"]])
    assert result == {"attached": 1, "already_attached": 0, "skipped_mapped": 1}
    found, _method = route_for_inbound(ad_id=None, media_id=None, shortcode="OWN1", source_type="reel")
    assert found["routing_id"] == other_route["routing_id"]


def test_select_all_attaches_every_match_except_the_unticked(store, assets, monkeypatch):  # noqa: F811
    client = _client()
    _source, route = _mapped(client, store)
    items = [_media(client, "reel", f"BULK{index}") for index in range(4)]
    _media(client, "post", "OTHER1")

    result = _attach(client, routing_id=route["routing_id"], all_matching=True, source_type="reel",
                     search="BULK", excluded_ids=[items[0]["id"]])
    assert result["attached"] == 3
    assert route_for_inbound(ad_id=None, media_id=None, shortcode="BULK0", source_type="reel")[0] is None
    assert route_for_inbound(ad_id=None, media_id=None, shortcode="OTHER1", source_type="reel")[0] is None


def test_stop_sharing_and_owned_posts(store, assets):  # noqa: F811
    client = _client()
    source, route = _mapped(client, store)
    reel = _media(client, "reel", "MEMBER4")
    _attach(client, routing_id=route["routing_id"], item_ids=[reel["id"]])

    assert client.post(f"/admin/instagram-media/{source['id']}/detach-mapping", headers=auth()).status_code == 409
    assert client.post(f"/admin/instagram-media/{reel['id']}/detach-mapping", headers=auth()).status_code == 200
    assert route_for_inbound(ad_id=None, media_id=None, shortcode="MEMBER4", source_type="reel")[0] is None


def test_deleting_the_mapping_releases_its_posts_for_good(store, assets):  # noqa: F811
    client = _client()
    source, route = _mapped(client, store)
    reel = _media(client, "reel", "MEMBER5")
    _attach(client, routing_id=route["routing_id"], item_ids=[reel["id"]])

    assert client.delete(f"/admin/instagram-media/{source['id']}/mapping", headers=auth()).status_code == 200
    assert store.restore_instagram_source_route(route["routing_id"])
    assert route_for_inbound(ad_id=None, media_id=None, shortcode="MEMBER5", source_type="reel")[0] is None


def test_a_shared_photo_post_is_not_claimed(store, assets):  # noqa: F811
    client = _client()
    _source, route = _mapped(client, store)
    post = _media(client, "post", "MEMBER6")
    _attach(client, routing_id=route["routing_id"], item_ids=[post["id"]])
    assert route_for_inbound(ad_id=None, media_id=None, post_media_id="dm-attachment-id", source_type="post")[0] is None


@pytest.mark.parametrize("body", [{"routing_id": "1", "item_ids": [1]}, {"routing_id": 1}, {"routing_id": 1, "item_ids": "x"}])
def test_bad_requests_are_refused(store, body):  # noqa: F811
    assert _client().post(ATTACH, headers=auth(), json=body).status_code == 400


def test_a_story_or_missing_mapping_cannot_be_shared(store):  # noqa: F811
    assert _client().post(ATTACH, headers=auth(), json={"routing_id": 999, "item_ids": [1]}).status_code == 404


def test_admin_needs_a_login():
    assert TestClient(app_module.app).post(ATTACH, json={"routing_id": 1, "item_ids": [1]}).status_code in {401, 403}
