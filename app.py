"""CLI and local deployment entry point. Python 3.10+; install requirements-lock.txt for PDF and rich terminal."""
import argparse
import json
import sys

from course_agent.agent import run_agent
from course_agent.config import Config, ROOT


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="CourseMate 课程资料学习助手")
    sub = parser.add_subparsers(dest="command")
    chat = sub.add_parser('chat', help='交互终端工作台（默认）')
    chat.add_argument('--mode', choices=['live', 'demo'], default='live')
    chat.add_argument('--resume', help='恢复会话编号')
    chat.add_argument('--plain', action='store_true', help='简洁终端模式')
    sub.add_parser('probe', help='真实请求当前模型并输出接口回执')
    serve = sub.add_parser("serve", help="启动本机网页")
    serve.add_argument("--port", type=int, default=8765)
    run = sub.add_parser("run", help="执行一次问答")
    run.add_argument("question")
    run.add_argument("--mode", choices=["live", "demo"], default="live")
    run.add_argument("--save", action="store_true", help="允许模型保存笔记")
    sub.add_parser("check", help="检查非敏感配置和资料")
    args = parser.parse_args()
    config = Config.from_env()
    if args.command in (None, 'chat'):
        from course_agent.terminal import chat as terminal_chat
        return terminal_chat(config, getattr(args, 'mode', 'live'), getattr(args, 'resume', None), getattr(args, 'plain', False))
    elif args.command == 'probe':
        from course_agent.provider import probe
        try:
            print(json.dumps(probe(config), ensure_ascii=False, indent=2))
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    elif args.command == "serve":
        from course_agent.server import serve
        serve(config, args.port)
    elif args.command == "check":
        from course_agent.tools import Skills
        print(json.dumps({"python": sys.version.split()[0], **config.public(),
                          **Skills(ROOT / "data", ROOT / "outputs").list_documents()}, ensure_ascii=False, indent=2))
    else:
        try:
            result = run_agent(args.question, config, args.mode, args.save)
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "completed" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
