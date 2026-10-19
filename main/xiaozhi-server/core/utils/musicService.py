"""Machine-local music selection and authenticated loopback bridge calls."""
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[4]
RUNTIME = ROOT / '.runtime/music'
BASE_URL = 'http://127.0.0.1:8007'


def music_is_playing():
    try:
        if music_source() != 'netease':
            return False
        return json.loads((RUNTIME / 'playback.json').read_text(encoding='utf8')).get('playing') is True
    except (OSError, ValueError, AttributeError, RuntimeError):
        return False


def music_source():
    path = RUNTIME / 'settings.json'
    if not path.is_file():
        return 'local'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))['source']
        if value in ('local', 'netease'):
            return value
    except (OSError, ValueError, KeyError):
        pass
    raise RuntimeError('音乐模式配置无效，请运行 music.cmd --source local 或 netease')


async def music_request(endpoint, **data):
    try:
        token = (RUNTIME / 'bridge.token').read_text(encoding='ascii').strip()
        async with httpx.AsyncClient(trust_env=False, timeout=22) as client:
            response = await client.post(BASE_URL + '/api/' + endpoint,
                                         headers={'Authorization': 'Bearer ' + token}, json=data)
        body = response.json()
        if response.status_code != 200 or not body.get('ok'):
            # Bridge exposes only fixed, sanitized errors, never upstream responses.
            raise RuntimeError(body.get('message', '音乐服务操作失败'))
        return body
    except (OSError, httpx.HTTPError, ValueError):
        raise RuntimeError('本机音乐服务未就绪，请运行 start-all.cmd，或使用 music.cmd --source local') from None


async def music_attention(conn, action):
    """Best effort: music failure must never break ordinary speech replies."""
    if not (RUNTIME / 'bridge.token').is_file():
        return
    if not getattr(conn, 'music_bridge_used', False):
        # Also cover music started from the local management page.
        try:
            if music_source() != 'netease':
                return
        except RuntimeError:
            return
    try:
        token = (RUNTIME / 'bridge.token').read_text(encoding='ascii').strip()
        async with httpx.AsyncClient(trust_env=False, timeout=0.7) as client:
            await client.post(BASE_URL + '/api/attention',
                              headers={'Authorization': 'Bearer ' + token},
                              json={'action': action, 'owner': conn.session_id})
    except (OSError, httpx.HTTPError, ValueError):
        pass
