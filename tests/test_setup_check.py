"""Setup check (23 Oct): settings that silently send customers to the manager."""
from fastapi.testclient import TestClient

from camellia import app as app_module, setup_check

from test_instagram_ads_inventory import route, store  # noqa: F401


def _kinds(database):
    return {p["kind"]: p for p in setup_check.check(database)}


def test_custom_note_promising_a_menu_without_one_is_flagged(store):
    route(store, code="MenuPromise", media_id="1", source_description="Sharing the menu below", menu_mode="NONE")
    route(store, code="MenuFine", media_id="2", source_description="Thanks for asking!", menu_mode="NONE")
    problem = _kinds(store)["menu_promised"]
    assert problem["count"] == 1 and "MenuPromise" in problem["items"][0]


def test_mapping_that_ticks_an_inactive_answer_is_flagged(store, monkeypatch):
    route(store, code="Dead", media_id="3", response_mode="APPROVED_QA", approved_qa_id="general_price",
          approved_qa_ids=["general_price"], source_context="general")
    real = setup_check.approved_qa.listing

    def expired_listing():
        data = real()
        for record in data["records"]:
            if record["qa_id"] == "general_price":
                record["status"] = "EXPIRED"
        return data

    monkeypatch.setattr(setup_check.approved_qa, "listing", expired_listing)
    problem = _kinds(store)["dead_answers"]
    assert problem["count"] == 1 and "(expired)" in problem["items"][0]


def test_endpoint_and_dashboard_card(store, monkeypatch):
    from camellia.config import config
    monkeypatch.setattr(config, "ADMIN_API_KEY", "setup-check-admin")
    response = TestClient(app_module.app).get("/admin/setup-check", headers={"x-admin-key": "setup-check-admin"})
    assert response.status_code == 200 and isinstance(response.json()["problems"], list)
    assert TestClient(app_module.app).get("/admin/setup-check").status_code in (401, 403)
    source = open(app_module.__file__, encoding="utf-8").read()
    assert 'id="dashSetupCheck"' in source and "loadSetupCheck();" in source
