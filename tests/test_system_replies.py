"""System Q&A (23 Oct): the built-in conversation replies are editable."""
import pytest
from fastapi.testclient import TestClient

from camellia import app as app_module, grounded_answers, system_replies
from camellia.config import config
from camellia.database import Database


@pytest.fixture
def client(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "system-replies.db"))
    monkeypatch.setattr(app_module, "db", database)
    monkeypatch.setattr(system_replies, "db", database)
    monkeypatch.setattr(config, "ADMIN_API_KEY", "system-replies-admin")
    return TestClient(app_module.app), {"x-admin-key": "system-replies-admin"}


def test_defaults_are_what_customers_got_before(client):
    assert grounded_answers.system_reply("Hii") == "Hi! Thanks for reaching out to Camellia.artisanal 😊 How can I help you today?"
    assert grounded_answers.system_reply("thank you") == "You're most welcome! 😊"
    assert grounded_answers.system_reply("bye") == "Thank you for connecting with Camellia.artisanal. Have a lovely day!"


def test_an_edited_greeting_is_sent_and_reset_restores_it(client):
    http, headers = client
    saved = http.put("/admin/system-replies/greeting", headers=headers, json={"text": "Namaste! How can we help?"})
    assert saved.status_code == 200
    assert next(r for r in saved.json()["replies"] if r["id"] == "greeting")["edited"] is True
    assert grounded_answers.system_reply("Hello") == "Namaste! How can we help?"
    assert http.post("/admin/system-replies/greeting/reset", headers=headers).status_code == 200
    assert grounded_answers.system_reply("Hello").startswith("Hi! Thanks for reaching out")


def test_every_reply_is_listed_with_its_trigger(client):
    http, headers = client
    replies = http.get("/admin/system-replies", headers=headers).json()["replies"]
    assert {r["id"] for r in replies} == set(system_replies.REPLIES)
    assert all(r["trigger"] and r["text"] == r["default"] for r in replies)


@pytest.mark.parametrize("body,status", [({"text": ""}, 400), ({"text": "x" * 1001}, 400), ([], 400)])
def test_bad_edits_are_refused(client, body, status):
    http, headers = client
    assert http.put("/admin/system-replies/greeting", headers=headers, json=body).status_code == status


def test_unknown_replies_and_no_login_are_refused(client):
    http, headers = client
    assert http.put("/admin/system-replies/nope", headers=headers, json={"text": "Hello there"}).status_code == 404
    assert http.get("/admin/system-replies").status_code in (401, 403)


def test_the_tab_is_on_the_approved_qa_page():
    from camellia.admin_approved_qa import HTML, SCRIPT
    assert 'id="aqTabSystem"' in HTML and 'id="aqSystemView"' in HTML
    assert "/admin/system-replies" in SCRIPT and "'order_prebook'" in SCRIPT and "/admin/handoff-message" in SCRIPT


def test_publishing_from_system_qa_refreshes_its_cards():
    # 24 Oct: after editing "Pre-booking steps" the card kept showing the old text.
    from camellia import admin_approved_qa
    source = open(admin_approved_qa.__file__, encoding="utf-8").read()
    start = source.index("async function loadApprovedQa()")
    assert "aqLoadSystem()" in source[start:source.index("\n", start)]
