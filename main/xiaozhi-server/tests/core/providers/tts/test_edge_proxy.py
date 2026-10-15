"""Exercise the real edge-tts/aiohttp path against a local WebSocket server."""
import pytest
from aiohttp import web

from core.providers.tts import edge


@pytest.mark.asyncio
@pytest.mark.parametrize("config, expected_lookups", [
    ({}, 0),
    ({"proxy": ""}, 0),
    ({"proxy": None}, 1),
])
async def test_proxy_discovery_is_skipped_unless_requested(monkeypatch, config, expected_lookups):
    lookups = []

    def system_proxy(url):
        # Real Windows discovery can take seconds before any connection begins.
        lookups.append(url.host)
        raise LookupError("No proxy configured")

    monkeypatch.setattr("aiohttp.client.get_env_proxy_for_url", system_proxy)

    async def speech(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive_str()  # Speech configuration.
        await ws.receive_str()  # SSML request.
        headers = b"X-RequestId:test\r\nPath:audio\r\nContent-Type:audio/mpeg\r\n"
        await ws.send_bytes(len(headers).to_bytes(2, "big") + headers + b"fake-mp3")
        await ws.send_str("Path:turn.end\r\n\r\n")
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/speech", speech)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    try:
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(edge.edge_tts.communicate, "WSS_URL", f"ws://127.0.0.1:{port}/speech?test=1")
        provider = edge.TTSProvider({"voice": "zh-CN-YunyangNeural", **config}, True)
        audio = await provider.text_to_speak("Hello", None)
        assert audio == b"fake-mp3"
        assert len(lookups) == expected_lookups
    finally:
        await runner.cleanup()
