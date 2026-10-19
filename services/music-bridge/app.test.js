'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const code=fs.readFileSync(path.join(__dirname,'app.js'),'utf8');

function page({token='',hash='',fetch,storageBlocked=false}={}) {
  const elements=new Map(), timers=[];
  const element=id=>{
    if(!elements.has(id))elements.set(id,{textContent:'',value:'',dataset:{}});
    return elements.get(id);
  };
  element('account').textContent='正在读取状态…';
  const context={
    document:{getElementById:element,activeElement:null},
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
  const p=page({fetch:async()=>{calls++;}});
  await flush();
  assert.equal(calls,0);
  assert.match(p.element('account').textContent,/本机管理页未授权/);
  assert.match(p.element('error').textContent,/music.cmd --open/);
  assert.match(p.element('error').textContent,/不代表网易云账号已失效/);
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
