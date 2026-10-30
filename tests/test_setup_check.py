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
    assert problem["count"] == 1 and "MenuPromise" in problem["items"][0]["text"]
    assert problem["items"][0]["routing_id"]


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
    assert problem["count"] == 1 and "(expired)" in problem["items"][0]["text"]
    assert problem["items"][0]["source_type"] == "post"


def test_endpoint_and_dashboard_card(store, monkeypatch):
    from camellia.config import config
    monkeypatch.setattr(config, "ADMIN_API_KEY", "setup-check-admin")
    response = TestClient(app_module.app).get("/admin/setup-check", headers={"x-admin-key": "setup-check-admin"})
    assert response.status_code == 200 and isinstance(response.json()["problems"], list)
    assert TestClient(app_module.app).get("/admin/setup-check").status_code in (401, 403)
    source = open(app_module.__file__, encoding="utf-8").read()
    assert 'id="dashSetupCheck"' in source and "loadSetupCheck();" in source
    assert "data-setup-route" in source and "openSetupRoute" in source


def test_dashboard_escalations_include_exact_mapped_source_action(store, monkeypatch):
    from camellia.config import config
    mapped = route(store, code="EscalatedQuestion", media_id="188001",
                   preview_url="https://cdn.example.test/escalated.jpg")
    store.bind_instagram_ad("120000000000001", mapped["routing_id"], "test")
    store.record_escalation("1907508513949156", "instagram", "What is the price per box?", "No exact approved answer",
                            {"ad_id": "120000000000001", "customer_username": "camellia_customer"})
    monkeypatch.setattr(config, "ADMIN_API_KEY", "setup-check-admin")
    response = TestClient(app_module.app).get("/admin/escalations?status=pending", headers={"x-admin-key": "setup-check-admin"})
    assert response.status_code == 200
    escalation = response.json()["escalations"][0]
    assert escalation["mapping_routing_id"] == mapped["routing_id"]
    assert escalation["ad_id"] == "120000000000001"
    assert escalation["customer_username"] == "camellia_customer"
    assert escalation["source_preview_url"] == "https://cdn.example.test/escalated.jpg"
    source = open(app_module.__file__, encoding="utf-8").read()
    assert 'id="dashEscalations"' in source and 'id="candidateEscalations"' in source
    assert "renderEscalationReview" in source and "openEscalationMapping" in source
    assert "data-escalation-ignore" in source and "status:'ignored'" in source
    assert "Escalated customer questions (${escalations.length})" in source
    escalation_renderer = source[source.index("function renderEscalationReview("):source.index("async function ignoreEscalation(")]
    assert "data-escalation-list" in escalation_renderer and "listWasOpen" in escalation_renderer
    assert "return `<details style=" not in escalation_renderer
    assert "const mappingLabel = e.mapping_routing_id ? 'Edit mapping' : e.ad_id ? 'Map Ad'" in source
    assert "data-escalation-map=\"${Number(e.id)}\"" in source
    assert "#setupRoutePreviewOverlay.active { display:flex; }" in source
    assert "Number(item.routing_id)===Number(id)" in source
    ignore_function = source[source.index("async function ignoreEscalation("):source.index("async function loadEscalationReview(")]
    assert "confirm(" not in ignore_function and "status:'ignored'" in ignore_function
    assert 'id="setupRoutePreviewOverlay"' in source and "continueSetupRouteEdit" in source
    assert "Review question</button>" not in source


def test_dashboard_ignore_removes_escalation_from_pending_list(store, monkeypatch):
    from camellia.config import config
    escalation_id = store.record_escalation("1907508513949156", "instagram", "Ignore this question", "test", {})
    monkeypatch.setattr(config, "ADMIN_API_KEY", "setup-check-admin")
    client = TestClient(app_module.app)
    response = client.post(f"/admin/escalations/{escalation_id}/resolve", json={"status": "ignored"}, headers={"x-admin-key": "setup-check-admin"})
    assert response.status_code == 200 and response.json()["status"] == "ignored"
    pending = client.get("/admin/escalations?status=pending", headers={"x-admin-key": "setup-check-admin"})
    assert all(item["user_message"] != "Ignore this question" for item in pending.json()["escalations"])


def test_dashboard_menu_box_uses_the_current_weekly_menu_not_a_dish_count():
    # 24 Oct: image-only weekly menus have no dish list, so the box always said
    # "No active menu" even with this week's menu published.
    source = open(app_module.__file__, encoding="utf-8").read()
    start = source.index("async function loadDashboardOverview()")
    body = source[start:start + 4000]
    assert "/admin/weekly-savoury" in body and "dishes > 0" not in body
