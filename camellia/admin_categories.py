"""One Admin page for the four category lists. Each list keeps its own table and
rules (see MEMORY.md); this page only reads them together. Add, rename and delete
go through each list's existing route, so its guards still apply."""
from fastapi import APIRouter, Depends

from camellia import approved_qa, menu_catalogue
from camellia.admin_menu_review import reviewer
from camellia.instagram_routing import decode_topic

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
             "usage": usage.get(topic["topic_id"], {}), "gallery": gallery.get(topic["topic_id"], 0)}
            for topic in map(decode_topic, _database().list_instagram_topics())]


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
        return {
            "topics": _topics(conn),
            "menu_categories": _menu_categories(conn),
            "menu_topics": menu_catalogue.list_customer_topics(),
            "routing_keys": [{"value": key, "label": menu_catalogue._CORE_TOPIC_LABELS[key]}
                             for key in menu_catalogue.CUSTOMER_TOPICS],
            "qa_categories": _qa_categories(conn),
        }


HTML = r'''
<div id="tab-categories" class="panel-card" data-group="categories" style="display:none;">
 <div class="admin-page-heading"><div><h2>Categories</h2><p>Every list of categories in one place. Each list does a different job, so a name added to one does not appear in the others.</p></div></div>
 <div id="catMessage" role="status" class="result-box" style="display:none;"></div>
 <section class="ig-media-card" style="display:block;margin-bottom:16px;" aria-labelledby="catTopicsTitle">
  <h3 id="catTopicsTitle">Topics: Stories, Posts, Reels and Ads</h3>
  <p class="source-map-help">Group your Instagram content. Default answers and menus are set on a Topic from its mapping form.</p>
  <form class="source-secondary-actions" onsubmit="event.preventDefault();catAdd('topics')"><input id="catAddTopics" class="field-control" style="flex:1 1 200px;width:auto;" maxlength="60" placeholder="New Topic name" aria-label="New Topic name"><button class="btn-secondary" type="submit">+ Add Topic</button></form>
  <div id="catTopics" style="margin-top:12px;"></div>
 </section>
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
function catRender() {
 const data = catData;
 catFill('catTopics', data.topics.map(topic => catRow(topic.name, catTopicUsage(topic), [
  ['Rename', () => catRename('topics', topic.topic_id, topic.name)], ['Delete', () => catDeleteTopic(topic)],
 ], !topic.active)));
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
const CAT_INPUTS = {topics:'catAddTopics', menus:'catAddMenus', menuTopics:'catAddMenuTopics', qa:'catAddQa'};
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
 if (!others.length) return catNote('Add another Topic first, so its content has somewhere to move.', true);
 catMoving = topic;
 document.getElementById('catMoveTitle').textContent = 'Delete ' + topic.name;
 document.getElementById('catMoveText').textContent = (used === 1 ? '1 item uses' : used + ' items use') + ' this Topic. Choose where to move them before it is deleted.';
 document.getElementById('catMoveTarget').replaceChildren(new Option('Choose a Topic', ''), ...others.map(item => new Option(item.name, item.topic_id)));
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
