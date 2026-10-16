"""
/health has to say when menu cards cannot reach Instagram.

Meta fetches every menu card from PUBLIC_BASE_URL. After the 2026-10-16 move
to the client's Railway the bot sent the menu text but no cards, while /health
said healthy: an empty or bare-domain value drops every card silently, and an
old server's domain works only until that server is stopped.
"""
import pytest
from fastapi.testclient import TestClient

from camellia.config import config
import camellia.database as db_module
from camellia.app import app

db_module.db.init_db()
client = TestClient(app)


def _menu_warnings():
    body = client.get("/health").json()
    return [w for w in body.get("warnings", []) if "PUBLIC_BASE_URL" in w]


@pytest.mark.parametrize("value", ["", "camellia-artisanal-production.up.railway.app"])
def test_missing_or_bare_base_url_is_flagged(monkeypatch, value):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", value)
    assert any("menu cards are dropped" in w for w in _menu_warnings())


def test_base_url_on_another_server_is_flagged(monkeypatch):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "camellia-artisanal-production.up.railway.app")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://web-production-0cad70.up.railway.app")
    assert any("different server" in w for w in _menu_warnings())


def test_correct_base_url_is_silent(monkeypatch):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "camellia-artisanal-production.up.railway.app")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://camellia-artisanal-production.up.railway.app")
    assert _menu_warnings() == []
