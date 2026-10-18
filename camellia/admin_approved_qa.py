"""Dedicated approved authority UI and guarded dry-run/publication routes."""
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from camellia import approved_qa
from camellia import admin_chatbot_control


def create_router(require_admin, actor):
    router = APIRouter(prefix="/admin/approved-qa")

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "Approved Q&A or version not found") from exc
        except Exception as exc:
            logging.getLogger(__name__).exception("Approved Q&A operation failed")
            raise HTTPException(503, "Approved Q&A storage unavailable; published content was preserved") from exc

    async def body(request):
        try:
            value = await request.json()
        except Exception as exc:
            raise HTTPException(400, "Invalid JSON body") from exc
        if not isinstance(value, dict):
            raise HTTPException(400, "Expected a JSON object")
        return value

    @router.get("")
    def listing(x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        return call(approved_qa.listing)

    @router.post("/test")
    async def test(request: Request, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        data = await body(request)
        message = data.get("message")
        if not isinstance(message, str) or len(message) > 4000:
            raise HTTPException(400, "Enter a test question")
        return call(approved_qa.test, message)

    @router.post("/preview")
    async def preview(request: Request, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        return call(approved_qa.preview, await body(request))

    @router.post("/publish")
    async def publish(request: Request, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        return call(approved_qa.publish, await body(request), actor())

    @router.post("/contexts")
    async def add_context(request: Request, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        return call(approved_qa.save_context, (await body(request)).get("label"), actor())

    @router.patch("/contexts/{value}")
    async def rename_context(value: str, request: Request, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        return call(approved_qa.save_context, (await body(request)).get("label"), actor(), value)

    @router.delete("/contexts/{value}")
    def delete_context(value: str, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        return call(approved_qa.delete_context, value)

    @router.get("/{qa_id}")
    def detail(qa_id: str, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        return call(approved_qa.detail, qa_id)

    @router.post("/{qa_id}/archive")
    def archive(qa_id: str, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        return call(approved_qa.archive_or_restore, qa_id, actor())

    @router.post("/{qa_id}/restore")
    def restore(qa_id: str, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        return call(approved_qa.archive_or_restore, qa_id, actor(), restore=True)

    @router.post("/{qa_id}/restore/{version_id}")
    def restore_version(qa_id: str, version_id: int, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        return call(approved_qa.archive_or_restore, qa_id, actor(), restore=True, version_id=version_id)

    # QA-C1 deliberately reuses the already-mounted Admin router bridge so the
    # customer-facing app module does not need another route-registration edit.
    # These routes keep their own /admin/chatbot-control prefix.
    router.routes.extend(admin_chatbot_control.create_router(require_admin, actor).routes)
    return router


HTML = r'''
<div id="tab-approved-qa" class="panel-card" data-group="approved_qa" style="display:none;">
 <div class="admin-page-heading"><div><h2>Approved Q&amp;A</h2><p>Only client-approved answers are managed here. Preview your changes before publishing.</p></div><button class="btn-secondary" onclick="aqEdit()">+ Add Approved Q&amp;A</button></div>
 <div id="aqMessage" role="status" class="result-box" style="display:none;"></div>
 <div id="aqCounts" class="admin-kpi-grid"></div>
 <div style="display:flex;gap:10px;flex-wrap:wrap;margin:16px 0;">
  <input id="aqSearch" aria-label="Search approved questions and answers" placeholder="Search Approved Q&A" oninput="aqRender()" class="field-control">
  <button class="btn-secondary" onclick="aqFilter('ACTIVE')">Active</button><button class="btn-secondary" onclick="aqFilter('SCHEDULED')">Scheduled</button><button class="btn-secondary" onclick="aqFilter('EXPIRING_SOON')">Expiring Soon</button><button class="btn-secondary" onclick="aqFilter('ARCHIVED')">Archived / Expired</button>
 </div>
 <p id="aqFilterLabel">Active</p><div id="aqList"></div>
 <details style="margin-top:18px"><summary>Test a customer question (nothing is sent)</summary><label for="aqTestQuestion">Customer wording</label><input id="aqTestQuestion" class="field-control"><button class="btn-secondary" onclick="aqTest()">Test</button><pre id="aqTestResult" style="white-space:pre-wrap"></pre></details>
 <dialog id="aqDialog" style="position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);margin:0;width:min(800px,95vw);max-height:90vh;overflow:auto;border:1px solid #ddd;border-radius:12px;padding:24px;">
  <button class="btn-secondary" style="float:right" onclick="document.getElementById('aqDialog').close()">Close</button><div id="aqDialogContent"></div>
 </dialog>
 <dialog id="aqContextDialog" style="position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);margin:0;width:min(520px,95vw);max-height:85vh;overflow:auto;border:1px solid #ddd;border-radius:12px;padding:24px;">
  <button class="btn-secondary" style="float:right" onclick="document.getElementById('aqContextDialog').close()">Close</button>
  <h3>Categories</h3><p style="opacity:.75">Group answers by what a post or ad is about. A mapping can only use answers from its own category and General.</p>
  <div id="aqContextList"></div>
  <label for="aqNewContext" style="display:block;margin-top:14px">New category</label>
  <div style="display:flex;gap:8px"><input id="aqNewContext" class="field-control" maxlength="60" placeholder="e.g. Kolkata Bengali Home Table"><button type="button" class="btn-secondary" onclick="aqAddContext()">Add</button></div>
  <p id="aqContextNote" role="status" style="margin-top:10px"></p>
 </dialog>
</div>
'''

SCRIPT = r'''
<script>
let aqRoles = [], aqContexts = [], aqRecords = [], aqStatus = 'ACTIVE', aqDraft = null, aqToken = null, aqCreating = false, aqSequence = [], aqAssets = [], aqHidden = [];
function aqMessage(text, error=false) { const el=document.getElementById('aqMessage');el.style.display='block';el.className='result-box '+(error?'error':'success');el.textContent=text; }
async function aqApi(path='', method='GET', payload) {
 const response=await fetch('/admin/approved-qa'+path,{method,headers:{...getAuthHeaders(),'Content-Type':'application/json'},...(payload?{body:JSON.stringify(payload)}:{}),cache:'no-store'});
 const data=await response.json();if(!response.ok)throw new Error(data.detail||'Approved Q&A request failed');return data;
}
function aqDate(value) { return value?new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:true}).format(new Date(value))+' IST':'No limit'; }
function aqLocal(value) { if(!value)return '';const date=new Date(value);return new Date(date.getTime()+330*60000).toISOString().slice(0,19); }
function aqVariations(record) { return [...new Set([...(record.source_aliases||[]),...(record.aliases||[])].filter(Boolean))]; }
function aqVariationList(record) {
 const variations=aqVariations(record);
 if(!variations.length)return '<p style="margin:8px 0;opacity:.72">No additional question variations.</p>';
 return `<ol style="margin:8px 0 8px 22px">${variations.map(v=>`<li style="margin:4px 0">${escapeHtml(v)}</li>`).join('')}</ol>`;
}
async function loadApprovedQa() { if(!adminAuthenticated)return;try { const data=await aqApi();aqRecords=data.records;aqContexts=data.contexts||[];aqRoles=data.mapped_roles||[];document.getElementById('aqCounts').innerHTML=Object.entries(data.counts).map(([key,count])=>`<div class="admin-kpi"><div class="admin-kpi-label">${escapeHtml(key.replaceAll('_',' '))}</div><div class="admin-kpi-value">${count}</div></div>`).join('');aqRender(); }catch(e){aqMessage(e.message,true);} }
function aqFilter(value) { aqStatus=value;document.getElementById('aqFilterLabel').textContent=value==='ARCHIVED'?'Archived / Expired':value.replaceAll('_',' ');aqRender(); }
function aqRender() {
 const search=document.getElementById('aqSearch').value.toLowerCase();
 const rows=aqRecords.filter(r=>(aqStatus==='EXPIRING_SOON'?r.expiring_soon:aqStatus==='ARCHIVED'?['ARCHIVED','EXPIRED'].includes(r.status):r.status===aqStatus)&&[r.question,r.answer,r.context,...(r.source_aliases||[]),...(r.aliases||[])].join(' ').toLowerCase().includes(search));
 document.getElementById('aqList').innerHTML=rows.map(r=>{const variations=aqVariations(r);return `<article style="border:1px solid var(--border);padding:18px;border-radius:10px;margin-bottom:12px"><h3>${escapeHtml(r.question)}</h3><p style="white-space:pre-wrap">${escapeHtml(r.answer.slice(0,240))}${r.answer.length>240?'…':''}</p><p>${escapeHtml(r.context)} · ${escapeHtml(r.status)}</p><details style="margin:8px 0"><summary><strong>${variations.length} accepted question variation${variations.length===1?'':'s'}</strong> — click to view</summary>${aqVariationList(r)}</details><p>Valid from: ${aqDate(r.valid_from)}<br>Valid until: ${aqDate(r.valid_until)}</p><button class="btn-secondary" data-aq-action="view" data-aq-id="${escapeHtml(r.qa_id)}">View</button> <button class="btn-secondary" data-aq-action="edit" data-aq-id="${escapeHtml(r.qa_id)}">Edit</button> <button class="btn-secondary" data-aq-action="archive" data-aq-id="${escapeHtml(r.qa_id)}">Delete</button>${r.status==='ARCHIVED'?` <button class="btn-secondary" data-aq-action="restore" data-aq-id="${escapeHtml(r.qa_id)}">Restore</button>`:''}</article>`;}).join('')||'<p>No approved Q&A in this view.</p>';
 document.querySelectorAll('[data-aq-action]').forEach(button=>button.onclick=()=>{const id=button.dataset.aqId;({view:aqView,edit:aqEdit,archive:aqArchive,restore:aqRestore})[button.dataset.aqAction](id);});
}
async function aqView(id) { try { const r=await aqApi('/'+encodeURIComponent(id));document.getElementById('aqDialogContent').innerHTML=`<h3>${escapeHtml(r.question)}</h3><pre style="white-space:pre-wrap">${escapeHtml(r.answer)}</pre><p>Context: ${escapeHtml(r.context)} · ${escapeHtml(r.status)}</p><p>From ${aqDate(r.valid_from)}<br>Until ${aqDate(r.valid_until)}</p><h4>Accepted question variations</h4>${aqVariationList(r)}<details><summary>Read-only assets &amp; metadata</summary><pre style="white-space:pre-wrap">${escapeHtml(JSON.stringify({qa_id:r.qa_id,assets:r.asset_keys,created_at:r.created_at,created_by:r.created_by,updated_at:r.updated_at,updated_by:r.updated_by},null,2))}</pre></details><h4>Version history</h4>${r.versions.map(v=>`<details><summary>Version ${v.id} · ${aqDate(v.changed_at)} · ${escapeHtml(v.changed_by)} · ${escapeHtml(v.change_reason)}</summary><pre style="white-space:pre-wrap">${escapeHtml(JSON.stringify(v.effective_snapshot,null,2))}</pre><button class="btn-secondary" data-aq-version="${v.id}">Restore this version</button></details>`).join('')||'<p>Canonical source; no Admin changes.</p>'}`;document.querySelectorAll('[data-aq-version]').forEach(b=>b.onclick=()=>aqRestore(id,b.dataset.aqVersion));document.getElementById('aqDialog').showModal(); }catch(e){aqMessage(e.message,true);} }
function aqEdit(id) {
 aqDraft=id?aqRecords.find(r=>r.qa_id===id):null;aqToken=null;aqCreating=!id;
 aqSequence=JSON.parse(JSON.stringify(aqDraft?.sequence||[]));aqHidden=[...(aqDraft?.hidden_aliases||[])];aqLoadAssets();
 document.getElementById('aqDialogContent').innerHTML='<h3>'+ (id?'Edit':'Add')+' Approved Q&amp;A</h3><form id="aqForm" onsubmit="event.preventDefault();aqPreview()"><label for="aqQuestion">Question *</label><textarea id="aqQuestion" required class="field-control"></textarea><label for="aqAnswer">Approved answer *</label><textarea id="aqAnswer" required rows="9" class="field-control"></textarea><section aria-label="Response Sequence / Menu &amp; Images" style="margin-top:12px"><b>Response Sequence / Menu &amp; Images (optional)</b><p class="source-map-help">The approved answer above is sent first, then these blocks in the order shown, each as its own message. Menu images come from the current menu catalogue.</p><button type="button" class="btn-secondary" onclick="aqAddBlock(\'text\')">+ Add Text Block</button> <button type="button" class="btn-secondary" onclick="aqAddBlock(\'menu\')">+ Add Menu / Image</button><div id="aqSequenceBlocks"></div></section><label for="aqContext">Category / context</label><div style="display:flex;gap:8px"><select id="aqContext" required class="field-control"></select><button type="button" class="btn-secondary" onclick="aqManageContexts()">Manage</button></div><label for="aqMappedRole">On mapped posts/ads, use for</label><select id="aqMappedRole" class="field-control" onchange="aqRoleChanged()"></select><p class="source-map-help">Only on posts, reels and ads where this answer is ticked. Customers with no ad never get these, so the same question can get a different answer on each post.</p><label for="aqMappedQuestions">Customer questions it answers there (one per line)</label><textarea id="aqMappedQuestions" rows="5" class="field-control"></textarea><div style="margin-top:12px"><strong>Built-in question variations</strong><div id="aqBuiltIn"></div></div><label for="aqAliases">Additional Admin question variations (one per line)</label><textarea id="aqAliases" rows="3" class="field-control"></textarea><button type="button" class="btn-secondary" onclick="aqAddVariation()">+ Add variation</button><label for="aqFrom">Valid From (Asia/Kolkata)</label><input id="aqFrom" type="datetime-local" step="1" class="field-control"><label for="aqUntil">Valid Until (Asia/Kolkata)</label><input id="aqUntil" type="datetime-local" step="1" class="field-control"><p>Asset references stay read-only.</p><button class="btn-secondary" type="submit">Preview / Test</button> <button id="aqPublishButton" class="btn-secondary" type="button" onclick="aqPublish()">Preview Required Before Publish</button><div id="aqPreviewResult" role="status" style="margin-top:12px"></div></form>';
 const contextSelect=document.getElementById('aqContext');contextSelect.replaceChildren(...aqContexts.map(option=>new Option(option.label,option.value)));
 const roleSelect=document.getElementById('aqMappedRole');roleSelect.replaceChildren(new Option('Nothing extra','none'),...aqRoles.map(role=>new Option(role.label,role.value)));roleSelect.value=aqDraft?.mapped_role||'none';document.getElementById('aqMappedQuestions').value=(aqDraft?.mapped_questions||[]).join('\n');
 const r=aqDraft||{};document.getElementById('aqQuestion').value=r.question||'';document.getElementById('aqAnswer').value=r.answer||'';document.getElementById('aqContext').value=r.context||'general';document.getElementById('aqAliases').value=(r.aliases||[]).join('\n');document.getElementById('aqFrom').value=aqLocal(r.valid_from);document.getElementById('aqUntil').value=aqLocal(r.valid_until);
 aqRenderBuiltIn();aqRenderBlocks();document.getElementById('aqForm').oninput=()=>aqMarkDirty();document.getElementById('aqDialog').showModal();
}
function aqMarkDirty() {
 aqToken=null;
 const button=document.getElementById('aqPublishButton');if(button){button.disabled=false;button.textContent='Preview Required Before Publish';}
 const result=document.getElementById('aqPreviewResult');if(result)result.innerHTML='<p><strong>Changes detected.</strong> Preview/Test is required before publishing. If you click the publish button now, the preview will refresh first.</p>';
}
function aqPayload() { return {create:aqCreating,...(aqDraft?.qa_id?{qa_id:aqDraft.qa_id}:{}),question:document.getElementById('aqQuestion').value,answer:document.getElementById('aqAnswer').value,context:document.getElementById('aqContext').value,aliases:document.getElementById('aqAliases').value.split('\n').map(v=>v.trim()).filter(Boolean),valid_from:document.getElementById('aqFrom').value||null,valid_until:document.getElementById('aqUntil').value||null,blocks:aqSequence,hidden_aliases:aqHidden,mapped_role:document.getElementById('aqMappedRole').value,mapped_questions:document.getElementById('aqMappedQuestions').value.split('\n').map(v=>v.trim()).filter(Boolean)}; }
async function aqPreview() { try { const data=await aqApi('/preview','POST',aqPayload());aqDraft={...(aqDraft||{}),qa_id:data.qa_id};aqToken=data.preview_token;const button=document.getElementById('aqPublishButton');button.disabled=false;button.textContent='Publish Approved Q&A';document.getElementById('aqPreviewResult').innerHTML=`<p><strong>Preview ready.</strong> Review the proposed validity and answer below, then click Publish Approved Q&amp;A.</p><h4>Current approved answer</h4><pre style="white-space:pre-wrap">${escapeHtml(data.current?.answer||'New Q&A')}</pre><h4>Proposed approved answer</h4><pre style="white-space:pre-wrap">${escapeHtml(data.proposed.answer)}</pre><p>${escapeHtml(data.proposed.status)} · From ${aqDate(data.proposed.valid_from)} · Until ${aqDate(data.proposed.valid_until)}</p><h4>Test result</h4><pre style="white-space:pre-wrap">${escapeHtml(JSON.stringify(data.test,null,2))}</pre>`;return true; }catch(e){aqToken=null;const button=document.getElementById('aqPublishButton');button.disabled=false;button.textContent='Preview Required Before Publish';document.getElementById('aqPreviewResult').textContent=e.message;return false;} }
async function aqPublish() { if(!aqToken){await aqPreview();return;}try {await aqApi('/publish','POST',{...aqPayload(),preview_token:aqToken});document.getElementById('aqDialog').close();aqMessage('Approved Q&A published.');await loadApprovedQa();}catch(e){document.getElementById('aqPreviewResult').textContent=e.message;aqToken=null;const button=document.getElementById('aqPublishButton');button.disabled=false;button.textContent='Preview Required Before Publish';} }
async function aqArchive(id) { if(!confirm('Delete this approved Q&A from customer matching? It will be archived with history preserved.'))return;try{await aqApi('/'+encodeURIComponent(id)+'/archive','POST');aqMessage('Q&A archived. Restore is available in history.');await loadApprovedQa();}catch(e){aqMessage(e.message,true);} }
async function aqRestore(id,version) { if(!confirm('Restore this approved Q&A? Its validity and conflicts will be checked.'))return;try{await aqApi('/'+encodeURIComponent(id)+'/restore'+(version?'/'+version:''),'POST');document.getElementById('aqDialog').close();aqMessage('Approved Q&A restored.');await loadApprovedQa();}catch(e){aqMessage(e.message,true);} }
// Reply sequence and built-in variations, editable like Custom Q&A (18 Oct).
// Choosing a use fills the usual customer questions; the client edits freely.
function aqRoleChanged() { const role=aqRoles.find(item=>item.value===document.getElementById('aqMappedRole').value);document.getElementById('aqMappedQuestions').value=(role?.questions||[]).join('\n');aqMarkDirty(); }
function aqAll(root,selector) { return root.querySelectorAll?[...root.querySelectorAll(selector)]:[]; }
async function aqLoadAssets() { try{const response=await fetch('/admin/custom-qa/assets',{headers:getAuthHeaders()});aqAssets=response.ok?((await response.json()).assets||[]):[];}catch(e){aqAssets=[];}aqRenderBlocks(); }
function aqAddBlock(type) { aqSequence.push(type==='text'?{type,text:''}:{type,menu_id:''});aqRenderBlocks();aqMarkDirty(); }
function aqMoveBlock(index,step) { const target=index+step;if(target<0||target>=aqSequence.length)return;[aqSequence[index],aqSequence[target]]=[aqSequence[target],aqSequence[index]];aqRenderBlocks();aqMarkDirty(); }
function aqDeleteBlock(index) { aqSequence.splice(index,1);aqRenderBlocks();aqMarkDirty(); }
function aqRenderBlocks() {
 const box=document.getElementById('aqSequenceBlocks');if(!box)return;
 box.innerHTML=aqSequence.map((block,index)=>`<div style="border:1px solid var(--border);padding:10px;margin-top:8px"><label class="field-label">${index+1}. ${block.type==='text'?'Text Block':'Menu / Image'}</label>${block.type==='text'?`<textarea class="field-control" aria-label="Text block ${index+1}" data-aq-block="${index}">${escapeHtml(block.text)}</textarea>`:`<select class="field-control" aria-label="Menu block ${index+1}" data-aq-block="${index}"><option value="">Select menu / image</option>${!aqAssets.some(asset=>asset.menu_id===block.menu_id)&&block.menu_id?`<option selected value="${escapeHtml(block.menu_id)}">${escapeHtml(block.menu_id)} — unavailable</option>`:''}${aqAssets.map(asset=>`<option value="${escapeHtml(asset.menu_id)}" ${asset.menu_id===block.menu_id?'selected':''}>${escapeHtml(asset.name)}</option>`).join('')}</select>`}<button type="button" class="btn-secondary" data-aq-move="${index}" data-step="-1" ${index===0?'disabled':''}>Move Up</button> <button type="button" class="btn-secondary" data-aq-move="${index}" data-step="1" ${index===aqSequence.length-1?'disabled':''}>Move Down</button> <button type="button" class="btn-danger" data-aq-delete-block="${index}">Delete Block</button></div>`).join('');
 aqAll(box,'[data-aq-block]').forEach(field=>field.oninput=field.onchange=()=>{const block=aqSequence[field.dataset.aqBlock];if(block.type==='text')block.text=field.value;else block.menu_id=field.value;});
 aqAll(box,'[data-aq-move]').forEach(button=>button.onclick=()=>aqMoveBlock(Number(button.dataset.aqMove),Number(button.dataset.step)));
 aqAll(box,'[data-aq-delete-block]').forEach(button=>button.onclick=()=>aqDeleteBlock(Number(button.dataset.aqDeleteBlock)));
}
function aqRenderBuiltIn() {
 const box=document.getElementById('aqBuiltIn');if(!box)return;
 const active=(aqDraft?.source_aliases||[]).concat(aqDraft?.hidden_aliases||[]).filter(v=>!aqHidden.includes(v));
 box.innerHTML=(active.length?`<ol style="margin:8px 0 8px 22px">${active.map(v=>`<li style="margin:4px 0">${escapeHtml(v)} <button type="button" class="btn-secondary" data-aq-edit-variation="${escapeHtml(v)}">Edit</button> <button type="button" class="btn-secondary" data-aq-hide-variation="${escapeHtml(v)}">Delete</button></li>`).join('')}</ol>`:'<p style="opacity:.72">None for this Q&amp;A.</p>')+(aqHidden.length?`<p style="margin:8px 0 4px"><b>Removed</b> — these exact wordings no longer get this answer. Similar sentences may still match through the built-in rules; use Test to check.</p><ol style="margin:4px 0 8px 22px">${aqHidden.map(v=>`<li><s>${escapeHtml(v)}</s> <button type="button" class="btn-secondary" data-aq-restore-variation="${escapeHtml(v)}">Restore</button></li>`).join('')}</ol>`:'');
 aqAll(box,'[data-aq-hide-variation]').forEach(button=>button.onclick=()=>{aqHidden.push(button.dataset.aqHideVariation);aqRenderBuiltIn();aqMarkDirty();});
 aqAll(box,'[data-aq-restore-variation]').forEach(button=>button.onclick=()=>{aqHidden=aqHidden.filter(v=>v!==button.dataset.aqRestoreVariation);aqRenderBuiltIn();aqMarkDirty();});
 aqAll(box,'[data-aq-edit-variation]').forEach(button=>button.onclick=()=>{const old=button.dataset.aqEditVariation;const wording=prompt('Edit this question variation',old);if(wording===null||!wording.trim()||wording.trim()===old)return;aqHidden.push(old);aqAppendVariation(wording.trim());aqRenderBuiltIn();});
}
function aqAppendVariation(wording) { const field=document.getElementById('aqAliases');field.value=(field.value.trim()?field.value.trim()+'\n':'')+wording;aqMarkDirty(); }
function aqAddVariation() { const wording=prompt('New question variation');if(wording&&wording.trim())aqAppendVariation(wording.trim()); }
// Client-added categories (18 Oct, Kolkata vs Gurgaon Home Table); built-in ones stay fixed.
function aqContextOptions() { const select=document.getElementById('aqContext');if(!select)return;const current=select.value;select.replaceChildren(...aqContexts.map(option=>new Option(option.label,option.value)));if(aqContexts.some(option=>option.value===current))select.value=current; }
async function aqReloadContexts() { const data=await aqApi();aqContexts=data.contexts||[];aqRoles=data.mapped_roles||[];aqContextOptions();aqRenderContexts(); }
function aqManageContexts() { aqContextNote('');aqRenderContexts();document.getElementById('aqContextDialog').showModal(); }
function aqContextNote(text,error=false) { const el=document.getElementById('aqContextNote');el.textContent=text;el.style.color=error?'var(--danger)':''; }
function aqRenderContexts() {
 const list=document.getElementById('aqContextList');
 list.innerHTML=aqContexts.filter(option=>!option.legacy).map(option=>`<div style="display:flex;gap:8px;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)"><span>${escapeHtml(option.label)}</span>${option.custom?`<span><button type="button" class="btn-secondary" data-ctx-rename="${escapeHtml(option.value)}">Edit</button> <button type="button" class="btn-secondary" data-ctx-delete="${escapeHtml(option.value)}">Delete</button></span>`:'<small style="opacity:.7">Built-in</small>'}</div>`).join('');
 list.querySelectorAll('[data-ctx-rename]').forEach(button=>button.onclick=()=>aqRenameContext(button.dataset.ctxRename));
 list.querySelectorAll('[data-ctx-delete]').forEach(button=>button.onclick=()=>aqDeleteContext(button.dataset.ctxDelete));
}
async function aqAddContext() { const input=document.getElementById('aqNewContext');try{const added=await aqApi('/contexts','POST',{label:input.value});input.value='';await aqReloadContexts();const select=document.getElementById('aqContext');if(select&&document.getElementById('aqForm')){select.value=added.value;aqMarkDirty();}aqContextNote('Category added.');}catch(e){aqContextNote(e.message,true);} }
async function aqRenameContext(value) { const current=aqContexts.find(option=>option.value===value);const label=prompt('New name for this category',current?.label||'');if(label===null)return;try{await aqApi('/contexts/'+encodeURIComponent(value),'PATCH',{label});await aqReloadContexts();aqContextNote('Category renamed.');}catch(e){aqContextNote(e.message,true);} }
async function aqDeleteContext(value) { if(!confirm('Delete this category?'))return;try{await aqApi('/contexts/'+encodeURIComponent(value),'DELETE');await aqReloadContexts();aqContextNote('Category deleted.');}catch(e){aqContextNote(e.message,true);} }
async function aqTest() { try{const result=await aqApi('/test','POST',{message:document.getElementById('aqTestQuestion').value});document.getElementById('aqTestResult').textContent=JSON.stringify(result,null,2);}catch(e){document.getElementById('aqTestResult').textContent=e.message;} }
</script>
'''

# The control card is global (inserted immediately below .admin-header), not a
# child of the Approved Q&A tab. Appending its script here only reuses the
# already-established Admin HTML injection point.
SCRIPT += admin_chatbot_control.SCRIPT
