"""Groq's token budget is per model and per minute, so /health reports it that way."""

import time
from unittest.mock import MagicMock, patch

from camellia.ai_responder import ai_responder
from camellia.config import config
from camellia.monitoring import ProviderEvents


def _groq_reply(content, usage):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"choices": [{"message": {"content": content}}], "usage": usage}
    return resp


def _query(resp, **kwargs):
    events = ProviderEvents()
    ai_responder._groq_cooldown_until = 0.0
    with patch("camellia.ai_responder.httpx.Client") as client, \
         patch("camellia.monitoring.provider_events", events), \
         patch.object(config, "GROQ_ENABLED", True), \
         patch.object(config, "GROQ_API_KEY", "key"):
        client.return_value.__enter__.return_value.post.return_value = resp
        ai_responder._query_groq("hello", **kwargs)
    return events


def test_a_successful_call_records_its_tokens_by_operation_and_model():
    events = _query(_groq_reply("hi", {"total_tokens": 120}), model="side-model")

    assert events.token_summary() == {
        "since_start_by_operation": {"structured": 120},
        "last_minute_by_model": {"side-model": 120},
    }
    assert events.snapshot()["recent"][0]["tokens"] == 120


def test_a_refused_answer_still_counts_against_the_budget():
    events = _query(_groq_reply("OUT_OF_CONTEXT", {"total_tokens": 90}))

    assert events.token_summary()["since_start_by_operation"] == {"answer": 90}


def test_a_reply_without_usage_records_no_tokens():
    events = _query(_groq_reply("hi", None))

    assert events.token_summary() == {"since_start_by_operation": {}, "last_minute_by_model": {}}


def test_tokens_leave_the_minute_window_but_stay_in_the_total():
    events = ProviderEvents()
    events.record("answer", "success", time.monotonic(), model="m", tokens=50)
    with patch("camellia.monitoring.time.monotonic", return_value=time.monotonic() + 61):
        summary = events.token_summary()

    assert summary == {"since_start_by_operation": {"answer": 50}, "last_minute_by_model": {}}


def test_health_status_carries_the_token_summary():
    assert set(ai_responder.status()["tokens"]) == {"since_start_by_operation", "last_minute_by_model"}
