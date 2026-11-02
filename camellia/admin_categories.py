"""One Admin page for the category lists. Topics are the master categories: each
can link one Menu category, one Q&A category and, by choice, one Customer message
topic. Each list keeps its own table and rules (see MEMORY.md), and nothing here
changes a customer reply: links only record which lists belong together."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request

from camellia import answer_slots, approved_qa, category_buttons, menu_catalogue
from camellia.admin_menu_review import reviewer
from camellia.instagram_routing import (
    InstagramTopicError, create_topic, decode_route, decode_topic, matches_custom_response_variation, update_topic,
)

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
             "customer_topic_id": topic.get("customer_topic_id"), "button_name": topic.get("button_name"),
             "suggested_name": _tidy_name(topic["name"]), "setup": _setup_steps(topic),
             **_copyable_defaults(topic)}
            for topic in map(decode_topic, _database().list_instagram_topics())]


def _setup_steps(topic):
    """What is done on a category, worded for staff. The page only draws this."""
    slots = answer_slots.labels(topic["topic_id"])
    answered = [label for slot, label in slots.items()
                if answer_slots.effective({"topic_id": topic["topic_id"]}, slot)]
    buttons = category_buttons.stored_buttons("topic", topic["topic_id"]) or []
    name = topic.get("button_name")
    return [
        {"key": "links", "done": bool(topic.get("menu_category_id") or topic.get("qa_context")),
         "status": "Linked" if topic.get("menu_category_id") or topic.get("qa_context") else "Not linked yet"},
        {"key": "answers", "done": bool(answered),
         "status": f"{len(answered)} of {len(slots)} set" + (": " + ", ".join(answered) if answered else "")},
        {"key": "buttons", "done": bool(buttons),
         "status": ", ".join(button["label"] for button in buttons) or "None chosen yet"},
        {"key": "name", "done": bool(name) and "Details" in answered,
         "status": ("Needs a Details answer first" if "Details" not in answered else name) if name else "No name yet"},
    ]


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
    button_name = " ".join(str(body.get("button_name") or "").split()) or None
    if button_name and len(button_name) > category_buttons.NAME_LIMIT:
        raise HTTPException(400, f"Keep the button name to {category_buttons.NAME_LIMIT} characters.")
    database.set_instagram_topic_links(topic_id, menu_category_id=menu_category_id, qa_context=qa_context,
                                       customer_topic_id=customer_topic_id, button_name=button_name)
    return {"status": "success"}


@router.get("/question-buttons/{owner_type}/{owner_id}")
def question_buttons(owner_type: str, owner_id: str, _actor=Depends(reviewer)):
    _slot_owner(owner_type)
    return {"buttons": category_buttons.stored_buttons(owner_type, owner_id),
            "choices": [{"slot": slot, "label": label} for slot, (label, _question) in category_buttons.QUESTIONS.items()]}


@router.put("/question-buttons/{owner_type}/{owner_id}")
async def save_question_buttons(owner_type: str, owner_id: str, request: Request, _actor=Depends(reviewer)):
    _slot_owner(owner_type)
    try:
        category_buttons.save_buttons(owner_type, owner_id, (await _body(request)).get("buttons"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "success"}


def _slot_owner(owner_type):
    if owner_type not in answer_slots.OWNERS:
        raise HTTPException(400, "Unknown answer owner.")


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


@router.get("/slots/{owner_type}/{owner_id}")
def slots(owner_type: str, owner_id: str, _actor=Depends(reviewer)):
    _slot_owner(owner_type)
    return {"slots": answer_slots.list_slots(owner_type, owner_id)}


@router.put("/slots/{owner_type}/{owner_id}/{slot}")
async def save_slot(owner_type: str, owner_id: str, slot: str, request: Request, _actor=Depends(reviewer)):
    _slot_owner(owner_type)
    try:
        answer_slots.save(owner_type, owner_id, slot, await _body(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "success"}


@router.delete("/slots/{owner_type}/{owner_id}/{slot}")
def clear_slot(owner_type: str, owner_id: str, slot: str, _actor=Depends(reviewer)):
    _slot_owner(owner_type)
    answer_slots.clear(owner_type, owner_id, slot)
    return {"status": "success"}


@router.post("/slots/{owner_type}/{owner_id}/{slot}/save-to-approved")
def save_slot_to_approved(owner_type: str, owner_id: str, slot: str, actor=Depends(reviewer)):
    _slot_owner(owner_type)
    try:
        return {"qa_id": answer_slots.save_to_approved(owner_type, owner_id, slot, actor)}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/needs-answers")
def needs_answers(_actor=Depends(reviewer)):
    return {"groups": answer_slots.needs_answers()}


@router.post("/needs-answers/dismiss")
async def dismiss_gap(request: Request, _actor=Depends(reviewer)):
    body = await _body(request)
    if not body.get("topic_id") or not body.get("slot"):
        raise HTTPException(400, "Choose a question to set aside.")
    answer_slots.dismiss_gap(body["topic_id"], body["slot"])
    return {"status": "success"}


@router.post("/own-questions/{topic_id}")
async def add_own_question(topic_id: str, request: Request, _actor=Depends(reviewer)):
    if not _database().get_instagram_topic(topic_id):
        raise HTTPException(404, "Master category not found.")
    try:
        return {"slot": answer_slots.add_own_question(topic_id, (await _body(request)).get("label"))}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/own-questions/{topic_id}/{slot}")
def remove_own_question(topic_id: str, slot: str, _actor=Depends(reviewer)):
    answer_slots.remove_own_question(topic_id, slot)
    return {"status": "success"}


@router.post("/needs-answers/phrase")
async def add_phrase(request: Request, _actor=Depends(reviewer)):
    """Adds a real customer question to its mapping's exact phrases."""
    body = await _body(request)
    phrase = " ".join(str(body.get("phrase") or "").split())
    database = _database()
    routing_id = body.get("routing_id")
    row = database.get_instagram_source_route(routing_id) if isinstance(routing_id, int) else None
    if not row:
        raise HTTPException(404, "That mapping no longer exists.")
    route = decode_route(row)
    phrases = list(route.get("custom_response_variations") or [])
    if route.get("response_mode", "CUSTOM") != "CUSTOM" or not str(route.get("source_description") or "").strip():
        raise HTTPException(409, "Only a mapping with a Custom response can use phrases.")
    if not 1 <= len(phrase) <= 180 or len(phrases) >= 30:
        raise HTTPException(409, "A phrase is 1 to 180 characters, and a mapping holds up to 30.")
    if not matches_custom_response_variation(route, phrase):
        database.save_instagram_source_route({**route, "custom_response_variations": [*phrases, phrase]}, route["routing_id"])
    return {"status": "success"}


HTML = r'''
<div id="tab-categories" class="panel-card" data-group="categories" style="display:none;">
 <div class="admin-page-heading"><div><h2>Categories</h2><p>Keep one category for each product and city. Open a category and finish each tab. A dot means that tab still needs something.</p></div></div>
 <div id="catMessage" role="status" class="result-box" style="display:none;"></div>
 <section class="ig-media-card" style="display:block;margin-bottom:16px;" aria-labelledby="catNeedsTitle">
  <h3 id="catNeedsTitle">Customers asked, but no answer is set</h3>
  <p class="source-map-help">From the last 14 days. Press Set answer and the gap clears. Customers already waiting stay with staff.</p>
  <div id="catNeeds"></div>
 </section>
 <section class="ig-media-card" style="display:block;margin-bottom:16px;" aria-labelledby="catTopicsTitle">
  <h3 id="catTopicsTitle">Master categories</h3>
  <div id="catTopics" style="margin:12px 0 16px;"></div>
  <b>Add a category</b>
  <p class="source-map-help">Put the product and city in the name, such as Sondesh Delivery Mumbai.</p>
  <form class="source-secondary-actions" onsubmit="event.preventDefault();catAddMaster()"><input id="catAddTopics" class="field-control" style="flex:1 1 200px;width:auto;" maxlength="60" placeholder="Category name" aria-label="New master category name"><select id="catCopyFrom" class="field-control" style="flex:1 1 200px;width:auto;" aria-label="Copy from an existing category" onchange="catRenderCopy()"></select><button class="btn-secondary" type="submit">+ Add</button></form>
  <div id="catCopyReview" style="margin-top:8px;"></div>
 </section>
 <details id="catAdvanced">
 <summary style="cursor:pointer;font-weight:600;margin-bottom:12px;">Advanced: the separate Menu, Customer message and Q&amp;A lists (most days you will not need these)</summary>
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
 <div id="catPanelBackdrop" hidden style="position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:5000;" onclick="catClosePanel()"></div>
 <aside id="catPanel" hidden role="dialog" aria-labelledby="catPanelTitle" style="position:fixed;top:0;right:0;bottom:0;width:min(600px,100vw);box-sizing:border-box;overflow:auto;padding:18px;background:var(--surface);z-index:5001;box-shadow:-8px 0 24px rgba(0,0,0,.18);"></aside>
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
let catPanelTopic = null, catPanelTab = null;
// Tab order: most-used first. Each key matches a step from _setup_steps.
const CAT_TABS = [
 ['answers', 'Answers', 'One answer for each kind of question customers ask. Add your own question at the end of the list.'],
 ['buttons', 'Buttons', 'Shown under the reply. Only questions with an answer appear.'],
 ['name', 'Button name', 'Shown when the bot asks which product a customer means.'],
 ['links', 'Links', 'Pick the lists this category uses.'],
];
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
function catStep(topic, key) {
 const step = document.createElement('section'); step.dataset.pane = key;
 const mark = document.createElement('span'); mark.dataset.step = topic.topic_id + ':' + key; mark.style.cssText = 'display:block;font-size:.85rem;margin-top:2px;';
 const help = document.createElement('p'); help.className = 'source-map-help'; help.textContent = CAT_TABS.find(tab => tab[0] === key)[2];
 step.append(mark, help);
 return step;
}
function catPaintSteps(topic) {
 for (const step of topic.setup) {
  const mark = document.querySelector('[data-step="' + topic.topic_id + ':' + step.key + '"]');
  if (mark) { mark.textContent = (step.done ? '✓ ' : '○ ') + step.status; mark.style.color = step.done ? 'var(--success, #2e7d32)' : 'var(--text-muted)'; }
 }
 const done = topic.setup.filter(step => step.done).length;
 document.querySelectorAll('[data-progress="' + topic.topic_id + '"]').forEach(progress => { progress.textContent = done === topic.setup.length ? '✓ Ready' : done + ' of ' + topic.setup.length + ' tabs done'; });
 if (topic.topic_id === catPanelTopic) for (const step of topic.setup) { const dot = document.querySelector('[data-tab-dot="' + step.key + '"]'); if (dot) dot.hidden = step.done; }
}
// Saving inside a card must not redraw it, or a half-typed answer would be lost.
async function catStepsChanged() {
 try { catData = await catApi('/admin/categories'); catData.topics.forEach(catPaintSteps); } catch (_error) { /* The marks catch up on the next load. */ }
}
function catLinksStep(topic) {
 const linked = topic.menu_category_id || topic.qa_context;
 const step = catStep(topic, 'links');
 const [qaField, qa] = catLinkSelect('Answers', 'Not linked',
  catData.qa_categories.map(item => [item.value, item.label + ' (' + catPlural(item.answers, 'answer') + ')']),
  (linked ? topic.qa_context : topic.suggested_qa_context) ?? '');
 const [menuField, menu] = catLinkSelect('Menus', 'Not linked',
  catData.menu_categories.map(item => [String(item.category_id), item.name + ' (' + catPlural(item.menus, 'menu') + ')']),
  String((linked ? topic.menu_category_id : topic.suggested_menu_category_id) ?? ''));
 const fields = document.createElement('div'); fields.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;';
 const [customerField, customer] = catLinkSelect('Customer message topic (optional)', 'Not linked',
  catData.menu_topics.map(item => [item.topic_id, item.name]), topic.customer_topic_id ?? '');
 fields.append(qaField, menuField, customerField);
 const save = document.createElement('button'); save.type = 'button'; save.className = 'btn-secondary'; save.style.marginTop = '8px'; save.textContent = 'Save';
 save.setAttribute('aria-label', 'Save the lists for ' + topic.name);
 save.onclick = () => catSaveLinks(topic, {menu_category_id: menu.value ? Number(menu.value) : null, qa_context: qa.value || null, customer_topic_id: customer.value || null});
 step.append(fields, save);
 if (!linked) { const note = document.createElement('small'); note.className = 'ig-media-muted'; note.style.display = 'block'; note.textContent = 'These are suggestions. Check them, then press Save.'; step.append(note); }
 return step;
}
function catNameStep(topic) {
 const step = catStep(topic, 'name');
 const input = document.createElement('input'); input.className = 'field-control'; input.maxLength = 20; input.value = topic.button_name || '';
 input.setAttribute('aria-label', 'Button name for ' + topic.name); input.placeholder = 'Up to 20 letters, such as Sondesh';
 const chat = document.createElement('p'); chat.style.cssText = 'margin:8px 0;font-size:.85rem;';
 const chip = document.createElement('span'); chip.style.cssText = 'display:inline-block;margin-top:4px;padding:4px 12px;border:1px solid var(--border);border-radius:999px;';
 const draw = () => { chip.textContent = input.value.trim() || 'Your name here'; };
 input.oninput = draw; draw();
 chat.append('Customers will see: ', chip);
 const save = document.createElement('button'); save.type = 'button'; save.className = 'btn-secondary'; save.textContent = 'Save';
 save.setAttribute('aria-label', 'Save the button name for ' + topic.name);
 save.onclick = () => catSaveLinks(topic, {button_name: input.value || null});
 step.append(input, chat, save);
 return step;
}
function catPanelMenu(topic) {
 const menu = document.createElement('details'); menu.style.cssText = 'position:relative;flex:none;';
 const summary = document.createElement('summary'); summary.textContent = '⋯'; summary.setAttribute('aria-label', 'More actions for ' + topic.name); summary.style.cssText = 'cursor:pointer;list-style:none;font-size:1.4rem;padding:0 10px;';
 const actions = document.createElement('div'); actions.style.cssText = 'position:absolute;right:0;z-index:2;display:grid;gap:6px;padding:8px;min-width:170px;background:var(--surface);border:1px solid var(--border);border-radius:10px;';
 [...(topic.suggested_name !== topic.name ? [['Fix capitals: ' + topic.suggested_name, () => catRenameMaster(topic, topic.suggested_name)]] : []),
  ['Rename', () => catRenameMaster(topic)], [topic.active ? 'Turn off' : 'Turn on', () => catToggleMaster(topic)], ['Delete', () => catDeleteTopic(topic)]]
  .forEach(([label, handler]) => {
   const button = document.createElement('button'); button.type = 'button'; button.className = 'btn-secondary'; button.textContent = label;
   button.setAttribute('aria-label', label + ' ' + topic.name); button.onclick = () => { menu.open = false; handler(); }; actions.append(button);
  });
 menu.append(summary, actions);
 return menu;
}
function catMasterRow(topic) {
 const card = document.createElement('button'); card.type = 'button'; card.id = 'catCard-' + topic.topic_id;
 card.style.cssText = 'display:block;width:100%;text-align:left;font:inherit;color:inherit;cursor:pointer;background:transparent;border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:10px;';
 card.setAttribute('aria-label', 'Open ' + topic.name);
 const name = document.createElement('b'); name.textContent = topic.name;
 const progress = document.createElement('span'); progress.dataset.progress = topic.topic_id; progress.style.cssText = 'margin-left:8px;font-size:.85rem;';
 const usage = document.createElement('small'); usage.className = 'ig-media-muted'; usage.style.display = 'block';
 usage.textContent = (topic.active ? '' : 'Turned off · ') + 'Used on: ' + catTopicUsage(topic);
 card.append(name, progress, usage);
 card.onclick = () => catOpenPanel(topic.topic_id);
 return card;
}
function catShowTab(key) {
 catPanelTab = key;
 document.querySelectorAll('#catPanel [data-pane]').forEach(pane => { pane.hidden = pane.dataset.pane !== key; });
 document.querySelectorAll('#catPanel [data-tab]').forEach(tab => {
  const active = tab.dataset.tab === key;
  tab.setAttribute('aria-selected', String(active)); tab.style.borderBottom = active ? '3px solid var(--primary, #5a3a2e)' : '3px solid transparent'; tab.style.fontWeight = active ? '700' : '500';
 });
}
function catDrawPanel(topic) {
 const panel = document.getElementById('catPanel');
 const head = document.createElement('div'); head.style.cssText = 'display:flex;align-items:center;gap:8px;';
 const back = document.createElement('button'); back.type = 'button'; back.className = 'btn-secondary'; back.textContent = '←'; back.setAttribute('aria-label', 'Back to categories'); back.onclick = catClosePanel;
 const title = document.createElement('div'); title.style.cssText = 'flex:1;min-width:0;';
 const name = document.createElement('h3'); name.id = 'catPanelTitle'; name.textContent = topic.name; name.style.margin = '0';
 const progress = document.createElement('small'); progress.dataset.progress = topic.topic_id;
 title.append(name, progress); head.append(back, title, catPanelMenu(topic));
 const tabs = document.createElement('div'); tabs.setAttribute('role', 'tablist'); tabs.style.cssText = 'display:flex;gap:4px;overflow-x:auto;margin:14px 0;border-bottom:1px solid var(--border);';
 for (const [key, label] of CAT_TABS) {
  const tab = document.createElement('button'); tab.type = 'button'; tab.setAttribute('role', 'tab'); tab.dataset.tab = key;
  tab.style.cssText = 'flex:none;background:none;border:0;padding:10px 12px;font:inherit;color:inherit;cursor:pointer;';
  const dot = document.createElement('span'); dot.dataset.tabDot = key; dot.textContent = ' ●'; dot.style.cssText = 'color:var(--danger);font-size:.6rem;vertical-align:middle;'; dot.setAttribute('aria-label', 'needs something');
  tab.append(label, dot); tab.onclick = () => catShowTab(key); tabs.append(tab);
 }
 const answers = catStep(topic, 'answers'), buttons = catStep(topic, 'buttons');
 const answerBox = document.createElement('div'); answerBox.id = 'catAnswers-' + topic.topic_id; answers.append(answerBox);
 const buttonBox = document.createElement('div'); buttons.append(buttonBox);
 panel.replaceChildren(head, tabs, answers, buttons, catNameStep(topic), catLinksStep(topic));
 catSlotEditor(answerBox, 'topic', topic.topic_id); catQuestionButtons(buttonBox, 'topic', topic.topic_id);
 catPaintSteps(topic); catShowTab(catPanelTab);
}
function catOpenPanel(topicId, tab) {
 const topic = catData?.topics.find(item => item.topic_id === topicId);
 if (!topic) return;
 catPanelTopic = topicId;
 catPanelTab = tab || CAT_TABS.map(item => item[0]).find(key => !topic.setup.find(step => step.key === key).done) || 'answers';
 // The tab's fade-in animation would otherwise pin a fixed panel inside the page.
 const panel = document.getElementById('catPanel'), backdrop = document.getElementById('catPanelBackdrop');
 if (panel.parentElement !== document.body) document.body.append(backdrop, panel);
 panel.hidden = false; backdrop.hidden = false;
 catDrawPanel(topic);
 document.getElementById('catPanel').scrollTop = 0;
}
function catClosePanel() {
 catPanelTopic = null;
 document.getElementById('catPanel').hidden = true; document.getElementById('catPanelBackdrop').hidden = true;
}
document.addEventListener('keydown', event => { if (event.key === 'Escape' && catPanelTopic && !document.getElementById('catMoveDialog').open) catClosePanel(); });
function catOpenAnswers(topicId) { catOpenPanel(topicId, 'answers'); }
async function catSlotEditor(box, owner, ownerId) {
 const base = '/admin/categories/slots/' + owner + '/' + encodeURIComponent(ownerId);
 box.textContent = 'Loading answers...';
 box.style.marginTop = '8px';
 try {
  const [{slots}, options] = await Promise.all([catApi(base), catApi('/admin/instagram-routes/approved-qa-options')]);
  const menus = typeof igMenus === 'undefined' ? [] : igMenus;
  box.replaceChildren(...slots.map(slot => catSlotRow(box, owner, ownerId, base, slot, options.records || [], menus)));
  if (owner === 'topic') box.append(catOwnQuestionForm(box, ownerId));
 } catch (error) { box.textContent = error.message; }
}
// A post with no choice of its own uses its category's buttons, as do ads promoting it.
async function catQuestionButtons(box, owner, ownerId) {
 const url = '/admin/categories/question-buttons/' + owner + '/' + encodeURIComponent(ownerId);
 const heading = document.createElement('b'); heading.textContent = 'Question buttons';
 const help = document.createElement('p'); help.className = 'source-map-help';
 help.textContent = owner === 'topic' ? 'Offered under the reply on this category\'s posts and ads. Only questions with an answer appear.' : 'Leave as it is to use the category\'s buttons. Saving here sets buttons for this post and the ads promoting it.';
 const note = document.createElement('small'); note.setAttribute('role', 'status'); note.style.display = 'block';
 try {
  const data = await catApi(url);
  const saved = Object.fromEntries((data.buttons || []).map(button => [button.slot, button.label]));
  const rows = data.choices.map(choice => {
   const row = document.createElement('label'); row.style.cssText = 'display:flex;gap:8px;align-items:center;padding:3px 0;';
   const tick = document.createElement('input'); tick.type = 'checkbox'; tick.style.cssText = 'width:auto;flex:none;'; tick.checked = choice.slot in saved;
   tick.setAttribute('aria-label', 'Show ' + choice.label);
   const label = document.createElement('input'); label.className = 'field-control'; label.maxLength = 20; label.value = saved[choice.slot] || choice.label;
   label.setAttribute('aria-label', choice.label + ' button text');
   row.append(tick, label);
   return {slot: choice.slot, tick, label, row};
  });
  const save = document.createElement('button'); save.type = 'button'; save.className = 'btn-secondary'; save.textContent = 'Save buttons';
  save.onclick = async () => {
   try { await catApi(url, 'PUT', {buttons: rows.filter(item => item.tick.checked).map(item => ({slot: item.slot, label: item.label.value}))}); note.textContent = 'Buttons saved.'; if (owner === 'topic') catStepsChanged(); }
   catch (error) { note.textContent = error.message; }
  };
  box.replaceChildren(heading, help, ...rows.map(item => item.row), save, note);
 } catch (error) { box.textContent = error.message; }
}
function catOwnQuestionForm(box, topicId) {
 const form = document.createElement('form'); form.className = 'source-secondary-actions'; form.style.cssText = 'padding-top:8px;border-top:1px solid var(--border);';
 const input = document.createElement('input'); input.className = 'field-control'; input.style.cssText = 'flex:1 1 200px;width:auto;'; input.maxLength = 60;
 input.placeholder = 'Your own question, such as Are seats available?'; input.setAttribute('aria-label', 'Your own question');
 const add = document.createElement('button'); add.type = 'submit'; add.className = 'btn-secondary'; add.textContent = '+ Add question';
 const note = document.createElement('small'); note.setAttribute('role', 'status'); note.style.cssText = 'display:block;flex-basis:100%;';
 form.onsubmit = async event => {
  event.preventDefault();
  try { await catApi('/admin/categories/own-questions/' + encodeURIComponent(topicId), 'POST', {label: input.value}); await catSlotEditor(box, 'topic', topicId); catStepsChanged(); }
  catch (error) { note.textContent = error.message; }
 };
 form.append(input, add, note);
 return form;
}
function catSlotRow(box, owner, ownerId, base, slot, records, menus) {
 const answer = slot.answer || {};
 const row = document.createElement('div'); row.style.cssText = 'padding:8px 0;border-top:1px solid var(--border);';
 const title = document.createElement('b');
 title.textContent = slot.label + (answer.kind === 'CUSTOM' ? ' · Custom' : '') + (answer.expired ? ' · Expired' : '');
 const kind = document.createElement('select'); kind.className = 'field-control'; kind.setAttribute('aria-label', slot.label + ' answer type');
 kind.replaceChildren(new Option('Not set', ''), new Option('Approved Q&A', 'APPROVED_QA'), new Option('Custom response', 'CUSTOM'));
 kind.value = answer.kind || '';
 const approved = document.createElement('select'); approved.className = 'field-control'; approved.setAttribute('aria-label', slot.label + ' approved answer');
 approved.replaceChildren(new Option('Choose an answer', ''), ...records.map(record => new Option(record.question, record.qa_id)));
 approved.value = answer.qa_id || '';
 const text = document.createElement('textarea'); text.className = 'field-control'; text.rows = 3; text.maxLength = 2000; text.value = answer.kind === 'CUSTOM' ? answer.text : '';
 text.setAttribute('aria-label', slot.label + ' custom answer');
 const end = document.createElement('input'); end.type = 'date'; end.className = 'field-control'; end.value = (answer.valid_until || '').slice(0, 10);
 end.setAttribute('aria-label', slot.label + ' end date');
 const menu = document.createElement('select'); menu.className = 'field-control'; menu.setAttribute('aria-label', slot.label + ' menu');
 menu.replaceChildren(new Option('No menu', ''), ...menus.map(item => new Option(item.name, item.menu_id)));
 menu.value = answer.menu_id || '';
 const custom = document.createElement('div'); custom.append(text, end, menu);
 const show = () => { approved.hidden = kind.value !== 'APPROVED_QA'; custom.hidden = kind.value !== 'CUSTOM'; };
 kind.onchange = show; show();
 const note = document.createElement('small'); note.setAttribute('role', 'status'); note.style.display = 'block';
 const act = (label, handler) => {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'btn-secondary'; button.textContent = label;
  button.setAttribute('aria-label', label + ' ' + slot.label);
  button.onclick = async () => {
   try {
    await handler(); await catSlotEditor(box, owner, ownerId);
    catRenderNeeds((await catApi('/admin/categories/needs-answers')).groups);
    if (owner === 'topic') catStepsChanged();
   } catch (error) { note.textContent = error.message; }
  };
  return button;
 };
 const actions = document.createElement('div'); actions.className = 'source-secondary-actions';
 actions.append(act('Save answer', () => {
  if (!kind.value) return catApi(base + '/' + slot.slot, 'DELETE');
  return catApi(base + '/' + slot.slot, 'PUT', kind.value === 'APPROVED_QA' ? {kind: kind.value, qa_id: approved.value}
   : {kind: kind.value, custom_text: text.value, menu_id: menu.value || null, valid_until: end.value ? end.value + 'T23:59:59+05:30' : null});
 }));
 if (slot.own) actions.append(act('Remove question', () => catApi('/admin/categories/own-questions/' + encodeURIComponent(ownerId) + '/' + encodeURIComponent(slot.slot), 'DELETE')));
 if (answer.kind === 'CUSTOM') actions.append(act('Save to Approved Q&A', () => catApi(base + '/' + slot.slot + '/save-to-approved', 'POST')));
 row.append(title, kind, approved, custom, actions, note);
 return row;
}
function catRenderNeeds(groups) {
 const box = document.getElementById('catNeeds');
 box.closest('section').style.display = groups.length ? 'block' : 'none';
 if (!groups.length) { box.textContent = 'Nothing waiting. Gaps appear here as customers ask.'; return; }
 box.replaceChildren(...groups.map(group => {
  const row = catRow(group.topic_name + ': ' + catPlural(group.count, group.label + ' question'),
   group.state === 'expired' ? 'The answer has expired' : 'No answer set',
   [['Set answer', () => catOpenAnswers(group.topic_id)], ['Not needed', () => catDismissGap(group)]]);
  const wrap = document.createElement('div'); wrap.style.marginBottom = '10px'; wrap.append(row);
  group.examples.forEach(example => {
   const line = document.createElement('div'); line.className = 'source-secondary-actions'; line.style.alignItems = 'center';
   const words = document.createElement('small'); words.textContent = example.message;
   const add = document.createElement('button'); add.type = 'button'; add.className = 'btn-secondary'; add.textContent = 'Add as phrase';
   add.setAttribute('aria-label', 'Add as phrase: ' + example.message);
   add.onclick = async () => {
    try { await catApi('/admin/categories/needs-answers/phrase', 'POST', {routing_id: example.routing_id, phrase: example.message}); catNote('Added the phrase.'); }
    catch (error) { catNote(error.message, true); }
   };
   line.append(words, add); wrap.append(line);
  });
  return wrap;
 }));
}
async function catDismissGap(group) {
 try {
  await catApi('/admin/categories/needs-answers/dismiss', 'POST', {topic_id: group.topic_id, slot: group.slot});
  catRenderNeeds((await catApi('/admin/categories/needs-answers')).groups);
  catNote('Set aside. It comes back if a customer asks again.');
 } catch (error) { catNote(error.message, true); }
}
function catRenderCopy() {
 const box = document.getElementById('catCopyReview');
 const source = catData?.topics.find(topic => topic.topic_id === document.getElementById('catCopyFrom').value);
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
 data.topics.forEach(catPaintSteps);
 // A saved change reloads the list; the open category follows it, or closes if deleted.
 if (catPanelTopic) { const open = data.topics.find(topic => topic.topic_id === catPanelTopic); open ? catDrawPanel(open) : catClosePanel(); }
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
 try {
  const [categories, needs] = await Promise.all([catApi('/admin/categories'), catApi('/admin/categories/needs-answers').catch(() => ({groups: []}))]);
  catData = categories; catRender(); catRenderNeeds(needs.groups);
 } catch (error) { catNote(error.message || 'Could not load categories.', true); }
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
// Each step saves its own fields; the rest of the category's links are kept.
async function catSaveLinks(topic, changes) {
 try {
  await catApi('/admin/categories/master/' + encodeURIComponent(topic.topic_id) + '/links', 'PUT',
               {menu_category_id: topic.menu_category_id ?? null, qa_context: topic.qa_context ?? null, customer_topic_id: topic.customer_topic_id ?? null, button_name: topic.button_name ?? null, ...changes});
  await catChanged('Saved ' + topic.name + '.');
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
