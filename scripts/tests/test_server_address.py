"""Validate retention of unrelated NVS data using an official IDF-generated fixture."""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import server_address as repair


class ServerAddressTests(unittest.TestCase):
    def fixture(self, root):
        csv = root/'input.csv'
        csv.write_text('key,type,encoding,value\n'
                       'wifi,namespace,,\n'
                       'ota_url,data,string,http://192.168.0.102:8002/xiaozhi/ota/\n'
                       'ssid,data,string,example-wifi\n'
                       'password,data,string,example-not-a-real-password\n'
                       'calibration,data,hex2bin,01020304AABBCCDD\n'
                       'websocket,namespace,,\n'
                       'url,data,string,ws://192.168.0.102:8000/xiaozhi/v1/\n'
                       'token,data,string,example-not-a-real-token\n'
                       'version,data,u32,1\n'
                       'board,namespace,,\n'
                       'uuid,data,string,example-device-uuid\n',encoding='utf-8')
        dest=root/'nvs.bin'
        generator=repair.IDF/'esp-idf-v5.5.2/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py'
        subprocess.run([str(repair.IDF_PYTHON),str(generator),'generate',str(csv),str(dest),'0x4000'],
                       check=True,capture_output=True)
        return dest.read_bytes()

    def values(self, data):
        partition=repair.load_parser()('nvs',bytearray(data))
        namespaces={e.data['value']:e.key for p in partition.pages for e in p.entries
                    if e.state=='Written' and e.metadata['namespace']==0}
        result={}
        for page in partition.pages:
            for entry in page.entries:
                if entry.state!='Written' or entry.metadata['namespace']==0:
                    continue
                value=entry.data['value']
                if entry.children:
                    value=b''.join(bytes(c.raw) for c in entry.children)[:entry.data['size']]
                result[(namespaces[entry.metadata['namespace']],entry.key)]=value
        return result

    def test_update_preserves_wifi_identity_token_and_calibration(self):
        with TemporaryDirectory() as temp:
            before=self.fixture(Path(temp))
            after,changes=repair.patch_nvs(before,'192.168.0.100')
            original=self.values(before); actual=self.values(after)
            self.assertEqual(len(before),len(after))
            targets={('wifi','ota_url'),('websocket','url')}
            self.assertEqual({(c['namespace'],c['key']) for c in changes},targets)
            for key,value in original.items():
                if key not in targets:self.assertEqual(actual[key],value,key)
            self.assertEqual(actual[('wifi','ota_url')],b'http://192.168.0.100:8002/xiaozhi/ota/\0')
            self.assertEqual(actual[('websocket','url')],b'ws://192.168.0.100:8000/xiaozhi/v1/\0')
            # Once set, running again must not rewrite other bytes or create new keys.
            repeated,_=repair.patch_nvs(after,'192.168.0.100')
            self.assertEqual(repeated,after)

    def test_bad_page_checksum_refuses_patch(self):
        with TemporaryDirectory() as temp:
            bad=bytearray(self.fixture(Path(temp)))
            bad[28]^=1
            with self.assertRaises(RuntimeError):repair.patch_nvs(bad,'192.168.0.100')

    def test_separate_ports_preserve_all_non_endpoint_values(self):
        with TemporaryDirectory() as temp:
            before=self.fixture(Path(temp))
            after,_=repair.patch_nvs(before,'192.168.0.100',9000,9002)
            original=self.values(before); actual=self.values(after)
            self.assertEqual(actual[('wifi','ota_url')],b'http://192.168.0.100:9002/xiaozhi/ota/\0')
            self.assertEqual(actual[('websocket','url')],b'ws://192.168.0.100:9000/xiaozhi/v1/\0')
            for key,value in original.items():
                if key not in (('wifi','ota_url'),('websocket','url')):
                    self.assertEqual(actual[key],value,key)

    def test_invalid_ip_port_and_com_inputs_are_rejected(self):
        for host in ('127.0.0.1','198.18.0.1','8.8.8.8','localhost','192.168.0.999','::1'):
            with self.subTest(host=host),self.assertRaises(ValueError):repair.validate_host(host)
        for port in ('0','65536','-1','abc'):
            with self.subTest(port=port),self.assertRaises(ValueError):repair.validate_port(port)
        for port in ('COM0','COM-1','socket://example:123','3'):
            with self.subTest(port=port),self.assertRaises(ValueError):repair.validate_com_port(port)
        self.assertEqual(repair.validate_com_port('com3'),'COM3')

    def test_unhealthy_ota_is_rejected_even_if_error_mentions_websocket(self):
        settings={'server.websocket':'ws://192.168.0.100:8000/xiaozhi/v1/',
                  'server.ota':'http://192.168.0.100:8002/xiaozhi/ota/'}
        response=Mock(status_code=200,text='OTA接口不正常，缺少websocket地址')
        with patch.object(repair.asyncio,'run',side_effect=lambda coro:(coro.close() or True)), \
             patch.object(repair.httpx,'Client') as client:
            client.return_value.__enter__.return_value.get.return_value=response
            with self.assertRaises(RuntimeError):repair.verify_endpoints(settings)

    def test_extra_auth_or_remote_endpoint_is_not_replaced(self):
        for url in ('wss://example.com/x','ws://192.168.0.102/x?token=example',
                    'ws://user:pass@192.168.0.102/x','ws://8.8.8.8/x'):
            with self.subTest(url=url),self.assertRaises(RuntimeError):
                repair.replace_host(url,'192.168.0.100')


if __name__=='__main__':
    unittest.main()
