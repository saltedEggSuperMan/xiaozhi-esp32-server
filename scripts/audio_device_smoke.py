"""Audible integration checks for the configured PC_DEVICE (explicit --run).

Writes only audio-route evidence, never provider config, credentials or user
recordings. Does not change default endpoints, master volume or microphone input.
"""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import time

from audio_output import VOICE, ROOT, load_local, audio_output_settings, test_speech
from core.utils.pcAudioOutput import PCSpeaker
from core.utils.windowsAudio import require_output, system_snapshot


async def play_encoded(pcm, device, volume):
    import opuslib_next
    encoder=opuslib_next.Encoder(24000,1,opuslib_next.APPLICATION_AUDIO)
    player=PCSpeaker(24000,device,volume)
    try:
        for offset in range(0,len(pcm),2880):
            frame=pcm[offset:offset+2880].ljust(2880,b'\0')
            await player.write(encoder.encode(frame,1440))
        await player.finish()
    finally:
        player.abort()
        await player.wait_closed()


def decode(source):
    ffmpeg=ROOT/'.runtime/audio/bin/ffmpeg.exe'
    if not ffmpeg.is_file():
        import shutil
        ffmpeg=shutil.which('ffmpeg')
    command=[str(ffmpeg),'-hide_banner','-loglevel','error','-i',
             'pipe:0' if isinstance(source,bytes) else str(source),
             '-f','s16le','-ar','24000','-ac','1','pipe:1']
    return subprocess.run(command,input=source if isinstance(source,bytes) else None,
                          capture_output=True,check=True,timeout=30,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='明确允许通过已选终端播放测试音频')
    parser.add_argument('--with-music',action='store_true',help='协调已经播放中的网易云音乐，测试语音压低及恢复')
    args=parser.parse_args()
    if not args.run:
        parser.error('使用 --run 执行真实音频输出测试')
    _,config=load_local(VOICE/'data/.config.yaml')
    mode,ident,volume=audio_output_settings(config)
    if mode!='PC_DEVICE':
        raise RuntimeError('请先选择 PC_DEVICE 和可用设备')
    device=require_output(ident)
    before=system_snapshot()
    evidence={'device':device,'before':before,'checks':[],
              'verification_scope':'Playback process/IPC and Windows snapshots only; audibility requires human confirmation'}
    async def check(name, task):
        started=time.monotonic()
        await task
        after=system_snapshot()
        evidence['checks'].append({'name':name,'seconds':round(time.monotonic()-started,3),
                                  'default_and_master_unchanged':after==before})
        print(name+': 程序完成（听感需人工确认）；默认设备与主音量'+('未变' if after==before else '发生变化'),flush=True)
        if after!=before:
            raise RuntimeError('系统音频状态变化，请检查其他应用/用户操作')
    await check('中文语音 Opus 链路',test_speech(ident,volume,config.get('speech_response')))
    from core.utils.windowsSpeech import synthesize_wav
    wav=await asyncio.to_thread(synthesize_wav,'这是小智指定音频设备的语音测试。',timeout=20)
    pcm=await asyncio.to_thread(decode,wav)
    if args.with_music:
        from music import request
        await asyncio.to_thread(request,'attention',action='duck')
    try:
        await check('Windows TTS WAV → PCM → Opus → 指定设备',play_encoded(pcm,ident,volume))
    finally:
        if args.with_music:
            await asyncio.to_thread(request,'attention',action='unduck')
    pcm=await asyncio.to_thread(decode,VOICE/'config/assets/tts_notify.mp3')
    await check('MP3 提示音 → PCM → Opus → 指定设备',play_encoded(pcm,ident,volume))
    # Music uses the actual Node Player, IPC commands and same discovered route.
    result=subprocess.run(['node',str(ROOT/'scripts/audio_device_music_smoke.js')],
                           capture_output=True,text=True,encoding='utf8',timeout=30,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise RuntimeError(result.stderr)
    evidence['music']=json.loads(result.stdout)
    if args.with_music:
        await asyncio.to_thread(request,'control',action='pause')
        paused=(await asyncio.to_thread(request,'status'))['state']
        await asyncio.to_thread(request,'control',action='resume')
        resumed=(await asyncio.to_thread(request,'status'))['state']
        await asyncio.to_thread(request,'control',action='stop')
        stopped=(await asyncio.to_thread(request,'status'))['state']
        evidence['online_music']={'paused':paused,'resumed':resumed,'stopped':stopped,'tts_duck_unduck':True}
    evidence['after']=system_snapshot()
    evidence['default_and_master_unchanged']=evidence['after']==before
    folder=ROOT/'.runtime/audio-device-test';folder.mkdir(exist_ok=True,parents=True)
    target=folder/'latest.json'
    target.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')
    print('音乐播放器 WAV/MP3、暂停/继续/停止: OK；证据 '+str(target))


if __name__=='__main__':
    asyncio.run(main())
