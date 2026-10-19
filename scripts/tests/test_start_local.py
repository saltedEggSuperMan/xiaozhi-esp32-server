"""Check startup decisions without launching or stopping any real process."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import start_local as startup


class StartupTests(unittest.TestCase):
    def service(self, ready):
        return startup.Service('example', 'example', (18001,), ready, ['example.exe'], Path.cwd())

    def test_healthy_service_is_reused_without_process_or_port_checks(self):
        with patch.object(startup.subprocess, 'Popen') as launch, patch.object(startup, 'port_open') as port:
            with redirect_stdout(StringIO()) as output:
                startup.start_service(self.service(lambda: True), 10)
            launch.assert_not_called()
            port.assert_not_called()
            self.assertIn('已运行，跳过', output.getvalue())

    def test_listening_service_is_allowed_to_finish_starting(self):
        ready = Mock(side_effect=[False, False, True])
        with patch.object(startup.subprocess, 'Popen') as launch, patch.object(startup, 'port_open', return_value=True):
            with patch.object(startup.time, 'sleep'), redirect_stdout(StringIO()):
                startup.start_service(self.service(ready), 10)
            launch.assert_not_called()
            self.assertEqual(ready.call_count, 3)

    def test_unhealthy_occupied_port_does_not_trigger_duplicate_launch(self):
        with patch.object(startup.subprocess, 'Popen') as launch, patch.object(startup, 'port_open', return_value=True):
            with patch.object(startup.time, 'monotonic', side_effect=[0, 0, 11]), redirect_stdout(StringIO()):
                with self.assertRaises(startup.StartupError):
                    startup.start_service(self.service(lambda: False), 10)
            launch.assert_not_called()

    def test_stopped_service_starts_and_records_process_and_logs(self):
        ready = Mock(side_effect=[False, True])
        with TemporaryDirectory() as temp:
            runtime = Path(temp)
            with patch.object(startup, 'RUNTIME', runtime), patch.object(startup, 'LOGS', runtime/'logs'):
                with patch.object(startup, 'port_open', return_value=False), redirect_stdout(StringIO()):
                    with patch.object(startup.subprocess, 'Popen', return_value=Mock(pid=12345)) as launch:
                        startup.start_service(self.service(ready), 10)
                self.assertEqual(launch.call_count, 1)
                state = json.loads((runtime/'all-services.json').read_text())
                self.assertEqual(state['example']['pid'], 12345)
                self.assertTrue((runtime/'logs/example.out.log').is_file())
                self.assertTrue((runtime/'logs/example.err.log').is_file())

    def test_failed_process_reports_log_location(self):
        with TemporaryDirectory() as temp:
            runtime = Path(temp)
            process = Mock(pid=12345)
            process.poll.return_value = 1
            with patch.object(startup, 'RUNTIME', runtime), patch.object(startup, 'LOGS', runtime/'logs'):
                with patch.object(startup, 'port_open', return_value=False), patch.object(startup.subprocess, 'Popen', return_value=process):
                    with redirect_stdout(StringIO()), self.assertRaises(startup.StartupError) as error:
                        startup.start_service(self.service(lambda: False), 10)
                    self.assertIn('example.err.log', str(error.exception))

    def test_restart_preview_keeps_process_state(self):
        with TemporaryDirectory() as temp:
            runtime = Path(temp)
            state = runtime/'all-services.json'
            state.write_text('{"example": {"pid": 12345}}', encoding='utf-8')
            with patch.object(startup, 'RUNTIME', runtime), redirect_stdout(StringIO()):
                with patch.object(startup.subprocess, 'run', return_value=Mock(returncode=0)) as run:
                    startup.stop_services([self.service(lambda: True)], check=True)
            self.assertIn('-Check', run.call_args.args[0])
            self.assertEqual(json.loads(state.read_text())['example']['pid'], 12345)

    def test_failed_stop_leaves_state_and_aborts_restart(self):
        with TemporaryDirectory() as temp:
            runtime = Path(temp)
            state = runtime/'all-services.json'
            state.write_text('{"example": {"pid": 12345}}', encoding='utf-8')
            with patch.object(startup, 'RUNTIME', runtime), redirect_stdout(StringIO()):
                with patch.object(startup.subprocess, 'run', return_value=Mock(returncode=1)):
                    with self.assertRaises(startup.StartupError):
                        startup.stop_services([self.service(lambda: True)])
            self.assertEqual(json.loads(state.read_text())['example']['pid'], 12345)

    def test_stopped_service_state_is_removed_without_touching_other_entries(self):
        with TemporaryDirectory() as temp:
            runtime = Path(temp)
            state = runtime/'all-services.json'
            state.write_text('{"example": {"pid": 12345}, "other": {"pid": 6789}}', encoding='utf-8')
            with patch.object(startup, 'RUNTIME', runtime), redirect_stdout(StringIO()):
                with patch.object(startup.subprocess, 'run', return_value=Mock(returncode=0)) as run:
                    startup.stop_services([self.service(lambda: True)])
            self.assertNotIn('-Check', run.call_args.args[0])
            self.assertEqual(json.loads(state.read_text()), {'other': {'pid': 6789}})

    def audio_args(self, **kwargs):
        return SimpleNamespace(**{'check': False, 'audio_output': None,
                                  'select_audio_output': True, **kwargs})

    def test_menu_selects_both_outputs_and_enter_keeps_previous(self):
        for answer, expected in [('1', 'server_pc'), ('2', 'device'), ('', 'server_pc')]:
            with self.subTest(answer=answer), patch('builtins.input', return_value=answer), redirect_stdout(StringIO()):
                self.assertEqual(startup.choose_audio_output(self.audio_args(),
                                 {'audio_output': {'mode': 'server_pc'}}), expected)

    def test_menu_retries_invalid_input_and_eof_has_automation_hint(self):
        with patch('builtins.input', side_effect=['invalid', '2']), redirect_stdout(StringIO()):
            self.assertEqual(startup.choose_audio_output(self.audio_args(), {}), 'device')
        with patch('builtins.input', side_effect=EOFError), redirect_stdout(StringIO()):
            with self.assertRaisesRegex(startup.StartupError, 'AudioOutput'):
                startup.choose_audio_output(self.audio_args(), {})

    def test_check_explicit_mode_and_direct_powershell_do_not_prompt(self):
        with patch('builtins.input') as prompt:
            self.assertIsNone(startup.choose_audio_output(self.audio_args(check=True, audio_output='server_pc'), {}))
            self.assertEqual(startup.choose_audio_output(self.audio_args(audio_output='device'), {}), 'device')
            self.assertIsNone(startup.choose_audio_output(self.audio_args(select_audio_output=False), {}))
            prompt.assert_not_called()

    def test_cancel_leaves_configuration_and_processes_untouched(self):
        with patch('builtins.input', return_value='q'), redirect_stdout(StringIO()), \
             patch.object(startup, 'stop_services') as stop, patch.object(startup, 'save_audio_choice') as save:
            with self.assertRaises(KeyboardInterrupt):
                startup.choose_audio_output(self.audio_args(), {})
            stop.assert_not_called()
            save.assert_not_called()

    def test_saving_choice_preserves_other_settings_and_pc_volume(self):
        with TemporaryDirectory() as temp:
            voice = Path(temp)
            path = voice/'data/.config.yaml'
            path.parent.mkdir()
            path.write_text('manager-api:\n  secret: synthetic-test\naudio_output:\n  mode: server_pc\n  pc:\n    volume: 0.35\n    device: 8\n', encoding='utf-8')
            with patch.object(startup, 'VOICE', voice), redirect_stdout(StringIO()):
                selected = startup.save_audio_choice('device')
            self.assertEqual(selected, {'mode': 'device', 'pc': {'device': 8, 'volume': 0.35}})
            self.assertEqual(startup.read_yaml(path)['manager-api']['secret'], 'synthetic-test')

    def test_pc_output_failure_does_not_save_new_selection(self):
        with TemporaryDirectory() as temp:
            voice = Path(temp)
            path = voice/'data/.config.yaml'
            path.parent.mkdir()
            original = 'audio_output:\n  mode: device\n'
            path.write_text(original, encoding='utf-8')
            fake_audio = SimpleNamespace(query_devices=Mock(side_effect=RuntimeError('no audio')))
            with patch.object(startup, 'VOICE', voice), patch.dict('sys.modules', {'sounddevice': fake_audio}):
                with self.assertRaises(startup.StartupError):
                    startup.save_audio_choice('server_pc')
            self.assertEqual(path.read_text(encoding='utf-8'), original)

    def test_changed_or_unknown_audio_restarts_only_voice(self):
        for previous in [None, {'mode': 'device'}]:
            with self.subTest(previous=previous), TemporaryDirectory() as temp:
                runtime = Path(temp)
                voice = startup.Service('xiaozhi-server', 'voice', (8000, 8003), lambda: True, [], Path.cwd(),
                                        {'mode': 'server_pc'})
                (runtime/'all-services.json').write_text(json.dumps({'xiaozhi-server': {'audio_output': previous}}))
                with patch.object(startup, 'RUNTIME', runtime), patch.object(startup, 'port_open', return_value=True), \
                     patch.object(startup, 'stop_services') as stop:
                    startup.apply_audio_choice(voice, 'server_pc')
                    stop.assert_called_once_with([voice], only_voice=True)

    def test_matching_audio_selection_keeps_running_voice(self):
        with TemporaryDirectory() as temp:
            runtime = Path(temp)
            audio = {'mode': 'device'}
            (runtime/'all-services.json').write_text(json.dumps({'xiaozhi-server': {'audio_output': audio}}))
            voice = startup.Service('xiaozhi-server', 'voice', (8000,), lambda: True, [], Path.cwd(), audio)
            with patch.object(startup, 'RUNTIME', runtime), patch.object(startup, 'port_open', return_value=True), \
                 patch.object(startup, 'stop_services') as stop:
                startup.apply_audio_choice(voice, 'device')
                startup.apply_audio_choice(voice, None)
                stop.assert_not_called()

    def test_voice_only_stop_passes_filter_and_preserves_other_state(self):
        with TemporaryDirectory() as temp:
            runtime = Path(temp)
            state = runtime/'all-services.json'
            state.write_text('{"xiaozhi-server": {"pid": 1}, "manager-api": {"pid": 2}}')
            voice = startup.Service('xiaozhi-server', 'voice', (8000, 8003), lambda: True, [], Path.cwd())
            with patch.object(startup, 'RUNTIME', runtime), redirect_stdout(StringIO()), \
                 patch.object(startup.subprocess, 'run', return_value=Mock(returncode=0)) as run:
                startup.stop_services([voice], only_voice=True)
            self.assertIn('-OnlyVoice', run.call_args.args[0])
            self.assertEqual(json.loads(state.read_text()), {'manager-api': {'pid': 2}})

    def test_one_click_shutdown_uses_ports_without_contacting_services(self):
        configurations = [
            {'server': {'port': 8000, 'http_port': 8003}},
            {'server': {'port': 9000}},
            {'server': {'port': 9002}},
            {'server': {'port': 8002}},
        ]
        with TemporaryDirectory() as temp, patch.object(startup, 'RUNTIME', Path(temp)), \
             patch.object(startup, 'read_yaml', side_effect=configurations), \
             patch.object(startup, 'stop_services') as stop, \
             patch.object(startup, 'get_json') as api, redirect_stdout(StringIO()):
            startup.stop_project_services(check=True)
            services = stop.call_args.args[0]
            self.assertEqual({p for s in services for p in s.ports}, {9000, 9002, 8003, 8001, 8006})
            self.assertEqual({s.key for s in services}, {'manager-api', 'manager-web', 'xiaozhi-server', 'digital-test'})
            self.assertTrue(stop.call_args.kwargs['check'])
            api.assert_not_called()

    def test_music_menu_and_explicit_options(self):
        with TemporaryDirectory() as temp, patch.object(startup,'RUNTIME',Path(temp)):
            (Path(temp)/'music').mkdir()
            (Path(temp)/'music/settings.json').write_text('{"source":"netease"}')
            args=SimpleNamespace(check=False,music_source=None,audio_output=None,select_audio_output=True)
            for answer,expected in [('1','local'),('2','netease'),('','netease')]:
                with patch('builtins.input',return_value=answer),redirect_stdout(StringIO()):
                    self.assertEqual(startup.choose_music_source(args),expected)
            with patch('builtins.input') as prompt:
                args.check=True
                self.assertIsNone(startup.choose_music_source(args))
                args.check=False;args.music_source='local'
                self.assertEqual(startup.choose_music_source(args),'local')
                args.music_source=None;args.audio_output='server_pc'
                self.assertIsNone(startup.choose_music_source(args))
                prompt.assert_not_called()

    def test_shutdown_includes_installed_music_without_contacting_api(self):
        configurations=[{'server':{'port':8000,'http_port':8003}},{},
                        {'server':{'port':8002}},{'server':{'port':8002}}]
        with TemporaryDirectory() as temp, patch.object(startup,'RUNTIME',Path(temp)):
            (Path(temp)/'music').mkdir()
            (Path(temp)/'music/settings.json').write_text('{"source":"local"}')
            with patch.object(startup,'read_yaml',side_effect=configurations), \
                 patch.object(startup,'stop_services') as stop,redirect_stdout(StringIO()):
                startup.stop_project_services(check=True)
            services=stop.call_args.args[0]
            self.assertIn('music-bridge',{s.key for s in services})
            self.assertIn(8007,{p for s in services for p in s.ports})


if __name__ == '__main__':
    unittest.main()
