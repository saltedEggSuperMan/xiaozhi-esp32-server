"""Run directly: cached wake playback must not swallow the next short command."""
__test__ = False
import asyncio
import importlib.util
from pathlib import Path
import queue
import sys
import time
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

VOICE=Path(__file__).resolve().parents[1]
sys.path=[p for p in sys.path if Path(p or '.').resolve()!=Path(__file__).resolve().parent]
sys.path.insert(0,str(VOICE))

class WakeListeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_wake_drains_old_frames_and_listens_as_soon_as_reply_finishes(self):
        pending=queue.Queue()
        reset=Mock()
        conn=types.SimpleNamespace(config={'enable_wakeup_words_response_cache':True,'wakeup_words':['你好小智']},
            tts=types.SimpleNamespace(voice='test'),asr_audio_queue=pending,reset_audio_states=reset,
            logger=Mock(),dialogue=Mock(),just_woken_up=False)
        kinds=types.SimpleNamespace(FIRST='first',LAST='last')
        async def playback(_conn,kind,*args):
            self.assertTrue(conn.just_woken_up)
            pending.put(b'captured wake reply')
            await asyncio.sleep(0)
        audio=AsyncMock(side_effect=playback)
        config=Mock()
        config.get_wakeup_response.return_value={'file_path':'test.wav','text':'我在，请说。','time':time.time()+1000}
        stubs={
            'core.utils.dialogue':types.SimpleNamespace(Message=Mock()),
            'core.utils.util':types.SimpleNamespace(audio_to_data=AsyncMock(return_value=[b'opus']),
                remove_punctuation_and_length=lambda text:(len(text),text),opus_datas_to_wav_bytes=Mock()),
            'core.providers.tts.dto.dto':types.SimpleNamespace(SentenceType=kinds),
            'core.utils.wakeup_word':types.SimpleNamespace(WakeupWordsConfig=lambda:config),
            'core.handle.sendAudioHandle':types.SimpleNamespace(sendAudioMessage=audio,send_tts_message=AsyncMock()),
            'core.providers.tools.device_mcp':types.SimpleNamespace(MCPClient=Mock(),send_mcp_initialize_message=AsyncMock()),
        }
        spec=importlib.util.spec_from_file_location('wake_listening_test',VOICE/'core/handle/helloHandle.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,stubs):spec.loader.exec_module(module)
        self.assertTrue(await module.checkWakeupWords(conn,'你好小智'))
        self.assertEqual(audio.await_count,2)
        self.assertTrue(pending.empty())
        self.assertFalse(conn.just_woken_up)
        reset.assert_called_once()

if __name__=='__main__':unittest.main()
