"""Ads answer like the exact Post/Reel they promote.

The client mapped ads one by one, every renewal created a new Ad ID to map
again, and an ad mapped to a Story or to a note-only post handed price
questions to the manager. Now an unbound ad inherits its post's mapping, the
Ads tab says what a price question would get, and /health counts the gaps.
"""
from fastapi.testclient import TestClient

from camellia import app as app_module, instagram_ads as ads, instagram_routing
from camellia.instagram_routing import route_for_inbound

from test_instagram_ads_inventory import item, route, store, sync  # noqa: F401

AD = item()["id"]
HEADERS = {"x-admin-key": "ads-admin-fixture"}


def _live(store, monkeypatch):
    monkeypatch.setattr("camellia.database.db", store)
    sync(store, monkeypatch)


def _approved_post(store):
    return route(store, response_mode="APPROVED_QA", approved_qa_id="sondesh_price_client")


def test_unbound_ad_inherits_its_posts_mapping(store, monkeypatch):
    saved = _approved_post(store)
    _live(store, monkeypatch)
    found, method = route_for_inbound(ad_id=AD, media_id=None)
    assert method == "ad_creative" and found["routing_id"] == saved["routing_id"]
    row = ads.ads_workspace(store, "all")["items"][0]
    assert row["answers_from"] == "post" and row["answer_status"] == "APPROVED"


def test_disabled_ad_inherits_nothing(store, monkeypatch):
    saved = _approved_post(store)
    _live(store, monkeypatch)
    store.bind_instagram_ad(AD, saved["routing_id"])
    store.set_instagram_ad_binding_active(AD, False)
    assert route_for_inbound(ad_id=AD, media_id=None) == (None, None)
    assert ads.ads_workspace(store, "all")["items"][0]["answer_status"] == "DISABLED"


def test_binding_to_a_removed_mapping_falls_back_to_the_post(store, monkeypatch):
    post = _approved_post(store)
    other = route(store, code="OtherPost", media_id="180009")
    _live(store, monkeypatch)
    store.bind_instagram_ad(AD, other["routing_id"])
    store.archive_instagram_source_route(other["routing_id"])
    found, method = route_for_inbound(ad_id=AD, media_id=None)
    assert method == "ad_creative" and found["routing_id"] == post["routing_id"]


def test_a_story_is_never_inherited(store, monkeypatch):
    store.save_instagram_source_route({"source_type": "story", "media_id": "180001", "routing_topic": "Sondesh",
                                       "source_description": "Note", "active": True,
                                       "priority_products": [], "knowledge_scope": []})
    _live(store, monkeypatch)
    assert route_for_inbound(ad_id=AD, media_id=None) == (None, None)
    assert ads.ads_workspace(store, "all")["items"][0]["answer_status"] == "NONE"


def test_price_status_follows_the_live_matcher(store, monkeypatch):
    note_only = route(store)
    _live(store, monkeypatch)
    assert ads.price_answer_status(store, None) == "NONE"
    assert ads.price_answer_status(store, instagram_routing.decode_route(note_only)) == "NOTE"
    topic = instagram_routing.create_topic("Sondesh Delivery")
    instagram_routing.update_topic(topic["topic_id"], default_approved_qa_ids=["sondesh_price_client"])
    with_topic = {**instagram_routing.decode_route(note_only), "topic_id": topic["topic_id"], "routing_id": -1}
    assert ads.price_answer_status(store, with_topic) == "APPROVED"


def test_gaps_count_only_recently_messaged_ads_and_reach_health(store, monkeypatch):
    route(store)  # The ad's post has only a note.
    _live(store, monkeypatch)
    assert ads.ad_answer_gaps(store) == 0  # Nobody has messaged from the ad.
    store.log_conversation("customer", "instagram", "How much does it cost?", "note", True, "source_description", ad_id=AD)
    assert ads.ad_answer_gaps(store) == 1
    assert ads.ads_workspace(store, "all")["counts"]["answer_gaps"] == 1
    warnings = TestClient(app_module.app).get("/health").json()["warnings"]
    assert any("1 ad(s) customers messaged from this week" in w for w in warnings)


def test_media_item_endpoint_returns_the_editor_mapping(store, monkeypatch):
    saved = route(store)
    _live(store, monkeypatch)
    media = store.upsert_instagram_media({"instagram_media_id": "180001", "permalink": "https://www.instagram.com/p/ExactSondesh/",
                                          "shortcode": "ExactSondesh", "media_type": "IMAGE"})
    row = ads.ads_workspace(store, "all")["items"][0]
    assert row["answer_media_item_id"] == media["id"]
    body = TestClient(app_module.app).get(f"/admin/instagram-media/{media['id']}", headers=HEADERS).json()
    assert body["item"]["mapping_route"]["routing_id"] == saved["routing_id"]
