/* REST adapter for the attached standalone editor. No framework required. */
(() => {
  'use strict';
  const editor = window.SOPStudio;
  const $ = id => document.getElementById(id);
  let current = null, dirty = false, changeCounter = 0, busy = false, searchTimer, listSequence = 0;
  let draftQueue = Promise.resolve(), operationEpoch = 0;
  let user = 'anonymous';
  try { user = localStorage.getItem('sop-studio-user') || user; } catch (_) {}
  $('libUser').value = user;
  const notice = (text, error=false) => { $('libNotice').textContent=text; $('libNotice').dataset.error=String(error); };
  async function api(method, path, body, options={}) {
    let response;
    try {
      response = await fetch(path, {method, headers:{'Content-Type':'application/json','X-User':encodeURIComponent(user)},
        ...(body === undefined ? {} : {body:JSON.stringify(body)}), ...options});
    } catch (cause) { throw new Error('서버에 연결하지 못했습니다. 연결 상태를 확인하세요.', {cause}); }
    const payload = await response.json();
    if (!response.ok) {
      const error = new Error(payload.error?.message || `요청 실패 (${response.status})`);
      Object.assign(error, payload.error || {}, {status:response.status}); throw error;
    }
    return payload;
  }
  const libStore = {
    list: () => api('GET', `/api/sops?status=${encodeURIComponent($('libStatus').value)}&q=${encodeURIComponent($('libSearch').value)}`),
    async get(no) {
      const d = await api('GET', `/api/sops/by-no/${encodeURIComponent(no)}`);
      return {sop_no:no, doc:d.content, version_no:d.version_no, id:d.id, lock:d.lock};
    },
    put: rec => api('PUT', `/api/sops/${encodeURIComponent(rec.sop_no)}`, {
      doc:rec.doc, base_version_no:rec.base_version_no ?? 0, saved_by:user, change_note:rec.change_note || ''}),
    async remove(no) { const d=await this.get(no); return api('DELETE', `/api/sops/${d.id}`); }
  };
  function errorMessage(error) {
    if (error.status===409) return `다른 사람이 v${error.current_version_no}를 저장했습니다. 원본 JSON으로 작업을 보관하고, 다시 불러온 뒤 저장하세요.`;
    if (error.status===413) return '문서 크기가 20MB 제한을 넘었습니다. 이미지 크기를 줄여주세요.';
    if (error.status===422) return `입력 내용을 확인하세요. ${error.details?.map(d=>d.message).join(' / ') || error.message}`;
    return error.message;
  }
  const fail = e => { notice(errorMessage(e),true); editor.toast(errorMessage(e)); };
  async function release(context=current, actor=user) {
    if (!context) return;
    try { await api('DELETE', `/api/sops/${context.id}/lock`, {user:actor}, {keepalive:true}); } catch (_) {}
  }
  async function heartbeat() {
    const context = current, actor = user;
    if (!context || busy) return;
    try {
      const lock = await api('POST', `/api/sops/${context.id}/lock`, {user:actor,ttl_sec:120});
      if (current!==context || user!==actor) return;
      $('libLock').textContent=`편집 잠금: ${lock.locked_by} · 30초마다 갱신`;
    } catch (e) {
      if (current!==context || user!==actor) return;
      $('libLock').textContent=e.status===423 ? `${e.locked_by}님이 편집 중입니다. 저장 시 버전 충돌을 확인합니다.` : '잠금 갱신 실패 · 서버 연결을 확인하세요.';
    }
  }
  function canReplace() { return !dirty || confirm('서버에 저장하지 않은 변경사항이 있습니다. 현재 문서를 바꿀까요?'); }
  function clearContext() { operationEpoch++; release(); current=null; $('libLock').textContent=''; }
  window.addEventListener('sop:restore', event => {
    clearContext();
    const context=event.detail?._server;
    if (context?.sop_no===event.detail?.sop?.id && Number.isInteger(context.version_no) && typeof context.id==='string') current={...context};
    dirty=true; changeCounter++;
  });
  window.addEventListener('sop:dirty', () => { dirty=true; changeCounter++; });
  window.addEventListener('sop:draft', event => {
    const context=current, content=event.detail, actor=user, epoch=operationEpoch;
    if (!context || busy || content.sop?.id!==context.sop_no) return;
    draftQueue=draftQueue.catch(()=>{}).then(async()=>{
      if (busy || epoch!==operationEpoch) return;
      try { await api('PUT', `/api/sops/${context.id}/draft`, {user:actor,content}); }
      catch(e) { notice(`서버 임시 저장 실패 · ${errorMessage(e)}`,true); }
    });
  });
  async function libOpenDocument(no) {
    if (busy || !canReplace()) return;
    busy=true;
    try {
      await draftQueue;
      const d=await libStore.get(no);
      editor.restore(d.doc); editor.clearDraftPrompt();
      current={id:d.id,sop_no:no,version_no:d.version_no}; dirty=false;
      try {
        const draft=await api('GET', `/api/sops/${d.id}/draft?user=${encodeURIComponent(user)}`);
        if (confirm(`${new Date(draft.updated_at).toLocaleString()} 임시 문서가 있습니다. 복구할까요?`)) {
          editor.restore(draft.content);
          // Older drafts must keep their original base; never silently rebase onto latest.
          current={id:d.id,sop_no:no,version_no:draft.content._server?.version_no ?? 0}; dirty=true;
        }
      } catch(e) { if(e.status!==404) notice(`임시 문서를 확인하지 못했습니다: ${errorMessage(e)}`,true); }
      notice(`${no} · v${current.version_no} 열림${dirty?' · 임시 문서 복구됨':''}`);
      await refresh();
    } catch(e) { fail(e); }
    finally { busy=false; await heartbeat(); }
  }
  async function libSaveCurrent() {
    if (busy) return;
    const doc=editor.collect(), no=doc.sop?.id;
    if (!/^[A-Za-z0-9_.-]+$/.test(no || '')) return notice('SOP 번호는 영문·숫자·-·_·.으로 입력하세요.',true);
    if (doc.blocks.some(b=>b.type==='flowchart'&&!b.project)) return notice('순서도가 준비된 후 다시 저장하세요.',true);
    const counter=changeCounter, epoch=operationEpoch;
    busy=true; $('libSaveBtn').disabled=true;
    try {
      await draftQueue;
      const result=await libStore.put({sop_no:no,doc,base_version_no:current?.sop_no===no?current.version_no:0});
      if (operationEpoch!==epoch) return notice(`${no} v${result.version_no} 저장 완료 · 현재 편집 문서는 바뀌었습니다.`);
      if (current && current.id!==result.id) await release();
      current={id:result.id,sop_no:no,version_no:result.version_no};
      dirty=counter!==changeCounter;
      // Persist the new conflict baseline with the exact saved snapshot. Pending edits
      // stay in the editor and will be captured by the original autosave timer.
      const snapshot={...doc,_server:{...current}};
      try { localStorage.setItem('sop-studio-local-draft-v1',JSON.stringify(snapshot)); } catch(_) {}
      notice(`${no} · v${result.version_no} 서버 저장 완료${dirty?' · 추가 변경사항 있음':''}`);
      if(result.warning) notice(`${no} v${result.version_no} 저장됨 · ${result.warning.locked_by}님도 편집 중입니다.`,true);
      if(result.warnings?.length) notice(`v${result.version_no} 저장됨 · ${result.warnings.join(' / ')}`,true);
      editor.toast(`v${result.version_no} 서버 저장 완료`); await refresh();
    } catch(e) { fail(e); }
    finally { busy=false; $('libSaveBtn').disabled=false; await heartbeat(); }
  }
  function button(text, action, className='') {
    const b=document.createElement('button'); b.type='button'; b.textContent=text; b.className=className;
    b.onclick=()=>Promise.resolve(action()).catch(fail); return b;
  }
  async function refresh() {
    const seq=++listSequence;
    try {
      const rows=await libStore.list();
      if(seq!==listSequence) return;
      const tree=$('libTree'); tree.replaceChildren();
      if(!rows.length) {tree.textContent='저장된 문서가 없습니다.';return;}
      const areas=[...new Set(rows.map(r=>r.area))].sort();
      for(const area of areas) {
        const group=document.createElement('details');group.open=true;
        const title=document.createElement('summary');title.textContent=`AREA ${area||'미지정'}`;group.append(title);
        for(const row of rows.filter(r=>r.area===area).sort((a,b)=>a.sop_no.localeCompare(b.sop_no,undefined,{numeric:true}))) {
          const item=document.createElement('div');item.className='lib-row'+(current?.id===row.id?' is-current':'');
          const open=button(row.sop_no,()=>libOpenDocument(row.sop_no),'lib-open');
          const detail=document.createElement('small');detail.textContent=`${row.name||'제목 없음'} · v${row.version_no}`;open.append(detail);item.append(open);
          const actions=document.createElement('div');actions.className='lib-row-actions';
          actions.append(button('이력',()=>history(row)));
          if(row.status==='retired') actions.append(button('복구',async()=>{await api('POST',`/api/sops/${row.id}/restore`);await refresh();notice('문서를 복구했습니다.');}));
          else actions.append(button('폐기',async()=>{if(!confirm(`${row.sop_no} 문서를 폐기할까요? 버전 이력은 보존됩니다.`))return;await libStore.remove(row.sop_no);if(current?.id===row.id)clearContext();await refresh();notice('문서를 폐기했습니다.');}));
          item.append(actions);group.append(item);
        }
        tree.append(group);
      }
    } catch(e) { if(seq===listSequence){$('libTree').textContent='목록을 불러오지 못했습니다.';fail(e);} }
  }
  async function history(row) {
    const versions=await api('GET',`/api/sops/${row.id}/versions`);
    const dialog=document.createElement('dialog');dialog.className='lib-history';dialog.setAttribute('aria-label',`${row.sop_no} 버전 이력`);
    const head=document.createElement('header'),title=document.createElement('h2');title.textContent=`${row.sop_no} · 버전 이력`;
    head.append(title,button('닫기',()=>dialog.close()));dialog.append(head);
    const list=document.createElement('ul');
    for(const v of versions) {
      const li=document.createElement('li'),label=document.createElement('span');label.textContent=`v${v.version_no} · ${v.revision||'개정 미지정'} · ${v.saved_by}`;
      const date=document.createElement('small');date.textContent=`${new Date(v.saved_at).toLocaleString()} ${v.change_note}`;label.append(date);
      li.append(label,button('원본 다운로드',async()=>{const d=await api('GET',`/api/sops/${row.id}/versions/${v.version_no}`);download(d.content,`${row.sop_no}-v${v.version_no}.json`);}));list.append(li);
    }
    dialog.append(list);dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
  }
  function download(value, name) {
    const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  $('libSaveBtn').onclick=libSaveCurrent;
  $('libRefresh').onclick=refresh;
  $('libSearch').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(refresh,250);};
  $('libStatus').onchange=refresh;
  $('libNew').onclick=()=>{if(busy||!canReplace())return;editor.fresh();editor.clearDraftPrompt();notice('새 문서를 작성하세요.');refresh();};
  $('libUser').onchange=async()=>{
    if(busy){$('libUser').value=user;return;}
    await draftQueue;await release();user=$('libUser').value.trim()||'anonymous';$('libUser').value=user;
    try{localStorage.setItem('sop-studio-user',user);}catch(_){} await heartbeat();
  };
  $('libExport').onclick=async()=>{
    try {
      const rows=await api('GET','/api/sops?status=all'),items=[];
      for(const row of rows)items.push({...row,doc:(await libStore.get(row.sop_no)).doc});
      download({format:'sop-studio-library',exported_at:new Date().toISOString(),items},`sop-library-${new Date().toISOString().slice(0,10)}.json`);
      notice(`${items.length}개 문서를 내보냈습니다. 버전 전체는 서버에 보존됩니다.`);
    }catch(e){fail(e);}
  };
  $('libImport').onclick=()=>$('libImportFile').click();
  $('libImportFile').onchange=async event=>{
    const file=event.target.files[0];event.target.value='';if(!file||busy)return;
    busy=true;
    try {
      const data=JSON.parse(await file.text());
      const items=data.format==='sop-studio-library'?data.items:data.format==='sop-editor-mock'?[{sop_no:data.sop?.id,doc:data}]:null;
      if(!Array.isArray(items))throw new Error('문서 JSON 또는 라이브러리 내보내기 파일을 선택하세요.');
      for(const item of items)if(!item.doc||item.sop_no!==item.doc.sop?.id||item.doc.format!=='sop-editor-mock'||!Array.isArray(item.doc.blocks))throw new Error('가져오기 항목의 SOP 번호 또는 문서 형식이 잘못되었습니다.');
      let saved=0;const failures=[];
      for(const item of items){try{await libStore.put({...item,base_version_no:0});saved++;}catch(e){failures.push(`${item.sop_no}: ${errorMessage(e)}`);}}
      notice(`${saved}개 가져옴${failures.length?` · ${failures.join(' / ')}`:''}`,failures.length>0);await refresh();
    }catch(e){fail(e);}finally{busy=false;}
  };
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  window.addEventListener('pagehide',()=>release());
  setInterval(heartbeat,30000);
  window.SOPLibrary={context:()=>current?{...current}:null,libStore,libOpenDocument,libSaveCurrent,refresh};
  api('GET','/api/health').then(()=>notice('서버 연결됨 · 서버 저장으로 버전을 남기세요.')).catch(fail);
  refresh();
})();
