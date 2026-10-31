"""Reel sharing is verified on the webhook account ID, not the /me Graph ID.

31 Oct: the Approved Q&A Reel picker said "This connected account is not
verified for native Camellia Reel sharing" in production, because the check
compared the Instagram Login /me ID (a different number for the same account)
with the verified webhook account ID.
"""
from camellia import home_table_native_reels as native
from camellia.config import config

GRAPH_OWNER_ID = "28206314108997745"


def _configure(monkeypatch, webhook_id, graph_id=GRAPH_OWNER_ID):
    monkeypatch.setattr(config, "INSTAGRAM_ACCOUNT_ID", webhook_id)
    monkeypatch.setattr(config, "INSTAGRAM_GRAPH_ACCOUNT_ID", graph_id)
    monkeypatch.setattr(config, "INSTAGRAM_EXPECTED_USERNAME", "camellia.artisanal")
    monkeypatch.setattr(config, "INSTAGRAM_AUTH_MODE", "instagram_login")


def test_production_id_pair_enables_reel_sharing(monkeypatch):
    _configure(monkeypatch, native.VERIFIED_ACCOUNT_ID)
    assert native.native_sharing_enabled()


def test_another_webhook_account_is_refused(monkeypatch):
    _configure(monkeypatch, "17840000000000000")
    assert not native.native_sharing_enabled()


def test_unbound_graph_owner_is_refused(monkeypatch):
    _configure(monkeypatch, native.VERIFIED_ACCOUNT_ID, graph_id="")
    assert not native.native_sharing_enabled()


def test_other_username_is_refused(monkeypatch):
    _configure(monkeypatch, native.VERIFIED_ACCOUNT_ID)
    monkeypatch.setattr(config, "INSTAGRAM_EXPECTED_USERNAME", "someone.else")
    assert not native.native_sharing_enabled()
