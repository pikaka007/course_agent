"""Build a fresh source archive from an allowlist; never include Git history/data."""
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE_DIRS = ('course_agent', 'web', 'prompts', 'tests', 'scripts', 'docs', 'examples', 'skills')
SAFE_EXTENSIONS = {'.py', '.md', '.html', '.css', '.js', '.json', '.txt'}
SAFE_ROOT = ('app.py', 'README.md', '.env.example', '.gitignore', 'requirements.txt', 'requirements-lock.txt',
             '启动.bat', '启动.ps1', '配置并启动.ps1', '终端聊天.bat', '终端聊天.ps1', '安装终端依赖.ps1')


def build(root=ROOT):
    root = Path(root)
    files = [root/n for n in SAFE_ROOT if (root/n).is_file()]
    # Public documentation artwork; keep runtime screenshots out of the archive.
    files.extend(root/n for n in ('docs/assets/agent-architecture.png',) if (root/n).is_file())
    for folder in SAFE_DIRS:
        files.extend(p for p in (root/folder).rglob('*') if p.is_file() and not p.is_symlink()
                     and p.suffix in SAFE_EXTENSIONS and '__pycache__' not in p.parts)
    files.extend(root/n for n in ('data/README.md','outputs/.gitkeep','logs/.gitkeep','evidence/README.md') if (root/n).is_file())
    # Detect recognizable keys as well as the configured local key without printing them.
    from sys import path
    path.insert(0, str(root))
    from course_agent.config import Config
    secret = Config.from_env(root).api_key
    manifest=[]
    for p in files:
        if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):
            raise ValueError('Refusing linked source file')
        raw=p.read_bytes()
        if (secret and secret.encode() in raw) or re.search(rb'sk-[A-Za-z0-9_-]{20,}',raw):
            raise ValueError('Potential secret found; archive not created. Inspect local source.')
        manifest.append({'path':p.relative_to(root).as_posix(),'sha256':hashlib.sha256(raw).hexdigest()})
    out=root/'release'
    out.mkdir(exist_ok=True)
    target=out/'CourseMate-source.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p,'course-agent/'+p.relative_to(root).as_posix())
        z.writestr('source-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    print(f'Source archive: {target}\nFiles: {len(files)}; no Git history, runtime data or local config.')
    return target


if __name__=='__main__':
    build()
