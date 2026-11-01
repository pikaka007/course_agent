"""Opt-in real provider acceptance on self-authored data. Incurs API usage."""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from course_agent.agent import run_agent
from course_agent.config import ROOT, Config


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='明确启用真实模型请求，会产生用量')
    parser.add_argument('--case',choices=['all','pdf','absence'],default='all')
    args=parser.parse_args()
    if not args.live:
        parser.error('真实验收必须显式提供 --live')
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    config=Config.from_env(ROOT)
    destination=ROOT/'evidence/v4/live-acceptance';destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);(root/'data').mkdir();(root/'prompts').mkdir()
        shutil.copyfile(ROOT/'prompts/system.md',root/'prompts/system.md')
        writer=PdfWriter();writer.add_blank_page(width=500,height=500)
        page=writer.add_blank_page(width=500,height=500)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        content=DecodedStreamObject();content.set_data(b'BT /F1 14 Tf 40 400 Td (The course project budget is 120 yuan.) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(content);writer.write(root/'data/budget.pdf')
        questions=[('根据 budget.pdf，项目预算是多少？只回答一句，标注页码，并保存为 budget-v4.md。',True),
                   ('请查找 quantum error correction 的阈值。没有相关资料就明确说无法回答。',False)]
        rows=[]
        for question,save in questions:
            case = 'pdf' if save else 'absence'
            if args.case != 'all' and args.case != case:
                continue
            deltas=[]
            result=run_agent(question,config,root=root,allow_save=save,on_event=lambda e:deltas.append(e['text']) if e['stage']=='delta' else None)
            (destination/(result['run_id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            if save:
                passed=result['status']=='completed' and result.get('evidence_review',{}).get('ok') and '120' in result['answer'] and any('第2页' in s for s in result['sources']) and bool(result['saved_files'])
                for file in (root/'outputs').glob('*.md'):shutil.copyfile(file,destination/file.name)
            else:
                passed=result['status']=='completed' and (result['answer']=='现有资料无法确认。' or (not result['sources'] and '没有检索到' in result['answer']))
            rows.append({'case':case,'run_id':result['run_id'],'passed':bool(passed),'status':result['status'],'stream_chunks':len(deltas),'requests':len(result['provider_requests']),'tokens':sum(r.get('usage',{}).get('total_tokens',0) for r in result['provider_requests']),'seconds':result['elapsed_seconds']})
            print(json.dumps(rows[-1],ensure_ascii=True),flush=True)
        (destination/('summary-'+args.case+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        return 0 if all(r['passed'] for r in rows) else 1


if __name__=='__main__':raise SystemExit(main())
