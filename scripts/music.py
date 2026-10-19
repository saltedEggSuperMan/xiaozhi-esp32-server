"""Configure local/NetEase music, open the local account page, or control music."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import webbrowser

import httpx

ROOT=Path(__file__).resolve().parents[1]
DIR=ROOT/'.runtime/music'
URL='http://127.0.0.1:8007'


def installed():
    return (ROOT/'.runtime/music-api/node_modules/@neteasecloudmusicapienhanced/api/package.json').is_file() and (ROOT/'.runtime/mpv/mpv.exe').is_file()


def ready():
    try:
        with httpx.Client(trust_env=False,timeout=2) as client:
            return client.get(URL+'/health').json()=={'status':'ok','service':'xiaozhi-music-bridge'}
    except (httpx.HTTPError,ValueError):
        return False


def service():
    try:
        from .start_local import Service, StartupError
    except ImportError:
        from start_local import Service, StartupError
    node=shutil.which('node.exe')
    if not node or not installed():
        raise StartupError('音乐组件未安装，请运行 scripts/install_music.ps1')
    return Service('music-bridge','网易云音乐服务',(8007,),ready,[node,str(ROOT/'services/music-bridge/server.js')],ROOT)


def request(endpoint, **data):
    token=(DIR/'bridge.token').read_text(encoding='ascii').strip()
    with httpx.Client(trust_env=False,timeout=25) as client:
        response=client.post(URL+'/api/'+endpoint,headers={'Authorization':'Bearer '+token},json=data)
        body=response.json()
    if not body.get('ok'):
        raise RuntimeError(body.get('message','音乐操作失败'))
    return body


def source_choice(source):
    if source=='netease' and not installed():
        raise RuntimeError('网易云组件未安装，请运行 scripts/install_music.ps1')
    if ready():
        request('settings',source=source)
    else:
        DIR.mkdir(parents=True,exist_ok=True)
        file=DIR/'settings.json'
        settings=json.loads(file.read_text(encoding='utf8')) if file.exists() else {'volume':35}
        settings['source']=source
        temp=file.with_suffix('.tmp')
        temp.write_text(json.dumps(settings,indent=2),encoding='utf8')
        os.replace(temp,file)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',choices=('local','netease'))
    parser.add_argument('--open',action='store_true',help='打开扫码登录和播放管理页')
    parser.add_argument('--start',action='store_true')
    parser.add_argument('--restart',action='store_true')
    parser.add_argument('--status',action='store_true')
    parser.add_argument('--play',help='搜索并播放网易云歌曲')
    parser.add_argument('--kind',choices=('song','liked','playlist','cloud'),default='song')
    parser.add_argument('--action',choices=('pause','resume','stop','next','previous'))
    parser.add_argument('--volume',type=int)
    args=parser.parse_args()
    if args.source:
        source_choice(args.source)
        print('已切换音乐来源：'+args.source+'；下一次点歌立即生效，无需重启语音服务。')
    needs_service=args.open or args.start or args.restart or args.play or args.action or args.volume is not None
    if needs_service:
        try:
            from .start_local import start_service, startup_lock
        except ImportError:
            from start_local import start_service, startup_lock
        with startup_lock():
            if args.restart:
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                    str(ROOT/'scripts/stop_local.ps1'),'-Ports','8007','-OnlyMusic'],
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode: raise RuntimeError('音乐服务未能安全关闭，已取消重启')
            start_service(service(),40)
    if args.open:
        token=(DIR/'bridge.token').read_text(encoding='ascii').strip()
        webbrowser.open(URL+'/#token='+token)
        print('已打开本机音乐管理页；已有网易云登录会继续使用，未登录时再扫码。')
    if args.play:
        print(request('play',query=args.play,kind=args.kind).get('message'))
    if args.action:
        print(request('control',action=args.action).get('message'))
    if args.volume is not None:
        print(request('control',action='volume',volume=args.volume).get('message'))
    if args.status or not any((args.source,needs_service)):
        if ready(): print(json.dumps(request('status'),ensure_ascii=False,indent=2))
        else: print('音乐服务未启动；本地音乐文件播放仍可使用。')


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(str(exc) if isinstance(exc,RuntimeError) else '音乐操作失败：'+type(exc).__name__,file=sys.stderr)
        sys.exit(1)
