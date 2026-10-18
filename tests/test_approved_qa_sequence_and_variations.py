"""Approved Q&A reply sequence and removable built-in variations (18 Oct).

The client asked for the Custom Q&A "Response Sequence / Menu & Images" editor
in Approved Q&A, and Add/Edit/Delete for built-in question variations. A
removed wording must really stop getting that answer, not just vanish from
the screen.
"""
import pytest

from camellia import approved_qa, client_qa

from test_approved_qa_contexts import store  # noqa: F401


def _publish(body):
    draft = approved_qa.preview(body)
    return approved_qa.publish({**body, "qa_id": draft["qa_id"], "preview_token": draft["preview_token"]}, "test")


def test_reply_sequence_becomes_the_answers_blocks(store, monkeypatch):
    monkeypatch.setattr("camellia.custom_response_sequence.validate",
                        lambda value: [dict(b) for b in value or []])
    record = _publish(dict(question="What is the price of the Kolkata lunch?", answer="KOLKATA PRICE",
                           context="general", aliases=[],
                           blocks=[{"type": "text", "text": "Prices below."}, {"type": "menu", "menu_id": "kolkata_menu"},
                                   {"type": "text", "text": "Payment at booking."}]))
    assert record["sequence"][1] == {"type": "menu", "menu_id": "kolkata_menu"}
    entry = approved_qa.get(record["qa_id"])
    assert [(b.kind, b.value) for b in entry.blocks] == [
        ("TEXT", "Prices below."), ("MENU", "catalogue:kolkata_menu"), ("TEXT", "Payment at booking.")]
    # Clearing the sequence restores the plain answer.
    cleared = _publish(dict(qa_id=record["qa_id"], blocks=[]))
    assert cleared["sequence"] == [] and [b.kind for b in approved_qa.get(record["qa_id"]).blocks] == ["TEXT"]


def test_removed_builtin_variation_stops_matching_and_can_be_restored(store):
    key = "home_table_payment"
    wording = "how can i pay for home table"
    assert wording in client_qa._PARAPHRASE_ALIASES[key]
    assert approved_qa.match(wording, {}).entry.qa_id == key
    record = _publish(dict(qa_id=key, hidden_aliases=[wording]))
    assert wording not in record["source_aliases"] and record["hidden_aliases"] == [wording]
    match = approved_qa.match(wording, {})
    assert match is None or match.entry.qa_id != key
    assert (client_qa._match_base(wording, {}) or type("N", (), {"entry": type("E", (), {"qa_id": None})})).entry.qa_id != key
    # Other wordings keep working, and Restore brings it back.
    assert approved_qa.match("home table payment options", {}).entry.qa_id == key
    _publish(dict(qa_id=key, hidden_aliases=[]))
    assert approved_qa.match(wording, {}).entry.qa_id == key


def test_only_builtin_wordings_can_be_removed(store):
    with pytest.raises(ValueError, match="built-in"):
        approved_qa.preview(dict(qa_id="home_table_payment", hidden_aliases=["something the bot never had"]))


def test_editor_has_sequence_and_variation_controls():
    from camellia.admin_approved_qa import SCRIPT
    for marker in ("+ Add Text Block", "+ Add Menu / Image", "data-aq-hide-variation", "data-aq-edit-variation",
                   "data-aq-restore-variation", "+ Add variation", "blocks:aqSequence,hidden_aliases:aqHidden"):
        assert marker in SCRIPT, marker
