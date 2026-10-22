'use strict';
// Audible local fixtures exercise the production music player without exposing
// NetEase account data or depending on a particular song/online subscription.
const path=require('node:path');
const fs=require('node:fs');
const {Player}=require('../services/music-bridge/player');
const {AudioRoute}=require('../services/music-bridge/audio-route');
const root=path.resolve(__dirname,'..'),routes=new AudioRoute(root);
const player=new Player(path.join(root,'.runtime/mpv/mpv.exe'),()=>routes.read());
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
  const route=await routes.read();
  if(route.mode!=='PC_DEVICE')throw new Error('必须使用 PC_DEVICE');
  const folder=path.join(root,'.runtime/audio-device-test');fs.mkdirSync(folder,{recursive:true});
  const wave=Buffer.alloc(44+24000*2*2),bytes=wave.length-44;
  wave.write('RIFF',0);wave.writeUInt32LE(wave.length-8,4);wave.write('WAVEfmt ',8);
  wave.writeUInt32LE(16,16);wave.writeUInt16LE(1,20);wave.writeUInt16LE(1,22);
  wave.writeUInt32LE(24000,24);wave.writeUInt32LE(48000,28);wave.writeUInt16LE(2,32);wave.writeUInt16LE(16,34);
  wave.write('data',36);wave.writeUInt32LE(bytes,40);
  for(let i=0;i<bytes/2;i++)wave.writeInt16LE(Math.round(8000*Math.sin(2*Math.PI*330*i/24000)),44+i*2);
  const tone=path.join(folder,'tone.wav');fs.writeFileSync(tone,wave);
  const records=[];
  for(const fixture of ['.runtime/audio-device-test/tone.wav','main/xiaozhi-server/config/assets/tts_notify.mp3']){
    await player.command(['set_property','volume',40]);
    await player.load(path.join(root,fixture));
    await wait(200);
    const device=await player.command(['get_property','audio-device']);
    const ao=await player.command(['get_property','current-ao']);
    const volume=await player.command(['get_property','volume']);
    if(device!==route.mpv_device || ao!=='wasapi')throw new Error('实际播放器未绑定选定 WASAPI 终端');
    records.push({fixture,device,ao,volume});
    await player.command(['set_property','pause',true]);
    await player.command(['set_property','pause',false]);
    await wait(800);await player.stop();
  }
  player.close();
  await player.command(['set_property','volume',40]);await player.load(tone);await wait(200);
  const rebound=await player.command(['get_property','audio-device']);
  if(rebound!==route.mpv_device)throw new Error('重新打开后设备绑定不符');
  await player.stop();
  process.stdout.write(JSON.stringify({records,pause_resume_stop:true,reopen_same_endpoint:true}));
})().catch(err=>{process.stderr.write(err.message);process.exitCode=1;}).finally(()=>player.close());
