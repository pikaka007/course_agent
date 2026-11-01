"""Explicit opt-in integration acceptance; only authored test data is sent."""
import json,shutil,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from course_agent.config import ROOT,Config
from course_agent.agent import run_agent
from course_agent.life import Life
from course_agent.plugins import Plugins

def main():
 if '--live' not in sys.argv:raise SystemExit('Pass --live to allow real API usage.')
 config=Config.from_env(ROOT);dest=ROOT/'evidence/v5/live';dest.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory() as td:
  root=Path(td);shutil.copytree(ROOT/'skills',root/'skills');shutil.copytree(ROOT/'prompts',root/'prompts');(root/'data').mkdir()
  (root/'data/lesson.md').write_text('MCP connects an AI client to external tools through a server. The client first lists tools and then calls a tool.',encoding='utf-8')
  life=Life(root)
  for action,args in [('task',{'title':'复习MCP','minutes':30,'priority':1,'due':''}),('event',{'title':'课程','start':'2026-11-02T09:00+08:00','end':'2026-11-02T10:00+08:00'})]:
   p=life.propose(action,args);life.confirm(p['id'])
  manager=Plugins(root);manager.install(ROOT/'examples/focus_minutes');p=next(p for p in manager.packages() if p['id']=='focus_minutes');manager.enable(p['id'],True,p['hash'])
  cases=[('calendar','读取学习工作区，为复习MCP任务在2026年11月2日09:00至12:00寻找不与课程冲突的30分钟学习空档。只生成计划预览，不创建日历事件。'),('mail','请起草给teacher@example.com的课程咨询邮件，主题为MCP学习咨询，正文询问下次课是否会讲工具调用。生成待确认草稿即可，不发送。'),('quiz','根据lesson.md生成一道关于MCP的简短自测题，带参考答案和原文引用，加入待确认列表。'),('extension','请使用专注时长换算技能，计算3轮、每轮25分钟，总共多少分钟。')]
  summary=[]
  for case,question in cases:
   if '--case' in sys.argv and case!=sys.argv[sys.argv.index('--case')+1]:continue
   r=run_agent(question,config,root=root)
   (dest/(case+'-'+r['run_id']+'.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
   names=[t['name'] for t in r['trace'] if t['result']['ok']]
   needed={'calendar':'skill_study_plan','mail':'skill_mail_draft','quiz':'skill_review_quiz','extension':'skill_focus_minutes'}[case]
   passed=r['status']=='completed' and needed in names and r['answer']!='现有资料无法确认。'
   if case=='calendar':
    tool=next((t for t in r['trace'] if t['name']==needed and t['result']['ok']),None)
    passed=passed and bool(tool['result']['data']['result']['slots']) and len(life.rows('event'))==1
   if case in ('mail','quiz'):passed=passed and bool([p for p in life.rows('proposal') if p['action']==case and p['status']=='pending']) and not life.rows(case)
   row={'case':case,'run_id':r['run_id'],'passed':bool(passed),'status':r['status'],'requests':len(r['provider_requests']),'seconds':r['elapsed_seconds'],'tokens':sum(x.get('usage',{}).get('total_tokens',0) for x in r['provider_requests'])};summary.append(row);print(json.dumps(row),flush=True)
  filename='summary-'+sys.argv[sys.argv.index('--case')+1]+'.json' if '--case' in sys.argv else 'summary.json'
  (dest/filename).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
  return 0 if all(r['passed'] for r in summary) else 1
if __name__=='__main__':raise SystemExit(main())
