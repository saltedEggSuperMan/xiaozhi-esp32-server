"""Check startup decisions without launching or stopping any real process."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
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


if __name__ == '__main__':
    unittest.main()
