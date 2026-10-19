'use strict';
const $=id=>document.getElementById(id);
const fragment=new URLSearchParams(location.hash.slice(1));
let token=fragment.get('token') || '';
try {
  if(token)sessionStorage.setItem('musicToken',token);
  else token=sessionStorage.getItem('musicToken') || '';
}catch(_) { /* A private browser may block storage; the current tab still works. */ }
if(fragment.has('token'))history.replaceState(null,'',location.pathname);
let qrTimer=null, refreshing=false;
const openPageMessage='本机音乐管理页未授权，请运行 music.cmd --open 重新打开；这不代表网易云账号已失效。';
async function request(action,data={}) {
  const localAuthError=()=>Object.assign(new Error(openPageMessage),{code:'LOCAL_AUTH'});
  if(!token)throw localAuthError();
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),action==='status'?5000:30000);
  try {
    const res=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify(data),signal:controller.signal});
    if(res.status===401)throw localAuthError();
    const result=await res.json();if(!result.ok)throw new Error(result.message);return result;
  }catch(e){
    if(e.name==='AbortError')throw new Error('音乐服务响应超时，请检查服务后重试。');
    throw e;
  }finally{clearTimeout(timeout);}
}
async function run(job) {
  $('error').textContent='';
  try {const result=await job();if(result?.message)$('notice').textContent=result.message;await refresh();return result;}
  catch(e){$('error').textContent=e.message;}
}
async function refresh(){
  if(refreshing)return;refreshing=true;
  try {
    const s=await request('status');
    if(document.activeElement!==$('source'))$('source').value=s.source;
    $('account').textContent=s.logged_in?'已登录'+(s.profile?'：'+s.profile.nickname:'（可点击检查登录状态）'):'尚未登录';
    $('now').textContent=s.current?s.current.name+' — '+s.current.artist:'尚未播放';
    $('state').textContent=({loading:'正在加载',playing:'播放中',paused:'已暂停',stopped:'已停止',error:'播放失败'})[s.state]+(s.count?' · '+s.position+'/'+s.count:'')+(s.message?' · '+s.message:'');
    if(document.activeElement!==$('volume')){$('volume').value=s.volume;$('volumeValue').textContent=s.volume;}
  }catch(e){
    $('account').textContent=e.code==='LOCAL_AUTH'?'本机管理页未授权（网易云登录状态未检查）':'状态读取失败，请检查音乐服务后重试';
    $('error').textContent=e.message;
  }finally{refreshing=false;}
}
$('saveSource').onclick=()=>run(()=>request('settings',{source:$('source').value}));
$('login').onclick=()=>run(async()=>{
  clearInterval(qrTimer);const result=await request('login/start');$('qr').src=result.image;$('qrBox').hidden=false;$('qrState').textContent='等待扫码';
  let polling=false;
  qrTimer=setInterval(async()=>{if(polling)return;polling=true;try{
    const s=await request('login/check');$('qrState').textContent=s.message;
    if([800,803].includes(s.code)){clearInterval(qrTimer);if(s.code===803){$('qrBox').hidden=true;await refresh();}}
  }catch(e){clearInterval(qrTimer);$('error').textContent=e.message;}finally{polling=false;}},2500);
});
$('accountCheck').onclick=()=>run(()=>request('account'));
$('logout').onclick=()=>run(async()=>{clearInterval(qrTimer);$('qrBox').hidden=true;return request('logout');});
function rows(items,buttonName,play){
  $('results').replaceChildren();for(const item of items){
    const li=document.createElement('li'),text=document.createElement('span'),button=document.createElement('button');
    text.textContent=item.name+(item.artist?' — '+item.artist:'');button.textContent=buttonName;
    button.onclick=()=>run(()=>play(item));li.append(text,button);$('results').append(li);
  }if(!items.length)$('notice').textContent='没有找到结果';
}
$('searchForm').onsubmit=e=>{e.preventDefault();run(async()=>{const r=await request('search',{query:$('query').value});rows(r.songs,'播放',song=>request('play',{kind:'song',id:song.id}));});};
$('playlists').onclick=()=>run(async()=>{const r=await request('playlists');rows(r.playlists,'播放歌单',list=>request('play',{kind:'playlist',query:list.id}));});
$('liked').onclick=()=>run(()=>request('play',{kind:'liked',query:'random'}));
$('cloud').onclick=()=>run(()=>request('play',{kind:'cloud',query:'random'}));
$('controls').onclick=e=>{const action=e.target.dataset.action;if(action)run(()=>request('control',{action}));};
$('volume').oninput=()=>{$('volumeValue').textContent=$('volume').value;};
$('volume').onchange=()=>run(()=>request('control',{action:'volume',volume:Number($('volume').value)}));
refresh();setInterval(refresh,3000);
