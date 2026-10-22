"""Safety regressions: no physical devices, secrets or system changes."""
import asyncio
import json
import io
import wave
from array import array
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from scripts import audio_output as cli
from core.utils import pcAudioOutput as pc
from core.utils.audioOutputRouter import AudioOutputRouter
from core.utils.specifiedAudioOutput import SpecifiedPCMStream, output_options
from core.utils.windowsAudio import mpv_device, require_output

IDENT = '{0.0.0.00000000}.{12345678-1234-1234-1234-123456789abc}'


class DeviceAudioTests(unittest.TestCase):
    def test_explicit_mode_rejects_implicit_devices(self):
        for value in (None, '', 'default', 'auto', 0, True, 'speaker', 'wasapi/'+IDENT,
                      '{0.0.1.00000000}.{12345678-1234-1234-1234-123456789abc}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pc.audio_output_settings({'audio_output':{'mode':'PC_DEVICE','pc':{'device_id':value}}})
        self.assertEqual(pc.audio_output_settings({'audio_output':{'mode':'PC_DEVICE','pc':{'device_id':IDENT}}}),
                         ('PC_DEVICE', IDENT, 0.6))

    def test_missing_endpoint_and_same_name_never_rebind(self):
        with patch('core.utils.windowsAudio.list_outputs', return_value=[dict(id='other', name='same', selected=False, available=True)]):
            with self.assertRaises(RuntimeError):
                require_output(IDENT)

    def test_non_active_endpoint_is_unavailable(self):
        for state in ('disabled','not_present','unplugged'):
            with patch('core.utils.windowsAudio.list_outputs', return_value=[dict(id=IDENT, selected=True, available=False, state=state)]):
                with self.assertRaises(RuntimeError):
                    require_output(IDENT)

    def test_exact_shared_sink_has_no_default_or_fallback(self):
        args=output_options(IDENT)
        self.assertIn('--audio-device='+mpv_device(IDENT), args)
        self.assertIn('--ao=wasapi',args)
        self.assertIn('--audio-exclusive=no',args)
        self.assertIn('--audio-fallback-to-null=no',args)
        self.assertFalse(any('auto' in a or 'exclusive=yes' in a for a in args))

    def test_device_unavailable_does_not_even_launch_player(self):
        with patch('core.utils.specifiedAudioOutput.require_output', side_effect=RuntimeError('lost')), \
             patch('core.utils.specifiedAudioOutput.subprocess.Popen') as launch:
            with self.assertRaises(RuntimeError):
                SpecifiedPCMStream(IDENT).start()
            launch.assert_not_called()

    def test_monitor_loss_kills_bound_player(self):
        stream=SpecifiedPCMStream(IDENT)
        stream.process=Mock()
        stream.process.poll.return_value=None
        with patch('core.utils.specifiedAudioOutput.require_output', side_effect=RuntimeError('lost')):
            stream._monitor()
        stream.process.kill.assert_called_once()
        self.assertTrue(stream.finished.is_set())
        with self.assertRaises(RuntimeError):
            stream.write(b'pcm')

    def test_hot_reload_and_volume_persistence_preserve_other_config(self):
        with tempfile.TemporaryDirectory() as temp:
            voice=Path(temp)
            path=voice/'data/.config.yaml'
            path.parent.mkdir()
            path.write_text('manager-api:\n  secret: synthetic\naudio_output:\n  mode: device\n',encoding='utf8')
            with patch.object(cli,'VOICE',voice),patch.object(cli,'require_output'), \
                 patch.object(cli,'list_outputs',return_value=[]), \
                 patch('core.utils.specifiedAudioOutput.mpv_path'):
                result=cli.api_request(dict(action='save',mode='PC_DEVICE',device_id=IDENT,volume=0.25))
                self.assertEqual(result['device_id'],IDENT)
                self.assertNotIn('secret',json.dumps(result))
                _,saved=cli.load_local(path)
                self.assertEqual(saved['manager-api']['secret'],'synthetic')
                config={'_audio_output_local_path':str(path)}
                self.assertEqual(AudioOutputRouter.settings(config),('PC_DEVICE',IDENT,0.25))
                cli.api_request(dict(action='save',mode='PC_DEVICE',device_id=IDENT,volume=0.7))
                AudioOutputRouter._cache.clear()
                self.assertEqual(AudioOutputRouter.settings(config),('PC_DEVICE',IDENT,0.7))

    def test_failed_save_and_failed_test_leave_file_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            voice=Path(temp);path=voice/'data/.config.yaml';path.parent.mkdir()
            original='audio_output:\n  mode: device\n'
            path.write_text(original,encoding='utf8')
            for action in ('save','test'):
                with patch.object(cli,'VOICE',voice),patch.object(cli,'require_output',side_effect=RuntimeError('missing')):
                    with self.assertRaises(RuntimeError):
                        cli.api_request(dict(action=action,mode='PC_DEVICE',device_id=IDENT))
                self.assertEqual(path.read_text(encoding='utf8'),original)

    def test_invalid_volume_never_saves(self):
        for volume in (-1,1.1,float('nan'),True,'0.5'):
            with self.subTest(volume=volume),self.assertRaises(ValueError):
                pc.audio_output_settings({'audio_output':{'mode':'PC_DEVICE','pc':{'device_id':IDENT,'volume':volume}}})

    def test_volume_only_update_preserves_mode_endpoint_and_other_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            voice=Path(temp);path=voice/'data/.config.yaml';path.parent.mkdir()
            original=f'manager-api:\n  secret: synthetic\naudio_output:\n  mode: PC_DEVICE\n  pc:\n    device_id: "{IDENT}"\n    volume: 0.35\n'
            path.write_text(original,encoding='utf8')
            with patch.object(cli,'VOICE',voice),patch.object(cli,'list_outputs',return_value=[]):
                result=cli.api_request(dict(action='volume',volume=0.5,mode='device',device_id='must-ignore'))
                _,saved=cli.load_local(path)
            self.assertEqual(result['mode'],'PC_DEVICE');self.assertEqual(result['device_id'],IDENT)
            self.assertEqual(saved['audio_output']['pc']['volume'],0.5)
            self.assertEqual(saved['manager-api']['secret'],'synthetic')

    def test_volume_only_rejects_invalid_value_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            voice=Path(temp);path=voice/'data/.config.yaml';path.parent.mkdir()
            original='audio_output:\n  mode: device\n'
            path.write_text(original,encoding='utf8')
            with patch.object(cli,'VOICE',voice):
                for value in (None,True,-0.1,1.1,float('nan')):
                    with self.assertRaises(ValueError):
                        cli.api_request(dict(action='volume',volume=value))
                    self.assertEqual(path.read_text(encoding='utf8'),original)

    def test_chinese_sample_uses_offline_voice_and_converts_to_route_sample_rate(self):
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(22050)
            wav.writeframes(array('h',[1000,-1000]*1103).tobytes())
        with patch('core.utils.windowsSpeech.synthesize_wav',return_value=buffer.getvalue()) as synth:
            pcm=cli.speech_test_pcm({'windows_voice':'synthetic-voice'})
        self.assertIn('中文语音测试',synth.call_args.args[0])
        self.assertEqual(synth.call_args.kwargs['voice'],'synthetic-voice')
        self.assertAlmostEqual(len(pcm)/2,2206*24000/22050,delta=2)

    def test_synthesis_failure_cannot_be_reported_as_a_successful_beep(self):
        with patch.object(cli,'speech_test_pcm',side_effect=RuntimeError('voice unavailable')), \
             patch.object(cli,'PCSpeaker') as speaker:
            with self.assertRaisesRegex(RuntimeError,'voice unavailable'):
                asyncio.run(cli.test_speech(IDENT,0.35))
        speaker.assert_not_called()

    def test_device_test_plays_speech_and_never_saves_or_uses_tone(self):
        with tempfile.TemporaryDirectory() as temp:
            voice=Path(temp);path=voice/'data/.config.yaml';path.parent.mkdir()
            original='audio_output:\n  mode: device\n'
            path.write_text(original,encoding='utf8')
            with patch.object(cli,'VOICE',voice),patch.object(cli,'require_output'), \
                 patch.object(cli,'list_outputs',return_value=[]), \
                 patch('core.utils.specifiedAudioOutput.mpv_path'), \
                 patch.object(cli,'test_speech',new_callable=AsyncMock) as speech, \
                 patch.object(cli,'test_speaker',new_callable=AsyncMock) as tone:
                cli.api_request(dict(action='test',mode='PC_DEVICE',device_id=IDENT,volume=0.35))
            speech.assert_awaited_once_with(IDENT,0.35,None)
            tone.assert_not_awaited()
            self.assertEqual(path.read_text(encoding='utf8'),original)


if __name__=='__main__':
    unittest.main()
