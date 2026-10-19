"""Offline local speech configuration tests. Run directly with unittest/Python."""
__test__ = False

import asyncio
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
import wave

VOICE = Path(__file__).resolve().parents[1]
sys.path = [entry for entry in sys.path if Path(entry or '.').resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(VOICE))

from core.utils.speechResponse import apply_speech_response
from core.utils.windowsSpeech import WindowsSpeechWorker


class SpeechSettingsTests(unittest.TestCase):
    def test_default_does_not_replace_configured_tts_or_vad(self):
        config = {'selected_module': {'TTS': 'edge'}, 'VAD': {'vad': {'type': 'silero', 'min_silence_duration_ms': 700}}}
        apply_speech_response(config)
        self.assertEqual(config['selected_module']['TTS'], 'edge')
        self.assertEqual(config['VAD']['vad']['min_silence_duration_ms'], 700)

    def test_local_override_applies_to_management_private_and_common_config(self):
        settings = {'tts_mode': 'windows_local', 'vad_silence_ms': 400}
        for config in ({'selected_module': {'TTS': 'edge'}},
                       {'VAD': {'vad': {'type': 'silero', 'min_silence_duration_ms': 700}}}):
            apply_speech_response(config, settings)
            self.assertEqual(config['selected_module']['TTS'], 'TTS_WindowsLocal')
            self.assertEqual(config['TTS']['TTS_WindowsLocal']['voice'], 'Microsoft Huihui Desktop')
            if 'VAD' in config:
                self.assertEqual(config['VAD']['vad']['min_silence_duration_ms'], 400)

    def test_invalid_settings_are_rejected_before_installation(self):
        for settings in ({'tts_mode': 'unknown'}, {'vad_silence_ms': 20},
                         {'tts_mode': 'windows_local', 'windows_rate': 99},
                         {'tts_mode': 'windows_local', 'windows_voice': ''}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                apply_speech_response({}, settings)


class SapiWorkerTests(unittest.TestCase):
    def test_thread_owned_synthesis_emits_wav_and_treats_text_as_plain_text(self):
        token = types.SimpleNamespace(GetDescription=lambda: 'Microsoft Huihui Desktop')
        synth = Mock()
        synth.GetVoices.return_value = types.SimpleNamespace(Count=1, Item=lambda index: token)
        fmt = types.SimpleNamespace(Channels=1, BitsPerSample=16, SamplesPerSec=22050)
        stream = types.SimpleNamespace(Format=types.SimpleNamespace(Type=0, GetWaveFormatEx=lambda: fmt),
                                       GetData=lambda: bytes(4410))
        dispatch = Mock(side_effect=lambda name: synth if name == 'SAPI.SpVoice' else stream)
        pythoncom = types.SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        client = types.SimpleNamespace(Dispatch=dispatch)
        win32com = types.ModuleType('win32com')
        win32com.client = client
        with patch.dict(sys.modules, {'pythoncom': pythoncom, 'win32com': win32com, 'win32com.client': client}), \
             patch('sys.platform', 'win32'):
            worker = WindowsSpeechWorker()
            try:
                for _ in range(2):
                    audio = worker.synthesize('<speak>plain text</speak>', 'Microsoft Huihui Desktop')
                    with wave.open(io.BytesIO(audio), 'rb') as wav:
                        self.assertEqual(wav.getframerate(), 22050)
                        self.assertEqual(wav.getnframes(), 2205)
                synth.Speak.assert_called_with('<speak>plain text</speak>', 16)
            finally:
                worker.close()
        self.assertFalse(worker.thread.is_alive())
        pythoncom.CoInitialize.assert_called_once()
        pythoncom.CoUninitialize.assert_called_once()
        self.assertEqual(sum(call.args[0] == 'SAPI.SpVoice' for call in dispatch.call_args_list), 1)


if __name__ == '__main__':
    unittest.main()
