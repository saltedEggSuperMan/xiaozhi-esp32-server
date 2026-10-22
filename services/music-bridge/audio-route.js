'use strict';
const {spawn} = require('node:child_process');
const path = require('node:path');

class AudioRoute {
  constructor(root) {this.root=root;this.cached=null;this.reading=null;this.readAt=0;}
  async request(data) {
    const python=path.join(this.root,'main/xiaozhi-server/.venv/Scripts/python.exe');
    return new Promise((resolve,reject)=>{
      const child=spawn(python,['-X','utf8',path.join(this.root,'scripts/audio_output.py'),'--request-json'],
        {windowsHide:true,stdio:['pipe','pipe','ignore']});
      let output='',settled=false;
      const finish=(err,value)=>{if(settled)return;settled=true;clearTimeout(timer);err?reject(err):resolve(value);};
      const timer=setTimeout(()=>{child.kill();finish(new Error('本机音频配置响应超时'));},15000);
      child.once('error',()=>finish(new Error('无法启动本机音频配置，请使用 Windows 项目 Python 环境')));
      child.stdout.setEncoding('utf8');
      child.stdout.on('data',part=>{output+=part;if(output.length>65536){child.kill();finish(new Error('音频设备列表过大'));}});
      child.once('close',code=>{
        try {const result=JSON.parse(output);if(code || !result.ok)throw new Error(result.message || '音频设备操作失败');finish(null,result);}
        catch(err){finish(new Error(/^[\u3400-\u9fff]/.test(err.message)?err.message:'无法读取本机音频配置'));}
      });
      child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify(data));
    });
  }
  async read(force=false) {
    if(!force && this.cached && Date.now()-this.readAt<750)return this.cached;
    if(this.reading)return this.reading;
    this.reading=this.request({action:'describe'}).then(value=>{this.cached=value;this.readAt=Date.now();return value;}).finally(()=>{this.reading=null;});
    return this.reading;
  }
  async configure(data) {
    const value=await this.request(data);
    // A preview test must never become the effective persisted route.
    if(data.action==='save' || data.action==='volume'){this.cached=value;this.readAt=Date.now();}
    return value;
  }
}
function outputArgs(route) {
  if(route.mode!=='PC_DEVICE')return [];
  if(!route.available || !/^wasapi\/\{0\.0\.0\.00000000\}\.\{[0-9a-f-]{36}\}$/i.test(route.mpv_device || ''))
    throw new Error('选定音频设备不可用，已停止输出，不回退默认设备');
  return ['--ao=wasapi','--audio-device='+route.mpv_device,'--audio-exclusive=no',
    '--audio-fallback-to-null=no','--audio-client-name=xiaozhi-music','--audio-set-media-role=no'];
}
function signature(route) {return JSON.stringify([route.mode,route.mode==='PC_DEVICE'?route.device_id:null]);}
function musicVolume(route,percent) {
  if(route.mode!=='PC_DEVICE')return percent;
  // mpv audio_get_gain uses (volume/100)^3, while Python scales PCM linearly.
  // Invert that curve so master * music * duck is the actual linear gain,
  // rather than cubing all three controls and making music nearly inaudible.
  // https://github.com/mpv-player/mpv/blob/master/player/audio.c
  const gain=Math.max(0,Math.min(1,route.volume*percent/100));
  return 100*Math.cbrt(gain);
}
module.exports={AudioRoute,outputArgs,signature,musicVolume};
