import io
import json
import tempfile
import threading
import time
import urllib.request
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from course_agent.streaming import read_stream, Cancelled, CancelableHTTPHandler
from course_agent.evidence import review
from course_agent.sessions import Sessions
from course_agent.workspace import Workspace
from course_agent.tools import Skills, parse_document
from course_agent.agent import LLMClient, run_agent
from course_agent.config import Config
from scripts.evaluate_retrieval import evaluate
from course_agent.server import make_server
import test_server


def sse(deltas, finish='stop', done=True):
    events = [{'id':'test-completion', 'model':'test', 'choices':[{'index':0,'delta':d}]} for d in deltas]
    if finish:
        events.append({'choices':[{'index':0,'delta':{},'finish_reason':finish}]})
    events.append({'choices':[], 'usage':{'total_tokens':9}})
    raw = b''.join(b'data: '+json.dumps(e,ensure_ascii=False).encode()+b'\n\n' for e in events)
    return io.BytesIO(raw+(b'data: [DONE]\n\n' if done else b''))


def make_pdf(path, blank_first=True, encrypted=False, blank_only=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    if blank_first:
        writer.add_blank_page(width=500, height=500)
    if not blank_only:
        page = writer.add_blank_page(width=500,height=500)
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        content = DecodedStreamObject(); content.set_data(b'BT /F1 14 Tf 40 400 Td (MCP connects clients to tools.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(content)
    if encrypted:
        writer.encrypt('test-password')
    writer.write(path)


class StreamingTest(unittest.TestCase):
    def test_cancel_interrupts_waiting_http_read(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        release=threading.Event();ready=threading.Event();cancel=threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n');self.wfile.flush()
                ready.set();release.wait(3)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        timer=threading.Timer(.2,cancel.set)
        try:
            with urllib.request.build_opener(CancelableHTTPHandler()).open(f'http://127.0.0.1:{server.server_port}',timeout=5) as response:
                ready.wait(1);timer.start();start=time.monotonic()
                with self.assertRaises(Cancelled):read_stream(response,cancel)
                self.assertLess(time.monotonic()-start,1.5)
        finally:
            timer.cancel();release.set();server.shutdown();server.server_close();thread.join()
    def test_text_usage_and_hidden_reasoning(self):
        chunks=[]
        message, receipt=read_stream(sse([{'content':'中','reasoning_content':'hidden'},{'content':'文'}]),on_delta=chunks.append)
        self.assertEqual(''.join(chunks),'中文')
        self.assertEqual(message['content'],'中文')
        self.assertEqual(receipt['usage']['total_tokens'],9)
        self.assertNotIn('hidden',''.join(chunks))

    def test_fragmented_tool_arguments(self):
        first={'tool_calls':[{'index':0,'id':'tool1','function':{'name':'read_document','arguments':'{"filename":"'}}]}
        second={'tool_calls':[{'index':0,'function':{'arguments':'a.md","section":1}'}}]}
        message,_=read_stream(sse([first,second],finish='tool_calls'))
        self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']),{'filename':'a.md','section':1})

    def test_secret_split_across_all_boundaries(self):
        secret='private-example-token'
        text='prefix:'+secret+':suffix'
        for width in range(1,len(secret)+3):
            chunks=[]
            read_stream(sse([{'content':text[i:i+width]} for i in range(0,len(text),width)]),on_delta=chunks.append,secret=secret)
            self.assertEqual(''.join(chunks),'prefix:[REDACTED]:suffix')

    def test_truncated_or_length_limited_response_rejected(self):
        for response in [sse([{'content':'partial'}],done=False),sse([],finish=None),sse([],finish='length')]:
            with self.assertRaises(ValueError):read_stream(response)

    def test_cancel_during_chunks(self):
        cancel=threading.Event()
        with self.assertRaises(Cancelled):
            read_stream(sse([{'content':'one'},{'content':'two'}]),cancel,lambda _:cancel.set())


class EvidenceTest(unittest.TestCase):
    def check(self,quote='120 yuan',verdict='supported',ids=None):
        class Client:
            def complete(self,messages,tools):
                return {'content':json.dumps({'claims':ids if ids is not None else [{'id':0,'verdict':verdict,'reason':'test','evidence':[{'citation':'[a.md · 第1行]','quote':quote}]}]})}
        return review('Budget is 120 yuan. [a.md · 第1行]',[{'citation':'[a.md · 第1行]','text':'Budget is 120 yuan.'}],Client())
    def test_exact_supported_quote_passes(self):self.assertTrue(self.check()['ok'])
    def test_fabricated_quote_rejected(self):self.assertFalse(self.check(quote='200 yuan')['ok'])
    def test_unsupported_claim_rejected(self):self.assertFalse(self.check(verdict='unsupported')['ok'])
    def test_missing_or_bool_claim_id_rejected(self):
        self.assertFalse(self.check(ids=[])['ok'])
        self.assertFalse(self.check(ids=[{'id':False}])['ok'])


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.w=Workspace(self.root)
    def tearDown(self):self.tmp.cleanup()
    def append(self,s,rid='a'*12,question='预算'):
        self.w.append_turn(s,{'run_id':rid,'question':question,'answer':'120元','mode':'live','status':'completed'})
    def test_parallel_append_keeps_all_turns(self):
        s=self.w.session()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i:self.append(dict(s),f'{i:012x}'),range(12)))
        self.assertEqual(len(self.w.session(s['id'])['turns']),12)
    def test_search_export_delete_and_no_json_resurrection(self):
        s=self.w.session();self.append(s)
        legacy=self.root/'local/sessions'/f"{s['id']}.json";legacy.parent.mkdir(exist_ok=True)
        legacy.write_text(json.dumps(s),encoding='utf-8')
        logs=self.root/'logs';logs.mkdir(exist_ok=True);(logs/('a'*12+'.json')).write_text('{}')
        self.assertEqual(len(self.w.sessions('120')),1)
        self.w.delete_session(s['id'])
        self.assertFalse(legacy.exists());self.assertEqual(len(list(logs.iterdir())),0)
        self.assertEqual(Workspace(self.root).sessions(),[])
    def test_legacy_migrates_once(self):
        with tempfile.TemporaryDirectory() as td:
            state=Path(td);(state/'sessions').mkdir()
            s={'id':'b'*16,'title':'legacy','updated':datetime.now(timezone.utc).isoformat(),'turns':[{'run_id':'c'*12,'question':'old','answer':'yes'}]}
            (state/'sessions/legacy.json').write_text(json.dumps(s))
            store=Sessions(state);self.assertEqual(len(store.get(s['id'])['turns']),1)
            store.delete(s['id']);self.assertEqual(Sessions(state).list(),[])
    def test_retention_preview_does_not_delete(self):
        s=self.w.session();self.append(s)
        with self.w.store.connect() as db:db.execute('UPDATE sessions SET updated=?',((datetime.now(timezone.utc)-timedelta(days=60)).isoformat(),))
        self.assertEqual(self.w.store.expired(30),[s['id']]);self.assertEqual(len(self.w.sessions()),1)
    def test_history_view_is_bounded_but_export_and_delete_are_complete(self):
        s=self.w.session()
        for i in range(103):self.append(s,f'{i:012x}')
        self.assertEqual(len(self.w.session(s['id'])['turns']),100)
        self.assertEqual(len(self.w.store.get(s['id'],full=True)['turns']),103)
        self.assertEqual(len(self.w.store.delete(s['id'])),103)
    def test_pdf_real_page_and_search(self):
        path=self.root/'data/test.pdf';make_pdf(path)
        rows=list(parse_document(path));self.assertEqual(rows[0][0],2)
        hits=Skills(self.root/'data',self.root/'outputs').search_documents('MCP',3)['matches']
        self.assertEqual(hits[0]['citation'],'[test.pdf · 第2页]')
    def test_pdf_blank_and_encrypted_rejected(self):
        for kwargs in [{'blank_only':True},{'encrypted':True}]:
            path=self.root/'data/test.pdf';make_pdf(path,**kwargs)
            with self.assertRaises(ValueError):list(parse_document(path))
    def test_index_update_removal_and_scope(self):
        data=self.root/'data';(data/'a.md').write_text('MCP first');(data/'b.md').write_text('MCP second')
        skills=Skills(data,self.root/'outputs');self.assertEqual(len(skills.search_documents('MCP',3)['matches']),2)
        (data/'a.md').write_text('RAG changed');(data/'b.md').unlink()
        self.assertEqual(skills.search_documents('MCP',3)['matches'],[])
        scoped=Skills(data,self.root/'outputs',selected_documents=[])
        self.assertEqual(scoped.search_documents('RAG',3)['matches'],[])
    def test_authored_retrieval_evaluation(self):
        result=evaluate()['results'];self.assertEqual(result['v4_fulltext']['hit_at_3'],1)
        self.assertEqual(result['v4_fulltext']['negative_accuracy'],1)


class VerifiedLoopTest(unittest.TestCase):
    def test_exact_refusal_can_end_after_unrelated_read(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'data').mkdir();(root/'prompts').mkdir()
            (root/'prompts/system.md').write_text('Use evidence.');(root/'data/a.md').write_text('Budget is 120 yuan.')
            class Client:
                n=0
                def complete(self,messages,specs):
                    self.n+=1
                    if self.n==1:return {'tool_calls':[{'id':'call1','type':'function','function':{'name':'read_document','arguments':'{"filename":"a.md","section":1}'}}]}
                    return {'content':'现有资料无法确认。'}
            r=run_agent('Quantum threshold?',Config('','','test'),root=root,client=Client())
            self.assertEqual(r['status'],'completed')
            self.assertEqual(r['evidence_review']['status'],'insufficient_evidence')
    def test_inaccurate_answer_repaired_before_success(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'data').mkdir();(root/'prompts').mkdir()
            (root/'prompts/system.md').write_text('Use evidence.');(root/'data/a.md').write_text('Budget is 120 yuan.')
            class Client(LLMClient):
                def __init__(self):self.requests=[];self.n=0;self.reviews=0
                def stream_complete(self,messages,specs,on_delta,cancel=None):
                    if not specs:
                        self.reviews+=1
                        return {'content':json.dumps({'claims':[{'id':0,'verdict':'unsupported' if self.reviews==1 else 'supported','reason':'budget','evidence':[{'citation':'[a.md · 第1行]','quote':'120 yuan'}]}]})}
                    self.n+=1
                    if self.n==1:return {'tool_calls':[{'id':'call1','type':'function','function':{'name':'read_document','arguments':'{"filename":"a.md","section":1}'}}]}
                    return {'content':('Budget is 200 yuan.' if self.n==2 else 'Budget is 120 yuan.')+' [a.md · 第1行]'}
            r=run_agent('Budget?',Config('','','test'),root=root,client=Client())
            self.assertEqual(r['status'],'completed');self.assertIn('120 yuan',r['answer'])
            self.assertEqual(r['metrics']['evidence_repairs'],1)


class V4HTTPTest(unittest.TestCase):
    setUp=test_server.ServerTest.setUp
    tearDown=test_server.ServerTest.tearDown
    request=test_server.ServerTest.request
    def test_search_export_and_confirmed_delete(self):
        workspace=Workspace(self.root);session=workspace.session()
        workspace.append_turn(session,{'run_id':'d'*12,'question':'PDF页码测试','answer':'第二页','status':'completed','mode':'demo'})
        code,rows,_=self.request('/api/sessions?q=PDF');self.assertEqual(rows[0]['id'],session['id'])
        code,body,headers=self.request('/api/session-export?id='+session['id'])
        self.assertEqual(code,200);self.assertEqual(body['turns'][0]['answer'],'第二页')
        self.assertIn('attachment',headers['Content-Disposition'])
        auth={'X-Agent-Token':self.token}
        self.assertEqual(self.request('/api/sessions/delete',{'id':session['id']},auth)[0],400)
        self.assertEqual(self.request('/api/sessions/delete',{'id':session['id'],'confirm':True},auth)[0],200)
        self.assertEqual(workspace.sessions(),[])
    def test_retention_rejects_stale_preview(self):
        auth={'X-Agent-Token':self.token}
        self.assertEqual(self.request('/api/retention-clean',{'days':30,'ids':['e'*16],'confirm':True},auth)[0],400)
        self.assertEqual(self.request('/api/retention-preview?days=0')[0],400)


if __name__=='__main__':unittest.main()
