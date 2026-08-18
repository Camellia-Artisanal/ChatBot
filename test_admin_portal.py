import os
import json
import pytest
from fastapi.testclient import TestClient

from config import config
import database as db_module
from app import app

# Ensure database is initialized for test client
db_module.db.init_db()

client = TestClient(app)

ADMIN_PIN = config.ADMIN_PIN or "51990"
ADMIN_HEADERS = {"x-admin-pin": ADMIN_PIN}

@pytest.fixture(autouse=True)
def ensure_db():
    db_module.db.init_db()


def test_admin_dashboard_page():
    """Verifies that the /admin and /admin/dashboard pages return HTML successfully."""
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Kitchen & Menu Admin Portal" in resp.text
    assert "Kitchen Admin Portal" in resp.text and "Unlock Portal" in resp.text

    resp_dash = client.get("/admin/dashboard")
    assert resp_dash.status_code == 200
    assert resp_dash.text == resp.text

def test_verify_pin_endpoint():
    """Tests the PIN validation API endpoint."""
    # Correct PIN
    resp = client.post("/admin/verify-pin", json={"pin": ADMIN_PIN})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("valid") is True

    # Incorrect PIN
    resp_bad = client.post("/admin/verify-pin", json={"pin": "99999"})
    assert resp_bad.status_code == 401
    assert "detail" in resp_bad.json()

def test_admin_auth_protection():
    """Ensures protected endpoints reject unauthenticated requests with 403 Forbidden."""
    # Without headers
    resp = client.get("/admin/menu/active")
    assert resp.status_code == 403

    # With invalid PIN header
    resp = client.get("/admin/menu/active", headers={"x-admin-pin": "wrongpin"})
    assert resp.status_code == 403

    # With valid PIN header
    resp = client.get("/admin/menu/active", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json().get("status") == "success"

def test_parse_menu_card_text():
    """Tests raw text parsing and hot-reloading through the admin API."""
    test_text = """
    Weekend Special:
    * Ilish Machher Jhol - ₹520 (350g)
    * Lau Ghonto with Bori - ₹260 (250g)
    """
    resp = client.post("/admin/parse-menu-card", json={"raw_text": test_text}, headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "success"
    assert data.get("updated_items_count", 0) >= 2

def test_reset_menu_defaults():
    """Tests resetting menu items back to default Bengal cuisine baseline."""
    resp = client.post("/admin/menu/reset-defaults", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "success"

    # Verify active menu shows defaults
    resp_active = client.get("/admin/menu/active", headers=ADMIN_HEADERS)
    assert resp_active.status_code == 200
    menu = resp_active.json().get("menu", {})
    last_week = menu.get("weekly_menus", [])[-1]
    veg_names = [item["item"] for item in last_week.get("vegetarian", [])]
    nonveg_names = [item["item"] for item in last_week.get("non_vegetarian", [])]

    assert "Chholar Daal" in veg_names
    assert "Mangsho (Mutton Kosha)" in nonveg_names

def test_admin_analytics_and_escalations():
    """Tests the analytics summary and escalations list endpoints."""
    # Analytics
    resp_stats = client.get("/admin/analytics", headers=ADMIN_HEADERS)
    assert resp_stats.status_code == 200
    stats = resp_stats.json()
    assert "total_messages" in stats
    assert "matched_responses" in stats
    assert "match_rate_percent" in stats

    # Escalations
    resp_esc = client.get("/admin/escalations", headers=ADMIN_HEADERS)
    assert resp_esc.status_code == 200
    esc = resp_esc.json()
    assert "escalations" in esc
