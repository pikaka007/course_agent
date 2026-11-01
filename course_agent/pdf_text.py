"""PDF extraction in a bounded child process; page numbers never renumbered."""
import json
import subprocess
import sys
from pathlib import Path


def extract(path):
    try:
        run = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(path)],
                             capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise ValueError('PDF 解析超过 30 秒，请拆分文档。') from None
    try:
        payload = json.loads(run.stdout.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise ValueError('PDF 解析失败，请检查文件或安装 pypdf。') from None
    if 'error' in payload:
        raise ValueError(payload['error'])
    return [(r[0], f'第{r[0]}页', r[1]) for r in payload['pages']]


def worker(path):
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('暂不支持加密 PDF，请提供已解密的副本。')
        if len(reader.pages) > 500:
            raise ValueError('PDF 超过 500 页，请拆分后导入。')
        pages, total = [], 0
        for n, page in enumerate(reader.pages, 1):
            contents = page.get_contents()
            if contents and len(contents.get_data()) > 8 * 1024 * 1024:
                raise ValueError('PDF 单页内容流过大，请拆分或简化。')
            text = (page.extract_text() or '').strip()
            if len(text) > 24000:
                raise ValueError(f'PDF 第{n}页文字超过 24000 字，请拆分该页。')
            total += len(text)
            if total > 2_000_000:
                raise ValueError('PDF 总文字超过 200 万字，请拆分。')
            if text:
                pages.append((n, text))
        if not pages:
            raise ValueError('PDF 没有可提取文字，可能是扫描件；需要先做 OCR。')
        return {'pages': pages}
    except ImportError:
        return {'error': '请安装 requirements-lock.txt 中的 pypdf 依赖。'}
    except Exception as exc:
        return {'error': str(exc) if isinstance(exc, ValueError) else 'PDF 文件损坏或无法解析。'}


if __name__ == '__main__':
    sys.stdout.buffer.write(json.dumps(worker(sys.argv[1]), ensure_ascii=False).encode('utf-8'))
