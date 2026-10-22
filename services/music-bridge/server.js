'use strict';
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {randomBytes} = require('node:crypto');
const {createRequire} = require('node:module');
const {Player} = require('./player');
const {AudioRoute,signature} = require('./audio-route');
const {browserAuth}=require('./browser-auth');
const {testWithMusic}=require('./audio-test');
const {protect} = require('./vault');
const ROOT = path.resolve(__dirname,'../..');
const DIR = path.join(ROOT,'.runtime/music');
const PORT = 8007;
fs.mkdirSync(DIR,{recursive:true});
// Disable optional alternate-source resolution before loading third-party code.
process.env.ENABLE_GENERAL_UNBLOCK = 'false';
process.env.ENABLE_PROXY = 'false';
process.env.ENABLE_RANDOM_CN_IP = 'false';
// Upstream prints entire error response objects in some failure paths. Suppress
// those diagnostics in this isolated process so account cookies cannot enter logs.
for (const name of ['log','info','warn','error','debug']) console[name]=()=>{};
const ncm = createRequire(path.join(ROOT,'.runtime/music-api/package.json'))('@neteasecloudmusicapienhanced/api');
const {Music, validSongURL} = require('./music');
const audioRoute=new AudioRoute(ROOT);
const player = new Player(path.join(ROOT,'.runtime/mpv/mpv.exe'),()=>audioRoute.read());
function atomic(file, text) {
  const temp = file+'.tmp'; fs.writeFileSync(temp,text,{mode:0o600}); fs.renameSync(temp,file);
}
const settingsPath = path.join(DIR,'settings.json');
let settings = fs.existsSync(settingsPath) ? JSON.parse(fs.readFileSync(settingsPath,'utf8')) : {source:'local',volume:35};
if (!['local','netease'].includes(settings.source)) throw new Error('Invalid music source');
const tokenPath = path.join(DIR,'bridge.token');
const token = fs.existsSync(tokenPath) ? fs.readFileSync(tokenPath,'ascii').trim() : randomBytes(32).toString('hex');
if (!/^[a-f0-9]{64}$/.test(token)) throw new Error('Invalid local bridge token');
atomic(tokenPath,token);
const localAuth=browserAuth(token);
const cookiePath = path.join(DIR,'account.dpapi');
let cookie = '', profile = null, pendingQR = null;
try { if (fs.existsSync(cookiePath)) cookie = protect(fs.readFileSync(cookiePath,'ascii'),true); }
catch (_) { console.log('Saved music authorization unavailable; scan QR again.'); }
async function api(name, data={}) {
  try {
    const result = await ncm[name]({...data,cookie,timeout:10000,unblock:false});
    const code=result.body?.code ?? result.body?.data?.code;
    if (code !== 200 && !name.startsWith('login_qr_')) {
      if ([301,401,403].includes(code)) throw new Error('AUTH');
      throw new Error('API');
    }
    return result.body;
  } catch (err) {
    if (err.message === 'AUTH') throw new Error('网易云登录已失效，请重新扫码');
    throw new Error('网易云接口暂时不可用，请稍后重试');
  }
}
const music = new Music(player,api,() => settings,() => Boolean(cookie));
const queuePath=path.join(DIR,'queue.json');
let lastQueue='';
function saveQueue(){
  const saved=music.snapshot(),text=JSON.stringify(saved);
  if(text===lastQueue)return;
  if(saved)atomic(queuePath,text);
  else if(fs.existsSync(queuePath))fs.unlinkSync(queuePath);
  lastQueue=text;
}
try {if(fs.existsSync(queuePath))music.restore(JSON.parse(fs.readFileSync(queuePath,'utf8')));}
catch(_){/* A corrupt/stale snapshot never starts arbitrary media. */}
// No account or track data in this small shared snapshot. Voice capture uses it
// to avoid recognizing loudspeaker music as a new user utterance.
music.onState=state=>{
  atomic(path.join(DIR,'playback.json'),JSON.stringify({playing:state==='playing'}));saveQueue();
};
music.onState(music.state);
setInterval(()=>{try{saveQueue();}catch(_){music.message='播放队列保存失败，服务重启后可能无法恢复';}},5000).unref();
let serial = Promise.resolve();
function exclusive(task) { const job = serial.then(task); serial = job.catch(()=>{}); return job; }
player.on('end-file', event => {
  if (event.reason === 'eof') exclusive(() => music.advance(1)).catch(()=>{ music.message='下一首播放失败，请重新点歌'; });
  if (event.reason === 'error') { music.state='error'; music.message='音源无法播放，请切换歌曲'; }
});
player.on('exit', () => { music.needsReload=true;music.state='stopped'; });
function outputLost(message) {
  player.close();music.needsReload=true;music.state='error';music.message=message || '选定音频设备已断开，已停止输出；重连后请继续播放';
}
player.on('property-change',event=>{
  if(event.name==='time-pos' && Number.isFinite(event.data) && event.data>=0){music.positionSeconds=event.data;return;}
  if(event.name!=='audio-device-list' || player.outputRoute?.mode!=='PC_DEVICE' || !Array.isArray(event.data))return;
  const selected=player.outputRoute.device_id.replace('{0.0.0.00000000}.','');
  if(!event.data.some(d=>d.name==='wasapi/'+selected || d.name===player.outputRoute.mpv_device))outputLost();
});
let checkingOutput=false;
const outputWatch=setInterval(async()=>{
  if(checkingOutput || !player.socket)return;checkingOutput=true;
  try {
    const route=await audioRoute.read(true);
    if(player.socket && (signature(route)!==signature(player.outputRoute) || (route.mode==='PC_DEVICE' && !route.available)))outputLost();
    else if(player.socket && route.mode==='PC_DEVICE' && route.volume!==player.outputRoute.volume) {
      player.outputRoute=route;await music.setVolume();
    }
  } catch (_) {if(player.socket)outputLost('本机音频配置不可用，已停止输出；请刷新设备配置');}
  finally {checkingOutput=false;}
},1000);
outputWatch.unref();
async function route(name, data) {
  if(name==='audio/status')return await audioRoute.read(true);
  if(name==='audio/playback')return await player.diagnostics();
  if(name==='audio/volume') {
    const value=await audioRoute.configure({action:'volume',volume:data.volume});
    if(player.socket && value.mode==='PC_DEVICE')await music.setVolume();
    return {...value,message:'小智音量已更新并保存，当前播放继续'};
  }
  if(name==='audio/save' || name==='audio/test') {
    let value;
    if(name==='audio/save') {
      const current=await audioRoute.read();
      const target={mode:data.mode,device_id:data.device_id || current.device_id};
      if(signature(target)!==signature(current)){
        await music.control({action:'pause',quiet:true});player.close();music.needsReload=true;
      }
      value=await audioRoute.configure({...data,action:'save'});
      if(player.socket && value.mode==='PC_DEVICE')await music.setVolume();
    } else {
      value=await testWithMusic(music,player,()=>audioRoute.configure({...data,action:'test'}));
    }
    return {...value,message:name==='audio/save'?'音频输出已保存并生效':'中文测试语音已发送，请确认能否听懂句子；只有嘟声或电流声不算通过'};
  }
  if (name === 'status') return {...music.status(),source:settings.source,logged_in:Boolean(cookie),profile};
  if (name === 'settings') {
    if (!['local','netease'].includes(data.source)) throw new Error('音乐来源只能是 local 或 netease');
    if(data.source!==settings.source)await music.stop();
    settings = {...settings,source:data.source}; atomic(settingsPath,JSON.stringify(settings,null,2));
    return {source:settings.source,message:settings.source==='local'?'已切换本地音乐':'已切换网易云音乐'};
  }
  if (name === 'login/start') {
    const key = (await api('login_qr_key',{timestamp:Date.now()})).data?.unikey;
    if (!key) throw new Error('二维码生成失败，请重试');
    const qr = (await api('login_qr_create',{key,qrimg:true,timestamp:Date.now()})).data;
    if (!qr?.qrimg?.startsWith('data:image/')) throw new Error('二维码生成失败，请重试');
    pendingQR = {key,expires:Date.now()+180000}; return {image:qr.qrimg};
  }
  if (name === 'login/check') {
    if (!pendingQR || pendingQR.expires < Date.now()) return {code:800,message:'二维码已过期，请重新生成'};
    const result = await api('login_qr_check',{key:pendingQR.key,timestamp:Date.now()});
    const code = result.code;
    if (code === 803 && result.cookie) {
      // Encrypt before replacing the existing account; failed protection leaves it intact.
      atomic(cookiePath,protect(result.cookie)); cookie=result.cookie; pendingQR=null;
      profile=null; try {
        const info = await api('login_status',{timestamp:Date.now()});
        const p = info.data?.profile;
        if (p) profile={userId:p.userId,nickname:p.nickname};
      } catch (_) { /* Playback validates account independently. */ }
    }
    return {code,message:({800:'二维码已过期',801:'等待扫码',802:'已扫码，请在手机确认',803:'登录成功'})[code] || '等待登录确认'};
  }
  if (name === 'logout') {
    await music.stop(); cookie=''; profile=null; pendingQR=null;
    if (fs.existsSync(cookiePath)) fs.unlinkSync(cookiePath);
    return {message:'已清除本项目的网易云登录'};
  }
  if (name === 'account') {
    if (!cookie) throw new Error('请先扫码登录网易云');
    const info=await api('login_status',{timestamp:Date.now()});
    const p=info.data?.profile;
    if (!p?.userId) throw new Error('网易云登录已失效，请重新扫码');
    profile={userId:p.userId,nickname:p.nickname}; return {profile};
  }
  if (name === 'search') return {songs:await music.search(String(data.query || ''))};
  if (name === 'playlists') return {playlists:await music.playlists()};
  if (name === 'play') return await music.play(data);
  if (name === 'control') {
    const result=await music.control(data);
    if(data.action==='volume') atomic(settingsPath,JSON.stringify(settings,null,2));
    return result;
  }
  if (name === 'attention') {
    if (data.action==='pause') {
      const result=await music.control({action:'pause',quiet:true});
      music.ducked=false;
      if(player.socket) await music.setVolume();
      return result;
    }
    if (data.action==='duck') music.ducked=true;
    else if (data.action==='unduck') music.ducked=false;
    else throw new Error('无效的播放协调操作');
    if (player.socket) await music.setVolume();
    return {};
  }
  throw new Error('未知音乐操作');
}
function send(res,status,body) {
  res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store',
    'X-Content-Type-Options':'nosniff'}); res.end(JSON.stringify(body));
}
const server=http.createServer(async(req,res) => {
  const host='127.0.0.1:'+PORT;
  if (req.headers.host!==host) return send(res,403,{ok:false,message:'只允许本机地址'});
  if (req.url==='/health' && req.method==='GET') return send(res,200,{status:'ok',service:'xiaozhi-music-bridge'});
  if (req.url==='/' && req.method==='GET') {
    res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store',
      'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"});
    return res.end(fs.readFileSync(path.join(__dirname,'index.html')));
  }
  if (req.url==='/app.js' && req.method==='GET') {
    res.writeHead(200,{'Content-Type':'application/javascript','Cache-Control':'no-store'});
    return res.end(fs.readFileSync(path.join(__dirname,'app.js')));
  }
  if (req.method!=='POST' || !req.url.startsWith('/api/') || !localAuth.authorized(req))
    return send(res,401,{ok:false,message:'请使用 music.cmd --open 打开本机音乐管理页'});
  if (req.headers.origin && req.headers.origin!=='http://'+host)
    return send(res,403,{ok:false,message:'不允许跨站访问'});
  if (!(req.headers['content-type'] || '').startsWith('application/json'))
    return send(res,415,{ok:false,message:'需要 JSON 请求'});
  // Establish a browser-session cookie from the authorized launcher. Ordinary
  // refreshes/new tabs can then authenticate without per-tab sessionStorage.
  res.setHeader('Set-Cookie',localAuth.cookie);
  try {
    let raw='';
    req.setEncoding('utf8');
    for await (const part of req) {raw+=part; if (Buffer.byteLength(raw)>16384) throw new Error('请求过大');}
    const data=JSON.parse(raw || '{}');
    if (!data || Array.isArray(data) || typeof data!=='object') throw new Error('无效请求');
    const name=req.url.slice(5);
    const run=()=>route(name,data);
    const result=['status','attention'].includes(name)?await run():await exclusive(run);
    send(res,200,{ok:true,...result});
  } catch (err) {
    // Only our own Chinese messages are emitted. Upstream errors/URLs/cookies are omitted.
    const message=/^[\u3400-\u9fff]/.test(err.message)?err.message:'音乐操作失败，请检查服务或重新登录';
    send(res,400,{ok:false,message});
  }
});
server.requestTimeout=15000;
server.listen(PORT,'127.0.0.1',()=>process.stdout.write('Music bridge ready on loopback port '+PORT+'\n'));
function shutdown(){try{saveQueue();}catch(_){}player.close(); server.close(); setTimeout(()=>process.exit(0),500).unref();}
process.on('SIGINT',shutdown); process.on('SIGTERM',shutdown);
module.exports={validSongURL};
