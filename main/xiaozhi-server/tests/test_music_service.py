"""Offline music routing tests. Run directly to avoid global conftest mocks."""
__test__ = False
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

VOICE=Path(__file__).resolve().parents[1]
sys.path=[p for p in sys.path if Path(p or '.').resolve()!=Path(__file__).resolve().parent]
sys.path.insert(0,str(VOICE))
from core.utils import musicService as service


class MusicTests(unittest.IsolatedAsyncioTestCase):
    def test_source_defaults_local_and_can_change_live(self):
        with TemporaryDirectory() as temp, patch.object(service,'RUNTIME',Path(temp)):
            self.assertEqual(service.music_source(),'local')
            file=Path(temp)/'settings.json'
            for value in ('netease','local'):
                file.write_text(json.dumps({'source':value}),encoding='utf8')
                self.assertEqual(service.music_source(),value)
            file.write_text('{"source":"unexpected"}')
            with self.assertRaises(RuntimeError):service.music_source()

    async def test_control_action_is_payload_and_auth_is_header(self):
        with TemporaryDirectory() as temp, patch.object(service,'RUNTIME',Path(temp)):
            (Path(temp)/'bridge.token').write_text('test-token')
            client=AsyncMock()
            client.post.return_value=Mock(status_code=200,json=lambda:{'ok':True,'message':'paused'})
            context=AsyncMock();context.__aenter__.return_value=client
            with patch.object(service.httpx,'AsyncClient',return_value=context) as factory:
                self.assertEqual((await service.music_request('control',action='pause'))['message'],'paused')
                factory.assert_called_once_with(trust_env=False,timeout=22)
                call=client.post.call_args
                self.assertEqual(call.args[0],service.BASE_URL+'/api/control')
                self.assertEqual(call.kwargs['json'],{'action':'pause'})
                self.assertEqual(call.kwargs['headers'],{'Authorization':'Bearer test-token'})

    async def test_optional_music_failure_does_not_break_speech(self):
        conn=types.SimpleNamespace(session_id='test',music_bridge_used=False)
        with TemporaryDirectory() as temp, patch.object(service,'RUNTIME',Path(temp)):
            await service.music_attention(conn,'duck')
            (Path(temp)/'bridge.token').write_text('test')
            (Path(temp)/'settings.json').write_text('{broken')
            await service.music_attention(conn,'pause')

    def test_capture_gate_only_for_active_netease_playback(self):
        with TemporaryDirectory() as temp,patch.object(service,'RUNTIME',Path(temp)):
            root=Path(temp)
            (root/'playback.json').write_text('{"playing":true}')
            self.assertFalse(service.music_is_playing())
            (root/'settings.json').write_text('{"source":"netease"}')
            self.assertTrue(service.music_is_playing())
            (root/'playback.json').write_text('{"playing":false}')
            self.assertFalse(service.music_is_playing())

    async def test_music_mic_frames_never_reach_vad_or_asr(self):
        stubs={
            'core.utils.util':types.SimpleNamespace(audio_to_data=AsyncMock()),
            'core.handle.abortHandle':types.SimpleNamespace(handleAbortMessage=AsyncMock()),
            'core.handle.intentHandler':types.SimpleNamespace(handle_user_intent=AsyncMock()),
            'core.utils.output_counter':types.SimpleNamespace(check_device_output_limit=Mock()),
            'core.handle.sendAudioHandle':types.SimpleNamespace(send_stt_message=AsyncMock(),SentenceType=Mock()),
        }
        spec=importlib.util.spec_from_file_location('receive_music_test',VOICE/'core/handle/receiveAudioHandle.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,stubs):spec.loader.exec_module(module)
        conn=types.SimpleNamespace(reset_audio_states=Mock(),vad=Mock(),asr=types.SimpleNamespace(receive_audio=AsyncMock()),client_aec=False)
        module.no_voice_close_connect=AsyncMock()
        module.detect_music_wakeword=AsyncMock(return_value=False)
        with patch.object(module,'music_is_playing',return_value=True):
            await module.handleAudioMessage(conn,b'mic-frame')
        module.detect_music_wakeword.assert_awaited_once_with(conn,b'mic-frame')
        conn.vad.is_vad.assert_not_called();conn.asr.receive_audio.assert_not_awaited()
        with patch.object(module,'music_is_playing',return_value=False):
            await module.handleAudioMessage(conn,b'mic-frame')
        conn.asr.receive_audio.assert_awaited_once()

    async def test_music_keyword_pauses_and_replies_without_asr_or_llm(self):
        abort=AsyncMock()
        wake=AsyncMock(return_value=True)
        stubs={
            'core.utils.util':types.SimpleNamespace(audio_to_data=AsyncMock()),
            'core.handle.abortHandle':types.SimpleNamespace(handleAbortMessage=abort),
            'core.handle.helloHandle':types.SimpleNamespace(checkWakeupWords=wake),
            'core.handle.intentHandler':types.SimpleNamespace(handle_user_intent=AsyncMock()),
            'core.utils.output_counter':types.SimpleNamespace(check_device_output_limit=Mock()),
            'core.handle.sendAudioHandle':types.SimpleNamespace(send_stt_message=AsyncMock(),SentenceType=Mock()),
        }
        spec=importlib.util.spec_from_file_location('receive_music_hit_test',VOICE/'core/handle/receiveAudioHandle.py')
        module=importlib.util.module_from_spec(spec)
        conn=types.SimpleNamespace(reset_audio_states=Mock(),vad=Mock(),asr=types.SimpleNamespace(receive_audio=AsyncMock()),logger=Mock(),client_abort=True)
        with patch.dict(sys.modules,stubs):
            spec.loader.exec_module(module)
            with patch.object(module,'music_is_playing',return_value=True), \
                 patch.object(module,'detect_music_wakeword',new_callable=AsyncMock,return_value=True):
                await module.handleAudioMessage(conn,b'wake-frames')
        abort.assert_awaited_once_with(conn)
        wake.assert_awaited_once_with(conn,'你好小智')
        conn.asr.receive_audio.assert_not_awaited()
        self.assertFalse(conn.client_abort)

    async def test_plugin_dispatches_to_local_or_netease_without_extra_configuration(self):
        class Action:
            RESPONSE='response'; RECORD='record'
        def register(*args):return lambda function:function
        registry=types.SimpleNamespace(register_function=register,ToolType=types.SimpleNamespace(SYSTEM_CTL=1),
            ActionResponse=lambda **kw:types.SimpleNamespace(**{'result':None,'response':None,**kw}),Action=Action)
        spec=importlib.util.spec_from_file_location('music_plugin_test',VOICE/'plugins_func/functions/play_music.py')
        plugin=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'plugins_func.register':registry}):spec.loader.exec_module(plugin)
        conn=types.SimpleNamespace(logger=Mock())
        with patch.object(plugin,'music_source',return_value='local'), patch.object(plugin,'handle_music_command',new_callable=AsyncMock) as local:
            result=await plugin.play_music(conn,'Example')
            local.assert_awaited_once_with(conn,'播放音乐 Example')
            self.assertEqual(result.action,Action.RECORD)
        with patch.object(plugin,'music_source',return_value='netease'),patch.object(plugin,'music_request',new_callable=AsyncMock,return_value={'message':'ok'}) as request:
            result=await plugin.play_music(conn,'My playlist',action='playlist')
            request.assert_awaited_once_with('play',query='My playlist',kind='playlist')
            self.assertEqual(result.response,'ok')
            await plugin.play_music(conn,action='pause')
            request.assert_awaited_with('control',action='pause',volume=None)
            stop=AsyncMock()
            with patch.dict(sys.modules,{'core.handle.sendAudioHandle':types.SimpleNamespace(send_tts_message=stop)}):
                resumed=await plugin.play_music(conn,action='resume')
            self.assertEqual(resumed.action,Action.RECORD)
            self.assertIsNone(resumed.response)
            stop.assert_awaited_once_with(conn,'stop',notify=False)

    async def test_short_resume_commands_bypass_llm(self):
        send=AsyncMock()
        display,stop=AsyncMock(),AsyncMock()
        stubs={
            'core.utils.dialogue':types.SimpleNamespace(Message=Mock()),
            'plugins_func.register':types.SimpleNamespace(Action=Mock(),ActionResponse=Mock()),
            'core.handle.helloHandle':types.SimpleNamespace(checkWakeupWords=AsyncMock(return_value=False)),
            'core.handle.sendAudioHandle':types.SimpleNamespace(send_stt_message=send,send_display_message=display,send_tts_message=stop),
            'core.handle.reportHandle':types.SimpleNamespace(enqueue_tool_report=Mock()),
            'core.utils.util':types.SimpleNamespace(remove_punctuation_and_length=lambda text:(len(text),''.join(c for c in text if c.isalnum()))),
        }
        spec=importlib.util.spec_from_file_location('intent_music_test',VOICE/'core/handle/intentHandler.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,stubs):spec.loader.exec_module(module)
        conn=types.SimpleNamespace(client_abort=True)
        with patch.object(service,'music_source',return_value='netease'), \
             patch.object(service,'music_request',new_callable=AsyncMock,return_value={'message':'继续播放音乐'}) as request, \
             patch.object(module,'speak_txt') as speak, \
             patch.object(module,'analyze_intent_with_llm',new_callable=AsyncMock) as llm:
            for phrase in ('继续播放。','恢复播放','继续音乐','继续播放音乐'):
                self.assertTrue(await module.handle_user_intent(conn,phrase))
                request.assert_awaited_with('control',action='resume')
            self.assertFalse(conn.client_abort)
            self.assertEqual(display.await_count,4)
            self.assertEqual(stop.await_count,4)
            stop.assert_awaited_with(conn,'stop',notify=False)
            send.assert_not_awaited()
            speak.assert_not_called()
            llm.assert_not_awaited()
            # Failed resumes still explain how to recover instead of failing silently.
            request.side_effect=RuntimeError('当前没有音乐队列，请先点歌')
            self.assertTrue(await module.handle_user_intent(conn,'继续播放'))
            send.assert_awaited_once_with(conn,'继续播放')
            speak.assert_called_once_with(conn,'当前没有音乐队列，请先点歌')


if __name__=='__main__':unittest.main()
