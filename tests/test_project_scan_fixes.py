"""Defects found by the 31 Oct whole-project scan."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from camellia import app as app_module
from camellia.ad_gate import describe_entry_point
from camellia.database import Database
from camellia.instagram_routing import route_for_inbound
from camellia.order_calculator import OrderCalculator
from camellia.session_memory import session_memory

from test_followup_manual_sequences import assets, auth, store  # noqa: F401
from test_shared_media_mapping import _attach, _client, _mapped, _media

OLD = (datetime.utcnow() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
NOW = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
FILLERS = {"INTEGER": 0, "REAL": 0.0}


def _row(conn, table, **values):
    """Insert a row, filling each NOT NULL column these tests do not care about."""
    for column in conn.execute(f"PRAGMA table_info({table})").fetchall():
        if column["notnull"] and column["dflt_value"] is None and not column["pk"]:
            values.setdefault(column["name"], FILLERS.get(column["type"].upper(), "x"))
    columns = ",".join(values)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' * len(values))})", tuple(values.values()))


def _count(database, table, where="1=1", *params):
    with database.get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()["n"]


@pytest.fixture
def database(tmp_path):
    return Database(str(tmp_path / "scan.db"))


def test_retention_reaches_every_customer_history_table(database):
    with database.get_connection() as conn:
        for table, column in Database._CUSTOMER_HISTORY_TABLES:
            for stamp in (OLD, NOW):
                key = {"instagram_customer_identities": {"customer_external_id": stamp},
                       "instagram_pending_media_events": {"event_key": stamp}}.get(table, {})
                _row(conn, table, **{column: stamp}, **key)
        conn.commit()

    database.purge_expired_data(retention_days=90)

    for table, _column in Database._CUSTOMER_HISTORY_TABLES:
        assert _count(database, table) == 1, table


def test_retention_keeps_an_open_handoff_and_removes_closed_ones(database):
    with database.get_connection() as conn:
        _row(conn, "cross_channel_handoffs", handoff_ref="OPEN", source_channel="instagram",
             source_user_id="a", status="WAITING_FOR_HUMAN", created_at=OLD, expires_at=None)
        _row(conn, "cross_channel_handoffs", handoff_ref="DONE", source_channel="instagram",
             source_user_id="b", status="RESOLVED", created_at=OLD)
        _row(conn, "cross_channel_handoffs", handoff_ref="LAPSED", source_channel="instagram",
             source_user_id="c", status="WAITING_FOR_HUMAN", created_at=OLD, expires_at=OLD)
        conn.commit()

    database.purge_expired_data(retention_days=90)

    with database.get_connection() as conn:
        left = [row["handoff_ref"] for row in conn.execute("SELECT handoff_ref FROM cross_channel_handoffs")]
    assert left == ["OPEN"]


def test_erasure_reaches_every_table_holding_the_customer(database):
    user = "customer-9"
    database.log_conversation(user, "instagram", "my secret question", "reply", True, "x")
    with database.get_connection() as conn:
        for table in ("message_route_audit", "triage_shadow", "assisted_resolution_suggestions",
                      "auto_resolve_decisions"):
            _row(conn, table, user_id=user)
            _row(conn, table, user_id="someone-else")
        _row(conn, "instagram_customer_identities", customer_external_id=user, customer_username="name")
        _row(conn, "instagram_pending_media_events", sender_id=user, event_key="e1")
        _row(conn, "active_conversation_sessions", user_id=user, channel="instagram")
        _row(conn, "instagram_story_sources", story_media_id="s1", last_sender_id=user)
        _row(conn, "router_shadow", user_message="my secret question")
        _row(conn, "rag_audit_logs", user_message="my secret question")
        _row(conn, "cross_channel_handoffs", handoff_ref="CAM1", source_channel="instagram", source_user_id=user)
        conn.commit()
    database.save_whatsapp_short_link("CAM1", "https://wa.me/1?text=my-order", "CAM1-abc123")

    database.delete_user_data(user)

    for table in ("message_route_audit", "triage_shadow", "assisted_resolution_suggestions",
                  "auto_resolve_decisions"):
        assert _count(database, table, "user_id=?", user) == 0, table
        assert _count(database, table, "user_id=?", "someone-else") == 1, table
    for table in ("instagram_customer_identities", "instagram_pending_media_events",
                  "active_conversation_sessions", "router_shadow", "rag_audit_logs", "whatsapp_short_links"):
        assert _count(database, table) == 0, table
    assert _count(database, "instagram_story_sources", "last_sender_id IS NOT NULL") == 0


def test_api_key_is_never_stored_as_the_actor(store, assets):  # noqa: F811
    client = _client()
    _item, route = _mapped(client, store)
    response = client.post(f"/admin/instagram-routes/{route['routing_id']}/pairing/start", headers=auth())
    assert response.status_code == 200
    with store.get_connection() as conn:
        assert conn.execute("SELECT started_by FROM instagram_post_pairings").fetchone()["started_by"] == "admin-api"


def test_startup_scrub_removes_the_key_from_audit_names(database):
    with database.get_connection() as conn:
        _row(conn, "instagram_post_pairings", started_by="the-key")
        _row(conn, "instagram_post_pairings", started_by="staff-name")
        conn.commit()

    assert database.scrub_admin_key_from_audit("the-key") == 1
    assert database.scrub_admin_key_from_audit("") == 0

    assert _count(database, "instagram_post_pairings", "started_by='the-key'") == 0
    assert _count(database, "instagram_post_pairings", "started_by='staff-name'") == 1


def test_erasure_clears_outbound_audit_and_pending_drafts_only(database):
    user = "customer-7"
    database.log_conversation(user, "instagram", "my question", "reply", True, "x")
    with database.get_connection() as conn:
        _row(conn, "instagram_outbound_audit", recipient_id=app_module._audit_recipient_id(user))
        _row(conn, "instagram_outbound_audit", recipient_id=app_module._audit_recipient_id("someone-else"))
        _row(conn, "candidate_templates", user_message="my question", status="PENDING_APPROVAL")
        _row(conn, "candidate_templates", user_message="my question", status="APPROVED")
        conn.commit()

    database.delete_user_data(user)

    assert _count(database, "instagram_outbound_audit") == 1
    assert _count(database, "candidate_templates", "status='PENDING_APPROVAL'") == 0
    assert _count(database, "candidate_templates", "status='APPROVED'") == 1


def test_a_send_failure_does_not_reopen_a_finished_inbound_job(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "jobs.db"))
    monkeypatch.setattr(app_module, "db", database)
    job_id, _ = database.enqueue_instagram_inbound_job("event", "customer", "_process_instagram_event", {})
    monkeypatch.setattr(app_module, "_process_instagram_event", lambda *args, **kwargs: None)

    def locked(**_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(app_module, "_process_instagram_outbound_jobs", locked)
    app_module._process_instagram_inbound_jobs(job_id=job_id)

    assert _count(database, "instagram_inbound_jobs", "status='DONE'") == 1


def test_unmapped_ad_asking_for_the_weekly_menu_stays_silent(store):  # noqa: F811
    sender = "ad-customer"
    entry = describe_entry_point({
        "sender": {"id": sender},
        "message": {"mid": "m1", "text": "send the weekly menu",
                    "referral": {"source": "ADS", "ad_id": "999", "ads_context_data": {"ad_title": "Kolkata"}}},
    })
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(app_module.responder, "match_and_respond",
                      lambda *args, **kwargs: ("", True, "weekly_menu_silent", None))
        patch.setattr(app_module, "send_instagram_reply", lambda *args, **kwargs: {"status": "sent"})
        app_module._process_instagram_event(sender, "send the weekly menu", "999", entry)

    assert store.get_active_handoff("instagram", sender) is None


def test_old_session_route_does_not_count_as_this_shared_post(monkeypatch, store):  # noqa: F811
    sender = "returning-customer"
    session_memory.get_facts(sender, "instagram")["source_route"] = {"routing_id": 1, "active": True}
    handed_off = []
    monkeypatch.setattr(app_module, "_process_instagram_event",
                        lambda *args, **kwargs: pytest.fail("an unmapped post has no route to answer from"))
    monkeypatch.setattr(app_module, "_handoff_unmapped_shared_media",
                        lambda *args, **kwargs: handed_off.append(kwargs))

    app_module._handle_shared_attachment(sender, None, "unknown-dm-id")

    assert handed_off


def test_photo_post_on_a_reels_mapping_can_be_paired(store, assets):  # noqa: F811
    client = _client()
    _reel, route = _mapped(client, store, "reel", "PAIRREEL")
    pairing = store.start_post_pairing(route["routing_id"], 600, "tester")
    store.bind_post_pairing(pairing["id"], "dm-share-id")

    found, method = route_for_inbound(ad_id=None, media_id=None, post_media_id="dm-share-id", source_type="post")

    assert found["routing_id"] == route["routing_id"] and method == "shared_post_media_id"


def test_attached_post_matches_a_share_that_carries_its_own_id(store, assets):  # noqa: F811
    client = _client()
    _source, route = _mapped(client, store)
    member = _media(client, "post", "MEMBERPOST")
    store.update_instagram_media_route(member["id"], route_mode="CUSTOM", topic_id=None,
                                       custom_route_id=route["routing_id"], needs_review=False)

    found, _method = route_for_inbound(ad_id=None, media_id=None, source_type="post",
                                       post_media_id=member["instagram_media_id"])

    assert found["routing_id"] == route["routing_id"]


def test_select_all_reports_no_skips_for_posts_it_never_offered(store, assets):  # noqa: F811
    client = _client()
    _source, route = _mapped(client, store)
    _mapped(client, store, "reel", "OWNMAPPING")
    _media(client, "reel", "FREE1")

    result = _attach(client, routing_id=route["routing_id"], all_matching=True)

    assert result["attached"] == 1 and result["skipped_mapped"] == 0


@pytest.mark.parametrize("text,quantity", [
    ("dhokar dalna 4 pcs", 1), ("chicken kosha 150 g", 1), ("3 dhokar dalna 4 pcs", 3), ("2 boxes dalna", 2),
    ("dalna on 12 nov", 1), ("dalna by 7 pm", 1), ("2 dalna on 12 november", 2),
])
def test_portion_size_is_not_the_order_quantity(text, quantity):
    item = OrderCalculator()._build_meal_item({"name": "Dish", "price": 300}, text)
    assert item["quantity"] == quantity and item["total_price"] == 300 * quantity


@pytest.mark.parametrize("body", [None, "not json"])
def test_broken_json_body_is_a_client_error(store, body):  # noqa: F811
    response = TestClient(app_module.app, raise_server_exceptions=False).post(
        "/admin/instagram-media/attach-mapping", headers=auth(), content=body)
    assert response.status_code == 400


def test_shared_post_panel_can_pair_and_outlives_the_form_it_replaces():
    from camellia.admin_source_response import SCRIPT
    assert "Pair exact shared Post" in SCRIPT and "igPairingStatus-" in SCRIPT
    # initSourceResponse is still loading when the panel replaces its form.
    assert SCRIPT.count("!==state||!document.getElementById(prefix+'ApprovedQaId'))return;") == 2
    assert "sharedPick.openId!==item.id" in SCRIPT


def test_login_rejects_a_non_object_body():
    response = TestClient(app_module.app, base_url="https://testserver", raise_server_exceptions=False).post(
        "/admin/login", content="[]")
    assert response.status_code == 400


def test_benchmark_patterns_hold_no_raw_backspace():
    source = Path(__file__).parents[1] / "scripts" / "generalization_benchmark.py"
    assert bytes([8]) not in source.read_bytes()
