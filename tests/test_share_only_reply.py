"""A mapped Custom Post or Reel shared with no text gets its note after a short wait (15 Nov)."""
import uuid
from unittest.mock import patch

import pytest

from camellia import app as app_module
from camellia.ad_gate import EntryPoint
from camellia.session_memory import session_memory

from test_followup_manual_sequences import assets, auth, store  # noqa: F401
from test_shared_media_mapping import NOTE, _attach, _client, _mapped, _media


@pytest.fixture
def sends():
    sent = []
    with patch("camellia.app._send_or_queue_instagram_reply",
               side_effect=lambda recipient, text, **kwargs: sent.append((recipient, text, kwargs.get("image_url")))):
        yield sent


def _customer():
    sender = "share-" + uuid.uuid4().hex
    session_memory.clear_session(sender, "instagram")
    return sender


def _share(sender, shortcode):
    app_module._process_instagram_event(sender, "", None, EntryPoint(source="shared_media"),
                                        shared_reel={"shortcode": shortcode}, shared_source_type="reel")


def _wait_over(store):  # noqa: F811
    with store.get_connection() as conn:
        conn.execute("UPDATE instagram_share_replies SET due_at = due_at - 6, received_at = received_at - 6")
        conn.commit()
    app_module._send_due_share_replies()


def _status(store, sender):  # noqa: F811
    with store.get_connection() as conn:
        return [row[0] for row in conn.execute("SELECT status FROM instagram_share_replies WHERE sender_id=?", (sender,))]


def _reel(store, code="ALONE1", **changes):  # noqa: F811
    _item, route = _mapped(_client(), store, kind="reel", code=code)
    if changes:
        saved = app_module.decode_route(store.get_instagram_source_route(route["routing_id"]))
        store.save_instagram_source_route({**saved, **changes}, route["routing_id"])
    return route


def test_a_share_alone_waits_then_gets_the_custom_note(store, assets, sends):  # noqa: F811
    _reel(store)
    sender = _customer()
    _share(sender, "ALONE1")
    assert sends == []
    app_module._send_due_share_replies()
    assert sends == []  # Still inside the wait.
    _wait_over(store)
    assert [text for _, text, _ in sends] == [NOTE]
    stored = store.get_conversations(limit=1, channel="instagram", user_id=sender)[0]
    assert (stored["user_message"], stored["bot_response"], stored["template_id"]) == ("[shared post]", NOTE, "source_description")


def test_the_same_post_shared_twice_is_answered_once(store, assets, sends):  # noqa: F811
    _reel(store, "TWICE1")
    sender = _customer()
    _share(sender, "TWICE1")
    _wait_over(store)
    _share(sender, "TWICE1")
    _wait_over(store)
    assert len(sends) == 1


def test_words_typed_after_the_share_cancel_the_note(store, assets, sends):  # noqa: F811
    _reel(store, "TYPED1")
    sender = _customer()
    _share(sender, "TYPED1")
    store.enqueue_instagram_inbound_job("typed-" + sender, sender, "_process_instagram_event", {"args": [], "kwargs": {}})
    with store.get_connection() as conn:
        conn.execute("UPDATE instagram_share_replies SET due_at = due_at - 6")
        conn.commit()
    app_module._send_due_share_replies()
    assert sends == [] and _status(store, sender) == ["TEXT"]


def test_a_shared_mapping_answers_for_every_post_it_is_on(store, assets, sends):  # noqa: F811
    client = _client()
    _source, route = _mapped(client, store, kind="reel", code="OWNER1")
    member = _media(client, "reel", "MEMBER9")
    _attach(client, routing_id=route["routing_id"], item_ids=[member["id"]])
    sender = _customer()
    _share(sender, "MEMBER9")
    _wait_over(store)
    assert [text for _, text, _ in sends] == [NOTE]


@pytest.mark.parametrize("changes, expected", [
    ({"response_mode": "APPROVED_QA", "approved_qa_id": "general_price"}, []),
    ({"source_description": ""}, []),
])
def test_approved_and_empty_custom_mappings_stay_quiet(store, assets, sends, changes, expected):  # noqa: F811
    _reel(store, "QUIET" + str(len(changes["source_description"]) if "source_description" in changes else 1), **changes)
    sender = _customer()
    _share(sender, "QUIET0" if "source_description" in changes else "QUIET1")
    _wait_over(store)
    assert sends == expected and _status(store, sender) == []


def test_an_open_handoff_keeps_the_customer_with_staff(store, assets, sends):  # noqa: F811
    _reel(store, "HANDOFF1")
    sender = _customer()
    _share(sender, "HANDOFF1")
    with patch.object(app_module.db, "get_active_handoff", return_value={"handoff_ref": "H1"}):
        _wait_over(store)
    assert sends == [] and _status(store, sender) == ["HANDOFF"]


def test_a_late_wait_is_dropped_rather_than_sent_out_of_the_blue(store, assets, sends):  # noqa: F811
    _reel(store, "STALE1")
    sender = _customer()
    _share(sender, "STALE1")
    with store.get_connection() as conn:
        conn.execute("UPDATE instagram_share_replies SET due_at = due_at - 600")
        conn.commit()
    app_module._send_due_share_replies()
    assert sends == [] and _status(store, sender) == ["STALE"]


def test_an_unmapped_post_shared_alone_schedules_nothing(store, assets, sends):  # noqa: F811
    sender = _customer()
    with patch("camellia.app._handoff_unmapped_shared_media"):
        _share(sender, "NOMAP1")
    _wait_over(store)
    assert sends == [] and _status(store, sender) == []


def test_a_customers_waits_are_erased_with_their_data(store, assets):  # noqa: F811
    _reel(store, "ERASE1")
    sender = _customer()
    _share(sender, "ERASE1")
    store.delete_user_data(sender)
    assert _status(store, sender) == []
