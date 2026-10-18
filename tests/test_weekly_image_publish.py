"""Weekly Savoury publishes the uploaded image as it is (client, 18 Oct).

The client asked for upload → Publish with nothing read or checked. The bot
sends the image; a dish-price question must not get last week's price.
"""
import io
import json
from datetime import date

from fastapi.testclient import TestClient
from PIL import Image

from camellia import menu_dates, menu_images, menu_review, weekly_savoury
from camellia.app import app
from camellia.config import config
from camellia.database import db
from camellia.rag_fallback import rag_fallback_engine

from test_weekly_savoury_lifecycle import _draft, _publish, weekly_env  # noqa: F401


def _photo(fmt="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", (120, 160), "red").save(buffer, format=fmt)
    return buffer.getvalue()


def test_image_replaces_the_parsed_week_and_open_draft(weekly_env, monkeypatch):
    monkeypatch.setattr(menu_dates, "today_ist", lambda: date(2026, 9, 27))
    old = _publish(_draft("22 Sep - 28 Sep 2026", 850))
    open_draft = _draft("22 Sep - 28 Sep 2026", 900)
    response = TestClient(app).post("/admin/weekly-savoury/publish-image",
                                    headers={"X-Admin-Key": config.ADMIN_API_KEY},
                                    files={"file": ("menu.png", _photo(), "image/png")})
    assert response.status_code == 200, response.text
    current = weekly_savoury.current_version()
    approval = json.loads(current["approval_json"])
    assert approval["rows"] == [] and current["valid_until"] == "2026-10-03"
    assert menu_images.current_weekly_url() == approval["image_path"]
    assert db.get_menu_version(old["version_id"])["weekly_state"] == "SUPERSEDED"
    assert menu_review.get_draft(weekly_savoury.WEEKLY_KEY)["draft_id"] == open_draft["draft_id"]
    assert menu_review.get_draft(weekly_savoury.WEEKLY_KEY)["status"] == "REJECTED"
    assert not menu_review.customer_blocked_keys() & set(weekly_savoury.LEGACY_WEEKLY_KEYS)
    answer = rag_fallback_engine.retrieve_approved_menu("How much is Chicken Kosha this week?").auto_response
    assert "₹850" not in (answer or "") and "₹900" not in (answer or "")
    assert response.json()["weekly"]["current"]["image_url"] == approval["image_path"]


def test_pdf_publishes_its_first_page_and_bad_files_are_refused(weekly_env):
    version = menu_review.publish_weekly_image(_photo("PDF"), "application/pdf", "menu.pdf", "admin")
    assert version["image_path"].endswith(".png")
    response = TestClient(app).post("/admin/weekly-savoury/publish-image",
                                    headers={"X-Admin-Key": config.ADMIN_API_KEY},
                                    files={"file": ("menu.txt", b"hello", "text/plain")})
    assert response.status_code == 400
    assert weekly_savoury.current_version()["id"] == version["version_id"]
