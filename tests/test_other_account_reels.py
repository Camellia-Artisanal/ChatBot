"""A reel forwarded from another account opens no handoff, and the bot stays active."""
from unittest.mock import patch

import pytest

from camellia import app as app_module
from camellia.config import config

from test_ad_mapped_source_context import ad_customer, _process  # noqa: F401

OWNER = "owner-account"


@pytest.fixture
def synced(ad_customer, monkeypatch):  # noqa: F811
    db = ad_customer[2]
    monkeypatch.setattr(config, "INSTAGRAM_GRAPH_ACCOUNT_ID", OWNER)
    monkeypatch.setattr(db, "has_successful_instagram_media_inventory_sync", lambda owner: owner == OWNER)
    return ad_customer


def _own_reel(db, code):
    db.upsert_instagram_media({"instagram_media_id": f"media-{code}", "shortcode": code, "media_type": "VIDEO",
                               "permalink": f"https://www.instagram.com/reel/{code}/", "sync_status": "SYNCED",
                               "inventory_state": "CURRENT"})


def _share(sender, code):
    with patch.object(app_module, "send_instagram_reply", return_value={"status": "sent"}) as send, \
            patch.object(app_module.escalator, "handle_escalation"):
        app_module._handle_shared_attachment(sender, {"shortcode": code}, None)
    return send


def _rows(db, sender):
    return db.get_conversations(limit=5, channel="instagram", user_id=sender)


def test_another_accounts_reel_alone_gets_no_reply_and_no_handoff(synced):
    sender, _facts, db = synced
    send = _share(sender, "OTHERREEL1")
    assert not send.called
    assert db.get_active_handoff("instagram", sender) is None
    row = _rows(db, sender)[0]
    assert (row["template_id"], row["shortcode"], row["bot_response"]) == ("shared_reel_other_account", "OTHERREEL1", "")


def test_the_bot_stays_active_for_the_next_question(synced):
    sender, _facts, db = synced
    _share(sender, "OTHERREEL2")
    with patch.object(app_module, "send_instagram_reply", return_value={"status": "sent"}) as send, \
            patch.object(app_module.escalator, "handle_escalation"):
        app_module._process_instagram_event(sender, "How do I place an order?", None, app_module.EntryPoint(source="typed"))
    assert send.called
    assert _rows(db, sender)[0]["template_id"] != "waiting_for_human"


def test_a_reel_with_text_is_answered_normally_not_handed_off_for_the_reel(synced):
    sender, _facts, db = synced
    with patch.object(app_module, "send_instagram_reply", return_value={"status": "sent"}) as send, \
            patch.object(app_module.escalator, "handle_escalation"), \
            patch.object(app_module, "_handoff_unmapped_shared_media") as handoff:
        app_module._process_instagram_event(
            sender, "How do I place an order?", None, app_module.EntryPoint(source="shared_media"),
            shared_reel={"shortcode": "OTHERREEL3"}, shared_source_type="reel",
        )
    assert not handoff.called
    assert send.called


def test_our_own_unmapped_reel_still_goes_to_staff(synced):
    sender, _facts, db = synced
    _own_reel(db, "OURREEL1")
    _share(sender, "OURREEL1")
    assert db.get_active_handoff("instagram", sender) is not None


def test_without_a_complete_sync_it_stays_a_handoff(ad_customer, monkeypatch):  # noqa: F811
    sender, _facts, db = ad_customer
    monkeypatch.setattr(config, "INSTAGRAM_GRAPH_ACCOUNT_ID", OWNER)
    monkeypatch.setattr(db, "has_successful_instagram_media_inventory_sync", lambda owner: False)
    _share(sender, "UNKNOWNREEL")
    assert db.get_active_handoff("instagram", sender) is not None
