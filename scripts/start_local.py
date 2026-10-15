"""Start the configured Windows development stack; reuse healthy services."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Callable
from urllib.parse import urlparse
import webbrowser

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / 'main/xiaozhi-server'
WEB = ROOT / 'main/manager-web'
API = ROOT / 'main/manager-api'
RUNTIME = ROOT / '.runtime'
LOGS = RUNTIME / 'logs'
PYTHON = VOICE / '.venv/Scripts/python.exe'
POWERSHELL = 'powershell.exe'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class StartupError(Exception):
    pass


def say(message):
    print(message, flush=True)


def read_yaml(path):
    if not path.is_file():
        raise StartupError(f'缺少配置文件：{path}')
    return yaml.safe_load(path.read_text(encoding='utf-8')) or {}


def port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def get_json(url):
    with httpx.Client(trust_env=False, timeout=3) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def api_ready(base):
    data = get_json(base + '/user/pub-config')
    return data.get('code') == 0 and 'version' in data.get('data', {})


def web_ready(base, api_path):
    with httpx.Client(trust_env=False, timeout=3) as client:
        page = client.get(base)
        page.raise_for_status()
        if 'id="app"' not in page.text:
            return False
    return api_ready(base + api_path)


def voice_ready(ws_port, http_port):
    from websockets.asyncio.client import connect

    async def check_current():
        # No device-id: get a diagnostic without opening an agent session.
        async with connect(f'ws://127.0.0.1:{ws_port}/xiaozhi/v1/', open_timeout=3) as ws:
            return 'digital-human' in await asyncio.wait_for(ws.recv(), 3)

    if not asyncio.run(check_current()):
        return False
    if http_port:
        with httpx.Client(trust_env=False, timeout=3) as client:
            response = client.get(f'http://127.0.0.1:{http_port}/mcp/vision/explain')
            return response.status_code == 200 and 'MCP Vision' in response.text
    return True


def tester_ready(port):
    if get_json(f'http://127.0.0.1:{port}/health').get('status') != 'ok':
        return False
    with httpx.Client(trust_env=False, timeout=3) as client:
        response = client.get(f'http://127.0.0.1:{port}/index.html')
        return response.status_code == 200 and 'js/' in response.text and '小智' in response.text


def mysql_version(host, port):
    with socket.create_connection((host, port), timeout=2) as conn:
        packet = conn.recv(512)
    if len(packet) < 6 or packet[4] != 10:
        raise StartupError(f'{host}:{port} 未返回 MySQL 握手信息')
    version = packet[5:].split(b'\0', 1)[0].decode('ascii', errors='replace')
    if not version.startswith('8.'):
        raise StartupError(f'当前连接到 MySQL {version}，请检查项目的 MySQL 8 端口配置')
    return version


def redis_ready(config):
    def request(conn, stream, *parts):
        encoded = [str(part).encode() for part in parts]
        conn.sendall(b'*%d\r\n' % len(encoded) + b''.join(
            b'$%d\r\n' % len(part) + part + b'\r\n' for part in encoded))
        reply = stream.readline(1024)
        if reply.startswith(b'-'):
            raise StartupError('Redis 认证或连接失败，请检查 application-dev.yml 中的 Redis 配置')
        return reply

    with socket.create_connection((config.get('host', '127.0.0.1'), int(config.get('port', 6379))), timeout=2) as conn, conn.makefile('rb') as stream:
        if config.get('password'):
            parts = [config['username'], config['password']] if config.get('username') else [config['password']]
            request(conn, stream, 'AUTH', *parts)
        request(conn, stream, 'SELECT', config.get('database', 0))
        return request(conn, stream, 'PING') == b'+PONG\r\n'


def ensure_dependency(name, service_name, host, port):
    if port_open(host, port):
        say(f'[已运行，跳过] {name} {host}:{port}')
        return
    if host not in ('localhost', '127.0.0.1', '::1'):
        raise StartupError(f'{name} {host}:{port} 无法连接，请先启动远程服务')
    say(f'[启动] Windows 服务 {service_name}')
    result = subprocess.run([POWERSHELL, '-NoProfile', '-Command',
        f"$ErrorActionPreference='Stop'; Start-Service -Name '{service_name}'"],
        capture_output=True, creationflags=HIDDEN)
    if result.returncode:
        raise StartupError(f'无法启动 {service_name}。请以管理员身份运行 start-all.cmd，并确认该 Windows 服务已安装。')
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if port_open(host, port):
            return
        time.sleep(0.5)
    raise StartupError(f'{service_name} 已启动，但 {host}:{port} 仍未监听，请检查服务配置')


@dataclass
class Service:
    key: str
    name: str
    ports: tuple[int, ...]
    ready: Callable[[], bool]
    command: list[str]
    cwd: Path


def healthy(service):
    try:
        return service.ready()
    except Exception:
        return False


def start_service(service, timeout):
    if healthy(service):
        say(f'[已运行，跳过] {service.name}，健康检查通过')
        return
    occupied = any(port_open('127.0.0.1', port) for port in service.ports)
    process = None
    if occupied:
        say(f'[等待] {service.name} 端口已监听，等待健康检查通过……')
    else:
        say(f'[启动] {service.name}')
        LOGS.mkdir(parents=True, exist_ok=True)
        output = LOGS / f'{service.key}.out.log'
        error = LOGS / f'{service.key}.err.log'
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        for path in (output, error):
            if path.exists():
                shutil.copy2(path, path.with_name(f'{path.stem}.{stamp}{path.suffix}'))
        env = dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1')
        env['PATH'] = str(PYTHON.parent) + os.pathsep + env.get('PATH', '')
        with output.open('wb') as out, error.open('wb') as err:
            process = subprocess.Popen(service.command, cwd=service.cwd, env=env,
                stdin=subprocess.DEVNULL, stdout=out, stderr=err, creationflags=HIDDEN)
        state_path = RUNTIME / 'all-services.json'
        state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        state[service.key] = {'pid':process.pid, 'started_at':datetime.now().isoformat(),
                             'stdout':str(output), 'stderr':str(error)}
        state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
    deadline = time.monotonic() + timeout
    next_progress = time.monotonic() + 10
    while time.monotonic() < deadline:
        if healthy(service):
            say(f'[就绪] {service.name}')
            return
        if process is not None and process.poll() is not None:
            raise StartupError(f'{service.name} 提前退出（{process.returncode}），请查看 {LOGS / (service.key + ".err.log")} 和对应 .out.log')
        if time.monotonic() >= next_progress:
            say(f'[等待] {service.name} 正在启动……日志：{LOGS / (service.key + ".out.log")}')
            next_progress = time.monotonic() + 10
        time.sleep(0.5)
    raise StartupError(f'{service.name} 在 {timeout} 秒内未通过健康检查（端口 {service.ports}）。请检查端口占用及 {LOGS} 中的日志后重试。')


@contextmanager
def startup_lock():
    import msvcrt
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME / 'start-all.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise StartupError('另一个完整启动脚本正在运行，请等待其完成。') from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 10):
        raise StartupError('请使用本项目 Windows Python 3.10 虚拟环境运行')
    with startup_lock():
        dev = read_yaml(API / 'src/main/resources/application-dev.yml')
        app = read_yaml(API / 'src/main/resources/application.yml')
        local = read_yaml(VOICE / 'data/.config.yaml')
        defaults = read_yaml(VOICE / 'config.yaml')
        jdbc = dev['spring']['datasource']['druid']['url']
        mysql = urlparse(jdbc.removeprefix('jdbc:'))
        redis = dev['spring']['data']['redis']
        if int(redis.get('database', 0)) == 0:
            raise StartupError('本地完整启动需要专用 Redis 逻辑库（当前约定为 15）；Java 初始化会清空所选库，请检查 application-dev.yml。')
        server = {**defaults['server'], **local.get('server', {})}
        ws_port, http_port = int(server['port']), int(server.get('http_port', 8003))
        api_port = int(dev.get('server', {}).get('port', app['server']['port']))
        api_path = app['server']['servlet']['context-path'].rstrip('/')
        api_base = f'http://127.0.0.1:{api_port}{api_path}'
        if local.get('manager-api', {}).get('url', '').rstrip('/') != api_base:
            raise StartupError(f'语音服务 manager-api.url 应指向 {api_base}，请检查 data/.config.yaml')
        if not local['manager-api'].get('secret') or '你' in local['manager-api']['secret']:
            raise StartupError('请先配置 data/.config.yaml 中的 manager-api.secret')
        node = shutil.which('node.exe')
        cli = WEB / 'node_modules/@vue/cli-service/bin/vue-cli-service.js'
        if not node or not cli.is_file():
            raise StartupError('缺少 Node.js 或智控台依赖，请安装 Node.js 并在 main/manager-web 执行 npm ci')
        if not (VOICE / 'models/SenseVoiceSmall/model.pt').is_file():
            raise StartupError('缺少 models/SenseVoiceSmall/model.pt')
        say('[环境检查] JDK / Maven / Python / FFmpeg / ffprobe / Opus')
        for directory in (API, VOICE):
            result = subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(directory/'start-local.ps1'), '-Check'], cwd=directory)
            if result.returncode:
                raise StartupError(f'{directory.name} 环境检查失败，请按上方提示补齐环境')
        say('[环境检查] Node.js ' + subprocess.check_output([node, '--version'], text=True).strip())
        def ps(directory):
            return [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(directory/'start-local.ps1')]
        services = [
            Service('manager-api','Java 后端',(api_port,),lambda:api_ready(api_base),ps(API),API),
            Service('manager-web','智控台',(8001,),lambda:web_ready('http://127.0.0.1:8001',api_path),
                    [node,str(cli),'serve','--host','127.0.0.1','--port','8001'],WEB),
            Service('xiaozhi-server','Python 语音服务',tuple(p for p in (ws_port,http_port) if p),
                    lambda:voice_ready(ws_port,http_port),ps(VOICE),VOICE),
            Service('digital-test','浏览器测试页',(8006,),lambda:tester_ready(8006),
                    [str(PYTHON),'-u',str(ROOT/'main/digital-human/serve-local.py')],ROOT/'main/digital-human'),
        ]
        if args.check:
            for name,host,port in [('MySQL',mysql.hostname,mysql.port or 3306),('Redis',redis.get('host','127.0.0.1'),int(redis.get('port',6379)))]:
                say(f'[{"已监听" if port_open(host,port) else "未启动"}] {name} {host}:{port}')
            for service in services:
                say(f'[{"健康" if healthy(service) else "未就绪"}] {service.name}')
            return
        ensure_dependency('MySQL','MySQL84',mysql.hostname,mysql.port or 3306)
        say('[连接成功] MySQL ' + mysql_version(mysql.hostname,mysql.port or 3306))
        ensure_dependency('Redis','Redis',redis.get('host','127.0.0.1'),int(redis.get('port',6379)))
        if not redis_ready(redis):
            raise StartupError('Redis PING 验证失败')
        say(f'[连接成功] Redis，逻辑库 {redis["database"]}')
        for service in services:
            start_service(service,args.timeout)
            if service.key == 'manager-api':
                with httpx.Client(trust_env=False,timeout=5) as client:
                    result=client.post(api_base+'/config/server-base',headers={
                        'Authorization':'Bearer '+local['manager-api']['secret']})
                    if result.status_code != 200 or result.json().get('code') != 0:
                        raise StartupError('Java 后端已就绪，但 manager-api.secret 验证失败，请检查本地配置')
        say('\n完整服务已就绪。关闭当前终端后，后台服务会继续运行。')
        say('智控台：http://127.0.0.1:8001')
        say('测试页：http://127.0.0.1:8006/index.html')
        say(f'Java API：{api_base}\n语音接口：ws://127.0.0.1:{ws_port}/xiaozhi/v1/')
        say(f'日志目录：{LOGS}')
        if args.open_browser:
            webbrowser.open('http://127.0.0.1:8001')
            webbrowser.open('http://127.0.0.1:8006/index.html')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        say('\n启动等待已取消；已启动的后台服务会继续运行。')
        sys.exit(130)
    except Exception as exc:
        # Avoid traceback or arbitrary response bodies containing credentials.
        say(f'\n[启动失败] {exc}' if isinstance(exc,StartupError) else f'\n[启动失败] {type(exc).__name__}，请检查本地配置和日志。')
        sys.exit(1)
