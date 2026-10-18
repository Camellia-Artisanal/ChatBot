"""An ad Meta sends with no Post/Reel link gets answers set like Posts & Reels.

18 Oct: "YOU HOST ... _Group_1" had no post link, so Map Ad could only pick
another mapping. Now it opens the Posts & Reels form, saved as a mapping of
the ad itself (identity = Meta Ad ID) with the same Approved Q&A checklist.
"""
import sqlite3

from fastapi.testclient import TestClient

from camellia import app as app_module, instagram_ads, instagram_source_qa
from camellia.instagram_routing import route_for_inbound

from test_ad_mapped_source_context import ad_customer, _ad_entry, _process  # noqa: F401

AD = "120246622874740540"
BUNDLE = ["general_price", "general_details", "price_is_high"]
HEADERS = {"x-admin-key": "ad-source-test-admin"}


def _save(payload):
    return TestClient(app_module.app).post("/admin/instagram-routes", headers=HEADERS, json=payload)


def _ad_route(**extra):
    payload = {"source_type": "ad", "url": "", "ad_id": AD, "topic_id": "", "source_description": "",
               "menu_id": "", "response_mode": "APPROVED_QA", "approved_qa_ids": BUNDLE,
               "source_context": "general", "active": True, **extra}
    response = _save(payload)
    assert response.status_code == 200, response.text
    return response.json()["route"]


def test_ad_without_a_post_link_saves_a_checklist_and_answers_from_it(ad_customer):
    sender, facts, db = ad_customer
    saved = _ad_route()
    assert saved["source_type"] == "ad" and saved["qa_bundle"] and saved["approved_qa_ids"] == BUNDLE
    found, method = route_for_inbound(ad_id=AD, media_id=None)
    assert method == "ad_id" and found["routing_id"] == saved["routing_id"]
    _process(ad_customer, "How much does it cost?", _ad_entry(sender, AD, "How much does it cost?"))
    assert facts["client_qa_id"] == "general_price"
    assert db.get_active_handoff("instagram", sender) is None


def test_unticked_answers_are_not_sent_and_the_ad_id_cannot_change(ad_customer):
    sender, facts, db = ad_customer
    saved = _ad_route(approved_qa_ids=["general_details"])
    _process(ad_customer, "How do I pay?", _ad_entry(sender, AD, "How do I pay?"))
    assert facts.get("client_qa_id") != "general_payment_client"
    moved = _save({"routing_id": saved["routing_id"], "source_type": "ad", "url": "", "ad_id": "999",
                   "response_mode": "APPROVED_QA", "approved_qa_ids": BUNDLE, "source_context": "general"})
    assert moved.status_code == 400


def test_the_ads_card_shows_it_mapped_with_price_answers(ad_customer):
    _sender, _facts, db = ad_customer
    # Seen only from a customer's DM, so Meta's ads list has no row for it.
    db.observe_instagram_ad(AD, "YOU HOST _Group_1")
    _ad_route()
    row = next(r for r in instagram_ads.ads_workspace(db, "all")["items"] if r["ad_id"] == AD)
    assert row["mapped"] and row["mapping"]["source_type"] == "ad"
    assert row["answer_status"] == "APPROVED"
    assert row["name"] == "YOU HOST _Group_1"  # Mapping it must not lose its name.


def test_existing_post_reel_answer_lists_survive_the_table_rebuild(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE instagram_source_routes (routing_id INTEGER PRIMARY KEY, source_type TEXT, media_id TEXT, canonical_url TEXT, shortcode TEXT)")
    cursor.execute('''CREATE TABLE instagram_source_qa_bundles (
        routing_id INTEGER PRIMARY KEY, source_type TEXT NOT NULL CHECK(source_type IN ('post','reel')),
        context TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        created_by TEXT NOT NULL, updated_by TEXT NOT NULL)''')
    cursor.execute("CREATE TABLE instagram_post_pairings (id INTEGER PRIMARY KEY, routing_id INTEGER, status TEXT, bound_media_id TEXT, bound_at TEXT)")
    cursor.execute("CREATE TABLE instagram_media (instagram_media_id TEXT, permalink TEXT, shortcode TEXT, inventory_state TEXT, media_type TEXT, owner_instagram_account_id TEXT)")
    cursor.execute("CREATE TABLE instagram_media_inventory_syncs (owner_account_id TEXT)")
    cursor.execute("INSERT INTO instagram_source_qa_bundles VALUES (7,'post','general','t','t','a','a')")
    instagram_source_qa.initialize(cursor)
    instagram_source_qa.initialize(cursor)  # Runs on every start; must be a no-op the second time.
    assert [tuple(row) for row in cursor.execute("SELECT routing_id, source_type, context FROM instagram_source_qa_bundles")] == [(7, "post", "general")]
    cursor.execute("INSERT INTO instagram_source_qa_bundles VALUES (8,'ad','general','t','t','a','a')")
    triggers = {row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"source_qa_route_deleted", "source_qa_identity_guard"} <= triggers


def test_map_ad_opens_the_form_for_an_ad_without_a_post_link():
    from camellia.admin_ads import SCRIPT
    page = TestClient(app_module.app).get("/admin").text
    assert '<option value="ad" hidden>' in page
    assert "await openAdOnlyMapping(ad)" in SCRIPT and "document.getElementById('igAdId').value = ad.ad_id" in SCRIPT
