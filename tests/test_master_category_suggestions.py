"""Choosing a master category suggests answers and a menu, and never overrides staff."""
import json
import shutil
import subprocess

import pytest

from camellia.admin_source_response import SCRIPT

STUB = r'''
const elements={};
function element(id){return {id,value:'',textContent:'',hidden:false,style:{},events:0,after(node){elements[node.id]=node},closest(){return {hidden:false}},dispatchEvent(){this.events++}}}
const document={getElementById(id){return elements[id]},createElement(){return element('')},addEventListener(){}};
const Event=function(type){this.type=type};
for(const id of ['igMediaTopic','igMediaMenu','igMediaQaContext','igMediaDescription'])elements[id]=element(id);
elements.igMediaMenu.value='__none__';
const igTopics=[{topic_id:'sondesh',name:'Sondesh Delhi',qa_context:'sondesh',menu_category_id:7},
 {topic_id:'plain',name:'No Links'},{topic_id:'default',name:'Has Default',qa_context:'sondesh',menu_category_id:7,default_menu_id:'old'},
 {topic_id:'many',name:'Many Menus',menu_category_id:8}];
const igMenuCategoryRecords=[{category_id:7,name:'Sondesh'},{category_id:8,name:'Event'}];
const igMenus=[{menu_id:'box',name:'Sondesh box',category:'sondesh'},{menu_id:'a',name:'A',category:'event'},{menu_id:'b',name:'B',category:'event'}];
'''

CHECK = r'''
function run(topic, setup){
 elements.igMediaTopic.value=topic;elements.igMediaMenu.value='__none__';elements.igMediaMenu.events=0;elements.igMediaQaContext.value='general';
 sourceResponseState.igMedia={selectedIds:new Set(),records:[],context:'general'};
 if(setup)setup();
 applyCategorySuggestions('igMedia');
 return {context:sourceResponseState.igMedia.context,menu:elements.igMediaMenu.value,events:elements.igMediaMenu.events,note:elements.igMediaCategoryNote.textContent};
}
console.log(JSON.stringify({
 linked:run('sondesh'), plain:run('plain'), hasDefault:run('default'), many:run('many'),
 ticked:run('sondesh',()=>sourceResponseState.igMedia.selectedIds.add('x')),
 chosen:run('sondesh',()=>{elements.igMediaMenu.value='mine'}),
 none:run(''),
}));
'''


@pytest.fixture(scope="module")
def outcome():
    node = shutil.which("node")
    assert node
    script = SCRIPT.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    result = subprocess.run([node], input=STUB + script + CHECK, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_linked_category_suggests_answers_and_its_one_menu(outcome):
    assert outcome["linked"]["context"] == "sondesh"
    assert (outcome["linked"]["menu"], outcome["linked"]["events"]) == ("box", 1)
    assert "Sondesh box" in outcome["linked"]["note"] and "change" in outcome["linked"]["note"]


def test_an_unlinked_category_suggests_nothing(outcome):
    assert outcome["plain"] == {"context": "general", "menu": "__none__", "events": 0, "note": ""}
    assert outcome["none"]["note"] == ""


def test_a_default_menu_or_several_menus_are_left_alone(outcome):
    assert outcome["hasDefault"]["menu"] == "__none__"
    assert outcome["many"]["menu"] == "__none__"


def test_ticked_answers_and_a_chosen_menu_are_never_replaced(outcome):
    assert outcome["ticked"]["context"] == "general"
    assert outcome["chosen"]["menu"] == "mine"
