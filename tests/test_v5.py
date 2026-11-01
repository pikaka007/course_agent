import json,shutil,tempfile,unittest
from pathlib import Path
from course_agent.life import Life
from course_agent.plugins import Plugins
from course_agent.tools import Skills
from course_agent.config import ROOT,Config
from course_agent.agent import run_agent
import test_server

class StudyTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.life=Life(self.root)
  shutil.copytree(ROOT/'skills',self.root/'skills');shutil.copytree(ROOT/'prompts',self.root/'prompts')
 def tearDown(self):self.tmp.cleanup()
 def add(self,kind,args):
  p=self.life.propose(kind,args);r=self.life.confirm(p['id']);return self.life.get(kind,r['result_id'])
 def task(self):return self.add('task',{'title':'复习MCP','minutes':60,'priority':1,'due':''})
 def test_proposal_not_applied_until_confirmed_and_idempotent(self):
  p=self.life.propose('task',{'title':'阅读','minutes':30});self.assertEqual(self.life.rows('task'),[])
  self.life.confirm(p['id']);self.life.confirm(p['id']);self.assertEqual(len(self.life.rows('task')),1)
 def test_rejected_proposal_does_not_apply(self):
  p=self.life.propose('task',{'title':'阅读'});self.life.confirm(p['id'],False);self.life.confirm(p['id']);self.assertEqual(self.life.rows('task'),[])
 def test_progress_preserves_estimate_and_title(self):
  t=self.task();p=self.life.propose('task_update',{'id':t['id'],'status':'done'});self.life.confirm(p['id']);r=self.life.get('task',t['id']);self.assertEqual((r['status'],r['minutes']),('done',60))
 def test_planner_avoids_conflict_and_respects_window(self):
  t=self.task();self.add('event',{'title':'上课','start':'2026-11-02T09:00+08:00','end':'2026-11-02T10:00+08:00'})
  r=self.life.plan({'task_ids':[t['id']],'start':'2026-11-02T09:00','end':'2026-11-02T12:00'})
  self.assertIn('T10:00',r['slots'][0]['start']);self.assertFalse(r['saved'])
 def test_confirm_rechecks_conflicts(self):
  a={'title':'学习','start':'2026-11-02T09:00','end':'2026-11-02T10:00'};p=self.life.propose('event',a);self.add('event',a)
  with self.assertRaises(ValueError):self.life.confirm(p['id'])
 def test_unschedulable_task_is_explicit(self):
  t=self.task();r=self.life.plan({'task_ids':[t['id']],'start':'2026-11-02T09:00','end':'2026-11-02T09:30'});self.assertEqual(len(r['unscheduled']),1)
 def test_reschedule_excludes_self_but_rechecks_other_events(self):
  e=self.add('event',{'title':'学习','start':'2026-11-02T09:00','end':'2026-11-02T10:00'})
  p=self.life.propose('event_update',{'id':e['id'],'start':'2026-11-02T09:30','end':'2026-11-02T10:30'});self.life.confirm(p['id'])
  self.assertEqual(len(self.life.rows('event')),1)
  self.add('event',{'title':'课程','start':'2026-11-02T11:00','end':'2026-11-02T12:00'})
  p=self.life.propose('event_update',{'id':e['id'],'start':'2026-11-02T10:30','end':'2026-11-02T11:30'})
  with self.assertRaises(ValueError):self.life.confirm(p['id'])
 def test_late_task_edit_preserves_other_confirmed_changes(self):
  t=self.task();p=self.life.propose('task_update',{'id':t['id'],'title':'新标题'})
  status=self.life.propose('task_update',{'id':t['id'],'status':'done'});self.life.confirm(status['id']);self.life.confirm(p['id'])
  self.assertEqual(self.life.get('task',t['id'])['status'],'done')
 def test_plugin_source_review_cannot_read_arbitrary_path(self):
  manager=Plugins(self.root);manager.install(ROOT/'examples/focus_minutes')
  self.assertTrue(any(f['path']=='handler.py' for f in manager.source('focus_minutes')['files']))
  with self.assertRaises(ValueError):manager.source('../../.env')
 def test_ics_timezone_preview_and_atomic_rejection(self):
  content='BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Class\nDTSTART:20261102T010000Z\nDTEND:20261102T020000Z\nEND:VEVENT\nEND:VCALENDAR'
  r=self.life.import_ics(content);self.assertIn('T09:00',r['events'][0]['start']);self.assertEqual(self.life.rows('event'),[])
  with self.assertRaises(ValueError):self.life.import_ics(content.replace('SUMMARY:Class','RRULE:FREQ=DAILY\nSUMMARY:Class'))
 def test_ics_unsupported_timezone_rejected(self):
  with self.assertRaises(ValueError):self.life.import_ics('BEGIN:VEVENT\nDTSTART;TZID=America/New_York:20261102T090000\nDTEND:20261102T100000\nEND:VEVENT')
 def test_mail_unicode_export_and_no_header_injection(self):
  m=self.add('mail',{'to':'teacher@example.com','subject':'课程咨询','body':'您好，请问这处概念如何理解？'})
  from email import policy
  from email.parser import BytesParser
  parsed=BytesParser(policy=policy.default).parsebytes(self.life.mail_export(m['id']));self.assertEqual(parsed['Subject'],'课程咨询');self.assertEqual(parsed['X-Unsent'],'1')
  with self.assertRaises(ValueError):self.life.propose('mail',{'to':'a@b.com','subject':'Hi\nBcc: a@b.com','body':'hello'})
 def test_quiz_requires_current_source_and_self_rating_is_recorded(self):
  manager=Plugins(self.root);skills=Skills(self.root/'data',self.root/'outputs')
  a={'title':'自测','question':'MCP是什么？','expected':'协议','citation':'[a.md · 第1行]'}
  self.assertFalse(manager.dispatch('skill_review_quiz',a,skills)['ok'])
  skills.record('a.md',1,'第1行','MCP is a protocol.');r=manager.dispatch('skill_review_quiz',a,skills);self.assertTrue(r['ok'])
  p=r['data']['result']['proposal'];applied=self.life.confirm(p['id']);q=self.life.get('quiz',applied['result_id'])
  a=self.add('attempt',{'quiz_id':q['id'],'answer':'协议','rating':'hard'});self.assertIn('next_review',a)
 def test_plugin_install_disable_activation_and_hash_change(self):
  manager=Plugins(self.root);manager.install(ROOT/'examples/focus_minutes');self.assertFalse(next(p for p in manager.public() if p['id']=='focus_minutes')['enabled'])
  p=next(p for p in manager.packages() if p['id']=='focus_minutes');manager.enable(p['id'],True,p['hash']);skills=Skills(self.root/'data',self.root/'outputs')
  r=Plugins(self.root).dispatch('skill_focus_minutes',{'rounds':3,'minutes':25},skills);self.assertEqual(r['data']['result']['total_minutes'],75)
  handler=Path(p['folder'])/'handler.py';handler.write_text(handler.read_text()+'\n# modified')
  self.assertFalse(next(p for p in Plugins(self.root).public() if p['id']=='focus_minutes')['enabled'])
 def test_bad_argument_and_disabled_skill_blocked(self):
  manager=Plugins(self.root);p=manager.packages()[0];manager.enable(p['id'],False,p['hash']);self.assertNotIn('skill_'+p['id'],[s['function']['name'] for s in Plugins(self.root).specs()])
  self.assertFalse(manager.dispatch('skill_study_task',{'title':'x','minutes':True,'priority':1,'due':''},Skills(self.root/'data',self.root/'outputs'))['ok'])
 def test_new_skill_runs_through_unchanged_agent_loop(self):
  manager=Plugins(self.root);manager.install(ROOT/'examples/focus_minutes');p=next(p for p in manager.packages() if p['id']=='focus_minutes');manager.enable(p['id'],True,p['hash'])
  class Client:
   n=0
   def complete(self,messages,specs):
    self.n+=1
    if self.n==1:
     assert any(s['function']['name']=='skill_focus_minutes' for s in specs)
     return {'tool_calls':[{'id':'extension1','type':'function','function':{'name':'skill_focus_minutes','arguments':'{"rounds":3,"minutes":25}'}}]}
    return {'content':'总专注时长为75分钟。[技能_focus_minutes · 本次执行结果]'}
  r=run_agent('三轮每轮25分钟是多少？',Config('','','test'),root=self.root,client=Client());self.assertEqual(r['status'],'completed');self.assertEqual(r['trace'][0]['name'],'skill_focus_minutes')

class StudyHTTPTest(unittest.TestCase):
 setUp=test_server.ServerTest.setUp
 tearDown=test_server.ServerTest.tearDown
 request=test_server.ServerTest.request
 def test_task_preview_and_confirmation_http(self):
  auth={'X-Agent-Token':self.token};code,p,_=self.request('/api/study/propose',{'action':'task','args':{'title':'test'}},auth);self.assertEqual(code,200)
  self.assertEqual(self.request('/api/study')[1]['tasks'],[])
  self.assertEqual(self.request('/api/study/confirm',{'id':p['id'],'accept':True},auth)[0],200)
  self.assertEqual(len(self.request('/api/study')[1]['tasks']),1)
 def test_skills_mutation_requires_token(self):
  self.assertEqual(self.request('/api/skills/install',{'path':'invalid'})[0],403)

if __name__=='__main__':unittest.main()
