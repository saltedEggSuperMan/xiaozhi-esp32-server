'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {Music,validSongURL}=require('./music');
function fixture(overrides={}) {
  const calls=[], settings={source:'netease',volume:40};
  const player={socket:true,start:async()=>{},load:async url=>calls.push(['load',url]),
    command:async command=>calls.push(command),stop:async()=>calls.push(['stop'])};
  const api=async(name,data)=>{
    calls.push([name,data]);
    if(overrides[name])return overrides[name](data);
    if(name==='cloudsearch')return {result:{songs:[{id:1,name:'Test',ar:[{name:'Artist'}]}]}};
    if(name==='song_url_v1')return {data:[{url:'https://m801.music.126.net/song.mp3',freeTrialInfo:null}]};
    if(name==='login_status')return {data:{profile:{userId:9}}};
    if(name==='user_playlist')return {playlist:[{id:5,name:'Liked',specialType:5}]};
    if(name==='playlist_detail')return {playlist:{trackIds:[{id:1},{id:2}]}};
    if(name==='song_detail')return {songs:data.ids.split(',').map(id=>({id,name:'Song '+id}))};
    if(name==='likelist')return {ids:[1,2]};
    throw new Error(name);
  };
  return {music:new Music(player,api,()=>settings,()=>true),settings,calls};
}
test('only actual NetEase CDN http(s) URLs are accepted',()=>{
  assert.ok(validSongURL('https://m801.music.126.net/a.mp3?token=opaque'));
  for(const value of ['file:///C:/secret','http://127.0.0.1/a','https://music.126.net.evil.example/a','ftp://music.126.net/a','https://user:pass@music.126.net/a','https://music.126.net:1234/a']) assert.equal(validSongURL(value),false,value);
});
test('local mode refuses cloud playback and leaves original path available',async()=>{
  const {music,settings,calls}=fixture();settings.source='local';
  await assert.rejects(music.play({query:'Test'}),/本地音乐/);assert.equal(calls.length,0);
});
test('account required before search',async()=>{
  const {music,calls}=fixture();music.loggedIn=()=>false;
  await assert.rejects(music.search('Test'),/扫码/);assert.equal(calls.length,0);
});
test('play, pause, resume, volume and stop operate independent stereo player',async()=>{
  const {music,calls,settings}=fixture();await music.play({query:'Test'});
  assert.equal(music.status().current.name,'Test');assert.equal(music.state,'playing');
  await music.control({action:'pause'});assert.equal(music.state,'paused');
  await music.control({action:'resume'});assert.equal(music.state,'playing');
  music.ducked=true;await music.control({action:'volume',volume:20});
  assert.equal(settings.volume,20);assert.ok(calls.some(c=>c[0]==='set_property' && c[1]==='volume' && c[2]===3));
  await assert.rejects(music.control({action:'volume',volume:Infinity}),/范围/);
  await music.stop();assert.equal(music.queue.length,0);assert.equal(music.state,'stopped');
});
test('playlist resolves URLs at play time and advances on demand',async()=>{
  const {music,calls}=fixture();await music.play({kind:'playlist',query:'Liked'});
  assert.equal(music.queue.length,2);assert.equal(music.index,0);
  await music.advance(1);assert.equal(music.index,1);
  assert.equal(calls.filter(c=>c[0]==='song_url_v1').length,2);
  assert.ok(calls.filter(c=>c[0]==='song_url_v1').every(c=>c[1].crypto==='weapi' && c[1].unblock===false));
  await music.advance(1);assert.equal(music.state,'stopped');
});
test('trial and unsafe URLs are skipped, never played as full tracks',async()=>{
  const {music,calls}=fixture({song_url_v1:async data=>({data:[{url:data.id==='1'?'https://music.126.net/trial.mp3':'http://127.0.0.1/private',freeTrialInfo:data.id==='1'?{start:0,end:30}:null}]})});
  await assert.rejects(music.play({kind:'playlist',query:'Liked'}),/完整播放权限/);
  assert.equal(calls.filter(c=>c[0]==='load').length,0);
});
test('load failures propagate and do not claim success',async()=>{
  const {music}=fixture();music.player.load=async()=>{throw new Error('音源无法播放');};
  await assert.rejects(music.play({query:'Test'}),/音源无法播放/);
  assert.notEqual(music.state,'playing');
});
test('silent attention with no queue does not start mpv',async()=>{
  const {music,calls}=fixture();await music.control({action:'pause',quiet:true});assert.equal(calls.length,0);
});
test('liked playback uses account likes rather than first arbitrary playlist',async()=>{
  const {music,calls}=fixture();await music.play({kind:'liked',query:'random'});
  assert.equal(music.queue.length,2);
  assert.ok(calls.some(c=>c[0]==='likelist' && c[1].uid===9));
  assert.equal(calls.some(c=>c[0]==='user_playlist'),false);
});
