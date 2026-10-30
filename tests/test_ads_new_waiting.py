"""New ads show before Meta delivers them; Sync Now and auto-sync bring in their posts (19 Oct).

The client started three ads and checked 10-20 minutes later: still in Meta
review, so "Running Now" hid them and it looked like sync takes hours.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from camellia import instagram_ads as ads
from camellia.admin_ads import SCRIPT

from test_instagram_ads_inventory import item, route, store, sync  # noqa: F401


def _created(minutes_ago):
    stamp = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return stamp.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%dT%H:%M:%S%z")


def _ad(ad_id, effective, minutes_ago, media="180001"):
    row = item(ad_id, effective, effective_instagram_media_id=media)
    row["created_time"] = _created(minutes_ago)
    return row


def _delivering(monkeypatch, ad_ids=()):
    monkeypatch.setattr(ads, "delivery_evidence", lambda: {
        "status": "available", "rule": "", "ads": {ad_id: {"impressions": 5} for ad_id in ad_ids},
        "reporting_date": "2026-10-19", "account_timezone": "Asia/Kolkata", "checked_at": "now", "raw_running": len(ad_ids)})


def test_new_ads_show_in_running_now_with_their_label(store, monkeypatch):
    sync(store, monkeypatch, [_ad("111", "PENDING_REVIEW", 10), _ad("222", "ACTIVE", 15, media="180002"),
                              _ad("333", "PENDING_REVIEW", 3 * 24 * 60, media="180003"), _ad("444", "ACTIVE", 20, media="180004")])
    _delivering(monkeypatch, ["444"])
    result = ads.ads_workspace(store, "running")
    labels = {row["ad_id"]: row["waiting_for_meta"] for row in result["items"]}
    assert labels["111"] == "In review"
    assert labels["222"] == "Not delivering yet"
    assert "333" not in labels  # three days old: not new
    assert labels["444"] is None  # already delivering, listed with the running groups
    assert result["new_ad_ids"] == ["111", "222"] and result["counts"]["new_waiting"] == 2


def test_active_ads_are_not_called_undelivered_without_delivery_data(store, monkeypatch):
    sync(store, monkeypatch, [_ad("111", "PENDING_REVIEW", 10), _ad("222", "ACTIVE", 15, media="180002")])
    labels = {row["ad_id"]: row["waiting_for_meta"] for row in ads.ads_workspace(store, "all")["items"]}
    assert labels == {"111": "In review", "222": None}


def test_auto_sync_runs_ads_then_posts_and_stops(monkeypatch, store):
    calls = []
    monkeypatch.setattr(ads, "sync_instagram_ads", lambda database: calls.append("ads"))
    monkeypatch.setattr("camellia.instagram_media.sync_instagram_media", lambda database: calls.append("media"))

    async def run():
        stop = asyncio.Event()
        real_wait_for = asyncio.wait_for

        async def no_wait(awaitable, timeout):
            if calls:  # second pass: stop the loop
                stop.set()
                return await real_wait_for(awaitable, 1)
            awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(ads.asyncio, "wait_for", no_wait)
        await ads.ads_sync_loop(stop, store)

    asyncio.run(run())
    assert calls == ["ads", "media"]


def test_auto_sync_survives_a_failing_sync(monkeypatch, store):
    calls = []

    def boom(database):
        calls.append("ads")
        raise RuntimeError("meta down")

    monkeypatch.setattr(ads, "sync_instagram_ads", boom)
    monkeypatch.setattr("camellia.instagram_media.sync_instagram_media", lambda database: calls.append("media"))

    async def run():
        stop = asyncio.Event()
        real_wait_for = asyncio.wait_for

        async def no_wait(awaitable, timeout):
            if calls:
                stop.set()
                return await real_wait_for(awaitable, 1)
            awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(ads.asyncio, "wait_for", no_wait)
        await ads.ads_sync_loop(stop, store)

    asyncio.run(run())
    assert calls == ["ads", "media"]


def test_ads_card_queues_background_sync_and_lists_new_ads():
    from camellia.admin_ads import HTML, SCRIPT
    assert 'id="adsSyncNotice"' in HTML
    assert "/admin/instagram-ads/sync" in SCRIPT
    assert "/admin/instagram-ads/manual-sync-status" in SCRIPT
    assert "waitForManualAdsSync" in SCRIPT and "Syncing posts and reels" in SCRIPT
    assert "New: waiting for Meta" in SCRIPT and "new_ad_ids" in SCRIPT


def test_ads_workspace_surfaces_recent_unmapped_and_missing_referral_warnings():
    assert "unmapped_messaged" in SCRIPT
    assert "missing_ad_id_referrals" in SCRIPT
    assert "last 7 days" in SCRIPT


def test_an_ad_past_its_schedule_is_not_waiting(store, monkeypatch):
    sync(store, monkeypatch, [_ad("111", "ACTIVE", 30)])
    monkeypatch.setattr(ads, "delivery_evidence", lambda: {
        "status": "available", "rule": "", "ads": {}, "raw_running": 0,
        "schedules": {"111": {"eligibility": "ENDED"}}})
    assert ads.ads_workspace(store, "running")["new_ad_ids"] == []
