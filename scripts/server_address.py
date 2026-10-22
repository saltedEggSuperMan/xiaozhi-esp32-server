"""Update this deployment and a USB-connected ESP32 without Wi-Fi reprovisioning."""
import argparse
import asyncio
from datetime import datetime
import ipaddress
import json
from pathlib import Path
import socket
import re
import struct
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from zlib import crc32

import pymysql
import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
IDF = ROOT / '.runtime/fw244'
PARSER_DIR = IDF / 'esp-idf-v5.5.2/components/nvs_flash/nvs_partition_tool'
IDF_PYTHON = IDF / 'tools/python_env/idf5.5_py3.10_env/Scripts/python.exe'


def load_parser():
    sys.path.insert(0, str(PARSER_DIR))
    from nvs_parser import NVS_Partition
    return NVS_Partition


def validate_host(host):
    address = ipaddress.ip_address(host)
    if address.version != 4 or not any(address in ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('Use a private LAN IPv4 address, not loopback or a VPN benchmark address.')
    return str(address)


def validate_port(port):
    value = int(port)
    if not 1 <= value <= 65535:
        raise ValueError('Port must be between 1 and 65535.')
    return value


def validate_com_port(port):
    value = port.strip().upper()
    if not re.fullmatch(r'COM[1-9][0-9]*', value):
        raise ValueError('Use a Windows COM port such as COM3.')
    return value


def replace_host(url, host, port=None):
    value = urlsplit(url)
    if value.scheme not in ('http', 'https', 'ws', 'wss') or not value.hostname:
        raise RuntimeError('Existing endpoint is not a supported URL.')
    if value.username or value.password or value.query or value.fragment:
        raise RuntimeError('Endpoint contains extra authentication parameters; refusing automatic update.')
    try:
        private = ipaddress.ip_address(value.hostname).is_private
    except ValueError as exc:
        raise RuntimeError('Existing endpoint uses a hostname; refusing automatic replacement.') from exc
    if not private:
        raise RuntimeError('Existing endpoint is not a private IP; refusing to replace a remote hostname.')
    port = validate_port(port) if port is not None else value.port
    authority = host + (f':{port}' if port else '')
    return urlunsplit((value.scheme, authority, value.path, '', ''))


def patch_nvs(original, host, ws_port=None, ota_port=None):
    """Change only existing URL slots, with the same ESP-IDF CRCs and allocation."""
    parser = load_parser()
    partition = parser('nvs', bytearray(original))
    namespaces = {entry.data['value']: entry.key for page in partition.pages
                  for entry in page.entries if entry.state == 'Written' and entry.metadata['namespace'] == 0}
    output = bytearray(original)
    changes = []
    allowed = set()
    for page in partition.pages:
        if page.is_empty:
            continue
        if page.header['status'] not in ('Active', 'Full') or page.header['crc']['original'] != page.header['crc']['computed']:
            raise RuntimeError('NVS page is not healthy; refusing in-place changes.')
        for entry in page.entries:
            namespace = namespaces.get(entry.metadata['namespace'])
            if entry.state != 'Written' or (namespace, entry.key) not in (('wifi', 'ota_url'), ('websocket', 'url')):
                continue
            crc = entry.metadata['crc']
            if (entry.metadata['type'] != 'string' or crc['original'] != crc['computed']
                    or crc['data_original'] != crc['data_computed']):
                raise RuntimeError('NVS endpoint has an invalid type or checksum.')
            raw = b''.join(bytes(child.raw) for child in entry.children)[:entry.data['size']]
            if not raw.endswith(b'\0'):
                raise RuntimeError('NVS URL is not terminated.')
            old = raw[:-1].decode('utf-8')
            new = replace_host(old, host, ota_port if namespace == 'wifi' else ws_port)
            encoded = new.encode('utf-8') + b'\0'
            capacity = (entry.metadata['span'] - 1) * 32
            if len(encoded) > capacity:
                raise RuntimeError('New URL exceeds its allocated slot. No device settings were written.')
            pos = page.start_address + 64 + entry.index * 32
            header = bytearray(output[pos:pos + 32])
            struct.pack_into('<H', header, 24, len(encoded))
            struct.pack_into('<I', header, 28, crc32(encoded, 0xffffffff))
            struct.pack_into('<I', header, 4, crc32(header[:4] + header[8:], 0xffffffff))
            output[pos:pos + 32] = header
            output[pos + 32:pos + 32 + capacity] = encoded + b'\xff' * (capacity - len(encoded))
            allowed.update(range(pos + 4, pos + 8))
            allowed.update(range(pos + 24, pos + 26))
            allowed.update(range(pos + 28, pos + 32 + capacity))
            changes.append({'namespace': namespace, 'key': entry.key, 'before': old, 'after': new})
    if {c['namespace'] for c in changes} != {'wifi', 'websocket'}:
        raise RuntimeError('Both saved OTA and WS settings must exist. No device settings were written.')
    if len(output) != len(original) or any(a != b and i not in allowed for i, (a, b) in enumerate(zip(original, output))):
        raise RuntimeError('Unexpected bytes changed outside URL slots.')
    checked = parser('nvs', output)
    for page in checked.pages:
        for entry in page.entries:
            if entry.state == 'Written':
                crc = entry.metadata['crc']
                if crc['original'] != crc['computed']:
                    raise RuntimeError('Generated NVS entry checksum is invalid.')
                if entry.metadata['type'] in ('string', 'blob', 'blob_data') and crc['data_original'] != crc['data_computed']:
                    raise RuntimeError('Generated NVS data checksum is invalid.')
    return bytes(output), changes


def redis_command(stream, conn, *parts):
    encoded = [str(p).encode() for p in parts]
    conn.sendall(b'*%d\r\n' % len(encoded) + b''.join(b'$%d\r\n' % len(p) + p + b'\r\n' for p in encoded))
    reply = stream.readline(1024)
    if not reply or reply[:1] not in (b'+', b':'):
        raise RuntimeError('Redis command failed; no cache-wide deletion was attempted.')
    return reply


def redis_connection(config):
    conn = socket.create_connection((config.get('host', '127.0.0.1'), int(config.get('port', 6379))), timeout=5)
    stream = conn.makefile('rb')
    try:
        if config.get('password'):
            parts = [config['username'], config['password']] if config.get('username') else [config['password']]
            redis_command(stream, conn, 'AUTH', *parts)
        redis_command(stream, conn, 'SELECT', config.get('database', 15))
        redis_command(stream, conn, 'PING')
    except Exception:
        stream.close(); conn.close()
        raise
    return conn, stream


def esptool(log, port, *args):
    command = [str(IDF_PYTHON), '-m', 'esptool', '--chip', 'esp32s3', '--port', port,
               '--baud', '460800', '--before', 'default_reset', *args]
    with log.open('wb') as file:
        result = subprocess.run(command, stdout=file, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(f'ESP32 operation failed. See local log: {log}')


def verify_endpoints(settings):
    from websockets.asyncio.client import connect

    async def check_ws():
        async with connect(settings['server.websocket'], open_timeout=5) as ws:
            return 'digital-human' in await asyncio.wait_for(ws.recv(), 5)

    try:
        if not asyncio.run(check_ws()):
            raise RuntimeError('WS endpoint did not return this project diagnostic.')
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(settings['server.ota'])
            if response.status_code != 200 or 'OTA接口运行正常' not in response.text:
                raise RuntimeError('OTA endpoint did not return this project diagnostic.')
    except Exception as exc:
        raise RuntimeError('New WS/OTA endpoint check failed. Check the IP and exposed service ports; no device settings were written.') from exc


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--host', required=True, help='PC LAN IPv4 address')
    ap.add_argument('--port', default='COM3')
    ap.add_argument('--ws-port', type=validate_port, help='Device-facing WS port; omitted keeps the saved port')
    ap.add_argument('--ota-port', type=validate_port, help='Device-facing OTA port; omitted keeps the saved port')
    ap.add_argument('--apply', action='store_true', help='Write addresses; otherwise back up and preview only')
    args = ap.parse_args()
    args.host = validate_host(args.host)
    args.port = validate_com_port(args.port)
    if not IDF_PYTHON.is_file() or not PARSER_DIR.is_dir():
        raise RuntimeError('Project ESP-IDF tools are missing.')
    backup = ROOT / '.runtime' / ('server-address-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True)
    print('Backing up the device partition table and NVS...', flush=True)
    esptool(backup/'read.log', args.port, '--after', 'no_reset', 'read_flash', '0x8000', '0x8000', str(backup/'partition-nvs-before.bin'))
    data = (backup/'partition-nvs-before.bin').read_bytes()
    table = {}
    for off in range(0, 3072, 32):
        magic, _, _, start, size, name, _ = struct.unpack_from('<HBBII16sI', data, off)
        if magic != 0x50aa:
            break
        table[name.split(b'\0', 1)[0].decode()] = (start, size)
    if table.get('nvs') != (0x9000, 0x4000) or table.get('assets') != (0x800000, 0x800000):
        raise RuntimeError('Device partition layout does not match the supported N16R8 firmware.')
    old_nvs = data[0x1000:0x5000]
    (backup/'nvs-before.bin').write_bytes(old_nvs)
    patched, changes = patch_nvs(old_nvs, args.host, args.ws_port, args.ota_port)
    (backup/'nvs-after.bin').write_bytes(patched)
    dev = yaml.safe_load((ROOT/'main/manager-api/src/main/resources/application-dev.yml').read_text(encoding='utf-8'))
    source = dev['spring']['datasource']['druid']
    uri = urlsplit(source['url'].removeprefix('jdbc:'))
    conn = pymysql.connect(host=uri.hostname, port=uri.port or 3306, user=source['username'], password=str(source['password']),
                           database=uri.path.lstrip('/'), charset='utf8mb4', connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT param_code,param_value FROM sys_params WHERE param_code IN (%s,%s)', ('server.websocket','server.ota'))
            old_server = dict(cur.fetchall())
            if set(old_server) != {'server.websocket', 'server.ota'}:
                raise RuntimeError('Expected server parameters were not found.')
            new_server = {key: replace_host(value, args.host, args.ws_port if key == 'server.websocket' else args.ota_port)
                          for key, value in old_server.items()}
            report = {'host': args.host, 'port': args.port, 'ws_port': args.ws_port, 'ota_port': args.ota_port,
                      'device_changes': changes,
                      'server_before': old_server, 'server_after': new_server, 'applied': False}
            (backup/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            for change in changes:
                print(f"Device {change['namespace']}.{change['key']}: {change['before']} -> {change['after']}", flush=True)
            for key, value in new_server.items():
                print(f'Server {key}: {value}', flush=True)
            if not args.apply:
                restart_device(args.port)
                print('Preview only; device restarted. Add --apply to update.')
                return
            try:
                verify_endpoints(new_server)
            except RuntimeError:
                restart_device(args.port)
                raise
            cache, stream = redis_connection(dev['spring']['data']['redis'])
            try:
                for key, value in new_server.items():
                    cur.execute('UPDATE sys_params SET param_value=%s WHERE param_code=%s AND param_value=%s', (value, key, old_server[key]))
                    if cur.rowcount != 1 and value != old_server[key]:
                        raise RuntimeError('Server settings changed concurrently; update cancelled.')
                conn.commit()
                redis_command(stream, cache, 'HDEL', 'sys:params', 'server.websocket', 'server.ota')
            finally:
                stream.close(); cache.close()
        print('Server addresses updated; writing only device NVS at 0x9000...', flush=True)
        esptool(backup/'write.log', args.port, '--after', 'hard_reset', 'write_flash', '--flash_mode', 'dio',
                '--flash_freq', '80m', '--flash_size', '16MB', '0x9000', str(backup/'nvs-after.bin'))
        report['applied'] = True
        (backup/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('Completed. Wi-Fi credentials, binding, firmware and Otto resources were preserved.', flush=True)
        print(f'Private backup: {backup}', flush=True)
    finally:
        conn.close()


def restart_device(port):
    code = ('import sys,serial; from esptool.reset import HardReset; '
            's=serial.Serial(port=None,baudrate=115200); s.dtr=False; s.rts=False; '
            's.port=sys.argv[1]; s.open(); HardReset(s)(); s.close()')
    subprocess.run([str(IDF_PYTHON), '-c', code, port], check=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else 'Address update failed: '+type(exc).__name__, file=sys.stderr)
        sys.exit(1)
