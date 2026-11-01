"""Project .env overrides inherited environment; secrets stay local."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "deepseek-flash"


def load_env(path=ROOT / ".env"):
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name, value = name.strip(), value.strip()
            if name.startswith(("OPENAI_", "AGENT_")):
                if len(value) >= 2 and value[0] == value[-1] and ord(value[0]) in (34, 39):
                    value = value[1:-1]
                values[name] = value
    return values


@dataclass
class Config:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout: int = 90
    max_rounds: int = 10
    sources: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, root=ROOT):
        local = load_env(Path(root) / ".env")
        defaults = {"OPENAI_BASE_URL": "https://api.deepseek.com", "OPENAI_API_KEY": "", "OPENAI_MODEL": DEFAULT_MODEL}
        values = {k: local.get(k, os.getenv(k, v)) for k, v in defaults.items()}
        sources = {k: ".env" if k in local else "environment" if k in os.environ else "default" for k in defaults}
        return cls(values["OPENAI_BASE_URL"].rstrip("/"), values["OPENAI_API_KEY"], values["OPENAI_MODEL"], sources=sources)

    def public(self):
        url = urlsplit(self.base_url)
        endpoint = self.base_url if not (url.username or url.password or url.query or url.fragment) else "invalid-url"
        return {"base_url": endpoint, "endpoint_host": url.hostname or "", "model": self.model,
                "key_configured": bool(self.api_key), "sources": self.sources,
                "precedence": "项目 .env > 系统环境变量 > 默认值"}

    def validate(self):
        url = urlsplit(self.base_url)
        if not self.base_url or "YOUR_PROVIDER" in self.base_url:
            raise ValueError("请填写实际模型接口 Base URL。")
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"}):
            raise ValueError("模型接口必须使用 HTTPS；本机模型可使用 HTTP。")
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("接口地址不能包含凭据、查询参数或片段。")
        if not self.api_key:
            raise ValueError("尚未配置 API Key，请打开模型设置。")
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 120:
            raise ValueError("模型名称长度应为 1 到 120 个字符。")


def save_config(root, current, body):
    url, model, key = body.get("base_url", current.base_url), body.get("model", current.model), body.get("api_key", "")
    if any(not isinstance(v, str) or chr(10) in v or chr(13) in v for v in (url, model, key)):
        raise ValueError("配置必须为单行文本。")
    url = url.strip().rstrip("/")
    if not key:
        if url != current.base_url:
            raise ValueError("更换接口地址时必须重新输入该平台的密钥。")
        key = current.api_key
    proposed = Config(url, key.strip(), model.strip(), sources={k: ".env" for k in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL")})
    proposed.validate()
    path, temp = Path(root) / ".env", Path(root) / ".env.tmp"
    temp.write_text(chr(10).join(["OPENAI_BASE_URL=" + proposed.base_url, "OPENAI_MODEL=" + proposed.model, "OPENAI_API_KEY=" + proposed.api_key, ""]), encoding="utf-8")
    try:
        temp.chmod(0o600)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return proposed
