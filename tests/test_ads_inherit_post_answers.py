"""Ads answer like the exact Post/Reel they promote.

The client mapped ads one by one, every renewal created a new Ad ID to map
again, and an ad mapped to a Story or to a note-only post handed price
questions to the manager. Now an unbound ad inherits its post's mapping, the
Ads tab says what a price question would get, and /health counts the gaps.
"""
from fastapi.testclient import TestClient

from camellia import app as app_module, instagram_ads as ads, instagram_routing
from camellia.instagram_routing import route_for_inbound

from test_instagram_ads_inventory import item, route, store, sync  # noqa: F401

AD = item()["id"]
HEADERS = {"x-admin-key": "ads-admin-fixture"}


def _live(store, monkeypatch):
    monkeypatch.setattr("camellia.database.db", store)
    sync(store, monkeypatch)


def _approved_post(store):
    return route(store, response_mode="APPROVED_QA", approved_qa_id="sondesh_price_client")


def test_unbound_ad_inherits_its_posts_mapping(store, monkeypatch):
    saved = _approved_post(store)
    _live(store, monkeypatch)
    found, method = route_for_inbound(ad_id=AD, media_id=None)
    assert method == "ad_creative" and found["routing_id"] == saved["routing_id"]
    row = ads.ads_workspace(store, "all")["items"][0]
    assert row["answers_from"] == "post" and row["answer_status"] == "APPROVED"


def test_disabled_ad_inherits_nothing(store, monkeypatch):
    saved = _approved_post(store)
    _live(store, monkeypatch)
    store.bind_instagram_ad(AD, saved["routing_id"])
    store.set_instagram_ad_binding_active(AD, False)
    assert route_for_inbound(ad_id=AD, media_id=None) == (None, None)
    assert ads.ads_workspace(store, "all")["items"][0]["answer_status"] == "DISABLED"


def test_binding_to_a_removed_mapping_falls_back_to_the_post(store, monkeypatch):
    post = _approved_post(store)
    other = route(store, code="OtherPost", media_id="180009")
    _live(store, monkeypatch)
    store.bind_instagram_ad(AD, other["routing_id"])
    store.archive_instagram_source_route(other["routing_id"])
    found, method = route_for_inbound(ad_id=AD, media_id=None)
    assert method == "ad_creative" and found["routing_id"] == post["routing_id"]


def test_a_story_is_never_inherited(store, monkeypatch):
    store.save_instagram_source_route({"source_type": "story", "media_id": "180001", "routing_topic": "Sondesh",
                                       "source_description": "Note", "active": True,
                                       "priority_products": [], "knowledge_scope": []})
    _live(store, monkeypatch)
    assert route_for_inbound(ad_id=AD, media_id=None) == (None, None)
    assert ads.ads_workspace(store, "all")["items"][0]["answer_status"] == "NONE"


def test_price_status_follows_the_live_matcher(store, monkeypatch):
    note_only = route(store)
    _live(store, monkeypatch)
    assert ads.price_answer_status(store, None) == "NONE"
    assert ads.price_answer_status(store, instagram_routing.decode_route(note_only)) == "NOTE"
    topic = instagram_routing.create_topic("Sondesh Delivery")
    instagram_routing.update_topic(topic["topic_id"], default_approved_qa_ids=["sondesh_price_client"])
    with_topic = {**instagram_routing.decode_route(note_only), "topic_id": topic["topic_id"], "routing_id": -1}
    assert ads.price_answer_status(store, with_topic) == "APPROVED"


def test_gaps_count_only_recently_messaged_ads_and_reach_health(store, monkeypatch):
    route(store)  # The ad's post has only a note.
    _live(store, monkeypatch)
    assert ads.ad_answer_gaps(store) == 0  # Nobody has messaged from the ad.
    store.log_conversation("customer", "instagram", "How much does it cost?", "note", True, "source_description", ad_id=AD)
    assert ads.ad_answer_gaps(store) == 1
    assert ads.ads_workspace(store, "all")["counts"]["answer_gaps"] == 1
    warnings = TestClient(app_module.app).get("/health").json()["warnings"]
    assert any("1 ad(s) customers messaged from this week" in w for w in warnings)


def test_needs_answers_view_lists_the_gaps_without_ad_ids(store, monkeypatch):
    # The client cannot look ads up by Meta ID; the warning needs its own list.
    note_only = route(store)
    _live(store, monkeypatch)
    assert ads.ads_workspace(store, "needs_answers")["items"] == []
    store.log_conversation("customer", "instagram", "How much does it cost?", "note", True, "source_description", ad_id=AD)
    listed = TestClient(app_module.app).get("/admin/instagram-ads?view=needs_answers", headers=HEADERS).json()["items"]
    assert [row["ad_id"] for row in listed] == [AD]
    # Approved answers on the post clear it.
    store.save_instagram_source_route({**instagram_routing.decode_route(note_only), "response_mode": "APPROVED_QA",
                                       "approved_qa_id": "sondesh_price_client"}, note_only["routing_id"])
    assert ads.ads_workspace(store, "needs_answers")["items"] == []


def test_observed_unknown_ad_is_not_an_active_answer_or_routing_gap(store, monkeypatch):
    # An observed webhook ID has no Meta status; do not count it as a currently
    # active ad needing price answers until inventory confirms it is active.
    store.observe_instagram_ad(AD, "completed ad")
    store.log_conversation("customer", "instagram", "How much does it cost?", "handoff", False,
                           "unmapped_ad_referral_handoff", ad_id=AD)
    workspace = ads.ads_workspace(store, "all")
    assert workspace["counts"]["answer_gaps"] == 0
    assert workspace["counts"]["unmapped_messaged"] == 0
    assert ads.ads_workspace(store, "needs_answers")["items"] == []

    # Once inventory confirms the same ad is active, it should reappear in both
    # active-ad warning counters until its mapping/answers are fixed.
    sync(store, monkeypatch, [item(ad_id=AD, effective_instagram_media_id=None,
                                  instagram_permalink_url=None)])
    active_workspace = ads.ads_workspace(store, "all")
    assert active_workspace["counts"]["answer_gaps"] == 1
    assert active_workspace["counts"]["unmapped_messaged"] == 1


def test_an_ad_using_its_posts_answers_counts_as_mapped(store, monkeypatch):
    # 18 Oct: the client mapped the post four times and the card kept saying
    # "Not mapped", because only direct ad mappings counted.
    _approved_post(store)
    _live(store, monkeypatch)
    result = ads.ads_workspace(store, "all")
    assert result["counts"]["mapped"] == 1 and result["counts"]["unmapped"] == 0


def test_card_says_it_uses_the_posts_answers(tmp_path):
    import shutil, subprocess
    import pytest
    from camellia.admin_ads import SCRIPT
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    script = r'''
const assert = require('assert'); const nodes = {};
global.adminAuthenticated = true; global.getAuthHeaders = () => ({}); global.escapeHtml = v => String(v || '');
global.document = {querySelectorAll:()=>[],getElementById:id=>nodes[id] ||= {innerHTML:'',textContent:'',value:'',style:{},querySelectorAll:()=>[],close:()=>{},showModal:()=>{}}};
''' + SCRIPT.replace("<script>", "").replace("</script>", "") + r'''
const post = {routing_id:125,source_type:'post',active:true,display_label:'Kolkata Sondesh Delivery'};
adsWorkspace = {items:[{ad_id:'1',name:'MISHTI',mapped:false,history:[],answers_from:'post',effective_mapping:post,answer_status:'APPROVED'}],routes:[],counts:{},sync:{}};
adsFilter = 'all'; renderAdsWorkspace();
assert(nodes.adsList.innerHTML.includes("✅ Uses its Post/Reel's answers: <b>Kolkata Sondesh Delivery</b>"));
assert(!nodes.adsList.innerHTML.includes('Not mapped'));
'''
    target = tmp_path / "uses-post.js"
    target.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_custom_editor_offers_price_answers_without_manage_topics():
    # 18 Oct: setting answers via Manage Topics was too many steps; the
    # Posts & Reels editor (which Map Ad opens) now carries the checklist,
    # hidden for Approved Q&A, and saves it to the Topic with the mapping.
    from camellia.admin_source_response import SCRIPT as RESPONSE_SCRIPT
    page = TestClient(app_module.app).get("/admin").text
    assert 'id="igMediaTopicAnswersField"' in page and "function renderMediaTopicAnswers" in page
    assert "await saveMediaTopicAnswers();" in page
    assert "'igMediaTopicAnswers'" in RESPONSE_SCRIPT


def test_media_item_endpoint_returns_the_editor_mapping(store, monkeypatch):
    saved = route(store)
    _live(store, monkeypatch)
    media = store.upsert_instagram_media({"instagram_media_id": "180001", "permalink": "https://www.instagram.com/p/ExactSondesh/",
                                          "shortcode": "ExactSondesh", "media_type": "IMAGE"})
    row = ads.ads_workspace(store, "all")["items"][0]
    assert row["answer_media_item_id"] == media["id"]
    body = TestClient(app_module.app).get(f"/admin/instagram-media/{media['id']}", headers=HEADERS).json()
    assert body["item"]["mapping_route"]["routing_id"] == saved["routing_id"]


OLD_TWIN, NEW_TWIN = "120246579208900540", "120246622870690540"


def _twins(store, monkeypatch):
    # The promoted post itself is unmapped; the older twin is mapped to a Reel.
    monkeypatch.setattr("camellia.database.db", store)
    sync(store, monkeypatch, [item(OLD_TWIN), item(NEW_TWIN)])
    return route(store, code="TwinReel", media_id="190009", response_mode="APPROVED_QA",
                 approved_qa_id="sondesh_price_client", source_type="reel",
                 canonical_url="https://www.instagram.com/reel/TwinReel/")


def test_renewed_ad_uses_its_mapped_twins_mapping(store, monkeypatch):
    # 18 Oct: a renewed YOU HOST ad reached the manager while its twin was mapped.
    reel = _twins(store, monkeypatch)
    store.bind_instagram_ad(OLD_TWIN, reel["routing_id"])
    found, method = route_for_inbound(ad_id=NEW_TWIN, media_id=None)
    assert method == "ad_creative" and found["routing_id"] == reel["routing_id"]
    row = next(r for r in ads.ads_workspace(store, "all")["items"] if r["ad_id"] == NEW_TWIN)
    assert row["answers_from"] == "post" and row["answer_status"] == "APPROVED"


def test_twins_on_different_mappings_settle_nothing(store, monkeypatch):
    reel = _twins(store, monkeypatch)
    other = route(store, code="OtherReel", media_id="190010", source_type="reel",
                  canonical_url="https://www.instagram.com/reel/OtherReel/")
    third = "120246622870690999"
    sync(store, monkeypatch, [item(OLD_TWIN), item(NEW_TWIN), item(third)])
    store.bind_instagram_ad(OLD_TWIN, reel["routing_id"])
    store.bind_instagram_ad(third, other["routing_id"])
    assert route_for_inbound(ad_id=NEW_TWIN, media_id=None) == (None, None)


def test_group_map_ad_opens_the_ad_that_needs_answers(tmp_path):
    import shutil, subprocess
    import pytest
    from camellia.admin_ads import SCRIPT
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    script = r'''
const assert = require('assert'); const nodes = {};
global.adminAuthenticated = true; global.getAuthHeaders = () => ({}); global.escapeHtml = v => String(v || '');
global.document = {querySelectorAll:()=>[],getElementById:id=>nodes[id] ||= {innerHTML:'',textContent:'',value:'',style:{},querySelectorAll:()=>[],close:()=>{},showModal:()=>{}}};
''' + SCRIPT.replace("<script>", "").replace("</script>", "") + r'''
const mapped = {routing_id:98,source_type:'reel',active:true,display_label:'Savoury'};
adsWorkspace = {items:[{ad_id:'1',name:'YOU HOST',mapped:true,mapping:mapped,binding_active:true,history:[],answer_status:'APPROVED'},
                       {ad_id:'2',name:'YOU HOST',mapped:false,history:[],answer_status:'NONE'}],
                routes:[],counts:{},sync:{},running_groups:[{member_ad_ids:['1','2'],delivering_ad_ids:['1','2']}]};
adsFilter = 'running'; renderAdsWorkspace();
assert(nodes.adsList.innerHTML.includes('data-ads-group-map="2"'));
// An ad Meta gives no post link for: Map Ad opens its own (ad-only) mapping, and
// it can still be attached to an existing Post/Reel mapping (22 Oct).
adsWorkspace.items = [{ad_id:'3',name:'YOU HOST _Group_1',mapped:false,history:[],answer_status:'NONE'}];
adsFilter = 'all'; renderAdsWorkspace();
assert(nodes.adsList.innerHTML.includes('data-ads-action="map"') && nodes.adsList.innerHTML.includes('Use an existing Post/Reel'));
'''
    target = tmp_path / "group-map.js"
    target.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
