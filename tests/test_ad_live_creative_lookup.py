"""An ad not yet in the synced inventory still answers like its mapped Post.

31 Oct: a new ad's first "Hello, can I get more info on this?" reached the
manager although its Post was mapped with the "tell me more" response ticked,
because inheritance needed the synced inventory row.
"""
from starlette.background import BackgroundTasks

from camellia import app as app_module, instagram_ads as ads
from camellia.instagram_routing import route_for_inbound

from test_instagram_ads_inventory import item, mock_pages, route, store  # noqa: F401

AD = item()["id"]


def _live_ad(account_id="12345", **creative):
    payload = {**item(**creative), "account_id": account_id}
    payload.pop("name"), payload.pop("status"), payload.pop("effective_status")
    return payload


def _setup(store, monkeypatch):
    monkeypatch.setattr("camellia.database.db", store)
    monkeypatch.setattr(ads, "_LIVE_AD_CACHE", {})


def test_unsynced_ad_inherits_its_post_from_a_live_lookup(store, monkeypatch):
    _setup(store, monkeypatch)
    saved = route(store)
    calls = mock_pages(monkeypatch, [_live_ad()])
    found, method = route_for_inbound(ad_id=AD, media_id=None)
    assert method == "ad_creative" and found["routing_id"] == saved["routing_id"]
    assert str(calls[0].url).startswith(f"https://graph.facebook.com/v25.0/{AD}?")
    assert "access_token" not in str(calls[0].url)
    # Cached: a second message from the same ad makes no new Meta call.
    route_for_inbound(ad_id=AD, media_id=None)
    assert len(calls) == 1


def test_an_ad_from_another_account_inherits_nothing(store, monkeypatch):
    _setup(store, monkeypatch)
    route(store)
    mock_pages(monkeypatch, [_live_ad(account_id="999")])
    assert route_for_inbound(ad_id=AD, media_id=None) == (None, None)


def test_meta_failure_keeps_the_handoff(store, monkeypatch):
    _setup(store, monkeypatch)
    route(store)
    mock_pages(monkeypatch, [(500, {"error": {"code": 2}}), (500, {"error": {"code": 2}})])
    assert route_for_inbound(ad_id=AD, media_id=None) == (None, None)


def test_disabled_ad_is_not_looked_up(store, monkeypatch):
    _setup(store, monkeypatch)
    saved = route(store)
    store.bind_instagram_ad(AD, saved["routing_id"])
    store.set_instagram_ad_binding_active(AD, False)
    calls = mock_pages(monkeypatch, [])
    assert route_for_inbound(ad_id=AD, media_id=None) == (None, None)
    assert not calls


def test_no_marketing_token_makes_no_call(store, monkeypatch):
    _setup(store, monkeypatch)
    route(store)
    monkeypatch.setattr(ads.config, "META_MARKETING_ACCESS_TOKEN", "")
    calls = mock_pages(monkeypatch, [])
    assert route_for_inbound(ad_id=AD, media_id=None) == (None, None)
    assert not calls


def test_meta_failure_is_retried_soon_but_an_answer_is_kept(store, monkeypatch):
    _setup(store, monkeypatch)
    saved = route(store)
    now = [1000.0]
    monkeypatch.setattr(ads.time, "monotonic", lambda: now[0])
    calls = mock_pages(monkeypatch, [(500, {"error": {"code": 2}}), (500, {"error": {"code": 2}}), _live_ad()])
    assert route_for_inbound(ad_id=AD, media_id=None) == (None, None)
    assert len(calls) == 2
    # The same event looks the route up again: a failing Meta is not asked twice.
    route_for_inbound(ad_id=AD, media_id=None)
    assert len(calls) == 2
    now[0] += ads._LIVE_AD_RETRY_SECONDS + 1
    found, method = route_for_inbound(ad_id=AD, media_id=None)
    assert method == "ad_creative" and found["routing_id"] == saved["routing_id"]
    now[0] += ads._LIVE_AD_CACHE_SECONDS - 1
    route_for_inbound(ad_id=AD, media_id=None)
    assert len(calls) == 3


def test_an_ad_meta_answered_for_is_not_asked_again_soon(store, monkeypatch):
    _setup(store, monkeypatch)
    route(store)
    now = [1000.0]
    monkeypatch.setattr(ads.time, "monotonic", lambda: now[0])
    calls = mock_pages(monkeypatch, [_live_ad(account_id="999")])
    route_for_inbound(ad_id=AD, media_id=None)
    now[0] += ads._LIVE_AD_RETRY_SECONDS + 1
    route_for_inbound(ad_id=AD, media_id=None)
    assert len(calls) == 1


def test_webhook_handler_never_waits_on_meta(store, monkeypatch):
    # The async handler runs on the event loop: a Meta call here would freeze
    # every customer, the Admin and /health. The background worker does it.
    _setup(store, monkeypatch)
    monkeypatch.setattr(app_module, "db", store)
    monkeypatch.setattr(app_module.config, "INSTAGRAM_ACCOUNT_ID", "")
    route(store)
    calls = mock_pages(monkeypatch, [])
    app_module._dispatch_instagram_payload({"object": "instagram", "entry": [{"id": "1", "messaging": [{
        "sender": {"id": "customer-1"}, "recipient": {"id": "1"},
        "message": {"mid": "mid-live-ad", "text": "Hello, can I get more info on this?",
                    "referral": {"source": "ADS", "type": "OPEN_THREAD", "ad_id": AD}},
    }]}]}, BackgroundTasks())
    assert store.instagram_ad_binding_status(AD) == "UNBOUND"
    assert store.get_instagram_ad_binding(AD)
    assert not calls
