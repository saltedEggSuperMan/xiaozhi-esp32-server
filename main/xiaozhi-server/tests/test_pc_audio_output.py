"""Offline regressions. Run directly: python -B tests/test_pc_audio_output.py -v.

Avoid tests/conftest.py: it globally replaces asyncio.sleep and config loading.
No providers, credentials, actual sound devices or network are used here.
"""

__test__ = False

import asyncio
from array import array
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

VOICE = Path(__file__).resolve().parents[1]
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(VOICE))
os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")

from core.utils import pcAudioOutput as output


def load_isolated(name, path):
    spec = importlib.util.spec_from_file_location(name, VOICE / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeStream:
    def __init__(self):
        self.data = []
        self.started = False
        self.stopped = False
        self.aborted = False
        self.closed = False

    def start(self):
        self.started = True

    def write(self, pcm):
        self.data.append(pcm)

    def stop(self):
        self.stopped = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


def decoder(rate):
    return types.SimpleNamespace(decode=lambda packet, frames: array("h", [1000] * (rate * 60 // 1000)).tobytes())


class PCSpeakerTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_connections_alternate_without_losing_stream_reuse(self):
        first, second = output.PCSoundDevice(None), output.PCSoundDevice(None)
        streams = [FakeStream(), FakeStream()]
        with patch.object(output, "_open_output", side_effect=[(s, 24000, 1) for s in streams]) as opened, \
             patch.object(output, "_opus_decoder", side_effect=decoder):
            try:
                for device in (first, second, first):
                    player = output.PCSpeaker(24000, output=device)
                    await player.write(b"opus")
                    await player.finish()
                    self.assertFalse(device.leased)
                self.assertEqual(opened.call_count, 2)
                self.assertTrue(all(s.stopped and not s.closed for s in streams))
                self.assertEqual(len(streams[0].data), 2 * len(streams[1].data))
            finally:
                first.close()
                second.close()

    async def test_cached_active_session_blocks_others_then_releases(self):
        first, second = output.PCSoundDevice(None), output.PCSoundDevice(None)
        stream = FakeStream()
        entered, release = threading.Event(), threading.Event()

        def write(pcm):
            entered.set()
            release.wait(2)
            stream.data.append(pcm)

        stream.write = write
        with patch.object(output, "_open_output", side_effect=[(stream, 24000, 1), (FakeStream(), 24000, 1)]), \
             patch.object(output, "_opus_decoder", side_effect=decoder):
            active = output.PCSpeaker(24000, output=first)
            try:
                await active.write(b"opus")
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                blocked = output.PCSpeaker(24000, output=second)
                with self.assertRaises(RuntimeError):
                    await blocked.finish()
                self.assertTrue(first.leased)
                self.assertFalse(stream.closed)
                # Even an overlapping worker using the same cache must not close it.
                blocked_same = output.PCSpeaker(24000, output=first)
                with self.assertRaises(RuntimeError):
                    await blocked_same.finish()
                self.assertTrue(first.leased)
                self.assertFalse(stream.closed)
                release.set()
                await active.finish()
                retry = output.PCSpeaker(24000, output=second)
                await retry.write(b"opus")
                await retry.finish()
            finally:
                release.set()
                active.abort()
                await active.wait_closed()
                first.close()
                second.close()

    async def test_cached_open_failure_releases_lease_for_retry(self):
        device = output.PCSoundDevice(None)
        with patch.object(output, "_open_output", side_effect=[RuntimeError("no output"), (FakeStream(), 24000, 1)]), \
             patch.object(output, "_opus_decoder", side_effect=decoder):
            try:
                failed = output.PCSpeaker(24000, output=device)
                with self.assertRaisesRegex(RuntimeError, "no output"):
                    await failed.finish()
                self.assertFalse(device.leased)
                retry = output.PCSpeaker(24000, output=device)
                await retry.write(b"opus")
                await retry.finish()
            finally:
                device.close()

    async def test_connection_reuses_device_across_replies_and_abort(self):
        stream = FakeStream()
        device = output.PCSoundDevice(None)
        with patch.object(output, "_open_output", return_value=(stream, 24000, 1)) as open_device, \
             patch.object(output, "_opus_decoder", side_effect=decoder):
            try:
                for _ in range(2):
                    player = output.PCSpeaker(24000, output=device)
                    await player.write(b"opus")
                    await player.finish()
                    self.assertFalse(stream.closed)
                player = output.PCSpeaker(24000, output=device)
                await player.write(b"opus")
                player.abort()
                await player.wait_closed()
                self.assertTrue(stream.aborted)
                self.assertFalse(stream.closed)
                player = output.PCSpeaker(24000, output=device)
                await player.write(b"opus")
                await player.finish()
                open_device.assert_called_once()
            finally:
                device.close()
        self.assertTrue(stream.closed)
        self.assertTrue(output._speaker_lock.acquire(blocking=False))
        output._speaker_lock.release()

    async def test_disconnect_releases_idle_cached_device(self):
        stream = FakeStream()
        device = output.PCSoundDevice(None)
        with patch.object(output, "_open_output", return_value=(stream, 24000, 1)):
            device.open()
            conn = types.SimpleNamespace(pc_audio_player=None, pc_sound_device=device)
            await output.close_pc_audio(conn, release_device=True)
        self.assertTrue(stream.closed)
        self.assertIsNone(device.stream)

    async def test_resamples_mono_to_device_rate_and_stereo_and_drains(self):
        stream = FakeStream()
        with patch.object(output, "_open_output", return_value=(stream, 48000, 2)), \
             patch.object(output, "_opus_decoder", side_effect=decoder):
            player = output.PCSpeaker(24000, volume=0.5)
            for _ in range(12):
                await player.write(b"opus")
            await player.finish()
        samples = array("h", b"".join(stream.data))
        self.assertGreater(len(samples), 60000)
        self.assertEqual(set(samples), {500})
        self.assertTrue(all(len(chunk) <= 48000 // 50 * 4 for chunk in stream.data))
        self.assertTrue(stream.stopped and stream.closed)
        self.assertFalse(stream.aborted)
        self.assertTrue(player.done.is_set())

    async def test_abort_discards_backlog_and_releases_speaker(self):
        stream = FakeStream()
        entered, release = threading.Event(), threading.Event()

        def write(pcm):
            stream.data.append(pcm)
            entered.set()
            release.wait(2)

        stream.write = write
        with patch.object(output, "_open_output", return_value=(stream, 24000, 1)), \
             patch.object(output, "_opus_decoder", side_effect=decoder):
            player = output.PCSpeaker(24000)
            for _ in range(8):
                await player.write(b"opus")
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            player.abort()
            release.set()
            await player.wait_closed()
            await player.write(b"late")
        self.assertEqual(len(stream.data), 1)
        self.assertTrue(stream.aborted and stream.closed)
        self.assertTrue(output._speaker_lock.acquire(blocking=False))
        output._speaker_lock.release()

    async def test_finish_waits_for_hardware_drain(self):
        stream = FakeStream()
        draining, release = threading.Event(), threading.Event()

        def stop():
            draining.set()
            release.wait(2)
            stream.stopped = True

        stream.stop = stop
        with patch.object(output, "_open_output", return_value=(stream, 24000, 1)), \
             patch.object(output, "_opus_decoder", side_effect=decoder):
            player = output.PCSpeaker(24000)
            await player.write(b"opus")
            finishing = asyncio.create_task(player.finish())
            self.assertTrue(await asyncio.to_thread(draining.wait, 2))
            self.assertFalse(finishing.done())
            release.set()
            await finishing
        self.assertTrue(stream.closed)

    async def test_open_error_terminates_worker_without_leaking_lease(self):
        with patch.object(output, "_open_output", side_effect=RuntimeError("no output")), \
             patch.object(output, "_opus_decoder", side_effect=decoder):
            player = output.PCSpeaker(24000)
            with self.assertRaisesRegex(RuntimeError, "no output"):
                await player.finish()
        self.assertTrue(player.done.is_set())
        self.assertTrue(output._speaker_lock.acquire(blocking=False))
        output._speaker_lock.release()

    async def test_second_session_fails_without_mixing(self):
        with patch.object(output, "_open_output", return_value=(FakeStream(), 24000, 1)), \
             patch.object(output, "_opus_decoder", side_effect=decoder):
            first = output.PCSpeaker(24000)
            await first.write(b"opus")
            for _ in range(100):
                if not first.queue.qsize():
                    break
                await asyncio.sleep(0.01)
            second = output.PCSpeaker(24000)
            try:
                with self.assertRaisesRegex(RuntimeError, "另一个设备"):
                    await second.finish()
            finally:
                first.abort()
                await first.wait_closed()


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Stub only configuration-dependent imports; exercise real routing/controller.
        logger = Mock()
        logger.bind.return_value = logger
        self.logger = logger
        util = types.ModuleType("core.utils.util")
        util.audio_to_data = AsyncMock(return_value=[])
        log = types.ModuleType("config.logger")
        log.setup_logging = lambda: logger
        with patch.dict(sys.modules, {"core.utils.util": util, "config.logger": log}):
            controller = load_isolated("_test_rate", "core/utils/audioRateController.py")
            with patch.dict(sys.modules, {"core.utils.audioRateController": controller}):
                self.router = load_isolated("_test_send", "core/handle/sendAudioHandle.py")
        self.abort = load_isolated("_test_abort", "core/handle/abortHandle.py")

    def conn(self, mode="device"):
        settings = {"mode": mode}
        if mode == 'PC_DEVICE':
            settings['pc'] = {'device_id':'{0.0.0.00000000}.{12345678-1234-1234-1234-123456789abc}'}
        return types.SimpleNamespace(
            config={"audio_output": settings}, sentence_id="turn-1",
            client_abort=False, sample_rate=24000, logger=self.logger,
            websocket=types.SimpleNamespace(send=AsyncMock()), conn_from_mqtt_gateway=False,
            session_id="session", clearSpeakStatus=Mock(), calling=False,
            close_after_chat=False, tts=types.SimpleNamespace(tts_audio_first_sentence=True),
        )

    async def test_default_route_preserves_device_binary_output(self):
        conn = self.conn()
        conn.config = {}  # Existing installs do not need any new config/dependency.
        with patch.object(self.router, "send_pc_audio", new_callable=AsyncMock) as pc:
            await self.router.sendAudio(conn, [b"opus-1", b"opus-2"])
            self.assertEqual([call.args[0] for call in conn.websocket.send.call_args_list], [b"opus-1", b"opus-2"])
            pc.assert_not_awaited()
        conn.audio_rate_controller.reset()

    async def test_pc_route_never_sends_binary_but_keeps_tts_messages(self):
        conn = self.conn("server_pc")
        events = []

        async def finish(_conn):
            events.append("drain")

        async def send(message):
            events.append(json.loads(message)["state"])

        conn.websocket.send.side_effect = send
        with patch.object(self.router, "send_pc_audio", new_callable=AsyncMock) as pc, \
             patch.object(self.router, "finish_pc_audio", side_effect=finish):
            await self.router.send_tts_message(conn, "start")
            await self.router.sendAudio(conn, b"opus")
            await self.router.send_tts_message(conn, "stop")
            pc.assert_awaited_once_with(conn, b"opus")
        self.assertEqual(events, ["start", "drain", "stop"])
        conn.clearSpeakStatus.assert_called_once()

    async def test_explicit_device_routes_tts_and_notification_without_hardware_audio(self):
        conn=self.conn('PC_DEVICE')
        conn.config['enable_stop_tts_notify']=True
        with patch.object(self.router,'send_pc_audio',new_callable=AsyncMock) as pc, \
             patch.object(self.router,'finish_pc_audio',new_callable=AsyncMock), \
             patch.object(self.router,'audio_to_data',new_callable=AsyncMock,return_value=[b'notification']), \
             patch.object(self.router,'music_attention',new_callable=AsyncMock) as attention:
            await self.router.send_tts_message(conn,'start')
            await self.router.sendAudio(conn,b'tts')
            await self.router.send_tts_message(conn,'stop')
        self.assertEqual([call.args[1] for call in pc.await_args_list],[b'tts',[b'notification']])
        self.assertTrue(all(isinstance(call.args[0],str) for call in conn.websocket.send.call_args_list))
        self.assertEqual([call.args[1] for call in attention.await_args_list],['duck','unduck'])

    async def test_abort_waits_for_pc_close_and_keeps_device_stop(self):
        conn = self.conn("server_pc")
        conn.clear_queues = Mock()
        with patch.object(self.abort, "close_pc_audio", new_callable=AsyncMock) as close:
            await self.abort.handleAbortMessage(conn)
            close.assert_awaited_once_with(conn)
        self.assertTrue(conn.client_abort)
        conn.clear_queues.assert_called_once()
        self.assertEqual(json.loads(conn.websocket.send.call_args.args[0])["state"], "stop")

    async def test_silent_completion_never_plays_configured_stop_notification(self):
        conn = self.conn('server_pc')
        conn.config['enable_stop_tts_notify'] = True
        with patch.object(self.router, 'audio_to_data', new_callable=AsyncMock) as audio, \
             patch.object(self.router, 'sendAudio', new_callable=AsyncMock) as play, \
             patch.object(self.router, '_wait_for_audio_completion', new_callable=AsyncMock), \
             patch.object(self.router, 'music_attention', new_callable=AsyncMock) as attention:
            await self.router.send_tts_message(conn, 'stop', notify=False)
        audio.assert_not_awaited()
        play.assert_not_awaited()
        attention.assert_awaited_once_with(conn, 'unduck')
        self.assertEqual(json.loads(conn.websocket.send.call_args.args[0])['state'], 'stop')
        conn.clearSpeakStatus.assert_called_once()

    async def test_stale_sentence_does_not_stop_new_reply_after_backpressure(self):
        conn = self.conn("server_pc")

        async def advance(*args):
            conn.sentence_id = "turn-2"

        with patch.object(self.router, "send_pc_audio", side_effect=advance):
            await self.router.sendAudioMessage(conn, self.router.SentenceType.LAST,
                                               b"old", None, "turn-1")
        conn.websocket.send.assert_not_awaited()

    async def test_playback_failure_does_not_fall_back_to_device(self):
        conn = self.conn("server_pc")
        player = Mock()
        player.aborted = threading.Event()
        player.write = AsyncMock(side_effect=RuntimeError("test failure"))
        with patch.object(output, "PCSpeaker", return_value=player):
            await output.send_pc_audio(conn, b"opus")
            await output.send_pc_audio(conn, b"next")
        player.write.assert_awaited_once()
        player.abort.assert_called_once()
        conn.websocket.send.assert_not_awaited()


class ConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def test_validates_mode_device_and_volume(self):
        self.assertEqual(output.audio_output_settings({}), ("device", None, 0.6))
        for settings in ({"mode": "both"}, {"pc": {"volume": float("nan")}},
                         {"pc": {"volume": 2}}, {"pc": {"device": True}}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                output.audio_output_settings({"audio_output": settings})

    async def test_management_refresh_preserves_local_hardware_selection(self):
        from config import config_loader as loader

        local = {"manager-api": {"url": "http://offline.invalid", "secret": "test"},
                 '_audio_output_local_path': 'synthetic-local-path',
                 "audio_output": {"mode": "server_pc", "pc": {"volume": 0.4}}}
        remote = {"server": {"auth": {"enabled": True}}, "audio_output": {"mode": "device"}}
        with patch.object(loader, "init_service"), \
             patch.object(loader, "get_server_config", new=AsyncMock(return_value=remote)):
            config = await loader.get_config_from_api_async(local)
        self.assertEqual(config["audio_output"], local["audio_output"])
        self.assertEqual(config['_audio_output_local_path'], 'synthetic-local-path')
        self.assertTrue(config["server"]["auth"]["enabled"])

    async def test_full_deployment_load_applies_local_override_and_defaults(self):
        from config import config_loader as loader
        from core.utils.cache.manager import cache_manager

        defaults = {"audio_output": {"mode": "device", "pc": {"volume": 0.6, "device": None}}}
        custom = {"manager-api": {"url": "http://offline.invalid"},
                  "audio_output": {"mode": "server_pc"}}
        with patch.object(cache_manager, "get", return_value=None), \
             patch.object(cache_manager, "set"), patch.object(loader, "ensure_directories"), \
             patch.object(loader, "read_config", side_effect=[defaults, custom]), \
             patch.object(loader, "get_config_from_api_async", new=AsyncMock(return_value={})):
            config = await loader.load_config()
        self.assertEqual(config["audio_output"], {"mode": "server_pc", "pc": {"volume": 0.6, "device": None}})


if __name__ == "__main__":
    unittest.main()
