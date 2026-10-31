"""Shared read-only Approved Q&A selector for all Instagram source editors."""
import json

SELECTOR_TEMPLATE = r"""<style>[id$="QaFields"][hidden],.source-map-field[hidden]{display:none!important;} fieldset.source-map-field input[type="radio"]{width:auto;} fieldset.source-map-field{min-width:0;max-width:100%;} [id$="QaFields"],[id$="QaBundleFields"],[id$="QaChecklist"]{min-width:0;max-width:100%;} .source-qa-card{box-sizing:border-box;max-width:100%;}</style><fieldset class="source-map-field" style="border:0;padding:0;margin:0 0 16px"><legend class="field-label">Response Source</legend>
<div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap"><label><input type="radio" name="__PREFIX__ResponseMode" value="APPROVED_QA" checked onchange="setSourceResponseMode('__PREFIX__')"> Approved Q&amp;A</label>
<label><input type="radio" name="__PREFIX__ResponseMode" value="CUSTOM" onchange="setSourceResponseMode('__PREFIX__')"> Custom response <span class="ig-media-muted">(optional)</span></label></div>
<div id="__PREFIX__QaFields" hidden style="margin-top:12px">
<label class="field-label" for="__PREFIX__QaSearch">Search Approved Q&amp;A</label>
<input class="field-control" id="__PREFIX__QaSearch" type="search" placeholder="Question, QA ID or accepted variation" oninput="filterSourceQa('__PREFIX__')">
<label class="field-label" for="__PREFIX__ApprovedQaId">Approved Q&amp;A</label>
<select class="field-control" id="__PREFIX__ApprovedQaId" onchange="previewSourceQa('__PREFIX__')"><option value="">Select Approved Q&amp;A</option></select>
<p class="source-map-help">Read-only preview sourced from Approved Q&amp;A. Edit wording in the Approved Q&amp;A page. Custom content is retained when switching modes.</p>
<div id="__PREFIX__QaPreview" style="white-space:pre-wrap;overflow-wrap:anywhere"></div>
</div></fieldset>"""


STORY_FIELDS = r'''<style>#igStoryApprovedQaId[hidden]{display:none!important;}</style><label class="field-label" for="igStoryQaContext">Context</label>
<select class="field-control" id="igStoryQaContext" onchange="changeStoryQaContext()"></select>
<p id="igStoryQaCount" role="status">Selected: 0</p>
<div id="igStoryQaChecklist" style="max-height:40vh;overflow:auto;overflow-wrap:anywhere"></div>
<p>Choose each approved question explicitly. General questions are never automatically selected.</p>'''


def selector_html(prefix):
    assert prefix in {"ig", "igStory", "igMedia"}
    template = SELECTOR_TEMPLATE.replace("__PREFIX__", prefix)
    if prefix == "igStory":
        template = template.replace('<label class="field-label" for="igStoryApprovedQaId">Approved Q&amp;A</label>', '')
        template = template.replace('<select class="field-control" id="igStoryApprovedQaId"', '<select hidden class="field-control" id="igStoryApprovedQaId"')
        template = template.replace('<p class="source-map-help">', STORY_FIELDS + '<p class="source-map-help">')
    else:
        fields = STORY_FIELDS.replace('igStory', prefix).replace('changeStoryQaContext()', "changeStoryQaContext('" + prefix + "')")
        fields = '<div id="' + prefix + 'QaBundleFields" hidden>' + fields + '</div>'
        template = template.replace('<label class="field-label" for="' + prefix + 'ApprovedQaId">', '<label id="' + prefix + 'QaSingleLabel" class="field-label" for="' + prefix + 'ApprovedQaId">')
        template = template.replace('<p class="source-map-help">', fields + '<p class="source-map-help">')
        template = '<style>[id$="QaBundleFields"][hidden],[id$="ApprovedQaId"][hidden],[id$="QaSingleLabel"][hidden]{display:none!important;}</style>' + template
    return template


SCRIPT = r"""<script>
const sourceResponseState = {};
function sourceResponseHtml(prefix) { return prefix==='igStory' ? __STORY_SELECTOR__ : prefix==='igMedia' ? __MEDIA_SELECTOR__ : __POST_SELECTOR__; }
function sourceQaMultiple(prefix){return prefix==='igStory'||prefix==='igMedia'||(prefix==='ig'&&['post','reel','ad'].includes(document.getElementById('igSourceType')?.value||'post'));}
function sourceResponseFields(prefix) {
 const mode=document.querySelector(`input[name="${prefix}ResponseMode"]:checked`)?.value || 'CUSTOM';
 const variations=(document.getElementById(prefix+'CustomResponseVariations')?.value||'').split(/\r?\n/).map(value=>value.trim()).filter(Boolean);
 if(sourceQaMultiple(prefix) && mode==='APPROVED_QA'){
  const state=sourceResponseState[prefix];
   return {response_mode:mode,custom_response_variations:variations,approved_qa_ids:Array.from(state?.selectedIds||[]),[prefix==='igStory'?'story_context':'source_context']:state?.context||document.getElementById(prefix+'QaContext')?.value||'general'};
 }
 return {response_mode:mode,custom_response_variations:variations, approved_qa_id:mode==='APPROVED_QA' ? document.getElementById(prefix+'ApprovedQaId')?.value || '' : null};
}
function setSourceResponseMode(prefix) {
 if(!sourceResponseState[prefix]){initSourceResponse(prefix,sourceResponseFields(prefix));return;}
 const approved=sourceResponseFields(prefix).response_mode==='APPROVED_QA';
 document.getElementById(prefix+'QaFields').hidden=!approved;
 if(prefix!=='igStory'){const multi=sourceQaMultiple(prefix);document.getElementById(prefix+'QaBundleFields').hidden=!multi;document.getElementById(prefix+'ApprovedQaId').hidden=multi;document.getElementById(prefix+'QaSingleLabel').hidden=multi;}
 const ids={ig:['igTopicPicker','igSourceDescription','igCustomResponseVariations','igTopicDescription','igMenuPicker'],igStory:['igStoryTopicPicker','igStorySourceDescription','igStoryCustomResponseVariations','igStoryMenuPicker'],igMedia:['igMediaTopic','igMediaTopicAnswers','igMediaDescription','igMediaCustomResponseVariations','igMediaMenu']}[prefix];
 for(const id of ids){const element=document.getElementById(id); if(element)element.closest('.source-map-field')?.toggleAttribute('hidden',approved);}
 previewSourceQa(prefix);
}
async function initSourceResponse(prefix, route) {
 const state={selected:route.approved_qa_id || '',records:[],selectedIds:new Set(route.approved_qa_ids || (route.approved_qa_id?[route.approved_qa_id]:[])),context:(prefix==='igStory'?route.story_context:route.source_context)??null}; sourceResponseState[prefix]=state;
 document.querySelector(`input[name="${prefix}ResponseMode"][value="${route.response_mode || 'APPROVED_QA'}"]`).checked=true;
 addVariationSuggest(prefix);
 document.getElementById(prefix+'QaSearch').value='';
 const select=document.getElementById(prefix+'ApprovedQaId'); select.replaceChildren(new Option('Select Approved Q&A','')); select.disabled=true;
 setSourceResponseMode(prefix);
 try {
  const response=await fetch('/admin/instagram-routes/approved-qa-options',{headers:getAuthHeaders()});
  if(!response.ok)throw new Error('Effective Approved Q&A is unavailable.');
  const data=await response.json(); if(sourceResponseState[prefix]!==state)return;
  state.records=data.records || []; select.disabled=false;
  {
   state.contexts=data.contexts||Array.from(new Set(['general',...state.records.map(r=>r.context)])).map(value=>({value,label:value}));
   if(state.context===null)state.context=state.records.find(r=>state.selectedIds.has(r.qa_id))?.context||'general';
   const context=document.getElementById(prefix+'QaContext');context.replaceChildren(...state.contexts.map(o=>new Option(o.value==='general'?'General':o.label,o.value)));context.value=state.context;
  }
  filterSourceQa(prefix);
 } catch(error) {
  if(sourceResponseState[prefix]!==state)return;
  select.replaceChildren(new Option('Approved Q&A unavailable',''));
  document.getElementById(prefix+'QaPreview').textContent=error.message;
 }
}
// Suggestions only reach the box when ticked, and only Save stores them.
function variationLines(box){return box.value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);}
function addVariationSuggest(prefix){
 const box=document.getElementById(prefix+'CustomResponseVariations');if(!box)return;
 let list=document.getElementById(prefix+'VariationSuggestions');
 if(list){list.replaceChildren();return;}
 const button=document.createElement('button');button.type='button';button.className='btn-secondary';button.textContent='Suggest phrases';button.style.marginTop='8px';
 list=document.createElement('div');list.id=prefix+'VariationSuggestions';list.setAttribute('role','status');list.style.marginTop='8px';
 button.onclick=()=>suggestVariations(prefix,box,button,list);box.after(button,list);
}
async function suggestVariations(prefix,box,button,list){
 const reply=(document.getElementById(SOURCE_NOTE_FIELDS[prefix][0])?.value||'').trim();
 if(!reply){list.textContent='Write the custom response first.';return;}
 button.disabled=true;list.textContent='Suggesting phrases...';
 try{
  const response=await fetch('/admin/approved-qa/variation-suggestions',{method:'POST',headers:{...getAuthHeaders(),'Content-Type':'application/json'},body:JSON.stringify({reply,existing:variationLines(box)})});
  const data=await response.json();if(!response.ok)throw new Error(data.detail||'Suggestions are unavailable. Add phrases by hand.');
  list.replaceChildren();
  if(!data.suggestions.length){list.textContent='No new phrases to suggest.';return;}
  const help=document.createElement('p');help.className='source-map-help';help.textContent='Tick the phrases this reply should answer, then save.';list.append(help);
  for(const phrase of data.suggestions){
   const row=document.createElement('label');row.style.cssText='display:flex;gap:8px;align-items:center;padding:4px 0;overflow-wrap:anywhere';
   const check=document.createElement('input');check.type='checkbox';check.style.cssText='width:auto;flex:none';
   check.onchange=()=>{const lines=variationLines(box).filter(line=>line!==phrase);if(check.checked)lines.push(phrase);box.value=lines.join('\n');};
   row.append(check,document.createTextNode(phrase));list.append(row);
  }
 }catch(error){list.textContent=error.message;}
 finally{button.disabled=false;}
}
// One mapping on many Posts and Reels: a post using another post's mapping shows
// that instead of its own form, so edits reach every post sharing it.
const sharedPick={};
function mediaOwnsRoute(item,route){return route.media_id===item.instagram_media_id||route.canonical_url===item.permalink||Boolean(item.shortcode&&route.shortcode===item.shortcode);}
async function initSharedMapping(item){
 const route=item.mapping_route,box=document.getElementById('contentDetailsActions');if(!route||!box)return;
 const response=await fetch('/admin/instagram-media/shared-mapping/'+Number(route.routing_id),{headers:getAuthHeaders()});
 const shared=await response.json().catch(()=>({}));if(!response.ok)return;
 if(!mediaOwnsRoute(item,route)){
  box.innerHTML='<div class="source-map-form"><div class="source-map-field"><b>This '+escapeHtml(item.source_type)+' uses a shared mapping</b><p class="source-map-help">It shares its reply, menu and phrases with '+Number(shared.shared_count)+' posts. Edit the shared mapping once and all of them change.</p><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn-primary" type="button" id="sharedMappingEdit">Edit shared mapping</button><button class="btn-secondary" type="button" id="sharedMappingStop">Stop sharing</button></div><p id="sharedMappingNote" role="status"></p></div></div>';
  document.getElementById('sharedMappingEdit').onclick=()=>openSharedSource(shared.source_item_id);
  document.getElementById('sharedMappingStop').onclick=()=>stopSharingMapping(item);
  return;
 }
 const panel=document.createElement('div');panel.className='source-map-field';
 panel.innerHTML='<b>Use this mapping on more posts</b><p class="source-map-help">Shared with '+Number(shared.shared_count)+' other posts. Editing this mapping changes all of them, and deleting it removes it from them too. Works for customers who share a Reel or come from an ad. Posts with their own mapping are skipped.</p><button class="btn-secondary" type="button" id="sharedMappingOpen">Choose posts</button><div id="sharedMappingPicker"></div>';
 box.querySelector('.source-map-form')?.append(panel);
 document.getElementById('sharedMappingOpen').onclick=()=>openSharedPicker(Number(route.routing_id));
}
async function openSharedSource(itemId){
 const note=document.getElementById('sharedMappingNote');
 if(!itemId){note.textContent='The post that owns this mapping is not in Posts and Reels.';return;}
 const response=await fetch('/admin/instagram-media/'+Number(itemId),{headers:getAuthHeaders()});
 const data=await response.json().catch(()=>({}));
 if(!response.ok||!data.item){note.textContent=data.detail||'Could not open the shared mapping.';return;}
 openContentDetails('media',galleryMediaItem(data.item));
}
async function stopSharingMapping(item){
 if(!confirm('Stop sharing this mapping on this '+item.source_type+'? It will have no mapping until you add one.'))return;
 const response=await fetch('/admin/instagram-media/'+Number(item.id)+'/detach-mapping',{method:'POST',headers:getAuthHeaders()});
 const data=await response.json().catch(()=>({}));
 if(!response.ok){document.getElementById('sharedMappingNote').textContent=data.detail||'Could not stop sharing.';return;}
 closeContentDetails();await loadInstagramContentGallery(true);
}
function openSharedPicker(routingId){
 const state=sharedPick.state={routingId,selected:new Set(),excluded:new Set(),all:false,page:1,total:0};
 document.getElementById('sharedMappingOpen').hidden=true;
 const box=document.getElementById('sharedMappingPicker');
 box.innerHTML='<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px"><input class="field-control" id="sharedPickSearch" type="search" placeholder="Search captions" style="flex:1;min-width:160px"><select class="field-control" id="sharedPickType" style="width:auto"><option value="">Posts and Reels</option><option value="reel">Reels only</option><option value="post">Posts only</option></select></div><label style="display:flex;gap:8px;align-items:center;margin-top:8px"><input type="checkbox" id="sharedPickAll" style="width:auto"> <span id="sharedPickAllLabel">Select all matching</span></label><div id="sharedPickList" style="max-height:40vh;overflow:auto;margin-top:8px"></div><button class="btn-secondary" type="button" id="sharedPickMore" hidden>Load more</button><div style="margin-top:8px"><button class="btn-primary" type="button" id="sharedPickApply">Apply to selected</button></div><p id="sharedPickNote" role="status"></p>';
 let timer;const reload=()=>{clearTimeout(timer);timer=setTimeout(()=>loadSharedPicker(true),250);};
 document.getElementById('sharedPickSearch').oninput=reload;
 document.getElementById('sharedPickType').onchange=()=>loadSharedPicker(true);
 document.getElementById('sharedPickAll').onchange=event=>{state.all=event.target.checked;state.excluded.clear();state.selected.clear();for(const box of document.querySelectorAll('#sharedPickList input'))box.checked=state.all;sharedPickCount();};
 document.getElementById('sharedPickMore').onclick=()=>loadSharedPicker(false);
 document.getElementById('sharedPickApply').onclick=applySharedPicker;
 loadSharedPicker(true);
}
function sharedPickFilters(){return {search:document.getElementById('sharedPickSearch').value.trim(),source_type:document.getElementById('sharedPickType').value};}
function sharedPickCount(){const state=sharedPick.state,count=state.all?state.total-state.excluded.size:state.selected.size;document.getElementById('sharedPickApply').textContent='Apply to '+count+' selected';}
async function loadSharedPicker(reset){
 const state=sharedPick.state,filters=sharedPickFilters(),list=document.getElementById('sharedPickList');
 if(reset){state.page=1;state.all=false;state.selected.clear();state.excluded.clear();list.replaceChildren();document.getElementById('sharedPickAll').checked=false;}
 const params=new URLSearchParams({page:state.page,page_size:50,mapping_status:'unmapped'});if(filters.search)params.set('search',filters.search);if(filters.source_type)params.set('source_type',filters.source_type);
 const stamp=sharedPick.stamp=params.toString();
 const response=await fetch('/admin/instagram-media?'+params,{headers:getAuthHeaders()});
 const data=await response.json().catch(()=>({}));if(sharedPick.stamp!==stamp||sharedPick.state!==state)return;
 if(!response.ok){document.getElementById('sharedPickNote').textContent=data.detail||'Could not load posts.';return;}
 state.total=Number(data.total||0);state.page+=1;
 document.getElementById('sharedPickAllLabel').textContent='Select all '+state.total+' matching';
 document.getElementById('sharedPickMore').hidden=!data.has_more;
 if(!data.items.length&&reset)list.textContent='No unmapped posts match.';
 for(const item of data.items){
  const row=document.createElement('label');row.style.cssText='display:flex;gap:8px;align-items:center;padding:4px 0';
  const check=document.createElement('input');check.type='checkbox';check.style.cssText='width:auto;flex:none';check.checked=state.all;
  check.onchange=()=>{const set=state.all?state.excluded:state.selected;if(check.checked===state.all)set.delete(item.id);else set.add(item.id);sharedPickCount();};
  const picture=document.createElement('img');picture.alt='';picture.src=item.thumbnail_url||item.media_url||'';picture.style.cssText='width:44px;height:44px;object-fit:cover;border-radius:6px;flex:none';
  const text=document.createElement('span');text.style.cssText='min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';text.textContent=(item.media_type==='VIDEO'?'Reel · ':'Post · ')+(item.caption||item.permalink||item.instagram_media_id);
  row.append(check,picture,text);list.append(row);
 }
 sharedPickCount();
}
async function applySharedPicker(){
 const state=sharedPick.state,note=document.getElementById('sharedPickNote'),button=document.getElementById('sharedPickApply');
 const count=state.all?state.total-state.excluded.size:state.selected.size;
 if(!count){note.textContent='Tick at least one post.';return;}
 if(!confirm('Use this mapping on '+count+' posts? Customers from those Reels and ads will get this reply.'))return;
 button.disabled=true;
 try{
  const response=await fetch('/admin/instagram-media/attach-mapping',{method:'POST',headers:{...getAuthHeaders(),'Content-Type':'application/json'},body:JSON.stringify({routing_id:state.routingId,...(state.all?{all_matching:true,excluded_ids:[...state.excluded],...sharedPickFilters()}:{item_ids:[...state.selected]})})});
  const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||'Could not apply the mapping.');
  note.textContent='Added '+data.attached+' posts. Skipped '+data.skipped_mapped+' with their own mapping.';
  await loadSharedPicker(true);
 }catch(error){note.textContent=error.message;}
 finally{button.disabled=false;}
}
function filterSourceQa(prefix) {
 const state=sourceResponseState[prefix]; if(!state)return;
 if(sourceQaMultiple(prefix)){renderStoryQa(prefix);return;}
 const select=document.getElementById(prefix+'ApprovedQaId');
 const selected=select.value || state.selected;
 const query=document.getElementById(prefix+'QaSearch').value.trim().toLowerCase();
 select.replaceChildren(new Option('Select Approved Q&A',''));
 for(const record of state.records){
  const searchable=[record.question,record.qa_id,...(record.aliases||[]),...(record.source_aliases||[])].join(' ').toLowerCase();
  if(!query || searchable.includes(query) || record.qa_id===selected)select.add(new Option(record.question+' — '+record.qa_id,record.qa_id));
 }
 if(selected && !state.records.some(r=>r.qa_id===selected)){
  const option=new Option(selected+' — unavailable (choose an effective QA)',selected); option.disabled=true;select.add(option);
 }
 select.value=selected;previewSourceQa(prefix);
}
function previewSourceQa(prefix) {
 const state=sourceResponseState[prefix]; if(!state)return;
 if(sourceQaMultiple(prefix)){document.getElementById(prefix+'QaPreview').replaceChildren();return;}
 const select=document.getElementById(prefix+'ApprovedQaId'); state.selected=select.value || state.selected;
 const box=document.getElementById(prefix+'QaPreview'); box.replaceChildren();
 const record=state.records.find(r=>r.qa_id===select.value);
 if(!record){box.textContent=state.selected ? 'This QA is not currently effective. Select an active Approved Q&A before saving.' : 'Choose an Approved Q&A to preview.';return;}
 const title=document.createElement('strong');title.textContent=record.question+' ('+record.qa_id+')';box.append(title);
 const answer=document.createElement('p');answer.textContent=record.answer;box.append(answer);
 const count=document.createElement('p');count.textContent='Accepted question variations: '+record.variation_count;box.append(count);
 const label=document.createElement('strong');label.textContent='Ordered response assets / links';box.append(label);
 const list=document.createElement('ol');for(const block of record.ordered_blocks || record.blocks || []){
  const item=document.createElement('li');item.textContent=block.kind==='TEXT' ? 'Approved text' : block.kind==='LINK' ? block.url : block.kind+': '+block.value;list.append(item);
 }box.append(list);
}
function changeStoryQaContext(prefix='igStory'){const state=sourceResponseState[prefix];if(!state)return;state.context=document.getElementById(prefix+'QaContext').value;renderStoryQa(prefix);}
// 21 Oct: the checklist was "question — qa_id" rows with identical preview
// links; each answer is now a card (question, what it answers, first line of
// the answer), ticked answers show as chips on top, General is folded away.
const SOURCE_QA_ROLE_LABELS={price:'Price',details:'Details',booking:'Booking',location:'Location',date:'Date'};
function sourceQaCard(prefix,state,record){
 const card=document.createElement('label');card.className='source-qa-card';const ticked=state.selectedIds.has(record.qa_id);
 card.style.cssText='display:flex;gap:12px;align-items:flex-start;padding:12px;margin:8px 0;border:1px solid var(--border);border-radius:10px;cursor:pointer;background:'+(ticked?'var(--surface-2, rgba(0,0,0,.04))':'transparent');
 const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=record.qa_id;checkbox.checked=ticked;checkbox.style.cssText='width:20px;height:20px;margin-top:2px;flex:none';
 checkbox.onchange=()=>{if(checkbox.checked)state.selectedIds.add(record.qa_id);else state.selectedIds.delete(record.qa_id);renderStoryQa(prefix);};
 const body=document.createElement('div');body.style.cssText='flex:1;min-width:0';
 const title=document.createElement('div');title.style.cssText='font-weight:600;font-size:1rem';title.textContent=record.question;
 const role=SOURCE_QA_ROLE_LABELS[record.mapped_role];
 if(role){const tag=document.createElement('span');tag.textContent=role;tag.style.cssText='margin-left:8px;padding:1px 8px;border-radius:999px;font-size:.75rem;font-weight:500;border:1px solid var(--border)';title.append(tag);}
 // Skip the greeting most answers open with; the next line tells them apart.
 const lines=String(record.answer||'').split(/\n+/).map(line=>line.trim()).filter(Boolean);
 const firstLine=lines.find(line=>!/^(?:hi|hello|hey|thank|thanks|namaste|greetings)\b/i.test(line))||lines[0]||'';
 const snippet=document.createElement('div');snippet.style.cssText='opacity:.75;font-size:.9rem;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';snippet.textContent=firstLine;
 const details=document.createElement('details');details.style.marginTop='4px';details.onclick=event=>event.stopPropagation();details.open=Boolean(state.openAnswers?.[record.qa_id]);details.ontoggle=()=>{if(state.openAnswers)state.openAnswers[record.qa_id]=details.open;};
 const summary=document.createElement('summary');summary.textContent='View full answer';summary.style.fontSize='.85rem';
 const answer=document.createElement('p');answer.style.whiteSpace='pre-wrap';answer.textContent=record.answer;
 const id=document.createElement('small');id.style.opacity='.6';id.textContent='ID: '+record.qa_id;
 details.append(summary,answer,id);body.append(title,snippet,details);card.append(checkbox,body);return card;
}
function renderStoryQa(prefix='igStory'){
 const state=sourceResponseState[prefix];if(!state)return;
 const box=document.getElementById(prefix+'QaChecklist');box.replaceChildren();box.style.maxHeight='none';state.openGroups=state.openGroups||{};state.openAnswers=state.openAnswers||{};
 document.getElementById(prefix+'QaCount').textContent='Selected: '+state.selectedIds.size;
 const query=document.getElementById(prefix+'QaSearch').value.trim().toLowerCase();
 const everywhere=r=>r.context==='general'||r.shared;
 const eligible=state.records.filter(r=>r.context===state.context||everywhere(r));
 const byId=Object.fromEntries(state.records.map(r=>[r.qa_id,r]));
 const chips=document.createElement('div');chips.style.cssText='display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px';
 for(const key of state.selectedIds){const record=byId[key];if(!record||!eligible.includes(record))continue;
  const chip=document.createElement('button');chip.type='button';chip.className='btn-secondary';chip.style.cssText='padding:4px 10px;font-size:.85rem';
  chip.textContent=record.question+'  ×';chip.title='Remove';chip.onclick=()=>{state.selectedIds.delete(key);renderStoryQa(prefix);};chips.append(chip);}
 if(!chips.childElementCount){const none=document.createElement('p');none.style.cssText='opacity:.7;margin:6px 0 10px';none.textContent='No answers ticked yet. Tick the answers this post should give.';chips.append(none);}
 box.append(chips);
 const label=value=>String((state.contexts||[]).find(o=>o.value===value)?.label||value).replace(/ \(existing\)$/,'').replace(/^[a-z_]+$/,t=>t.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase()));
 // 23 Oct: answers usable on any post (e.g. Savoury price/details) are listed
 // under their own category, not inside General.
 const sharedOther=eligible.filter(r=>r.shared&&r.context!=='general'&&r.context!==state.context);
 const sharedGroups=[...new Set(sharedOther.map(r=>r.context))].map(value=>[label(value)+' — usable on any post',sharedOther.filter(r=>r.context===value),false]);
 const groups=(state.context==='general'
  ? [['General',eligible.filter(r=>r.context==='general'),true]]
  : [[label(state.context)+' answers',eligible.filter(r=>r.context===state.context),true],
     ['General — add only if this post needs them',eligible.filter(r=>r.context==='general'),false]]).concat(sharedGroups);
 for(const [title,records,open] of groups){
  const shown=records.filter(record=>!query||[record.question,record.qa_id,...(record.aliases||[]),...(record.source_aliases||[])].join(' ').toLowerCase().includes(query));
  // Ticking re-renders the list; groups the admin opened or closed stay that way.
  const group=document.createElement('details');const remembered=state.openGroups[title];group.open=Boolean(query)||(remembered===undefined?(open||records.some(r=>state.selectedIds.has(r.qa_id))):remembered);group.ontoggle=()=>{if(!query)state.openGroups[title]=group.open;};group.style.margin='6px 0';
  const summary=document.createElement('summary');summary.style.cssText='font-weight:600;cursor:pointer;padding:4px 0';
  summary.textContent=title+' ('+records.filter(r=>state.selectedIds.has(r.qa_id)).length+' of '+records.length+' ticked)';group.append(summary);
  for(const record of shown)group.append(sourceQaCard(prefix,state,record));
  if(!shown.length){const empty=document.createElement('p');empty.style.opacity='.7';empty.textContent=query?'No answers match the search.':'No answers in this category yet.';group.append(empty);}
  box.append(group);
 }
 for(const key of state.selectedIds){if(eligible.some(r=>r.qa_id===key))continue;
  const warning=document.createElement('p');warning.textContent=key+' — unavailable/expired or context changed. Remove it and choose a current Q&A before saving.';
  const remove=document.createElement('button');remove.type='button';remove.textContent='Remove selection';remove.onclick=()=>{state.selectedIds.delete(key);renderStoryQa(prefix);};warning.append(remove);box.append(warning);
 }
 const table=document.createElement('div');table.id=prefix+'AnswerTable';table.style.marginTop='14px';box.append(table);refreshAnswerTable(prefix);
}
// A Custom reply that promises a menu with none selected sent the text alone
// to customers (19 Oct, sondesh posts and stories).
const SOURCE_NOTE_FIELDS={ig:['igSourceDescription','igMenuPicker','igTopicPicker'],igStory:['igStorySourceDescription','igStoryMenuPicker','igStoryTopicPicker'],igMedia:['igMediaDescription','igMediaMenu','igMediaTopic']};
function sourceNoteMenuMissing(prefix){
 if(sourceResponseFields(prefix).response_mode!=='CUSTOM')return false;
 const [noteId,menuId,topicId]=SOURCE_NOTE_FIELDS[prefix]||[];
 if(!/\bmenu\b/i.test(document.getElementById(noteId)?.value||''))return false;
 const menu=document.getElementById(menuId)?.value||'__none__';
 if(menu==='__none__'||menu==='')return true;
 if(menu==='__inherit__'){const topics=typeof igTopics!=='undefined'?igTopics:[];const topic=topics.find(t=>t.topic_id===document.getElementById(topicId)?.value);return !(topic&&topic.default_menu_id);}
 return false;
}
function confirmSourceNoteMenu(prefix){return !sourceNoteMenuMissing(prefix)||confirm('This custom reply mentions a menu, but no menu is selected, so customers will get the text without a menu.\n\nSave anyway?');}
// What common ad questions get from the ticked answers (18 Oct): shows the
// result before saving, and the questions two ticked answers both claim.
async function refreshAnswerTable(prefix){
 const state=sourceResponseState[prefix];const box=document.getElementById(prefix+'AnswerTable');if(!state||!box)return;
 const ids=Array.from(state.selectedIds);const stamp=ids.join(',')+'|'+state.context;state.previewStamp=stamp;
 if(!ids.length){box.textContent='Tick answers to see what customers will get.';return;}
 try{
  const response=await fetch('/admin/instagram-routes/answer-preview',{method:'POST',headers:{...getAuthHeaders(),'Content-Type':'application/json'},body:JSON.stringify({approved_qa_ids:ids,context:state.context})});
  const data=await response.json();if(state.previewStamp!==stamp)return;if(!response.ok)throw new Error(data.detail||'Preview unavailable');
  const rows=data.rows.map(row=>`<tr><td style="padding:4px 8px">${escapeHtml(row.question)}</td><td style="padding:4px 8px">${row.answer?'✅ '+escapeHtml(row.answer):'⚠ Not from these answers (bot rules or manager)'}</td></tr>`).join('');
  const pairs={};for(const c of data.collisions){const key=c.answers.join(' and ');(pairs[key]=pairs[key]||[]).push(c.question);}
  const clash=Object.entries(pairs).map(([answers,questions])=>`<li>${answers.split(' and ').map(a=>'“'+escapeHtml(a)+'”').join(' and ')} both answer “${escapeHtml(questions[0])}”${questions.length>1?' and '+(questions.length-1)+' more':''} — untick one of them.</li>`).join('');
  box.innerHTML=`<b>What customers on this post will get</b><table style="border-collapse:collapse;margin-top:6px">${rows}</table>`+(clash?`<div role="alert" style="margin-top:8px;color:var(--danger)"><b>Fix before saving:</b><ul>${clash}</ul></div>`:'');
 }catch(error){if(state.previewStamp===stamp)box.textContent=error.message;}
}
</script>""".replace("__SELECTOR__", json.dumps(SELECTOR_TEMPLATE)).replace("__STORY_SELECTOR__", json.dumps(selector_html('igStory'))).replace("__MEDIA_SELECTOR__", json.dumps(selector_html('igMedia'))).replace("__POST_SELECTOR__", json.dumps(selector_html('ig')))
