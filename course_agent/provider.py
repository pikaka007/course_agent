"""Provider probes send only an explicit tiny test prompt, no local documents."""
import json
import urllib.request
import urllib.error
from .agent import LLMClient, NoRedirect


def list_models(config):
    config.validate()
    req = urllib.request.Request(config.base_url + '/models', headers={'Authorization': 'Bearer ' + config.api_key})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            body = json.loads(response.read(1024 * 1024))
        return [r['id'] for r in body['data'] if isinstance(r.get('id'), str)]
    except urllib.error.HTTPError as exc:
        raise ValueError(f'模型列表请求失败 HTTP {exc.code}，请核对接口和密钥。') from None
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('无法获取模型列表，请核对网络和接口。') from None


def probe(config):
    models = list_models(config)
    if config.model not in models:
        raise ValueError('所选模型不在接口模型列表中。可用：' + '、'.join(models[:30]))
    client = LLMClient(config)
    msg = client.complete([{'role': 'user', 'content': 'Reply with OK only. This is a connection test.'}], [])
    return {'ok': True, 'connection': config.public(), 'available_models': models,
            'reply': msg.get('content', ''), 'provider_requests': client.requests}
