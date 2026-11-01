"""Codex-style scrolling terminal: sessions, slash commands, progress and evidence."""
import getpass
import json
import queue
import sys
import threading
from pathlib import Path
from .agent import run_agent
from .config import ROOT, Config, save_config
from .workspace import Workspace
from .provider import list_models, probe
from .tools import Skills

HELP = '''/help                     查看命令
/new                      开始新会话
/sessions [关键词]         搜索本地会话
/resume [编号]             选择并恢复网页或终端会话
/docs                     列出本地资料
/use <文件名>|all          限定资料范围（保留文件名中的空格）
/import <路径>             导入本地资料
/model [模型名]            查看或切换模型
/models                   请求服务端模型列表
/config                   隐藏输入密钥，保存本地配置
/test                     发送一次真实连通性测试
/mode live|demo           切换真实模型或固定演示
/save on|off              控制笔记写入权限
/length <100..4000>        设置期望回答字数
/prompt <回答偏好>         自定义回答风格
/trace                    查看上一轮工具参数和结果
/sources                  查看上一轮引用原文
/retry                    重试上一个问题
/edit                     编辑上一问题，在新会话发送
/export                   导出当前会话到 local/exports
/review                   查看逐段证据核对
/skills                   查看技能和启用状态
/install <文件夹>          安装技能为停用状态
/enable <技能id>           审阅并启用技能
/disable <技能id>          停用技能
/study                    查看任务、日历、草稿与复习
/pending                  查看待确认操作
/confirm <编号>            确认一项预览
/reject <编号>             拒绝一项预览
/calendar <ICS路径>        读取日历预览
/mail <草稿编号>           导出本地邮件EML
/quit                     退出
Enter 发送；Esc+Enter 换行；↑↓ 输入历史；Tab 补全命令。
运行时 Ctrl+C 中断接收中的流；建立连接阶段可能仍需等待超时。'''


def clean_terminal(text):
    """Untrusted document/model output cannot inject terminal escape commands."""
    return ''.join(c for c in str(text) if c in '\n\t' or (ord(c) >= 32 and not 127 <= ord(c) <= 159))


def chat(config, mode='live', resume=None, plain=False, root=ROOT):
    workspace = Workspace(root)
    session = workspace.session(resume)
    selected, allow_save, last = None, False, None
    pending_input = ''
    console = prompt = None
    interactive = not plain and sys.stdin.isatty() and sys.stdout.isatty()
    if interactive:
        try:
            from rich.console import Console, Group
            from rich.panel import Panel
            from rich.markdown import Markdown
            from rich.live import Live
            from rich.spinner import Spinner
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.styles import Style
            console = Console()
            keys = KeyBindings()
            @keys.add('enter')
            def submit(event):
                event.current_buffer.validate_and_handle()
            @keys.add('escape', 'enter')
            def newline(event):
                event.current_buffer.insert_text('\n')
            prompt = PromptSession(multiline=True, key_bindings=keys,
                                   completer=WordCompleter([line.split()[0] for line in HELP.splitlines() if line.startswith('/')], meta_dict={line.split()[0]: ' '.join(line.split()[1:]) for line in HELP.splitlines() if line.startswith('/')}), complete_while_typing=True,
                                   style=Style.from_dict({'prompt': '#668ee8 bold', 'bottom-toolbar': 'bg:#202b40 #d4def5'}))
        except ImportError:
            print('安装 requirements.txt 可启用富文本、命令补全和多行编辑；当前使用简洁交互模式。')
    def say(text, markdown=False):
        text = clean_terminal(text)
        if console:
            console.print(Markdown(text) if markdown else text, markup=False, highlight=False)
        else:
            print(text, flush=True)

    if console:
        console.print(Panel('课程资料工作台\n输入问题开始，输入 /help 查看命令。工具调用与原文来源均可核对。', title='CourseMate', border_style='#668ee8', width=86))
    else:
        say('CourseMate 终端工作台 | /help 查看命令 | /quit 退出')
    say(f"模型 {config.model}\n接口 {config.public()['base_url']}\n会话 {session['id']} | {mode} | 保存权限关闭")
    if resume:
        for row in session['turns'][-3:]:
            say('你：' + row['question'])
            say(row['answer'], markdown=True)
    while True:
        try:
            if prompt:
                text = prompt.prompt([('class:prompt', '\n› ')], default=pending_input, bottom_toolbar=lambda: f" {mode}  |  {config.model}  |  {'允许保存' if allow_save else '只读'}  |  /help  |  Esc+Enter 换行 ").strip()
            else:
                text = input('\n› ' + (pending_input + '（输入修改后的问题）' if pending_input else '')).strip()
        except (EOFError, KeyboardInterrupt):
            say('会话已保存在本机，再见。')
            return 0
        pending_input = ''
        if not text:
            continue
        if text == '/retry':
            if not session['turns']:
                say('当前会话还没有可重试的问题。')
                continue
            text = session['turns'][-1]['question']
        try:
            if text.startswith('/'):
                command, _, arg = text.partition(' ')
                arg = arg.strip()
                if command in ('/quit', '/exit'):
                    return 0
                if command == '/help':
                    say(HELP)
                elif command == '/new':
                    session, last = workspace.session(), None
                    say('已开始新会话：' + session['id'])
                elif command == '/sessions':
                    rows = workspace.sessions(arg)
                    say('\n'.join(f"{r['id']}  {r['count']} 轮  {r['title']}" for r in rows) or '暂无会话。')
                elif command == '/resume':
                    if not arg:
                        rows = workspace.sessions()
                        if not rows:
                            say('暂无可恢复会话。')
                            continue
                        say('\n'.join(f"{i+1}. {r['title']} ({r['count']} 轮)" for i,r in enumerate(rows)))
                        choice = input('输入序号，留空取消：').strip()
                        if not choice:
                            continue
                        if not choice.isdigit() or not 1 <= int(choice) <= len(rows):
                            raise ValueError('会话序号无效。')
                        arg = rows[int(choice)-1]['id']
                    session = workspace.session(arg)
                    last = None
                    say('已恢复：' + session['title'])
                    for row in session['turns'][-3:]:
                        say('你：' + row['question'])
                        say(row['answer'], markdown=True)
                elif command == '/edit':
                    if not session['turns']:
                        raise ValueError('当前会话没有可编辑的问题。')
                    pending_input = session['turns'][-1]['question']
                    session, last = workspace.session(), None
                    say('问题已带入新会话，可编辑后发送；原会话保留。')
                elif command == '/export':
                    from .workspace import atomic_json
                    path = Path(root)/'local'/'exports'/(session['id']+'.json')
                    path.parent.mkdir(parents=True,exist_ok=True)
                    atomic_json(path, workspace.store.get(session['id'], full=True) if session['turns'] else session)
                    say('会话已导出：'+str(path))
                elif command == '/review':
                    say(json.dumps(last.get('evidence_review', {'status':'未执行逐段证据核对'}), ensure_ascii=False, indent=2) if last else '尚无执行结果。')
                elif command in ('/skills','/install','/enable','/disable'):
                    from .plugins import Plugins
                    manager=Plugins(root)
                    if command=='/skills':say(json.dumps(manager.public(),ensure_ascii=False,indent=2))
                    elif command=='/install':say(json.dumps(manager.install(arg.strip('"')),ensure_ascii=False,indent=2))
                    else:
                        package=next((p for p in manager.packages() if p['id']==arg),None)
                        if not package:raise ValueError('技能不存在。')
                        if command=='/enable' and package.get('kind')=='python':
                            say('Python技能拥有当前用户权限，并非沙箱。代码目录：'+package['folder'])
                            if input('确认已审阅并信任此代码？输入 TRUST 继续：')!='TRUST':continue
                        say(json.dumps(manager.enable(arg,command=='/enable',package['hash']),ensure_ascii=False))
                elif command in ('/study','/pending','/confirm','/reject','/calendar','/mail'):
                    from .life import Life
                    life=Life(root)
                    if command=='/study':say(json.dumps(life.snapshot(),ensure_ascii=False,indent=2))
                    elif command=='/pending':say(json.dumps([p for p in life.rows('proposal') if p['status']=='pending'],ensure_ascii=False,indent=2))
                    elif command in ('/confirm','/reject'):say(json.dumps(life.confirm(arg,command=='/confirm'),ensure_ascii=False,indent=2))
                    elif command=='/calendar':
                        path=Path(arg.strip('"'))
                        if path.stat().st_size>2000000:raise ValueError('ICS文件过大。')
                        say(json.dumps(life.import_ics(path.read_text('utf-8-sig')),ensure_ascii=False,indent=2))
                    else:
                        folder=Path(root)/'local/exports';folder.mkdir(parents=True,exist_ok=True)
                        data=life.mail_export(arg)
                        target=folder/('mail-'+arg+'.eml');target.write_bytes(data);say('已导出：'+str(target))
                elif command == '/docs':
                    docs = Skills(Path(root)/'data', Path(root)/'outputs').list_documents()['documents']
                    say('\n'.join(r['filename'] for r in docs) or '暂无资料，使用 /import 导入。')
                elif command == '/use':
                    names = {p.name for p in Skills(Path(root)/'data', Path(root)/'outputs').documents()}
                    if arg != 'all' and arg not in names:
                        raise ValueError('资料不存在，请先用 /docs 查看文件名。')
                    selected = None if arg == 'all' else [arg]
                    say('资料范围：' + ('全部' if selected is None else arg))
                elif command == '/import':
                    path = Path(arg.strip('"')).expanduser()
                    if path.stat().st_size > 40 * 1024 * 1024:
                        raise ValueError('文件超过 40 MB。')
                    result = workspace.import_document(path.name, path.read_bytes())
                    say('已导入：' + result['filename'])
                elif command == '/model':
                    if arg:
                        config = save_config(root, config, {'model': arg})
                    say(f"{config.model} @ {config.public()['endpoint_host']}")
                elif command == '/models':
                    say('\n'.join(list_models(config)))
                elif command == '/config':
                    url = input('Base URL（留空保留）：').strip() or config.base_url
                    model = input('模型（留空保留）：').strip() or config.model
                    if not sys.stdin.isatty():
                        raise ValueError('密钥输入需要交互终端；也可在网页设置中配置。')
                    key = getpass.getpass('API Key（隐藏输入，同平台留空保留）：')
                    config = save_config(root, config, {'base_url': url, 'model': model, 'api_key': key})
                    say('已保存本机 .env。密钥不会进入会话记录。')
                elif command == '/test':
                    say(json.dumps(probe(config), ensure_ascii=False, indent=2))
                elif command == '/mode':
                    if arg not in ('live', 'demo'):
                        raise ValueError('使用 /mode live 或 /mode demo')
                    mode = arg
                    say('模式：' + mode + ('（固定流程，不理解自由问题）' if mode == 'demo' else ''))
                elif command == '/save':
                    if arg not in ('on', 'off'):
                        raise ValueError('使用 /save on 或 /save off')
                    allow_save = arg == 'on'
                    say('保存权限：' + arg)
                elif command in ('/length', '/prompt'):
                    prefs = workspace.preferences()
                    prefs['answer_length' if command == '/length' else 'instructions'] = int(arg) if command == '/length' else arg
                    workspace.save_preferences(prefs)
                    say('偏好已保存，下次提问生效。')
                elif command in ('/trace', '/sources'):
                    say(json.dumps(last.get('trace' if command == '/trace' else 'source_records', []), ensure_ascii=False, indent=2) if last else '本次终端尚无执行结果。')
                else:
                    say('未知命令，输入 /help 查看帮助。')
                continue
            events, cancel, result_box = queue.Queue(), threading.Event(), {}
            def worker():
                try:
                    result_box['result'] = run_agent(text, config, mode, allow_save, root=root, history=session['turns'],
                        selected_documents=selected, cancel=cancel, session_id=session['id'], on_event=events.put)
                except Exception as exc:
                    result_box['error'] = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '本地运行失败。'
            worker_thread = threading.Thread(target=worker, daemon=True)
            worker_thread.start()
            live = Live(Spinner('dots', text='正在读取资料与生成回答…'), console=console, transient=True) if console else None
            draft = ''
            if live:
                live.start()
            try:
                while worker_thread.is_alive() or not events.empty():
                    try:
                        event = events.get(timeout=0.1)
                        stage = event['stage']
                        if stage == 'draft_reset':
                            draft = ''
                        elif stage == 'delta':
                            draft += clean_terminal(event.get('text', ''))
                            if live:
                                live.update(Group(Spinner('dots', text='生成草稿 · 尚未核对证据 · Ctrl+C 停止'), Markdown(draft)))
                        elif stage in ('evidence_check', 'evidence_repair'):
                            if live:
                                live.update(Group(Spinner('dots', text='正在核对证据…' if stage == 'evidence_check' else '正在修正缺少支持的结论…'), Markdown(draft)))
                            else:
                                say('  正在核对证据或修正草稿…')
                        elif stage == 'tool':
                            say('  调用 ' + event['name'])
                        elif stage == 'tool_done':
                            say('  ' + event['name'] + (' 完成' if event['ok'] else ' 返回错误'))
                        elif stage == 'model':
                            if live:
                                live.update(Spinner('dots', text=f"第 {event['round']} 轮模型处理… Ctrl+C 停止"))
                            else:
                                say(f"  模型处理 第 {event['round']} 轮")
                    except queue.Empty:
                        pass
                    except KeyboardInterrupt:
                        cancel.set()
                        say('已请求停止，等待在途请求结束；不会再执行后续工具。')
            finally:
                if live:
                    live.stop()
            if 'error' in result_box:
                raise ValueError(result_box['error'])
            last = result_box['result']
            workspace.append_turn(session, last)
            say(last['answer'], markdown=True)
            receipts = last.get('provider_requests', [])
            tokens = sum(r.get('usage', {}).get('total_tokens', 0) for r in receipts)
            say(f"\n{last['status']} | {last['elapsed_seconds']}s | {len(last['trace'])} 次工具调用 | {tokens} tokens | /sources 查看原文")
            for name in last['saved_files']:
                say('已保存：' + name)
        except (ValueError, OSError, RuntimeError) as exc:
            message = str(exc)
            if config.api_key:
                message = message.replace(config.api_key, '[REDACTED]')
            say('错误：' + message)
