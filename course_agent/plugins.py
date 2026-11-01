"""Versioned skill packages, explicit activation and hash-pinned executable code."""
import hashlib,json,os,re,shutil,subprocess,sys
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from .workspace import atomic_json
from .life import Life

ACTIONS={'context','plan','task','task_update','event','event_update','mail','quiz','attempt'}

def validate(value,spec):
    kind=spec.get('type')
    if kind=='object':
        if not isinstance(value,dict) or set(value)-set(spec.get('properties',{})) or set(spec.get('required',[]))-set(value):raise ValueError('技能参数字段不匹配。')
        for k,v in value.items():validate(v,spec['properties'][k])
    elif kind=='array':
        if not isinstance(value,list) or len(value)>spec.get('maxItems',100):raise ValueError('数组参数无效。')
        for v in value:validate(v,spec.get('items',{}))
    elif kind=='string':
        if not isinstance(value,str) or not spec.get('minLength',0)<=len(value)<=spec.get('maxLength',18000):raise ValueError('字符串参数无效。')
    elif kind=='integer':
        if type(value) is not int or not spec.get('minimum',-1000000)<=value<=spec.get('maximum',1000000):raise ValueError('整数参数无效。')
    elif kind=='boolean':
        if type(value) is not bool:raise ValueError('布尔参数无效。')
    else:raise ValueError('不支持的参数类型。')
    if 'enum' in spec and value not in spec['enum']:raise ValueError('参数不在枚举范围。')

class Plugins:
    def __init__(self,root):
        self.root=Path(root);self.local=self.root/'local';self.local.mkdir(exist_ok=True,parents=True)
        self.path=self.local/'skills.json'
        self.settings=json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}
    def packages(self):
        rows=[]
        for base,builtin in [(self.root/'skills',True),(self.local/'skills',False)]:
            for folder in sorted(base.glob('*')):
                if not folder.is_dir() or folder.is_symlink():continue
                try:
                    m=json.loads((folder/'skill.json').read_text('utf-8'));self.check(m)
                    if m['id']!=folder.name:raise ValueError('目录名必须与技能id一致。')
                    files=[p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
                    if any(p.is_symlink() for p in files):raise ValueError('技能不允许链接文件。')
                    fingerprint=hashlib.sha256(b''.join(p.relative_to(folder).as_posix().encode()+p.read_bytes() for p in sorted(files))).hexdigest()
                    setting=self.settings.get(m['id'],{})
                    enabled=setting.get('enabled',builtin)
                    if not builtin and setting.get('hash')!=fingerprint:enabled=False
                    missing=[]
                    for requirement in m.get('requires',[]):
                        try:version(requirement)
                        except PackageNotFoundError:missing.append(requirement)
                    if m['kind']=='python' and not (folder/'handler.py').is_file():missing.append('handler.py')
                    rows.append({**m,'folder':str(folder),'hash':fingerprint,'enabled':enabled and not missing,'builtin':builtin,'missing':missing,'last_run':setting.get('last_run')})
                except (ValueError,OSError,KeyError) as e:
                    rows.append({'id':folder.name,'title':folder.name,'enabled':False,'error':str(e),'builtin':builtin})
        return rows
    @staticmethod
    def check(m):
        if not isinstance(m,dict):raise ValueError('技能清单必须是对象。')
        if not isinstance(m.get('requires',[]),list) or any(not isinstance(r,str) or not re.fullmatch('[A-Za-z0-9_.-]{1,80}',r) for r in m.get('requires',[])):raise ValueError('依赖需为发行包名称列表。')
        if m.get('version')!=1 or not re.fullmatch('[a-z][a-z0-9_]{2,40}',m.get('id','')):raise ValueError('需要version=1和合法技能id。')
        if m.get('kind') not in ('builtin','python'):raise ValueError('当前支持内置适配或Python JSON协议技能，不能直接执行任意SKILL.md。')
        if not isinstance(m.get('description'),str) or len(m['description'])>2000:raise ValueError('缺少技能说明。')
        if m['kind']=='builtin' and m.get('action') not in ACTIONS:raise ValueError('内置操作不存在。')
        if not isinstance(m.get('parameters'),dict) or m['parameters'].get('type')!='object':raise ValueError('需要object参数Schema。')
    def public(self):return [{k:v for k,v in p.items() if k not in ('folder','parameters')} for p in self.packages()]
    def source(self,id):
        p=next((p for p in self.packages() if p['id']==id and not p.get('error')),None)
        if not p:raise ValueError('技能不存在或无效。')
        folder=Path(p['folder'])
        files=[f for f in sorted(folder.rglob('*')) if f.is_file() and '__pycache__' not in f.parts]
        if any(f.is_symlink() for f in files) or sum(f.stat().st_size for f in files)>2_000_000:raise ValueError('源码不可预览。')
        return {'id':id,'hash':p['hash'],'files':[{'path':f.relative_to(folder).as_posix(),'text':f.read_text('utf-8',errors='replace')} for f in files]}
    def enable(self,id,enabled,fingerprint):
        p=next((p for p in self.packages() if p['id']==id),None)
        if not p or p.get('error') or p.get('missing'):raise ValueError('技能无效或缺少执行入口。')
        if type(enabled) is not bool or fingerprint!=p['hash']:raise ValueError('技能内容有变化，请重新审阅后启用。')
        self.settings[id]={**self.settings.get(id,{}),'enabled':enabled,'hash':fingerprint};atomic_json(self.path,self.settings)
        return {'enabled':enabled,'id':id}
    def install(self,directory):
        src=Path(directory).resolve()
        if not src.is_dir() or src.is_symlink():raise ValueError('请提供技能文件夹路径。')
        m=json.loads((src/'skill.json').read_text('utf-8'));self.check(m)
        if m['kind']!='python':raise ValueError('第三方包必须使用python JSON执行协议。')
        if any(p['id']==m['id'] for p in self.packages()):raise ValueError('同名技能已存在，请使用新版本id。')
        files=list(src.rglob('*'))
        if len(files)>100 or any(p.is_symlink() for p in files) or sum(p.stat().st_size for p in files if p.is_file())>2_000_000:raise ValueError('技能包过大或包含链接。')
        if any(p.is_file() and p.suffix.lower() not in ('.py','.json','.md','.txt') for p in files):raise ValueError('技能包仅允许源码、JSON和说明。')
        dst=self.local/'skills'/m['id'];dst.parent.mkdir(exist_ok=True);shutil.copytree(src,dst)
        return {'id':m['id'],'enabled':False,'message':'已安装但未执行。检查代码和依赖后，按内容哈希明确启用。Python技能拥有当前用户的本机权限，不是安全沙箱。'}
    def specs(self):return [{'type':'function','function':{'name':'skill_'+p['id'],'description':p['description'],'parameters':p['parameters']}} for p in self.packages() if p['enabled']]
    def dispatch(self,name,args,skills):
        p=next((p for p in self.packages() if 'skill_'+p['id']==name and p['enabled']),None)
        if not p:return {'ok':False,'message':'技能未启用或内容已改变。'}
        status='error'
        try:
            a=json.loads(args) if isinstance(args,str) else args;validate(a,p['parameters'])
            life=Life(self.root)
            if p['kind']=='builtin':
                action=p['action']
                if action=='context':result=life.snapshot()
                elif action=='plan':result=life.plan(a)
                else:
                    if action=='quiz':
                        cite=a.get('citation');record=skills.source_records.get(cite)
                        if not record:raise ValueError('自测题必须引用本轮读取的原文。')
                        a={**a,'source':record['text']}
                    result={'proposal':life.propose(action,a),'applied':False,'message':'仅创建预览；用户确认后才生效。'}
            else:
                env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','PATH','TEMP','TMP')};env['PYTHONIOENCODING']='utf-8'
                proc=subprocess.run([sys.executable,'-I',str(Path(p['folder'])/'handler.py')],input=json.dumps(a),cwd=p['folder'],env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
                if proc.returncode or len(proc.stdout)>100000:raise ValueError('技能执行失败，请检查其依赖和入口；未输出脚本错误原文以免泄露配置。')
                result=json.loads(proc.stdout)
            record=skills.record('技能_'+p['id'],1,'本次执行结果',json.dumps(result,ensure_ascii=False)[:24000])
            result={'result':result,'citation':record['citation'],'trust':'本地工具返回；生成的草稿仍需人工核对'}
            status='completed'
            return {'ok':True,'data':result}
        except (ValueError,OSError,subprocess.TimeoutExpired,TypeError,KeyError) as e:
            return {'ok':False,'message':str(e) if isinstance(e,ValueError) else '技能执行失败或超时。'}

        finally:
            try:
                settings=json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}
                settings[p['id']]={**settings.get(p['id'],{}),'last_run':{'status':status,'at':datetime.now(timezone.utc).isoformat()}}
                atomic_json(self.path,settings)
            except (OSError,ValueError):pass
