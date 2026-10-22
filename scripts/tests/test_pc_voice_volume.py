"""Device volume MCP calls follow the PC_DEVICE software route, offline."""
import asyncio
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from scripts.audio_output import VOICE
from core.utils.audioOutputRouter import AudioOutputRouter

IDENT='{0.0.0.00000000}.{12345678-1234-1234-1234-123456789abc}'


class VoiceVolumeTests(unittest.IsolatedAsyncioTestCase):
    def executor(self):
        self.call=AsyncMock(return_value='true')
        registry=SimpleNamespace(Action=SimpleNamespace(ERROR='error',NOTFOUND='notfound',REQLLM='llm'),
                                 ActionResponse=lambda **data:SimpleNamespace(**data))
        base=SimpleNamespace(ToolType=None,ToolDefinition=None,ToolExecutor=object)
        spec=importlib.util.spec_from_file_location('core.providers.tools.device_mcp._volume_test',
                VOICE/'core/providers/tools/device_mcp/mcp_executor.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules',{'plugins_func.register':registry,'core.providers.tools.base':base,
             'core.providers.tools.device_mcp.mcp_handler':SimpleNamespace(call_mcp_tool=self.call)}):
            spec.loader.exec_module(module)
        return module.DeviceMCPExecutor

    def conn(self,config):
        return SimpleNamespace(config=config,mcp_client=SimpleNamespace(is_ready=AsyncMock(return_value=True),
            name_mapping={'volume_tool':'self.audio_speaker.set_volume','status_tool':'self.get_device_status'}))

    async def test_voice_volume_persists_software_gain_without_a_hardware_call(self):
        executor=self.executor()
        with TemporaryDirectory() as temp:
            path=Path(temp)/'config.yaml'
            path.write_text(f'manager-api:\n  secret: synthetic\naudio_output:\n  mode: PC_DEVICE\n  pc:\n    device_id: "{IDENT}"\n    volume: 0.35\n',encoding='utf8')
            conn=self.conn({'_audio_output_local_path':str(path)})
            result=await executor(conn).execute(conn,'volume_tool',{'volume':20})
            self.assertEqual(json.loads(result.result)['audio_speaker']['volume'],20)
            self.assertEqual(AudioOutputRouter.settings(conn.config)[2],0.2)
            self.call.assert_not_awaited();self.assertIn('secret: synthetic',path.read_text(encoding='utf8'))

    async def test_pc_status_reports_software_volume_and_preserves_other_device_status(self):
        executor=self.executor()
        self.call.return_value=json.dumps({'audio_speaker':{'volume':99},'screen':{'brightness':75}})
        conn=self.conn({'audio_output':{'mode':'PC_DEVICE','pc':{'device_id':IDENT,'volume':0.3}}})
        result=await executor(conn).execute(conn,'status_tool',{})
        status=json.loads(result.result)
        self.assertEqual(status['audio_speaker']['volume'],30)
        self.assertEqual(status['screen']['brightness'],75)

    async def test_hardware_and_legacy_pc_keep_hardware_volume_behavior(self):
        executor=self.executor()
        for mode in ('device','server_pc'):
            conn=self.conn({'audio_output':{'mode':mode}})
            await executor(conn).execute(conn,'volume_tool',{'volume':40})
        self.assertEqual(self.call.await_count,2)

    async def test_invalid_voice_volume_does_not_modify_config_or_call_hardware(self):
        executor=self.executor()
        conn=self.conn({'audio_output':{'mode':'PC_DEVICE','pc':{'device_id':IDENT,'volume':0.35}}})
        result=await executor(conn).execute(conn,'volume_tool',{'volume':True})
        self.assertEqual(result.action,'notfound');self.call.assert_not_awaited()
        self.assertEqual(conn.config['audio_output']['pc']['volume'],0.35)
