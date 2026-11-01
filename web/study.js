'use strict';
let studyState=null, selectedMail=null, reviewIndex=0;
const answerDrafts=new Map();
const actionNames={task:'创建任务',task_update:'修改任务',event:'加入本地日历',event_update:'调整日历时间',mail:'保存邮件草稿',quiz:'加入自测题库',attempt:'记录自评'};
const fieldNames={title:'标题',minutes:'预计分钟',priority:'优先级',due:'完成时间',status:'状态',start:'开始',end:'结束',location:'地点',to:'收件人',subject:'主题',body:'正文',question:'题目',expected:'参考答案',citation:'出处',source:'原文',answer:'你的回答',rating:'自评'};
function values(form){return Object.fromEntries(new FormData(form));}
function studyRow(title,detail){const d=node('div','','study-row');d.append(node('strong',title),node('p',detail,'subtle'));return d;}
function button(label,fn,cls='text-button'){const b=node('button',label,cls);b.type='button';b.onclick=act(fn);return b;}
function empty(target,title,detail){$(target).replaceChildren(node('h3',title),node('p',detail,'subtle'));$(target).classList.add('empty-content');}
function localTime(value){return value?value.slice(0,16).replace('T',' '):'未设置';}
function editorError(message=''){$('editor-error').textContent=message;$('editor-error').classList.toggle('hidden',!message);}
function editorAct(fn){return async e=>{try{editorError();await fn(e);}catch(err){editorError(err.message);}};}
function openEditor(kind,record=null){
 editorError();$('editor-preview').replaceChildren();$('plan-results').replaceChildren();
 document.querySelectorAll('.editor-body').forEach(n=>n.classList.toggle('hidden',n.id!=='editor-'+kind));
 $('editor-title').textContent={task:record?'编辑任务':'新建任务',mail:record?'编辑草稿副本':'新建邮件草稿',plan:'寻找学习空档',event:'调整安排时间',preview:'核对并确认'}[kind];
 const form=$(kind+'-form');if(form){form.reset();if(record)for(const e of form.elements){if(e.name&&record[e.name]!==undefined)e.value=e.type==='datetime-local'?record[e.name].slice(0,16):record[e.name];}}
 if(!$('editor-dialog').open)$('editor-dialog').showModal();
 $('editor-close').focus();
}
function proposalCard(p){
 const box=node('section','','proposal-card');box.dataset.proposal=p.id;
 box.append(node('h3',actionNames[p.action]||'待确认操作'));
 if(p.status!=='pending'){box.append(node('p',p.status==='applied'?'已应用到本地工作区':'已拒绝','proposal-status'));return box;}
 box.append(node('p','请检查以下内容。确认前不会生效。','subtle'));
 for(const [key,value] of Object.entries(p.args)){if(!value||key==='id'||key==='task_id')continue;const display=key==='status'?({todo:'待开始',doing:'进行中',done:'已完成'}[value]||value):key==='priority'?(['','高','中','低'][value]||value):value;const row=node('div','','proposal-field');row.append(node('strong',fieldNames[key]||key),node('p',String(display)));box.append(row);}
 const error=node('p','','inline-error');error.setAttribute('role','alert');
 const actions=node('div','','button-row');
 for(const [accept,label] of [[true,'确认保存'],[false,'拒绝此操作']])actions.append(button(label,async()=>{
  actions.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{await api('/api/study/confirm',{id:p.id,accept});await refreshStudy();document.querySelectorAll('[data-proposal]').forEach(n=>{if(n.dataset.proposal===p.id)n.replaceWith(proposalCard({...p,status:accept?'applied':'rejected'}));});notice(accept?'已保存到本地。':'已拒绝，本地内容未变更。');}
  catch(e){error.textContent=e.message;actions.querySelectorAll('button').forEach(b=>b.disabled=false);}
 },accept?'primary':'text-button'));
 box.append(actions,error);return box;
}
async function proposeStudy(action,args){const p=await api('/api/study/propose',{action,args});await refreshStudy();openEditor('preview');$('editor-preview').append(proposalCard(p));}
async function attachProposals(result,turn){
 const ids=[...new Set((result.trace||[]).map(t=>t.result?.data?.result?.proposal?.id).filter(Boolean))];if(!ids.length)return;
 const state=await api('/api/study');const wrap=node('div','','chat-proposals');for(const id of ids){const p=state.proposals.find(x=>x.id===id);if(p)wrap.append(proposalCard(p));}turn.append(wrap);
}
function renderTasks(){
 const filter=$('task-filter').value,rows=studyState.tasks.filter(t=>filter==='all'||(filter==='done'?t.status==='done':t.status!=='done'));
 $('study-tasks').classList.remove('empty-content');$('study-tasks').replaceChildren(...rows.map(t=>{
  const status={todo:'待开始',doing:'进行中',done:'已完成'}[t.status],d=studyRow(t.title,`${t.minutes} 分钟 · ${status} · ${['','高','中','低'][t.priority]}优先级`);
  if(t.due)d.append(node('small','完成时间 '+localTime(t.due)));
  if(t.status!=='done')d.append(button(t.status==='todo'?'开始学习':'标记完成',()=>proposeStudy('task_update',{id:t.id,status:t.status==='todo'?'doing':'done'})));
  else d.append(button('重新开始',()=>proposeStudy('task_update',{id:t.id,status:'todo'})));
  d.append(button('编辑',()=>openEditor('task',t)));return d;
 }));if(!rows.length)empty('study-tasks','这里还没有'+(filter==='done'?'已完成任务':'任务'),'点击“新建任务”，记录想学习的内容和预计时长。');
}
function renderCalendar(){
 const box=$('study-events');box.replaceChildren();box.classList.remove('empty-content');let date='';
 for(const e of [...studyState.events].sort((a,b)=>a.start.localeCompare(b.start))){const day=e.start.slice(0,10);if(day!==date){box.append(node('h3',day,'agenda-date'));date=day;}const d=studyRow(e.title,localTime(e.start)+' — '+localTime(e.end));d.append(button('调整时间',()=>openEditor('event',e)));box.append(d);}
 if(!studyState.events.length)empty('study-events','日历还是空的','导入已有日历，或为任务寻找合适的学习空档。');
}
function renderMail(){
 const query=$('mail-search').value.trim().toLowerCase(),rows=studyState.drafts.filter(m=>(m.subject+' '+m.to).toLowerCase().includes(query));
 if(!rows.some(m=>m.id===selectedMail))selectedMail=rows[0]?.id;
 $('study-mails').classList.remove('empty-content');$('study-mails').replaceChildren(...rows.map(m=>{const b=button('',()=>{selectedMail=m.id;renderMail();},'mail-item'+(selectedMail===m.id?' selected':''));b.setAttribute('aria-pressed',String(selectedMail===m.id));b.append(node('strong',m.subject),node('small',m.to));return b;}));
 const detail=$('mail-detail');detail.classList.remove('empty-content');detail.replaceChildren();const m=rows.find(m=>m.id===selectedMail);
 if(!m){empty('mail-detail',query?'没有匹配的草稿':'从一封草稿开始',query?'换一个主题或收件人关键词。':'点击“新建草稿”，也可以让助手根据你的用途起草。');return;}
 detail.append(node('p','本地草稿 · 未发送','subtle'),node('h2',m.subject),node('p','收件人：'+m.to),node('pre',m.body,'mail-body'));
 const actions=node('div','','button-row'),download=node('a','导出 .eml','secondary');download.href='/api/mail-export?id='+encodeURIComponent(m.id);actions.append(button('编辑副本',()=>openEditor('mail',m),'primary'),download);detail.append(actions);
}
function renderReview(){
 const rows=studyState.quizzes.filter(q=>{const a=studyState.attempts.find(a=>a.quiz_id===q.id);return $('review-filter').value==='all'||!a||new Date(a.next_review)<=new Date();});
 reviewIndex=Math.max(0,Math.min(reviewIndex,rows.length-1));$('review-count').textContent=rows.length?`第 ${reviewIndex+1} / ${rows.length} 题`:'';
 $('study-quizzes').classList.remove('empty-content');$('study-quizzes').replaceChildren();
 if(!rows.length){empty('study-quizzes','现在没有待复习题目','可以切换到全部题目，或从课程资料生成一道新题。');return;}
 const q=rows[reviewIndex],d=node('article','','quiz-card'),answer=node('textarea');answer.rows=6;answer.value=answerDrafts.get(q.id)||'';answer.placeholder='用自己的话写下答案…';answer.setAttribute('aria-label','你的自测回答');answer.oninput=()=>answerDrafts.set(q.id,answer.value);
 d.append(node('h2',q.title),node('p',q.question,'quiz-question'),answer);
 const evidence=node('details');evidence.append(node('summary','写好后，展开参考答案'),node('p',q.expected),node('strong',q.citation),node('pre',q.source));
 const ratings=node('div','','button-row'),hint=node('p','','inline-error');hint.setAttribute('role','alert');
 for(const [rating,label] of [['again','再练一次 · 1天'],['hard','较难 · 3天'],['good','掌握 · 7天']])ratings.append(button(label,async()=>{
  if(!answer.value.trim()){hint.textContent='请先写下你的回答。';answer.focus();return;}
  ratings.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{const p=await api('/api/study/propose',{action:'attempt',args:{quiz_id:q.id,answer:answer.value,rating}});await api('/api/study/confirm',{id:p.id,accept:true});answerDrafts.delete(q.id);await refreshStudy();notice('已记录自评并安排下次复习。');}catch(e){hint.textContent=e.message;ratings.querySelectorAll('button').forEach(b=>b.disabled=false);}
 },'secondary'));
 evidence.append(node('p','对照原文后，为自己选择下一次复习时间。','subtle'),ratings,hint);d.append(evidence);
 const nav=node('div','','review-navigation');const previous=button('上一题',()=>{reviewIndex--;renderReview();}),next=button('下一题',()=>{reviewIndex++;renderReview();});previous.disabled=reviewIndex===0;next.disabled=reviewIndex===rows.length-1;nav.append(previous,next);d.append(nav);$('study-quizzes').append(d);
}
async function refreshStudy(){
 studyState=await api('/api/study');const status=$('study-status');status.replaceChildren();if(studyState.reminders.length){const d=node('details','','reminders');d.append(node('summary',`近期提醒 ${studyState.reminders.length} 条`));studyState.reminders.forEach(r=>d.append(node('p',r.title+' · '+localTime(r.at))));status.append(d);}
 renderTasks();renderCalendar();renderMail();renderReview();
 for(const [id,types] of [['study-proposals',['task','task_update','event','event_update']],['mail-proposals',['mail']],['review-proposals',['quiz','attempt']]]){
  const pending=studyState.proposals.filter(p=>p.status==='pending'&&types.includes(p.action));$(id).replaceChildren(...pending.map(proposalCard));
 }
}
function showSlots(rows){$('plan-results').replaceChildren(...rows.map(a=>{const d=studyRow(a.title,localTime(a.start)+' — '+localTime(a.end));d.append(button('预览此安排',()=>proposeStudy('event',a),'secondary'));return d;}));}
$('task-form').onsubmit=editorAct(async e=>{e.preventDefault();const a=values(e.target);a.minutes=Number(a.minutes);a.priority=Number(a.priority);const action=a.id?'task_update':'task';if(!a.id)delete a.id;await proposeStudy(action,a);});
$('mail-form').onsubmit=editorAct(async e=>{e.preventDefault();await proposeStudy('mail',values(e.target));});
$('event-form').onsubmit=editorAct(async e=>{e.preventDefault();await proposeStudy('event_update',values(e.target));});
$('plan-form').onsubmit=editorAct(async e=>{e.preventDefault();await refreshStudy();const a=values(e.target);a.day_start=Number(a.day_start);a.day_end=Number(a.day_end);a.task_ids=studyState.tasks.filter(t=>t.status!=='done').slice(0,30).map(t=>t.id);if(!a.task_ids.length)throw Error('先创建一项未完成的任务，再寻找空档。');const plan=await api('/api/study/plan',a);showSlots(plan.slots);plan.unscheduled.forEach(t=>$('plan-results').append(node('p',t.title+'：'+t.reason)));});
$('ics-upload').onchange=act(async()=>{const file=$('ics-upload').files[0];if(!file)return;try{if(file.size>2000000)throw Error('ICS文件超过2MB。');const r=await api('/api/study/import',{content:await file.text()});openEditor('preview');$('editor-title').textContent='导入日历预览';showSlots(r.events);}finally{$('ics-upload').value='';}});
$('skill-install').onsubmit=act(async e=>{e.preventDefault();const r=await api('/api/skills/install',values(e.target));notice(r.message);await refreshSkills();});
$('study-refresh').onclick=act(refreshStudy);$('skills-refresh').onclick=act(refreshSkills);
$('editor-close').onclick=()=>$('editor-dialog').close();
$('task-filter').onchange=renderTasks;$('mail-search').oninput=renderMail;$('review-filter').onchange=()=>{reviewIndex=0;renderReview();};
for(const b of document.querySelectorAll('[data-editor]'))b.onclick=()=>openEditor(b.dataset.editor);
for(const b of document.querySelectorAll('[data-ask]'))b.onclick=()=>{view('chat');$('question').value=b.dataset.ask;$('question').focus();};
document.addEventListener('workspace-view',act(async e=>{if(['study','mail','review'].includes(e.detail))await refreshStudy();if(e.detail==='skills')await refreshSkills();}));
function sourceButton(s,d){const b=node('button','查看源码与清单','text-button');b.onclick=act(async()=>{const result=await api('/api/skills/source?id='+encodeURIComponent(s.id));const detail=node('details');detail.open=true;detail.append(node('summary','源码审阅'),node('p','内容指纹：'+result.hash,'subtle'));for(const f of result.files)detail.append(node('strong',f.path),node('pre',f.text));d.append(detail);b.disabled=true;});return b;}
async function refreshSkills(){const rows=await api('/api/skills');$('skills-list').replaceChildren(...rows.map(s=>{const d=studyRow(s.title||s.id,s.description||s.error);d.append(node('p',(s.enabled?'已启用':'未启用')+' / '+(s.kind==='python'?'本机Python执行':'内置适配')+' / '+(s.permissions||[]).join('、'),'subtle'));if(s.missing?.length)d.append(node('p','缺少：'+s.missing.join('、')));if(s.hash){d.append(sourceButton(s,d));const b=node('button',s.enabled?'停用':'启用','secondary');b.onclick=act(async()=>{if(!s.enabled&&s.kind==='python'&&!confirm('该Python技能拥有当前用户的本机权限，可能读写文件或联网。确认已审阅并信任代码后启用？'))return;await api('/api/skills/enable',{id:s.id,enabled:!s.enabled,hash:s.hash});await refreshSkills();});d.append(b);}if(s.last_run)d.append(node('small','最近执行：'+JSON.stringify(s.last_run)));return d;}));}
