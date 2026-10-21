"""Short WhatsApp links: /w/<ref>-<code> redirects to the full wa.me link (21 Oct)."""
import re

import pytest
from fastapi.testclient import TestClient

from camellia import app as app_module, handoff, order_intake
from camellia.config import config
from camellia.database import Database

BASE = "https://camellia-artisanal-production.up.railway.app"


@pytest.fixture
def store(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "short-links.db"))
    for module in (app_module, handoff):
        monkeypatch.setattr(module, "db", database)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", BASE + "/")
    monkeypatch.setattr(config, "WHATSAPP_BUSINESS_NUMBER", "918796551990")
    return database


def test_handoff_link_is_short_and_opens_the_same_whatsapp_chat(store):
    full = handoff.full_whatsapp_link("CAM-12345")
    short = handoff.whatsapp_link("CAM-12345")
    assert re.fullmatch(re.escape(BASE) + r"/w/CAM-12345-[A-Za-z0-9]{6}", short)
    assert len(short) <= 75 < len(full)
    response = TestClient(app_module.app).get(short[len(BASE):], follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == full


def test_a_resend_reuses_the_same_short_link(store):
    first = handoff.whatsapp_link("CAM-22222")
    assert handoff.whatsapp_link("CAM-22222") == first


def test_guessing_the_reference_alone_opens_nothing(store):
    handoff.whatsapp_link("CAM-33333")
    client = TestClient(app_module.app)
    for path in ("/w/CAM-33333", "/w/CAM-33333-aaaaaa", "/w/<script>"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404 and "expired" in response.text


def test_only_whatsapp_targets_are_ever_redirected_to(store):
    store.save_whatsapp_short_link("CAM-44444", "https://evil.example/x", "CAM-44444-abcdef")
    assert TestClient(app_module.app).get("/w/CAM-44444-abcdef", follow_redirects=False).status_code == 404


@pytest.mark.parametrize("base", ["", "not a url", "ftp://x", "https://example.com/app"])
def test_without_a_public_address_the_full_link_is_sent(store, monkeypatch, base):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", base)
    assert handoff.whatsapp_link("CAM-55555").startswith("https://wa.me/")


def test_order_draft_links_are_short_too(store):
    draft = order_intake.new_order_draft()
    text = order_intake._short_link(order_intake.whatsapp_draft_link(draft))
    assert text.startswith(BASE + "/w/ORDER-")


def test_old_short_links_are_purged_with_other_data(store):
    store.save_whatsapp_short_link("CAM-66666", "https://wa.me/1?text=x", "CAM-66666-old123")
    with store.get_connection() as conn:
        conn.execute("UPDATE whatsapp_short_links SET created_at='2000-01-01 00:00:00'")
        conn.commit()
    store.purge_expired_data(retention_days=30)
    assert store.get_whatsapp_short_link("CAM-66666-old123") is None
