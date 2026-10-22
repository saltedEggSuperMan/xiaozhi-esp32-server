'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {AudioRoute,outputArgs,signature,musicVolume}=require('./audio-route');
const {Player}=require('./player');
const id='{0.0.0.00000000}.{12345678-1234-1234-1234-123456789abc}';
const selected={mode:'PC_DEVICE',available:true,device_id:id,mpv_device:'wasapi/'+id,volume:0.4};
test('explicit output pins exact WASAPI shared endpoint without automatic selection',()=>{
  const args=outputArgs(selected);
  assert.ok(args.includes('--audio-device=wasapi/'+id));assert.ok(args.includes('--ao=wasapi'));
  assert.ok(args.includes('--audio-exclusive=no'));assert.ok(args.includes('--audio-fallback-to-null=no'));
});
test('missing and invalid endpoints refuse playback rather than default fallback',()=>{
  for(const route of [{...selected,available:false},{...selected,mpv_device:'auto'},
    {...selected,mpv_device:'wasapi/speaker'}])assert.throws(()=>outputArgs(route));
});
test('legacy hardware and PC music output keep existing behavior',()=>{
  for(const mode of ['device','server_pc'])assert.deepEqual(outputArgs({mode}),[]);
});
test('global software volume combines with music volume without a system command',async()=>{
  const player=new Player('unused',async()=>selected);player.start=async()=>{};
  let sent;player._command=async command=>{sent=command;};
  await player.command(['set_property','volume',50]);
  assert.deepEqual(sent.slice(0,2),['set_property','volume']);
  assert.ok(Math.abs((sent[2]/100)**3-0.2)<1e-9,'mpv output gain must equal 40% master * 50% music');
});
test('software gain preserves silence, full scale and legacy volume behavior',()=>{
  assert.equal(musicVolume(selected,0),0);
  assert.equal(musicVolume({...selected,volume:1},100),100);
  assert.equal(musicVolume({mode:'server_pc',volume:0.4},50),50);
});
test('music duck is a 15 percent linear gain, rather than cubed attenuation',()=>{
  const normal=(musicVolume(selected,50)/100)**3;
  const ducked=(musicVolume(selected,7.5)/100)**3;
  assert.ok(Math.abs(ducked/normal-0.15)<1e-9);
});
test('unavailable endpoint closes existing player and never sends a new command',async()=>{
  const player=new Player('unused',async()=>({...selected,available:false}));
  let closed=false,started=false;player.close=()=>{closed=true;};player.start=async()=>{started=true;};
  await assert.rejects(player.command(['set_property','pause',false]));assert.ok(closed);assert.equal(started,false);
});
test('ID change stops existing route; volume change preserves route identity',()=>{
  assert.equal(signature(selected),signature({...selected,volume:0.9}));
  assert.notEqual(signature(selected),signature({...selected,device_id:'other'}));
});
test('testing a preview device never changes the persisted route cache',async()=>{
  const routes=new AudioRoute('unused');routes.cached=selected;routes.request=async()=>({...selected,device_id:'preview'});
  await routes.configure({action:'test'});assert.equal(routes.cached.device_id,id);
  await routes.configure({action:'save'});assert.equal(routes.cached.device_id,'preview');
});
test('stop works even if selected device is missing, without starting another player',async()=>{
  const player=new Player('unused',async()=>{throw new Error('missing');});
  player.socket={destroyed:false};let stopped;
  player._command=async command=>{stopped=command;};
  await player.stop();assert.deepEqual(stopped,['stop']);
});
