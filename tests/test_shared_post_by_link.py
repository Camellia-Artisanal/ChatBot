"""A shared Post finds a mapping saved without a media id via the post's link (23 Oct).

Kolkata Home Table post #144 (and 8 others) were mapped from their link, so a
customer sharing the post got "unmapped" and the manager handoff.
"""
from camellia.instagram_routing import route_for_inbound

from test_instagram_ads_inventory import item, route, store, sync  # noqa: F401
from test_ad_mapped_source_context import ad_customer, _ad_entry, _mapped_source, _process  # noqa: F401

SHARED_ID = "18002504423797970"


def test_shared_post_matches_by_the_link_an_ad_supplies(store, monkeypatch):
    monkeypatch.setattr("camellia.database.db", store)
    mapped = route(store, code="DevLtwmgIMF", media_id=None)
    sync(store, monkeypatch, [item("120246668093100540", effective_instagram_media_id=SHARED_ID,
                                    instagram_permalink_url="https://www.instagram.com/p/DevLtwmgIMF")])
    found, method = route_for_inbound(ad_id=None, media_id=None, post_media_id=SHARED_ID, source_type="post")
    assert found and found["routing_id"] == mapped["routing_id"] and method == "shared_post_media_id"


def test_shared_post_matches_by_the_link_in_the_media_library(store, monkeypatch):
    monkeypatch.setattr("camellia.database.db", store)
    mapped = route(store, code="DevLwJ1geSa", media_id=None)
    store.upsert_instagram_media({"instagram_media_id": "18000000000000143", "media_type": "IMAGE",
                                  "permalink": "https://www.instagram.com/p/DevLwJ1geSa/", "shortcode": "DevLwJ1geSa"})
    found, _ = route_for_inbound(ad_id=None, media_id=None, post_media_id="18000000000000143", source_type="post")
    assert found and found["routing_id"] == mapped["routing_id"]


def test_an_unknown_share_stays_unmapped(store, monkeypatch):
    monkeypatch.setattr("camellia.database.db", store)
    route(store, code="DevLtwmgIMF", media_id=None)
    assert route_for_inbound(ad_id=None, media_id=None, post_media_id="999", source_type="post")[0] is None


def test_a_posts_own_price_answer_is_not_blocked_by_an_expired_weekly_menu(ad_customer, monkeypatch):
    # 23 Oct: Kolkata Home Table post/reel replies went to the manager because
    # the weekly savoury menu had expired; that menu has nothing to do with them.
    from camellia import responder as responder_module
    monkeypatch.setattr(responder_module.menu_review, "menu_question_blocked", lambda *a, **k: True)
    sender, facts, db = ad_customer
    _mapped_source(ad_customer, "post", "ad-sondesh", topic_name="Sondesh", response_mode="APPROVED_QA",
                   approved_qa_ids=["sondesh_price_client"], source_context="sondesh")
    _process(ad_customer, "How much does it cost?", _ad_entry(sender, "ad-sondesh", "How much does it cost?"))
    row = db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]
    assert "handoff" not in row["template_id"] and not db.get_active_handoff("instagram", sender)


def test_a_savoury_price_answer_still_waits_for_the_weekly_menu(ad_customer, monkeypatch):
    from camellia import responder as responder_module
    monkeypatch.setattr(responder_module.menu_review, "menu_question_blocked", lambda *a, **k: True)
    sender, facts, db = ad_customer
    _mapped_source(ad_customer, "post", "ad-savoury", topic_name="Savoury", response_mode="APPROVED_QA",
                   approved_qa_ids=["general_price"], source_context="general")
    _process(ad_customer, "How much does it cost?", _ad_entry(sender, "ad-savoury", "How much does it cost?"))
    row = db.get_conversations(limit=1, channel="instagram", user_id=sender)[0]
    assert "handoff" in row["template_id"]
