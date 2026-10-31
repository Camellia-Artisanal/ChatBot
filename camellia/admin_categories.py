"""One Admin page for the category lists. Topics are the master categories: each
can link one Menu category, one Q&A category and, by choice, one Customer message
topic. Each list keeps its own table and rules (see MEMORY.md), and nothing here
changes a customer reply: links only record which lists belong together."""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from camellia import approved_qa, menu_catalogue
from camellia.admin_menu_review import reviewer
from camellia.instagram_routing import InstagramTopicError, create_topic, decode_topic, update_topic

router = APIRouter(prefix="/admin/categories")


def _database():
    from camellia import database
    return database.db


def _topics(conn):
    usage = {}
    for row in conn.execute("SELECT topic_id, source_type, COUNT(*) AS n FROM instagram_source_routes "
                            "WHERE topic_id IS NOT NULL GROUP BY topic_id, source_type"):
        usage.setdefault(row["topic_id"], {})[row["source_type"]] = row["n"]
    gallery = {row["topic_id"]: row["n"] for row in conn.execute(
        "SELECT topic_id, COUNT(*) AS n FROM instagram_media WHERE topic_id IS NOT NULL GROUP BY topic_id")}
    return [{"topic_id": topic["topic_id"], "name": topic["name"], "active": bool(topic["active"]),
             "usage": usage.get(topic["topic_id"], {}), "gallery": gallery.get(topic["topic_id"], 0),
             "menu_category_id": topic.get("menu_category_id"), "qa_context": topic.get("qa_context"),
             "customer_topic_id": topic.get("customer_topic_id"), "suggested_name": _tidy_name(topic["name"]),
             **_copyable_defaults(topic)}
            for topic in map(decode_topic, _database().list_instagram_topics())]


def _copyable_defaults(topic):
    menu = _database().get_menu_catalogue_entry(topic["default_menu_id"]) if topic.get("default_menu_id") else None
    answers = [approved_qa.get(key) for key in topic.get("default_approved_qa_ids") or []]
    return {"default_menu": {"menu_id": menu["menu_id"], "name": menu["name"]} if menu else None,
            "default_answers": [{"qa_id": entry.qa_id, "question": entry.question} for entry in answers if entry]}


def _tidy_name(name):
    """Capitals only: the wording stays as staff wrote it, and NCR stays NCR."""
    return " ".join(word[:1].upper() + word[1:] if word.islower() else word for word in name.split())


def _words(text):
    return set(re.findall(r"[a-z0-9]+", str(text).casefold()))


def _best_match(name, options):
    """The option sharing most words with this name, the fuller one on a tie."""
    scored = [(len(_words(name) & _words(label)), weight, value) for value, label, weight in options]
    best = max(scored, key=lambda item: item[:2], default=(0, 0, None))
    return best[2] if best[0] else None


def _with_suggestions(topics, menus, qa_categories):
    menu_options = [(item["category_id"], item["name"], item["menus"]) for item in menus]
    qa_options = [(item["value"], item["label"], item["answers"] + item["mappings"]) for item in qa_categories]
    for topic in topics:
        topic["suggested_menu_category_id"] = _best_match(topic["name"], menu_options)
        topic["suggested_qa_context"] = _best_match(topic["name"], qa_options)
    return topics


def _menu_categories(conn):
    counts = {row["name"]: row["n"] for row in conn.execute(
        "SELECT LOWER(category) AS name, COUNT(*) AS n FROM menu_catalogue GROUP BY LOWER(category)")}
    return [{"category_id": category["category_id"], "name": category["name"],
             "protected": bool(category["is_system"]) or category["name"] == "Savoury Menu",
             "menus": counts.get(category["name"].lower(), 0)}
            for category in menu_catalogue.list_categories()]


def _qa_categories(conn):
    categories = []
    for option in approved_qa.context_options():
        if not option["legacy"]:
            answers, mappings = approved_qa.context_usage(conn, option["value"])
            categories.append({"value": option["value"], "label": option["label"],
                               "editable": bool(option.get("custom")),
                               "answers": answers, "mappings": mappings})
    return categories


@router.get("")
def categories(_actor=Depends(reviewer)):
    with _database().get_connection() as conn:
        menus, qa_categories = _menu_categories(conn), _qa_categories(conn)
        return {
            "topics": _with_suggestions(_topics(conn), menus, qa_categories),
            "menu_categories": menus,
            "menu_topics": menu_catalogue.list_customer_topics(),
            "routing_keys": [{"value": key, "label": menu_catalogue._CORE_TOPIC_LABELS[key]}
                             for key in menu_catalogue.CUSTOMER_TOPICS],
            "qa_categories": qa_categories,
        }


async def _body(request):
    try:
        value = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Invalid JSON body") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, "Expected a JSON object")
    return value


def _name_matches(left, right):
    return str(left or "").casefold() == str(right or "").casefold()


def _menu_for(name):
    """Links a Menu category with this name, or adds one."""
    found = next((item for item in menu_catalogue.list_categories() if _name_matches(item["name"], name)), None)
    return found or menu_catalogue.create_category(name)


def _qa_for(name, actor):
    """Links a Q&A category with this name, or adds one."""
    found = next((item for item in approved_qa.context_options()
                  if not item["legacy"] and _name_matches(item["label"], name)), None)
    return found["value"] if found else approved_qa.save_context(name, actor)["value"]


@router.post("/master")
async def add_master(request: Request, actor=Depends(reviewer)):
    body = await _body(request)
    name = " ".join(str(body.get("name") or "").split())
    if not name:
        raise HTTPException(400, "Enter a name first.")
    database = _database()
    if database.find_instagram_topic_by_name(name):
        raise HTTPException(409, f"A master category named '{name}' already exists.")
    try:
        qa_context = _qa_for(name, actor)
        menu = _menu_for(name)
        topic = create_topic(name)
        database.set_instagram_topic_links(topic["topic_id"], menu_category_id=menu["category_id"], qa_context=qa_context)
        copied = {key: body[key] for key in ("default_menu_id", "default_approved_qa_ids") if body.get(key)}
        if copied:
            update_topic(topic["topic_id"], **copied)
    except (ValueError, menu_catalogue.MenuCatalogueError, InstagramTopicError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"topic_id": topic["topic_id"], "name": name}


@router.put("/master/{topic_id}/links")
async def save_master_links(topic_id: str, request: Request, _actor=Depends(reviewer)):
    body = await _body(request)
    database = _database()
    if not database.get_instagram_topic(topic_id):
        raise HTTPException(404, "Master category not found.")
    menu_category_id, qa_context = body.get("menu_category_id") or None, body.get("qa_context") or None
    customer_topic_id = body.get("customer_topic_id") or None
    if menu_category_id is not None and menu_category_id not in {item["category_id"] for item in menu_catalogue.list_categories()}:
        raise HTTPException(400, "Choose a Menu category from the list.")
    if qa_context is not None and qa_context not in {item["value"] for item in approved_qa.context_options() if not item["legacy"]}:
        raise HTTPException(400, "Choose a Q&A category from the list.")
    if customer_topic_id is not None and customer_topic_id not in {item["topic_id"] for item in menu_catalogue.list_customer_topics()}:
        raise HTTPException(400, "Choose a Customer message topic from the list.")
    database.set_instagram_topic_links(topic_id, menu_category_id=menu_category_id, qa_context=qa_context,
                                       customer_topic_id=customer_topic_id)
    return {"status": "success"}


def _rename_link(rename, label, kept):
    try:
        rename()
    except (ValueError, KeyError, menu_catalogue.MenuCatalogueError):
        kept.append(label)


@router.patch("/master/{topic_id}")
async def rename_master(topic_id: str, request: Request, actor=Depends(reviewer)):
    """A linked list follows only if it had the same name: "Kolkata Sondesh" keeps its city."""
    name = " ".join(str((await _body(request)).get("name") or "").split())
    if not name:
        raise HTTPException(400, "Enter a name first.")
    database = _database()
    topic = database.get_instagram_topic(topic_id)
    if not topic:
        raise HTTPException(404, "Master category not found.")
    try:
        update_topic(topic_id, name=name)
    except InstagramTopicError as exc:
        raise HTTPException(409, str(exc)) from exc
    kept = []
    menu = database.get_menu_category(topic["menu_category_id"]) if topic.get("menu_category_id") else None
    if menu:
        if _name_matches(menu["name"], topic["name"]) and not menu["is_system"]:
            _rename_link(lambda: menu_catalogue.update_category(menu["category_id"], name), menu["name"], kept)
        else:
            kept.append(menu["name"])
    qa = next((item for item in approved_qa.context_options() if item["value"] == topic.get("qa_context")), None)
    if qa:
        if _name_matches(qa["label"], topic["name"]) and qa.get("custom"):
            _rename_link(lambda: approved_qa.save_context(name, actor, qa["value"]), qa["label"], kept)
        else:
            kept.append(qa["label"])
    return {"status": "success", "name": name, "kept_names": kept}


HTML = r'''
<div id="tab-categories" class="panel-card" data-group="categories" style="display:none;">
 <div class="admin-page-heading"><div><h2>Categories</h2><p>Master categories are the one list to work from: one for each product and city. Linking only records which lists belong together, so customer replies do not change.</p></div></div>
 <div id="catMessage" role="status" class="result-box" style="display:none;"></div>
 <section class="ig-media-card" style="display:block;margin-bottom:16px;" aria-labelledby="catTopicsTitle">
  <h3 id="catTopicsTitle">Master categories</h3>
  <p class="source-map-help">Add one name and it creates or links its Menu and Q&amp;A categories. Put the city in the name, such as Sondesh Delivery Mumbai. Check the suggested links on each category, then press Save links.</p>
  <form class="source-secondary-actions" onsubmit="event.preventDefault();catAddMaster()"><input id="catAddTopics" class="field-control" style="flex:1 1 200px;width:auto;" maxlength="60" placeholder="New master category name" aria-label="New master category name"><select id="catCopyFrom" class="field-control" style="flex:1 1 200px;width:auto;" aria-label="Copy from an existing category" onchange="catRenderCopy()"></select><button class="btn-secondary" type="submit">+ Add</button></form>
  <div id="catCopyReview" style="margin-top:8px;"></div>
  <div id="catTopics" style="margin-top:12px;"></div>
 </section>
 <details id="catAdvanced">
 <summary style="cursor:pointer;font-weight:600;margin-bottom:12px;">Advanced: Menu, Customer message and Q&amp;A lists</summary>
 <section class="ig-media-card" style="display:block;margin-bottom:16px;" aria-labelledby="catMenusTitle">
  <h3 id="catMenusTitle">Menu categories</h3>
  <p class="source-map-help">How menus are filed in the Menu tab. Customers do not see these names.</p>
  <form class="source-secondary-actions" onsubmit="event.preventDefault();catAdd('menus')"><input id="catAddMenus" class="field-control" style="flex:1 1 200px;width:auto;" maxlength="60" placeholder="New menu category name" aria-label="New menu category name"><button class="btn-secondary" type="submit">+ Add category</button></form>
  <div id="catMenus" style="margin-top:12px;"></div>
 </section>
 <section class="ig-media-card" style="display:block;margin-bottom:16px;" aria-labelledby="catMenuTopicsTitle">
  <h3 id="catMenuTopicsTitle">Customer message topics</h3>
  <p class="source-map-help">Decide which menus a customer gets when they ask for a menu by topic. A menu with no topic is never sent on request.</p>
  <form class="source-secondary-actions" onsubmit="event.preventDefault();catAdd('menuTopics')"><input id="catAddMenuTopics" class="field-control" style="flex:1 1 200px;width:auto;" maxlength="60" placeholder="New topic name" aria-label="New customer message topic name"><select id="catRoutingKey" class="field-control" style="flex:1 1 200px;width:auto;" aria-label="Type of customer request"></select><button class="btn-secondary" type="submit">+ Add topic</button></form>
  <div id="catMenuTopics" style="margin-top:12px;"></div>
 </section>
 <section class="ig-media-card" style="display:block;" aria-labelledby="catQaTitle">
  <h3 id="catQaTitle">Approved Q&amp;A categories</h3>
  <p class="source-map-help">Keep answers apart, such as Kolkata and Gurgaon, so a mapping only gives the answers meant for it.</p>
  <form class="source-secondary-actions" onsubmit="event.preventDefault();catAdd('qa')"><input id="catAddQa" class="field-control" style="flex:1 1 200px;width:auto;" maxlength="60" placeholder="New Q&amp;A category name" aria-label="New Q&amp;A category name"><button class="btn-secondary" type="submit">+ Add category</button></form>
  <div id="catQa" style="margin-top:12px;"></div>
 </section>
 </details>
 <dialog id="catMoveDialog" aria-labelledby="catMoveTitle" style="margin:auto;padding:20px;border:1px solid var(--border);border-radius:12px;width:min(460px,calc(100vw - 32px));box-sizing:border-box;">
  <form onsubmit="event.preventDefault();catConfirmMove()">
   <h3 id="catMoveTitle">Delete Topic</h3>
   <p id="catMoveText" class="source-map-help"></p>
   <label class="field-label" for="catMoveTarget">Move them to</label>
   <select id="catMoveTarget" class="field-control" required></select>
   <div class="source-secondary-actions" style="margin-top:12px;"><button class="btn-secondary" type="submit">Move and delete</button><button class="btn-secondary" type="button" onclick="document.getElementById('catMoveDialog').close()">Cancel</button></div>
  </form>
 </dialog>
</div>
'''

SCRIPT = r'''
<script>
let catData = null, catMoving = null;
const CAT_URLS = {
 topics: '/admin/instagram-topics', menus: '/admin/menu-categories',
 menuTopics: '/admin/weekly-savoury/customer-topics', qa: '/admin/approved-qa/contexts',
};
function catNote(text, error = false) {
 const box = document.getElementById('catMessage');
 box.textContent = text; box.style.display = text ? 'block' : 'none'; box.style.color = error ? 'var(--danger)' : '';
 // The lists are long, so the Add or Delete just pressed may be far below this box.
 if (text) box.scrollIntoView({block:'center'});
}
async function catApi(url, method = 'GET', payload) {
 const response = await fetch(url, {method, headers:{...getAuthHeaders(),'Content-Type':'application/json'}, cache:'no-store', ...(payload ? {body:JSON.stringify(payload)} : {})});
 const data = await response.json().catch(() => ({}));
 if (!response.ok) throw new Error(data.detail?.message || (typeof data.detail === 'string' ? data.detail : 'Could not save the change.'));
 return data;
}
function catPlural(count, word) { return count + ' ' + word + (count === 1 ? '' : 's'); }
function catRow(name, usage, actions, inactive = false) {
 const row = document.createElement('div');
 row.className = 'source-topic-row';
 const text = document.createElement('span');
 const title = document.createElement('b'); title.textContent = name;
 const detail = document.createElement('small'); detail.className = 'ig-media-muted'; detail.style.display = 'block';
 detail.textContent = (inactive ? 'Inactive · ' : '') + usage;
 text.append(title, detail);
 const buttons = document.createElement('div'); buttons.className = 'source-secondary-actions';
 actions.forEach(([label, handler]) => {
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'btn-secondary'; button.textContent = label;
  button.setAttribute('aria-label', label + ' ' + name); button.onclick = handler;
  buttons.append(button);
 });
 row.append(text, buttons);
 return row;
}
function catTopicUsage(topic) {
 const parts = [['story','Stor'], ['post','Post'], ['reel','Reel'], ['ad','Ad']].flatMap(([key, word]) => {
  const count = topic.usage[key] || 0;
  if (!count) return [];
  return [key === 'story' ? count + (count === 1 ? ' Story' : ' Stories') : catPlural(count, word)];
 });
 return parts.length ? parts.join(', ') : 'Not used yet';
}
function catFill(id, rows) { document.getElementById(id).replaceChildren(...rows); }
function catLinkSelect(label, none, options, value) {
 const field = document.createElement('label');
 field.style.cssText = 'flex:1 1 180px;min-width:0;font-size:.85rem;';
 const select = document.createElement('select'); select.className = 'field-control';
 select.replaceChildren(new Option(none, ''), ...options.map(([key, name]) => new Option(name, key)));
 select.value = value ?? '';
 field.append(label, select);
 return [field, select];
}
function catMasterRow(topic) {
 const linked = topic.menu_category_id || topic.qa_context;
 const actions = [
  ...(topic.suggested_name !== topic.name ? [['Fix capitals: ' + topic.suggested_name, () => catRenameMaster(topic, topic.suggested_name)]] : []),
  ['Rename', () => catRenameMaster(topic)],
  [topic.active ? 'Turn off' : 'Turn on', () => catToggleMaster(topic)],
  ['Delete', () => catDeleteTopic(topic)],
 ];
 const row = catRow(topic.name, catTopicUsage(topic), actions, !topic.active);
 const wrap = document.createElement('div'); wrap.style.cssText = 'border-bottom:1px solid var(--border);padding-bottom:10px;margin-bottom:10px;';
 const links = document.createElement('div'); links.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-top:6px;';
 const [menuField, menu] = catLinkSelect('Menu category', 'Not linked',
  catData.menu_categories.map(item => [String(item.category_id), item.name + ' (' + catPlural(item.menus, 'menu') + ')']),
  String((linked ? topic.menu_category_id : topic.suggested_menu_category_id) ?? ''));
 const [qaField, qa] = catLinkSelect('Q&A category', 'Not linked',
  catData.qa_categories.map(item => [item.value, item.label + ' (' + catPlural(item.answers, 'answer') + ')']),
  (linked ? topic.qa_context : topic.suggested_qa_context) ?? '');
 const [customerField, customer] = catLinkSelect('Customer message topic (optional)', 'Not linked',
  catData.menu_topics.map(item => [item.topic_id, item.name]), topic.customer_topic_id ?? '');
 const save = document.createElement('button');
 save.type = 'button'; save.className = 'btn-secondary'; save.textContent = 'Save links';
 save.setAttribute('aria-label', 'Save links for ' + topic.name);
 save.onclick = () => catSaveLinks(topic, menu.value, qa.value, customer.value);
 links.append(menuField, qaField, customerField, save);
 wrap.append(row, links);
 if (!linked) {
  const note = document.createElement('small'); note.className = 'ig-media-muted'; note.style.display = 'block';
  note.textContent = 'Not saved yet. These are suggestions, so check them before saving.';
  wrap.append(note);
 }
 return wrap;
}
function catRenderCopy() {
 const box = document.getElementById('catCopyReview');
 const source = catData.topics.find(topic => topic.topic_id === document.getElementById('catCopyFrom').value);
 box.replaceChildren();
 if (!source) return;
 const items = [...(source.default_menu ? [['menu', source.default_menu.menu_id, 'Menu: ' + source.default_menu.name]] : []),
                ...source.default_answers.map(answer => ['answer', answer.qa_id, 'Answer: ' + answer.question])];
 const help = document.createElement('p'); help.className = 'source-map-help';
 help.textContent = items.length ? 'Untick anything that does not apply to the new city.' : source.name + ' has no default menu or answers to copy.';
 box.append(help);
 items.forEach(([kind, value, label]) => {
  const row = document.createElement('label'); row.style.cssText = 'display:flex;gap:8px;align-items:center;padding:2px 0;';
  const check = document.createElement('input'); check.type = 'checkbox'; check.checked = true; check.style.cssText = 'width:auto;flex:none;';
  check.dataset.kind = kind; check.value = value;
  row.append(check, document.createTextNode(label)); box.append(row);
 });
}
function catRender() {
 const data = catData;
 catFill('catTopics', data.topics.map(catMasterRow));
 const copyFrom = document.getElementById('catCopyFrom'), copying = copyFrom.value;
 copyFrom.replaceChildren(new Option('Start blank', ''), ...data.topics.map(topic => new Option('Copy from ' + topic.name, topic.topic_id)));
 copyFrom.value = copying;
 catFill('catMenus', data.menu_categories.map(category => catRow(category.name,
  (category.menus ? catPlural(category.menus, 'menu') : 'Not used yet') + (category.protected ? ' · Protected' : ''),
  category.protected ? [] : [['Rename', () => catRename('menus', category.category_id, category.name)],
                             ['Delete', () => catDelete('menus', category.category_id, category.name)]])));
 const keys = Object.fromEntries(data.routing_keys.map(key => [key.value, key.label]));
 catFill('catMenuTopics', data.menu_topics.map(topic => catRow(topic.name,
  (keys[topic.routing_key] || topic.routing_key) + ' · ' + (topic.menu_count ? catPlural(topic.menu_count, 'menu') : 'Not used yet') + (topic.is_system ? ' · Built-in' : ''),
  [['Rename', () => catRename('menuTopics', topic.topic_id, topic.name, {routing_key: topic.routing_key})],
   ...(topic.is_system ? [] : [['Delete', () => catDelete('menuTopics', topic.topic_id, topic.name)]])])));
 catFill('catQa', data.qa_categories.map(category => catRow(category.label,
  catPlural(category.answers, 'answer') + ', ' + catPlural(category.mappings, 'mapping') + (category.editable ? '' : ' · Built-in'),
  category.editable ? [['Rename', () => catRename('qa', category.value, category.label)],
                       ['Delete', () => catDelete('qa', category.value, category.label)]] : [])));
 const select = document.getElementById('catRoutingKey'), current = select.value;
 select.replaceChildren(new Option('What are customers asking for?', ''), ...data.routing_keys.map(key => new Option(key.label, key.value)));
 select.value = current;
}
async function loadCategories() {
 if (!adminAuthenticated) return;
 try { catData = await catApi('/admin/categories'); catRender(); }
 catch (error) { catNote(error.message || 'Could not load categories.', true); }
}
// Pickers in the mapping and menu forms read these lists, so refresh them too.
async function catChanged(note) {
 catNote(note);
 await loadCategories();
 if (typeof loadTopicsAndMenus === 'function') await loadTopicsAndMenus();
}
async function catAddMaster() {
 const input = document.getElementById('catAddTopics'), name = input.value.trim();
 if (!name) return catNote('Enter a name first.', true);
 const ticked = kind => [...document.querySelectorAll('#catCopyReview input:checked')].filter(box => box.dataset.kind === kind).map(box => box.value);
 try {
  await catApi('/admin/categories/master', 'POST', {name, default_menu_id: ticked('menu')[0] || null, default_approved_qa_ids: ticked('answer')});
  input.value = ''; document.getElementById('catCopyFrom').value = ''; catRenderCopy();
  await catChanged('Added ' + name + '.');
 } catch (error) { catNote(error.message, true); }
}
async function catSaveLinks(topic, menu, qa, customer) {
 try {
  await catApi('/admin/categories/master/' + encodeURIComponent(topic.topic_id) + '/links', 'PUT',
               {menu_category_id: menu ? Number(menu) : null, qa_context: qa || null, customer_topic_id: customer || null});
  await catChanged('Saved links for ' + topic.name + '.');
 } catch (error) { catNote(error.message, true); }
}
async function catRenameMaster(topic, suggested) {
 const name = (suggested || prompt('New name', topic.name) || '').trim();
 if (!name || name === topic.name) return;
 try {
  const result = await catApi('/admin/categories/master/' + encodeURIComponent(topic.topic_id), 'PATCH', {name});
  await catChanged('Renamed to ' + name + '.' + (result.kept_names.length ? ' Kept the name of ' + result.kept_names.join(' and ') + '.' : ''));
 } catch (error) { catNote(error.message, true); }
}
async function catToggleMaster(topic) {
 try {
  await catApi(CAT_URLS.topics + '/' + encodeURIComponent(topic.topic_id), 'PATCH', {active: !topic.active});
  await catChanged(topic.active ? topic.name + ' is hidden from new mappings. Existing mappings keep answering.' : topic.name + ' is on.');
 } catch (error) { catNote(error.message, true); }
}
const CAT_INPUTS = {menus:'catAddMenus', menuTopics:'catAddMenuTopics', qa:'catAddQa'};
async function catAdd(list) {
 const input = document.getElementById(CAT_INPUTS[list]), name = input.value.trim();
 if (!name) return catNote('Enter a name first.', true);
 const payload = list === 'qa' ? {label:name} : {name};
 if (list === 'menuTopics') {
  payload.routing_key = document.getElementById('catRoutingKey').value;
  if (!payload.routing_key) return catNote('Choose what customers are asking for.', true);
 }
 try {
  await catApi(CAT_URLS[list], 'POST', payload); input.value = '';
  if (list === 'menuTopics') document.getElementById('catRoutingKey').value = '';
  await catChanged('Added ' + name + '.');
 } catch (error) { catNote(error.message, true); }
}
async function catRename(list, id, current, extra = {}) {
 const name = (prompt('New name', current) || '').trim();
 if (!name || name === current) return;
 const payload = {...extra, ...(list === 'qa' ? {label:name} : {name})};
 try { await catApi(CAT_URLS[list] + '/' + encodeURIComponent(id), 'PATCH', payload); await catChanged('Renamed to ' + name + '.'); }
 catch (error) { catNote(error.message, true); }
}
async function catDelete(list, id, name) {
 if (!confirm('Delete ' + name + '?')) return;
 try { await catApi(CAT_URLS[list] + '/' + encodeURIComponent(id), 'DELETE'); await catChanged('Deleted ' + name + '.'); }
 catch (error) { catNote(error.message, true); }
}
async function catDeleteTopic(topic) {
 const used = Object.values(topic.usage).reduce((total, count) => total + count, 0) + topic.gallery;
 if (!used) {
  if (!confirm('Delete ' + topic.name + '?')) return;
  try { await catApi(CAT_URLS.topics + '/' + encodeURIComponent(topic.topic_id), 'DELETE', {}); await catChanged('Deleted ' + topic.name + '.'); }
  catch (error) { catNote(error.message, true); }
  return;
 }
 const others = catData.topics.filter(item => item.topic_id !== topic.topic_id && item.active);
 if (!others.length) return catNote('Add another master category first, so its content has somewhere to move.', true);
 catMoving = topic;
 const menu = catData.menu_categories.find(item => item.category_id === topic.menu_category_id);
 const qa = catData.qa_categories.find(item => item.value === topic.qa_context);
 document.getElementById('catMoveTitle').textContent = 'Delete ' + topic.name;
 document.getElementById('catMoveText').textContent = 'In use by ' + catPlural(used, 'mapping') + '. Its links cover ' + catPlural(qa?.answers || 0, 'answer') + ' and ' + catPlural(menu?.menus || 0, 'menu') + ', which stay in their own lists. Choose where to move the mappings before it is deleted.';
 document.getElementById('catMoveTarget').replaceChildren(new Option('Choose a master category', ''), ...others.map(item => new Option(item.name, item.topic_id)));
 document.getElementById('catMoveDialog').showModal();
}
async function catConfirmMove() {
 const dialog = document.getElementById('catMoveDialog');
 const replacement_topic_id = document.getElementById('catMoveTarget').value;
 try {
  await catApi(CAT_URLS.topics + '/' + encodeURIComponent(catMoving.topic_id), 'DELETE', {replacement_topic_id});
  dialog.close(); await catChanged('Deleted ' + catMoving.name + ' and moved its content.');
 } catch (error) { dialog.close(); catNote(error.message, true); }
}
</script>
'''
