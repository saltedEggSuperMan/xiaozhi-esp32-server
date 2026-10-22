'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const code=fs.readFileSync(path.join(__dirname,'app.js'),'utf8');

function page({token='',hash='',fetch,storageBlocked=false}={}) {
  const elements=new Map(), timers=[];
  const node=()=>({textContent:'',value:'',dataset:{},children:[],
    replaceChildren(){this.children=[];this.value='';},
    append(...children){for(const child of children){this.children.push(child);if(!this.value)this.value=child.value;}}});
  const element=id=>{
    if(!elements.has(id))elements.set(id,node());
    return elements.get(id);
  };
  element('account').textContent='正在读取状态…';
  const context={
    document:{getElementById:element,activeElement:null,createElement:node},
    location:{hash,pathname:'/'},history:{replaceState(){}},URLSearchParams,AbortController,
    sessionStorage:{
      getItem(){if(storageBlocked)throw new Error('blocked');return token;},
      setItem(){if(storageBlocked)throw new Error('blocked');},
    },
    fetch:fetch || (async()=>{throw new Error('unexpected fetch');}),
    setInterval(){return 1;},clearInterval(){},
    setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){},
  };
  vm.runInNewContext(code,context);
  return {element,timers};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));

test('bare URL gives a local authorization error without claiming NetEase expired',async()=>{
  let calls=0;
  const p=page({fetch:async()=>{calls++;return {status:401};}});
  await flush();
  assert.ok(calls>0);
  assert.match(p.element('account').textContent,/本机管理页未授权/);
  assert.match(p.element('error').textContent,/music.cmd --open/);
  assert.match(p.element('error').textContent,/不代表网易云账号已失效/);
});

test('new tab without per-tab token uses the existing same-origin browser session',async()=>{
  const p=page({fetch:async(_url,options)=>{
    assert.equal(options.credentials,'same-origin');assert.equal(options.headers.Authorization,undefined);
    return {status:200,json:async()=>({ok:true,logged_in:true,profile:{nickname:'Test'},source:'netease',state:'stopped',
      mode:'PC_DEVICE',device_id:'endpoint',devices:[{id:'endpoint',name:'Output',available:true}],volume:0.35})};
  }});
  await flush();assert.equal(p.element('account').textContent,'已登录：Test');assert.equal(p.element('error').textContent,'');
});

test('stale local token replaces the loading label with an actionable message',async()=>{
  const p=page({token:'old-local-token',fetch:async()=>({status:401})});
  await flush();
  assert.match(p.element('account').textContent,/本机管理页未授权/);
  assert.match(p.element('error').textContent,/music.cmd --open/);
});

test('valid token shows the existing logged-in account',async()=>{
  const p=page({token:'test-local-token',fetch:async()=>({status:200,json:async()=>({
    ok:true,logged_in:true,profile:{nickname:'Test'},source:'netease',state:'stopped',volume:50,
  })})});
  await flush();
  assert.equal(p.element('account').textContent,'已登录：Test');
  assert.equal(p.element('source').value,'netease');
});

test('unresponsive service times out and leaves loading state',async()=>{
  const p=page({token:'test-local-token',fetch:(_url,options)=>new Promise((resolve,reject)=>{
    options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})));
  })});
  p.timers[0]();
  await flush();
  assert.match(p.element('account').textContent,/状态读取失败/);
  assert.match(p.element('error').textContent,/响应超时/);
});

test('blocked session storage does not break a newly authorized tab',async()=>{
  const p=page({hash:'#token=test-local-token',storageBlocked:true,fetch:async()=>({status:200,json:async()=>({
    ok:true,logged_in:true,source:'netease',state:'stopped',volume:50,
  })})});
  await flush();
  assert.match(p.element('account').textContent,/已登录/);
});

test('audio configuration displays selected endpoint, status and software volume',async()=>{
  const saved={ok:true,mode:'PC_DEVICE',device_id:'endpoint-one',volume:0.35,
    devices:[{id:'endpoint-one',name:'Speaker',available:true,selected:true,hands_free:true}]};
  const p=page({token:'test',fetch:async url=>({status:200,json:async()=>url.endsWith('/audio/status')?saved:
    {ok:true,logged_in:true,source:'netease',state:'stopped',volume:50}})});
  await flush();
  assert.equal(p.element('audioMode').value,'PC_DEVICE');
  assert.equal(p.element('audioDevice').value,'endpoint-one');
  assert.equal(p.element('audioVolume').value,35);
  assert.match(p.element('audioStatus').textContent,/可用/);
  assert.equal(p.element('audioDeviceOptions').hidden,false);
});

test('refresh keeps an edited selection and disconnected saved ID never selects another device',async()=>{
  const saved={ok:true,mode:'PC_DEVICE',device_id:'missing',volume:0.35,
    devices:[{id:'other',name:'Other',available:true}]};
  const p=page({token:'test',fetch:async url=>({status:200,json:async()=>url.endsWith('/audio/status')?saved:
    {ok:true,source:'netease',state:'stopped',volume:50}})});
  await flush();
  assert.equal(p.element('audioDevice').value,'missing');
  assert.match(p.element('audioStatus').textContent,/不.*回退/);
  p.element('audioMode').value='device';p.element('audioMode').onchange();
  await p.element('audioRefresh').onclick();
  assert.equal(p.element('audioMode').value,'device');
});
test('master volume slider saves on release using volume-only API',async()=>{
  let volume=0.35;const calls=[];
  const saved=()=>({ok:true,mode:'PC_DEVICE',device_id:'endpoint',volume,
    devices:[{id:'endpoint',name:'Speaker',available:true}]});
  const p=page({token:'test',fetch:async(url,options)=>{
    calls.push(url);
    if(url.endsWith('/audio/volume')) {
      const data=JSON.parse(options.body);assert.deepEqual(Object.keys(data),['volume']);volume=data.volume;
    }
    return {status:200,json:async()=>url.includes('/audio/')?saved():{ok:true,source:'netease',state:'playing',volume:54}};
  }});
  await flush();p.element('audioVolume').value='50';p.element('audioVolume').oninput();
  await p.element('audioVolume').onchange();
  assert.equal(volume,0.5);assert.ok(calls.includes('/api/audio/volume'));
  assert.equal(calls.includes('/api/audio/save'),false);
});
test('search row play button submits the exact selected ID without a random action',async()=>{
  const calls=[];
  const p=page({token:'test',fetch:async(url,options)=>{
    calls.push({url,data:JSON.parse(options.body)});
    return {status:200,json:async()=>{
      if(url.endsWith('/search'))return {ok:true,songs:[{id:'101',name:'First',artist:'A'},{id:'202',name:'Selected',artist:'B'}]};
      if(url.endsWith('/audio/status'))return {ok:true,mode:'PC_DEVICE',device_id:'endpoint',volume:0.35,devices:[]};
      return {ok:true,source:'netease',state:'playing',volume:40};
    }};
  }});
  await flush();p.element('query').value='Artist';
  p.element('searchForm').onsubmit({preventDefault(){}});await flush();
  const button=p.element('results').children[1].children[1];
  await button.onclick();
  const plays=calls.filter(call=>call.url==='/api/play');
  assert.deepEqual(plays,[{url:'/api/play',data:{kind:'song',id:'202'}}]);
});
