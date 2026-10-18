"""Outbound send queue: failures end, later replies flow, retries never repeat."""

import pytest

from camellia import app as app_module
from camellia.config import config
from camellia.database import Database
from camellia.monitoring import classify_instagram_delivery


@pytest.fixture
def database(tmp_path, monkeypatch):
    database = Database(str(tmp_path / "outbound.db"))
    monkeypatch.setattr(app_module, "db", database)
    monkeypatch.setattr(app_module.instagram_chatbot_control, "is_sender_allowed", lambda *a, **k: True)
    return database


def _make_due(database):
    with database.get_connection() as conn:
        conn.execute("UPDATE instagram_outbound_jobs SET next_attempt_at = datetime('now', '-1 second')")
        conn.commit()


def _statuses(database):
    with database.get_connection() as conn:
        return [row["status"] for row in conn.execute("SELECT status FROM instagram_outbound_jobs ORDER BY id")]


def _batch(database, event, texts, sender="customer-1"):
    inbound, _ = database.enqueue_instagram_inbound_job(event, sender, "task", {})
    for number, text in enumerate(texts, 1):
        database.enqueue_instagram_outbound_job(inbound, number, sender, {"text": text})
    return inbound


def test_retryable_failure_stops_and_releases_newer_replies(database, monkeypatch):
    _batch(database, "event-1", ["stuck"])
    _batch(database, "event-2", ["newer"])
    sent = []

    def send(recipient, text, **kwargs):
        if text == "stuck":
            return {"status": "failed", "delivery_status": "AUTH_FAILURE"}
        sent.append(text)
        return {"message_id": "ok"}

    monkeypatch.setattr(app_module, "send_instagram_reply", send)
    for _ in range(10):
        app_module._process_instagram_outbound_jobs()
        _make_due(database)
    assert _statuses(database) == ["FAILED", "SENT"]
    assert sent == ["newer"]


def test_exceptions_are_capped_too(database, monkeypatch):
    _batch(database, "event-1", ["boom"])

    def send(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(app_module, "send_instagram_reply", send)
    for _ in range(10):
        app_module._process_instagram_outbound_jobs()
        _make_due(database)
    assert _statuses(database) == ["FAILED"]


def test_permanent_failure_abandons_rest_of_answer(database, monkeypatch):
    _batch(database, "event-1", ["first", "second", "third"])
    _batch(database, "event-2", ["next answer"])
    sent = []

    def send(recipient, text, **kwargs):
        if text == "first":
            return {"status": "failed", "delivery_status": "PERMANENT_RECIPIENT_FAILURE"}
        sent.append(text)
        return {"message_id": "ok"}

    monkeypatch.setattr(app_module, "send_instagram_reply", send)
    app_module._process_instagram_outbound_jobs()
    app_module._process_instagram_outbound_jobs()
    assert _statuses(database) == ["FAILED", "FAILED", "FAILED", "SENT"]
    assert sent == ["next answer"]


def test_outside_window_and_unavailable_user_are_permanent():
    assert classify_instagram_delivery(http_status=400, meta_code=10, meta_subcode=2534022) == "PERMANENT_RECIPIENT_FAILURE"
    assert classify_instagram_delivery(http_status=400, meta_code=551) == "PERMANENT_RECIPIENT_FAILURE"
    assert classify_instagram_delivery(http_status=403, meta_code=10) == "AUTH_FAILURE"


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body, self.headers = code, body, {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %s" % self.status_code)


def test_partial_chunk_failure_resumes_after_accepted_chunks(database, monkeypatch):
    monkeypatch.setattr(config, "INSTAGRAM_ACCESS_TOKEN", "tok")
    posts = []

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            posts.append(json["message"].get("text", ""))
            if len(posts) == 2:
                return _Resp(500, {"error": {"code": 2, "message": "temporary"}})
            return _Resp(200, {"message_id": "mid-%d" % len(posts)})

    monkeypatch.setattr(app_module.httpx, "Client", Client)
    long_text = ("Para one. " * 60) + "\n\n" + ("Para two. " * 60)
    chunks = app_module._split_instagram_text(long_text)
    assert len(chunks) == 2
    _batch(database, "event-1", [long_text])
    app_module._process_instagram_outbound_jobs()
    _make_due(database)
    app_module._process_instagram_outbound_jobs()
    assert posts == [chunks[0], chunks[1], chunks[1]]
    assert _statuses(database) == ["SENT"]


def _open_handoff(database, sender="customer-1"):
    database.create_handoff(
        handoff_ref="HX-1", source_channel="instagram", source_user_id=sender, reason="test",
        summary=None, known_entities=None, unresolved_topic=None, escalation_id=None, expires_at=None,
    )


def test_stale_reply_is_dropped_after_staff_take_over(database, monkeypatch):
    inbound = _batch(database, "event-1", ["stale bot reply", "second block"])
    database.complete_instagram_inbound_job(inbound, success=True)
    with database.get_connection() as conn:
        conn.execute("UPDATE instagram_inbound_jobs SET updated_at = datetime('now', '-1 hour')")
        conn.commit()
    _open_handoff(database)
    sent = []
    monkeypatch.setattr(app_module, "send_instagram_reply", lambda r, text, **k: sent.append(text) or {"message_id": "ok"})
    app_module._process_instagram_outbound_jobs()
    assert sent == []
    assert _statuses(database) == ["FAILED", "FAILED"]


def test_handoff_text_from_the_same_answer_is_still_sent(database, monkeypatch):
    inbound = _batch(database, "event-1", ["handoff text"])
    _open_handoff(database)  # opened while the answer was being written
    database.complete_instagram_inbound_job(inbound, success=True)
    sent = []
    monkeypatch.setattr(app_module, "send_instagram_reply", lambda r, text, **k: sent.append(text) or {"message_id": "ok"})
    app_module._process_instagram_outbound_jobs()
    assert sent == ["handoff text"]
