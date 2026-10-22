"""Context selection is an Admin-only constraint, never a runtime migration."""
import json
import shutil
import subprocess
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from camellia import approved_qa, client_qa, app as app_module
from camellia.admin_approved_qa import SCRIPT
from camellia.database import Database
from camellia.config import config


@pytest.fixture
def store(tmp_path, monkeypatch):
    database = Database(str(tmp_path / 'contexts.db'))
    monkeypatch.setattr(approved_qa, '_database', lambda: database)
    monkeypatch.setattr(app_module, 'db', database)
    return database


@pytest.mark.parametrize('context', list(approved_qa.CANONICAL_CONTEXTS))
def test_new_qa_can_choose_canonical_context_without_copy_changes(store, context):
    body = dict(question='A unique context test?', answer='Exact approved test answer.', context=context,
                aliases=['Unique context variation'])
    draft = approved_qa.preview(body)
    record = approved_qa.publish({**body, 'qa_id': draft['qa_id'], 'preview_token': draft['preview_token']}, 'test')
    assert record['context'] == context
    assert record['question'] == body['question'] and record['answer'] == body['answer']
    assert record['aliases'] == body['aliases'] and record['asset_keys'] == []


def test_existing_and_retained_contexts_remain_exact_and_reads_do_not_write(store):
    key = 'sondesh_price_client'
    with store.get_connection() as conn:
        conn.execute("INSERT INTO approved_qa_versions(qa_id,snapshot_json,changed_at,changed_by,change_reason) VALUES (?,?,'now','test','history')",
                     (key, json.dumps({'context': 'Sandesh <legacy>'})))
    before = approved_qa.registry()
    values = {option['value'] for option in approved_qa.listing()['contexts']}
    assert 'Sandesh <legacy>' in values
    assert {entry.context for entry in client_qa.REGISTRY} <= values
    assert approved_qa.registry() == before
    draft = approved_qa.preview({'qa_id': key, 'context': 'Sandesh <legacy>'})
    assert draft['proposed']['context'] == 'Sandesh <legacy>'
    assert approved_qa.get(key).context == 'sondesh'


def test_arbitrary_context_is_rejected_by_api_before_any_mutation(store, monkeypatch):
    before = approved_qa.overlays(strict=True)
    monkeypatch.setattr(config, 'ADMIN_API_KEY', 'test-context-machine-key')
    response = TestClient(app_module.app, base_url='https://testserver').post(
        '/admin/approved-qa/preview', headers={'X-Admin-Key': 'test-context-machine-key'}, json={
        'question': 'Unique question?', 'answer': 'Exact test copy.', 'context': 'unapproved-context'})
    assert response.status_code == 409
    assert approved_qa.overlays(strict=True) == before


def test_existing_qa_matching_answer_assets_and_validity_unchanged(store):
    before = approved_qa.listing()['records']
    for entry in approved_qa.registry():
        draft = approved_qa.preview({'qa_id': entry.qa_id, 'context': entry.context})
        assert draft['proposed']['answer'] == entry.answer
        assert draft['proposed']['asset_keys'] == list(entry.asset_keys)
    assert approved_qa.listing()['records'] == before


def test_executed_editor_uses_safe_dropdown_and_loads_exact_legacy_selection():
    node = shutil.which('node')
    assert node, 'Node is required to execute the Admin editor'
    script = SCRIPT.split('<script>', 1)[1].split('</script>', 1)[0]
    harness = r'''
const elements = {};
const document = {getElementById(id){return elements[id] ||= {value:'',style:{},replaceChildren(...options){this.options=options},showModal(){}}}};
const Option = function(label,value){this.text=label;this.value=value};
const escapeHtml = value => String(value).replaceAll('<','&lt;').replaceAll('>','&gt;');
'''
    check = r'''
aqContexts=[{value:'general',label:'General'},{value:'Sandesh <legacy>',label:'Sandesh <legacy> (existing)'}];
aqRecords=[{qa_id:'admin_legacy',question:'Question',answer:'Answer',context:'Sandesh <legacy>'}];
aqEdit('admin_legacy');
if(document.getElementById('aqContext').value!=='Sandesh <legacy>')throw Error('legacy changed');
if(!document.getElementById('aqDialogContent').innerHTML.includes('<select id="aqContext"'))throw Error('not dropdown');
if(document.getElementById('aqDialogContent').innerHTML.includes('<input id="aqContext"'))throw Error('free typing');
if(document.getElementById('aqContext').options[1].text!=='Sandesh <legacy> (existing)')throw Error('unsafe option');
aqEdit();if(document.getElementById('aqContext').value!=='general')throw Error('new default');
'''
    result = _run_node(node, harness + script + check)
    assert result.returncode == 0, result.stderr


def test_list_shows_one_category_at_a_time_and_form_asks_category_first():
    # 21 Oct: the client wants Sondesh answers apart from General ones, and to
    # pick the category before writing the question.
    node = shutil.which('node')
    assert node, 'Node is required to execute the Admin editor'
    script = SCRIPT.split('<script>', 1)[1].split('</script>', 1)[0]
    harness = r'''
const elements = {};
const document = {getElementById(id){return elements[id] ||= {value:'',style:{},innerHTML:'',textContent:'',replaceChildren(...options){this.options=options},showModal(){}}},querySelectorAll(){return []}};
const Option = function(label,value){this.text=label;this.value=value};
const escapeHtml = value => String(value).replaceAll('<','&lt;').replaceAll('>','&gt;');
'''
    check = r'''
aqContexts=[{value:'general',label:'General'},{value:'sondesh',label:'Sondesh'}];
aqRecords=[{qa_id:'g1',question:'General Q',answer:'A',context:'general',status:'ACTIVE'},
           {qa_id:'s1',question:'Sondesh Q',answer:'A',context:'sondesh',status:'ACTIVE'},
           {qa_id:'s2',question:'Sondesh Q2',answer:'A',context:'sondesh',status:'ACTIVE'}];
aqRender();
const list=()=>document.getElementById('aqList').innerHTML;
if(!list().includes('General Q')||list().includes('Sondesh Q'))throw Error('default is not General only');
const picker=document.getElementById('aqCategoryFilter');
if(picker.options.map(o=>o.text).join('|')!=='General (1)|Sondesh (2)|All categories (3)')throw Error('options '+picker.options.map(o=>o.text));
picker.value='sondesh';aqCategoryChanged();
if(list().includes('General Q')||!list().includes('Sondesh Q2'))throw Error('sondesh filter');
aqEdit();
const form=document.getElementById('aqDialogContent').innerHTML;
if(form.indexOf('id="aqContext"')>form.indexOf('id="aqQuestion"'))throw Error('category not first');
if(document.getElementById('aqContext').value!=='sondesh')throw Error('new answer not in chosen category');
if(!document.getElementById('aqCategoryQuestions').innerHTML.includes('Sondesh Q2'))throw Error('existing sondesh questions not listed');
aqRoles=[{value:'price',label:'Price',questions:['How much does it cost?']}];
document.getElementById('aqMappedRole').value='price';aqRoleChanged();
const suggested=document.getElementById('aqMappedQuestions').value;
if(!suggested.includes('How much does it cost?')||!suggested.includes('How much is Sondesh?'))throw Error('suggested '+suggested);
document.getElementById('aqContext').value='general';aqFormCategoryChanged();
if(document.getElementById('aqMappedQuestions').value.includes('Sondesh'))throw Error('wording kept old category');
if(!document.getElementById('aqCategoryQuestions').innerHTML.includes('General Q'))throw Error('general list');
aqRecords.push({qa_id:'p1',question:'Priced',answer:'A',context:'sondesh',status:'ACTIVE',mapped_role:'price',mapped_questions:['Sondesh box cost?','Rate for 12 pcs?']});
aqEdit('p1');document.getElementById('aqMappedRole').value='price';
document.getElementById('aqContext').value='general';aqFormCategoryChanged();
if(document.getElementById('aqMappedQuestions').value!==['Sondesh box cost?','Rate for 12 pcs?'].join(String.fromCharCode(10)))throw Error('saved questions replaced: '+document.getElementById('aqMappedQuestions').value);
picker.value='all';aqCategoryChanged();
if(!list().includes('General Q')||!list().includes('Sondesh Q2'))throw Error('all');
'''
    result = _run_node(node, harness + script + check)
    assert result.returncode == 0, result.stderr


def test_topic_answer_list_shows_its_category_plus_general():
    # 21 Oct: a Topic's "reply with" list showed every category's answers.
    import re
    from camellia import app as app_module
    node = shutil.which('node')
    assert node
    source = open(app_module.__file__, encoding='utf-8').read()
    start = source.index('        const TOPIC_CATEGORY_HINTS = [')
    end = source.index('        async function renderMediaTopicAnswers() {')
    helpers = source[start:end]
    check = r'''
const escapeHtml = value => String(value).replaceAll('<','&lt;');
const records=[{qa_id:'sp',question:'Sondesh price',context:'sondesh'},{qa_id:'gp',question:'General price',context:'general'},
  {qa_id:'ht',question:'Home table price',context:'home_table'},{qa_id:'dl',question:'Deliver out of area',context:'delivery'}];
const contexts=[{value:'general',label:'General'},{value:'sondesh',label:'Sondesh'},{value:'home_table',label:'home_table (existing)'}];
if(topicAnswerCategory({name:'sondesh delivery delhi NCR'},records)!=='sondesh')throw Error('sondesh');
if(topicAnswerCategory({name:'Bengali Home Table'},records)!=='home_table')throw Error('home table');
if(topicAnswerCategory({name:'Something else'},records)!=='general')throw Error('fallback');
let html=topicAnswerListHtml(records,contexts,new Set(['dl']),'sondesh');
if(!html.includes('Sondesh price')||!html.includes('General price')||html.includes('Home table price'))throw Error('filter '+html);
if(!html.includes('Deliver out of area')||!html.includes('Already ticked from other categories'))throw Error('ticked kept');
html=topicAnswerListHtml(records,contexts,new Set(),'all');
if(!html.includes('Home table price'))throw Error('all');
if(topicAnswerCategory({name:'Kolkata Sweets'},records)!=='sondesh')throw Error('kolkata sweets');
html=topicAnswerListHtml(records,contexts,new Set(),topicAnswerCategory({name:'Durga Puja Specials'},records));
if(!html.includes('<option value="general" selected>'))throw Error('general option not selected');
if(!html.includes('General price')||html.includes('Sondesh price'))throw Error('general only');
'''
    result = _run_node(node, helpers + check)
    assert result.returncode == 0, result.stderr


def _run_node(node, source):
    """Run JS from a file: the admin script is too long for a Windows command line."""
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(source)
    try:
        return subprocess.run([node, handle.name], capture_output=True, text=True)
    finally:
        os.unlink(handle.name)


def test_empty_categories_built_in_hidden_own_ones_deletable():
    node = shutil.which('node')
    assert node
    script = SCRIPT.split('<script>', 1)[1].split('</script>', 1)[0]
    harness = r'''
const elements = {};
const document = {getElementById(id){return elements[id] ||= {value:'',style:{},innerHTML:'',textContent:'',hidden:true,replaceChildren(...options){this.options=options},showModal(){}}},querySelectorAll(){return []}};
const Option = function(label,value){this.text=label;this.value=value};
const escapeHtml = value => String(value);
'''
    check = r'''
aqContexts=[{value:'general',label:'General'},{value:'savoury_deliveries',label:'Savoury Deliveries'},{value:'kolkata_sondesh',label:'Kolkata Sondesh',custom:true}];
aqRecords=[{qa_id:'g1',question:'G',answer:'A',context:'general',status:'ACTIVE'}];
aqRender();
const texts=document.getElementById('aqCategoryFilter').options.map(o=>o.text).join('|');
if(texts.includes('Savoury Deliveries'))throw Error('empty built-in shown: '+texts);
if(!texts.includes('Kolkata Sondesh (0)'))throw Error('own category hidden: '+texts);
if(!document.getElementById('aqDeleteCategory').hidden)throw Error('delete shown on General');
document.getElementById('aqCategoryFilter').value='kolkata_sondesh';aqCategoryChanged();
if(document.getElementById('aqDeleteCategory').hidden)throw Error('delete hidden on empty own category');
'''
    result = _run_node(node, harness + script + check)
    assert result.returncode == 0, result.stderr
