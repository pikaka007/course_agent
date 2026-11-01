 'use strict';
let token = '', busy = false, sessionId = null, jobId = null, selected = null, state = null;
let pendingTurn = null;
const $ = id => document.getElementById(id);
const node = (tag, text = '', cls = '') => { const n = document.createElement(tag); n.textContent = text; if (cls) n.className = cls; return n; };
function notice(text, error = false) { $('notice').textContent = text; $('notice').classList.toggle('hidden', !text); $('notice').classList.toggle('error', error); }
async function api(url, body) {
  const r = await fetch(url, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Agent-Token':token}, body:JSON.stringify(body)});
  let data; try { data = await r.json(); } catch { throw Error('本地服务没有返回有效结果，请检查是否仍在运行。'); }
  if (!r.ok) throw Error(data.error || `请求失败 ${r.status}`);
  return data;
}
function act(fn) { return async event => { try { await fn(event); } catch(e) { notice(e.message, true); } }; }
function view(name) {
  notice('');
  $('source-pane')?.classList.add('hidden');
  document.querySelectorAll('.view').forEach(n => n.classList.toggle('hidden', n.id !== 'view-' + name));
  document.querySelectorAll('[data-view]').forEach(n => n.classList.toggle('active', n.dataset.view === name || (name==='skills' && n.dataset.view==='settings')));
  document.querySelector('.sidebar').classList.toggle('focused-workspace',name!=='chat');
  document.querySelector('.session-actions').classList.toggle('hidden',name!=='chat');
  $('session-label').classList.toggle('hidden',name!=='chat');
  document.dispatchEvent(new CustomEvent('workspace-view',{detail:name}));
  $('view-name').textContent = {chat:'学习对话',library:'资料库',settings:'工作台设置',study:'任务与计划',mail:'邮件草稿',review:'复习自测',skills:'设置 / 技能管理'}[name];
}
function setBusy(value) {
  busy = value;
  for (const id of ['run','new-chat','upload','use-selection','select-all']) $(id).disabled = value;
  document.querySelectorAll('#view-settings button,#view-settings input,#view-settings textarea,.document button,.document input').forEach(n => n.disabled = value);
  $('stop').classList.toggle('hidden', !value);
  $('progress').classList.toggle('hidden', !value);
}
async function refresh() {
  state = await api('/api/status'); token = state.token;
  $('provider-model').textContent = state.model;
  $('provider-host').textContent = state.endpoint_host + (state.key_configured ? ' / 密钥已配置' : ' / 待配置密钥');
  $('doc-count').textContent = state.documents.length;
  $('base-url').value = state.base_url; $('model-name').value = state.model; $('api-key').value = '';
  $('config-source').textContent = state.precedence + '。当前接口来源：' + (state.sources.OPENAI_BASE_URL || '启动参数');
  $('answer-length').value = state.preferences.answer_length; $('instructions').value = state.preferences.instructions;
  $('templates')?.replaceChildren(...state.preferences.templates.map(t => {
    const b = node('button','','template'); b.append(node('strong',t.title), node('small',t.question));
    b.onclick = () => { if (!busy) { $('question').value = t.question; $('question').focus(); } }; return b;
  }));
  $('template-editor').replaceChildren(); state.preferences.templates.forEach(addTemplate);
  if (selected) selected = selected.filter(name => state.documents.some(d => d.filename === name));
  renderDocuments(); await sessions(); await trash();
}
function renderDocuments() {
  $('scope-button').textContent = selected === null ? '全部资料' : selected.length===1 ? selected[0] : `${selected.length} 份资料`;
  $('scope-button').title = selected===null?'全部资料':selected.join('、');
  $('documents').replaceChildren(...state.documents.map(d => {
    const row = node('div','','document'), check = node('input'); check.type='checkbox'; check.checked = selected === null || selected.includes(d.filename); check.setAttribute('aria-label','使用 '+d.filename);
    check.onchange = () => { selected = [...$('documents').querySelectorAll('input:checked')].map(n=>n.dataset.file); $('scope-button').textContent = selected.length===1?selected[0]:`${selected.length} 份资料`; $('scope-button').title=selected.join('、'); };
    check.dataset.file=d.filename;
    const info = node('div','','doc-info'); info.append(node('strong',d.filename),node('small',`${(d.bytes/1024/1024).toFixed(2)} MB / 本地资料`));
    const remove=node('button','移入回收站'); remove.onclick=act(async()=> {await api('/api/documents/archive',{filename:d.filename}); await refresh(); notice('已移入回收站，可在资料库底部恢复。');});
    row.append(check,node('span',d.filename.split('.').pop().toUpperCase(),'doc-icon'),info,remove); return row;
  }));
  if (!state.documents.length) $('documents').append(node('p','还没有资料。导入课程文档后即可开始提问。','empty'));
}
async function trash() {
  const rows=await api('/api/trash');
  $('trash').replaceChildren(...rows.map(r=>{ const row=node('div','','trash-row'), b=node('button','恢复','text-button'); b.onclick=act(async()=>{await api('/api/documents/restore',{id:r.id});await refresh();notice('资料已恢复。');});row.append(node('span',r.filename),b);return row;}));
  if (!rows.length) $('trash').append(node('p','回收站为空。','subtle'));
}
async function sessions() {
  const rows=await api('/api/sessions?q='+encodeURIComponent($('session-search').value));
  $('sessions').replaceChildren(...rows.map(s=>{const b=node('button',s.title,'session'+(s.id===sessionId?' active':''));b.title=`${s.count} 轮 / ${s.updated}`;b.onclick=act(async()=>{if(busy)return;const detail=await api('/api/sessions/'+s.id);sessionId=s.id;sessionStorage.setItem('coursemate-session',s.id);$('conversation').replaceChildren();detail.results.forEach(showResult);$('session-label').textContent=s.title;view('chat');await sessions();});return b;}));
  if (!rows.length) $('sessions').append(node('p',$('session-search').value?'没有找到匹配的会话。':'开始提问后，会话将保存在这里。','empty'));
}
function newChat(){if(busy)return;sessionId=null;sessionStorage.removeItem('coursemate-session');$('conversation').replaceChildren(welcome.cloneNode(true));wireWelcome();$('session-label').textContent='新会话';$('question').value='';view('chat');refresh().catch(e=>notice(e.message,true));}
let welcome=$('welcome').cloneNode(true);
function wireWelcome(){const b=$('edit-templates');if(b)b.onclick=()=>{view('settings');$('template-editor').scrollIntoView({block:'center'});};}
function inline(parent, text, records=[]){text.split(/(\[[^\]\n]+ · [^\]\n]+\]|\*\*[^*]+\*\*|`[^`]+`)/g).forEach(p=>parent.append(records.some(r=>r.citation===p)?citationButton(p,records):p.startsWith('**')&&p.endsWith('**')?node('strong',p.slice(2,-2)):p.startsWith('`')&&p.endsWith('`')?node('code',p.slice(1,-1)):document.createTextNode(p)));}
function markdown(text,records=[]){const wrap=node('div','','answer');let code=null;for(const line of String(text||'').split('\n')){if(line.startsWith('```')){if(code){wrap.append(code);code=null;}else code=node('pre');continue;}if(code){code.textContent+=line+'\n';continue;}if(!line.trim())continue;const heading=/^#{1,4}\s/.test(line),p=node(heading?'h3':'p','',/^[-*]\s/.test(line)?'bullet':'');inline(p,line.replace(/^#{1,4}\s/,''),records);wrap.append(p);}if(code)wrap.append(code);return wrap;}
function dialog(title, content){$('dialog-title').textContent=title;$('dialog-content').replaceChildren(content);$('detail-dialog').showModal();}
function showResult(r, target){
  $('welcome')?.remove(); const turn=target||node('article','','turn'); turn.dataset.run=r.run_id;
  turn.replaceChildren(node('div',r.question,'user-message'));
  const label=node('div','CourseMate','assistant-label');label.append(node('span',r.mode==='demo'?'离线固定演示（不是模型回答）':r.status==='completed'?(r.document_choices?.length?'请选择资料':r.completion_quality==='partial'?'部分内容通过核对':r.evidence_review?.ok?(r.evidence_review.status==='insufficient_evidence'?'资料不足':'证据核对完成'):'已完成（未做逐段核对）'):r.status,'run-state'));turn.append(label,markdown(r.answer,r.source_records||[]));
  if(r.document_choices?.length){const choices=node('div','','button-row');for(const filename of r.document_choices){const b=node('button',filename,'secondary');b.onclick=()=>{if(busy)return;selected=[filename];renderDocuments();$('question').value=r.question;send();};choices.append(b);}turn.append(choices);}
  const footer=node('div','','answer-footer');
  if(r.citation_audit_attempts?.length){const diagnostics=node('button','引用诊断');diagnostics.onclick=()=>{const box=node('div');r.citation_audit_attempts.forEach((a,i)=>{const detail=node('details');detail.append(node('summary',`第 ${i+1} 次引用检查`),node('pre',JSON.stringify(a.audit,null,2)),node('pre',a.draft));box.append(detail);});dialog('引用校验详情',box);};footer.append(diagnostics);}
  const sources=node('button',`引用原文 ${r.source_records?.length||0}`);sources.onclick=()=>{const box=node('div');box.append(node('p',r.citation_audit?.scope||'此历史记录没有新版引用检查。','subtle'));if(r.citation_audit?.uncited_blocks?.length)box.append(node('p',`${r.citation_audit.uncited_blocks.length} 个内容段落缺少就近引用，请核对。`));(r.source_records||[]).forEach(s=>{const d=node('details');d.append(node('summary',s.citation),node('pre',s.text));box.append(d);});dialog('核对引用原文',box);};
  const trace=node('button',`执行过程 ${r.trace?.length||0}`);trace.onclick=()=>{const box=node('div');(r.trace||[]).forEach((t,i)=>{const d=node('details');d.append(node('summary',`${i+1}. ${t.name} / ${t.result.ok?'成功':'失败'}`),node('pre',JSON.stringify({arguments:t.arguments,result:t.result},null,2)));box.append(d);});dialog('工具执行过程',box);};
  const receipt=node('button','接口回执');receipt.onclick=()=>dialog('真实请求记录',node('pre',JSON.stringify({connection:r.connection||'旧记录未记录实际接口，不能据此认定为官方调用',requests:r.provider_requests||[],metrics:r.metrics},null,2)));
  const copy=node('button','复制回答');copy.onclick=act(async()=>{await navigator.clipboard.writeText(r.answer);notice('回答已复制。');});const retry=node('button','编辑为新会话');retry.onclick=()=>{if(busy)return;newChat();$('question').value=r.question;$('question').focus();notice('已复制问题到新会话，可修改后发送。原会话保留。');};
  const check=node('button','证据核对');check.onclick=()=>showReview(r.evidence_review);footer.append(sources,check,trace,receipt,copy,retry);
  (r.saved_files||[]).forEach(path=>{const filename=path.split('/').pop(),b=node('button','查看笔记');b.onclick=act(async()=>{const n=await api('/api/note?filename='+encodeURIComponent(filename));const box=node('div');const link=node('a','下载 Markdown');link.href='/api/note?filename='+encodeURIComponent(filename)+'&download=1';box.append(link,markdown(n.content));dialog(filename,box);});footer.append(b);});
  const tokens=(r.provider_requests||[]).reduce((n,p)=>n+(p.usage?.total_tokens||0),0);
  footer.append(node('div',`${r.elapsed_seconds||0} 秒 / ${r.model} / ${r.connection?.endpoint_host||'未记录接口'}${tokens?' / '+tokens+' tokens':''}`,'answer-meta'));
  turn.append(footer);if(typeof attachProposals==='function')attachProposals(r,turn).catch(e=>notice(e.message,true));if(!target)$('conversation').append(turn);return turn;
}
function eventLabel(e){if(e.stage==='draft_reset')return `第 ${e.round} 轮：准备生成`;if(e.stage==='model')return `第 ${e.round} 轮：模型处理中`;if(e.stage==='tool')return `调用 ${e.name}`;if(e.stage==='tool_done')return `${e.name} ${e.ok?'完成':'返回错误'}`;if(e.stage==='evidence_check')return '正在逐段核对证据，草稿尚未确认';if(e.stage==='evidence_repair')return '正在修正缺少支持的结论';if(e.stage==='citation_repair')return '正在校正引用';return e.stage==='finished'?'任务结束':'任务已开始';}
async function monitor(id){setBusy(true);let failures=0;try{for(;;){let job;try{job=await api('/api/jobs/'+id);failures=0;}catch(e){if(++failures>=3)throw e;await new Promise(r=>setTimeout(r,800));continue;}sessionId=job.session_id;sessionStorage.setItem('coursemate-session',sessionId);$('progress').replaceChildren(...job.events.slice(-3).map(e=>node('div',eventLabel(e))));if(job.status==='running'){
  if(!pendingTurn){$('welcome')?.remove();pendingTurn=node('article','','turn');$('conversation').append(pendingTurn);}
  if(pendingTurn.dataset.draft!==job.draft){const nearBottom=$('conversation').scrollHeight-$('conversation').scrollTop-$('conversation').clientHeight<130;pendingTurn.dataset.draft=job.draft;pendingTurn.replaceChildren(node('div',job.question,'user-message'),node('p','生成中的草稿 · 完成证据核对后才会保存为答案','draft-label'),markdown(job.draft||'正在检索资料…'));if(nearBottom)$('conversation').scrollTop=$('conversation').scrollHeight;}
}if(job.status!=='running'){sessionStorage.removeItem('coursemate-job');jobId=null;if(job.result){$('session-label').textContent=job.question.slice(0,40);showResult(job.result,pendingTurn);pendingTurn=null;notice(job.status==='completed'?'':job.result.answer,job.status==='error');}else throw Error(job.error||'执行失败');await sessions();$('conversation').scrollTop=$('conversation').scrollHeight;return;}await new Promise(r=>setTimeout(r,700));}}finally{setBusy(false);}}
async function send(){if(busy)return;const question=$('question').value.trim();if(!question){notice('先输入一个问题。');return;}if(selected&&selected.length===0){notice('请至少选择一份资料，或切换到全部资料。',true);return;}notice('');$('progress').replaceChildren();setBusy(true);try{const job=await api('/api/jobs',{question,mode:$('mode').value,allow_save:$('save').checked,session_id:sessionId,documents:selected});jobId=job.job_id;sessionId=job.session_id;sessionStorage.setItem('coursemate-job',jobId);$('welcome')?.remove();pendingTurn=node('article','','turn');pendingTurn.append(node('div',question,'user-message'),node('p','正在检索资料…','subtle'));$('conversation').append(pendingTurn);$('question').value='';$('conversation').scrollTop=$('conversation').scrollHeight;await monitor(jobId);}catch(e){if(pendingTurn)pendingTurn.append(node('p',e.message,'subtle'));notice(e.message,true);}finally{setBusy(false);}}
function addTemplate(t={title:'',question:''}){const row=node('div','','template-edit'),title=node('input'),question=node('textarea'),remove=node('button','移除模板');title.value=t.title;title.placeholder='任务名称';title.maxLength=40;title.setAttribute('aria-label','任务名称');question.value=t.question;question.placeholder='任务内容';question.maxLength=2000;question.rows=2;question.setAttribute('aria-label','任务内容');remove.type='button';remove.onclick=()=>row.remove();row.append(title,question,remove);$('template-editor').append(row);}
$('new-chat').onclick=newChat;
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>view(b.dataset.view));
$('open-model').onclick=()=>view('settings');$('refresh-sessions').onclick=act(sessions);$('scope-button').onclick=()=>view('library');
$('run').onclick=send;$('question').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send();}};
$('stop').onclick=act(async()=>{if(jobId){await api('/api/cancel',{job_id:jobId});notice('已请求停止。正在接收的流会中断；建立连接阶段可能仍需等待超时，平台可能已计费。');}});
$('mode').onchange=()=>{$('composer-hint').textContent=$('mode').value==='demo'?'离线演示执行固定 RAG 检索流程，不理解自由问题，也不会调用大模型。':'Enter 发送，Shift+Enter 换行。真实模式会将问题和检索片段发送到所选模型平台。';};
$('select-all').onclick=()=>{selected=null;renderDocuments();};$('use-selection').onclick=()=>{view('chat');$('question').focus();};
$('upload').onchange=act(async()=>{const files=[...$('upload').files];if(busy)return;setBusy(true);$('stop').classList.add('hidden');try{for(const file of files){if(file.size>40*1024*1024)throw Error('文件超过 40 MB：'+file.name);notice('正在导入 '+file.name);const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('读取文件失败'));reader.readAsDataURL(file);});await api('/api/documents/upload',{filename:file.name,content:data});}selected=files.map(f=>f.name);await refresh();notice(`已导入 ${files.length} 份资料。`);}finally{$('upload').value='';setBusy(false);}});
$('model-form').onsubmit=act(async e=>{e.preventDefault();const key=$('api-key').value;try{await api('/api/settings/model',{base_url:$('base-url').value,model:$('model-name').value,api_key:key});await refresh();notice('连接配置已保存到本机 .env。请测试连接确认所选模型可用。');}finally{$('api-key').value='';}});
$('load-models').onclick=act(async()=>{notice('正在获取已保存接口的模型列表…');const r=await api('/api/models',{});$('model-options').replaceChildren(...r.models.map(m=>{const o=node('option');o.value=m;return o;}));notice('可用模型：'+r.models.join('、')+'。在模型名称输入框中选择后保存。');});
$('test-connection').onclick=act(async()=>{const b=$('test-connection');b.disabled=true;notice('正在向已保存的模型发送简短测试请求…');try{const r=await api('/api/probe',{});$('probe-result').classList.remove('hidden');$('probe-result').textContent=JSON.stringify(r,null,2);notice('连接成功。下方回执可与平台后台核对；测试没有发送本地资料。');}finally{b.disabled=false;}});
$('preferences-form').onsubmit=act(async e=>{e.preventDefault();const templates=[...$('template-editor').children].map(r=>({title:r.querySelector('input').value,question:r.querySelector('textarea').value}));await api('/api/settings/preferences',{answer_length:Number($('answer-length').value),instructions:$('instructions').value,templates});await refresh();notice('回答偏好和任务模板已保存。');});
$('add-template').onclick=()=>addTemplate();
$('archive-history').onclick=act(async()=>{if(!confirm('把当前运行记录和会话移入本机归档？资料和笔记会保留。'))return;const r=await api('/api/history/archive',{confirm:true});newChat();notice(`已归档 ${r.archived} 个文件，保存在 ${r.location}`);});
$('close-dialog').onclick=()=>$('detail-dialog').close();
$('legacy-history').onclick=act(async()=>{const rows=await api('/api/runs');const box=node('div');rows.forEach(r=>{const b=node('button',r.question,'session');b.onclick=()=>{if(busy)return;$('detail-dialog').close();view('chat');sessionId=null;sessionStorage.removeItem('coursemate-session');$('conversation').replaceChildren();showResult(r);};box.append(b);});dialog('独立运行记录（仅查看）',box);});
async function init(){await refresh();wireWelcome();const saved=sessionStorage.getItem('coursemate-session');if(saved){try{const s=await api('/api/sessions/'+saved);sessionId=saved;$('conversation').replaceChildren();s.results.forEach(r=>showResult(r));$('session-label').textContent=s.title;}catch{sessionStorage.removeItem('coursemate-session');}}jobId=sessionStorage.getItem('coursemate-job');if(jobId){try{await monitor(jobId);}catch(e){sessionStorage.removeItem('coursemate-job');notice(e.message+'；可从最近会话查看已保存结果。',true);}}await sessions();}
init().catch(e=>notice(e.message,true));

function citationButton(cite,records){const b=node('button',cite,'citation');b.onclick=()=>{const r=records.find(s=>s.citation===cite);$('source-content').replaceChildren(node('strong',cite),node('p','显示本轮实际检索/读取到的原文。','subtle'),node('pre',r.text));$('source-pane').classList.remove('hidden');};return b;}
$('close-source').onclick=()=>$('source-pane').classList.add('hidden');
let searchTimer; $('session-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>sessions().catch(e=>notice(e.message,true)),250);};
$('export-session').onclick=()=>{if(!sessionId){notice('请先打开一个会话。');return;}const a=node('a');a.href='/api/session-export?id='+encodeURIComponent(sessionId);a.download=sessionId+'.json';a.click();};
$('delete-session').onclick=act(async()=>{if(busy||!sessionId)return;if(!confirm('删除此会话及其关联运行日志？资料、笔记和证据副本保留。'))return;await api('/api/sessions/delete',{id:sessionId,confirm:true});newChat();notice('会话及关联日志已删除。');});
$('retention-preview').onclick=act(async()=>{const days=Number($('retention-days').value),r=await api('/api/retention-preview?days='+days);const box=node('div');box.append(node('p',`将删除 ${r.count} 个超过 ${days} 天未更新的会话（包含归档会话）及其关联日志。资料、笔记、独立运行日志和证据副本保留。`));if(r.count){const b=node('button','确认删除这些过期会话','primary');b.onclick=act(async()=>{const result=await api('/api/retention-clean',{days,ids:r.ids,confirm:true});$('detail-dialog').close();newChat();notice(`已删除 ${result.deleted} 个过期会话。`);});box.append(b);}dialog('清理预览',box);});

function showReview(review){const box=node('div');if(review?.status==='partial')box.append(node('p','以下是原草稿的审核记录。未通过的段落已从最终结果中移除。','notice'));box.append(node('p',review?.scope||'本记录未做模型辅助证据核对。','subtle'));for(const c of review?.claims||[]){const d=node('details');d.open=true;d.append(node('summary',({supported:'有原文支持',unsupported:'缺少支持',uncertain:'需要核对'}[c.verdict]||c.verdict)+' · 第 '+(c.id+1)+' 段'),node('p',c.claim),node('p',c.reason,'subtle'));for(const e of c.evidence||[])d.append(node('strong',e.citation),node('pre',e.quote));box.append(d);}dialog('逐段核对结果',box);}



