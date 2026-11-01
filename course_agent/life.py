"""Local study workspace. Model mutations are proposals; UI/CLI confirms them."""
import json, re, sqlite3, uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
TZ=timezone(timedelta(hours=8))

def stamp(value):
    try:
        d=datetime.fromisoformat(value.replace('Z','+00:00'))
        return (d if d.tzinfo else d.replace(tzinfo=TZ)).astimezone(TZ)
    except (ValueError,TypeError,AttributeError):raise ValueError('时间须为 ISO 格式，例如 2026-11-02T09:00+08:00') from None

def text(value, maximum=4000):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum:raise ValueError('文字为空或超过长度限制。')
    return value.strip()

class Life:
    def __init__(self,root):
        self.root=Path(root);self.local=self.root/'local';self.local.mkdir(exist_ok=True,parents=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY,kind TEXT,body TEXT)')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.local/'study.sqlite3',timeout=15)
        try:
            with db:yield db
        finally:db.close()
    def put(self,kind,body,id=None):
        id=id or uuid.uuid4().hex[:16]
        with self.db() as db:db.execute('INSERT OR REPLACE INTO items VALUES(?,?,?)',(id,kind,json.dumps(body,ensure_ascii=False)))
        return {'id':id,**body}
    def rows(self,kind):
        with self.db() as db:return [{'id':r[0],**json.loads(r[1])} for r in db.execute('SELECT id,body FROM items WHERE kind=? ORDER BY rowid DESC',(kind,))]
    def get(self,kind,id):
        with self.db() as db:r=db.execute('SELECT body FROM items WHERE kind=? AND id=?',(kind,id)).fetchone()
        if not r:raise ValueError('记录不存在。')
        return {'id':id,**json.loads(r[0])}
    def snapshot(self):
        now=datetime.now(TZ)
        tasks=self.rows('task');events=self.rows('event')
        reminders=[{'title':t['title'],'at':t['due'],'kind':'任务到期'} for t in tasks if t['status']!='done' and t.get('due') and stamp(t['due'])<=now+timedelta(days=1)]
        reminders += [{'title':e['title'],'at':e['start'],'kind':'即将开始'} for e in events if now<=stamp(e['start'])<=now+timedelta(hours=24)]
        latest={}
        for a in self.rows('attempt'):latest.setdefault(a['quiz_id'],a)
        reminders += [{'title':self.get('quiz',a['quiz_id'])['title'],'at':a['next_review'],'kind':'复习到期'} for a in latest.values() if stamp(a['next_review'])<=now]
        return {'timezone':'Asia/Shanghai','now':now.isoformat(),'tasks':tasks,'events':events,'drafts':self.rows('mail'),'quizzes':self.rows('quiz'),'attempts':self.rows('attempt'),'proposals':self.rows('proposal'),'reminders':reminders,'connections':{'calendar':'本地 ICS；在线账户未连接','mail':'本地草稿；在线邮箱未连接'},'reminder_scope':'打开应用时检查，不提供后台推送'}
    def validate(self,action,args):
        if not isinstance(args,dict):raise ValueError('参数必须为对象。')
        allowed={'task':{'title','minutes','priority','due','status'},'event':{'title','start','end','task_id','location'},'event_update':{'id','start','end'},'mail':{'to','subject','body'},'quiz':{'title','question','expected','citation','source'},'task_update':{'id','status','minutes','priority','title','due'},'attempt':{'quiz_id','answer','rating'}}
        if action not in allowed or set(args)-allowed[action]:raise ValueError('操作或参数字段不受支持。')
        a=dict(args)
        if action in ('task','task_update'):
            if action=='task_update':a={**self.get('task',a['id']),**a};a.pop('id',None)
            a['title']=text(a.get('title'),160)
            a.setdefault('minutes',30);a.setdefault('priority',2);a.setdefault('status','todo');a.setdefault('due','')
            if type(a['minutes']) is not int or not 5<=a['minutes']<=480:raise ValueError('预计时长为5至480分钟。')
            if a['priority'] not in [1,2,3] or type(a['priority']) is not int or a['status'] not in ['todo','doing','done']:raise ValueError('优先级或状态无效。')
            if a['due']:a['due']=stamp(a['due']).isoformat()
        elif action in ('event','event_update'):
            if action=='event_update':a={**self.get('event',a['id']),**a};a.pop('id',None)
            a['title']=text(a.get('title'),160);start,end=stamp(a.get('start')),stamp(a.get('end'))
            if not timedelta(0)<end-start<=timedelta(days=7):raise ValueError('事件结束时间须晚于开始，且不超过7天。')
            a.update(start=start.isoformat(),end=end.isoformat())
            if a.get('task_id'):self.get('task',a['task_id'])
            a['location']=str(a.get('location',''))[:300]
        elif action=='mail':
            addr=text(a.get('to'),254)
            if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+',addr):raise ValueError('填写一个有效收件人邮箱。')
            a['to']=addr;a['subject']=text(a.get('subject'),200);a['body']=text(a.get('body'),18000)
            if '\r' in a['subject'] or '\n' in a['subject']:raise ValueError('主题不能换行。')
        elif action=='quiz':
            for key in ('title','question','expected','citation','source'):a[key]=text(a.get(key),12000 if key=='source' else 2000)
        else:
            self.get('quiz',a.get('quiz_id'));a['answer']=text(a.get('answer'),12000)
            if a.get('rating') not in ['again','hard','good']:raise ValueError('请选择再次练习、较难或掌握。')
            a['next_review']=(datetime.now(TZ)+timedelta(days={'again':1,'hard':3,'good':7}[a['rating']])).isoformat()
        return a
    def propose(self,action,args):
        validated=self.validate(action,args)
        if action in ('task_update','event_update'):validated['id']=args['id']
        if action in ('task_update','event_update'):validated=dict(args)
        return self.put('proposal',{'action':action,'args':validated,'status':'pending','created':datetime.now(TZ).isoformat()})
    def confirm(self,id,accept=True):
        # Single transaction makes double-click confirmation idempotent.
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT body FROM items WHERE id=? AND kind='proposal'",(id,)).fetchone()
            if not row:raise ValueError('待确认操作不存在。')
            p=json.loads(row[0])
            if p['status']!='pending':return p
            if not accept:p['status']='rejected'
            else:
                action=p['action'];args=p['args'];target=args.get('id') if action in ('task_update','event_update') else uuid.uuid4().hex[:16]
                a=self.validate(action,{k:v for k,v in args.items() if k!='next_review'})
                if action in ('event','event_update'):
                    existing=[json.loads(r[0]) for r in db.execute("SELECT body FROM items WHERE kind='event' AND id!=?",(target,))]
                    if any(stamp(e['start'])<stamp(a['end']) and stamp(a['start'])<stamp(e['end']) for e in existing):raise ValueError('该时间已被占用，请重新规划。')
                kind={'task_update':'task','event_update':'event'}.get(action,action)
                db.execute('INSERT OR REPLACE INTO items VALUES(?,?,?)',(target,kind,json.dumps(a,ensure_ascii=False)))
                p.update(status='applied',result_id=target)
            db.execute('UPDATE items SET body=? WHERE id=?',(json.dumps(p,ensure_ascii=False),id))
            return p
    def plan(self,args):
        ids=args.get('task_ids',[])
        if not isinstance(ids,list) or not 1<=len(ids)<=30 or len(set(ids))!=len(ids):raise ValueError('选择1至30个不同任务。')
        start,end=stamp(args.get('start')),stamp(args.get('end'))
        if not timedelta(0)<end-start<=timedelta(days=14):raise ValueError('规划范围须为未来14天以内的有效区间。')
        day_start=int(args.get('day_start',9));day_end=int(args.get('day_end',21))
        if not 0<=day_start<day_end<=24:raise ValueError('每日学习时段无效。')
        tasks=sorted([self.get('task',id) for id in ids],key=lambda t:(t['priority'],t.get('due') or '9999'))
        occupied=[(stamp(e['start']),stamp(e['end'])) for e in self.rows('event')];slots=[];unscheduled=[]
        for t in tasks:
            if t['status']=='done':continue
            if any(e.get('task_id')==t['id'] and stamp(e['end'])>start for e in self.rows('event')):
                unscheduled.append({'id':t['id'],'title':t['title'],'reason':'已有安排；可使用日历的调整时间操作，避免重复排期'});continue
            duration=timedelta(minutes=t['minutes']);at=start
            deadline=min(end,stamp(t['due'])) if t.get('due') else end
            while at+duration<=deadline:
                finish=at+duration
                close=at.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(hours=day_end)
                if at.hour>=day_start and finish<=close and not any(a<finish and at<b for a,b in occupied):
                    slots.append({'title':'学习：'+t['title'],'task_id':t['id'],'start':at.isoformat(),'end':finish.isoformat(),'location':''});occupied.append((at,finish));break
                at+=timedelta(minutes=15)
            else:unscheduled.append({'id':t['id'],'title':t['title'],'reason':'所选时段内没有足够的连续空档'})
        return {'slots':slots,'unscheduled':unscheduled,'saved':False,'timezone':'Asia/Shanghai'}
    def import_ics(self,content):
        if not isinstance(content,str) or len(content)>2_000_000:raise ValueError('日历文件过大。')
        lines=re.sub(r'\r?\n[ \t]','',content).splitlines();events=[];current=None
        for line in lines:
            if line=='BEGIN:VEVENT':current={}
            elif line=='END:VEVENT':
                if current is not None:events.append(current)
                current=None
            elif current is not None and ':' in line:
                key,value=line.split(':',1);current[key]=value
        if not events or len(events)>1000:raise ValueError('需要包含1至1000个事件的ICS文件。')
        def dt(row,name):
            key=next((k for k in row if k.split(';')[0]==name),None)
            if not key:raise ValueError('事件缺少开始或结束时间。')
            val=row[key];zone=timezone.utc if val.endswith('Z') else TZ
            if 'TZID=' in key and key.split('TZID=')[1].strip('"') not in ('Asia/Shanghai','UTC'):raise ValueError('当前导入支持 UTC 和 Asia/Shanghai；请导出为UTC，避免时区误读。')
            if 'TZID=UTC' in key:zone=timezone.utc
            fmt='%Y%m%d' if len(val)==8 else '%Y%m%dT%H%M%S'
            try:return datetime.strptime(val.rstrip('Z'),fmt).replace(tzinfo=zone).isoformat()
            except ValueError:raise ValueError('ICS日期格式不受支持。') from None
        previews=[]
        for row in events:
            if any(k.split(';')[0] in ('RRULE','RDATE','EXDATE','RECURRENCE-ID') for k in row):raise ValueError('重复事件需先展开为独立事件；本次未导入任何事件。')
            if row.get('STATUS')=='CANCELLED':continue
            previews.append(self.validate('event',{'title':row.get('SUMMARY','未命名事件').replace('\\n',' '),'start':dt(row,'DTSTART'),'end':dt(row,'DTEND'),'location':row.get('LOCATION','')}))
        return {'events':previews,'imported':False,'message':'预览后逐条确认；已有时间冲突会被阻止。'}
    def mail_export(self,id):
        draft=self.get('mail',id);m=EmailMessage(policy=SMTP);m['To']=draft['to'];m['Subject']=draft['subject'];m['X-Unsent']='1';m.set_content(draft['body'])
        return m.as_bytes()
