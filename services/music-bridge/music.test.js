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
test('search result ID without query plays only the selected song, never random likes',async()=>{
  const {music,calls}=fixture();
  await music.play({kind:'song',id:'202'});
  assert.equal(music.status().current.id,'202');assert.equal(music.queue.length,1);
  assert.ok(calls.some(c=>c[0]==='song_detail' && c[1].ids==='202'));
  assert.ok(calls.some(c=>c[0]==='song_url_v1' && c[1].id==='202'));
  assert.equal(calls.some(c=>['likelist','login_status','cloudsearch'].includes(c[0])),false);
});
test('explicit selected ID wins even if query is random, and repeated selections replace the queue',async()=>{
  const {music,calls}=fixture();await music.play({kind:'liked',query:'random'});
  calls.length=0;
  await music.play({kind:'song',query:'random',id:202});
  await music.play({kind:'song',id:'303'});
  assert.deepEqual(music.queue.map(song=>song.id),['303']);assert.equal(music.index,0);
  assert.deepEqual(calls.filter(c=>c[0]==='song_url_v1').map(c=>c[1].id),['202','303']);
  assert.equal(calls.some(c=>c[0]==='likelist'),false);
});
test('unavailable or trial selected song reports failure without playing another song',async()=>{
  const {music,calls}=fixture({song_url_v1:async()=>({data:[{url:'https://music.126.net/trial.mp3',freeTrialInfo:{start:0,end:30}}]})});
  await assert.rejects(music.play({kind:'song',id:'202'}),/完整播放权限/);
  assert.equal(calls.some(c=>c[0]==='likelist' || c[0]==='load'),false);
  assert.deepEqual(calls.filter(c=>c[0]==='song_url_v1').map(c=>c[1].id),['202']);
});
test('invalid selected IDs are rejected before lookup or random playback',async()=>{
  for(const id of [null,undefined,true,[],[202],{},'not-an-id']){
    const {music,calls}=fixture();await assert.rejects(music.play({kind:'song',id}),/编号无效/);
    assert.equal(calls.length,0);
  }
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
test('explicit resume after endpoint loss reloads retained track and queue',async()=>{
  const {music,calls}=fixture();await music.play({query:'Test'});
  music.player.socket=null;music.state='error';
  const before=calls.filter(c=>c[0]==='load').length;
  await music.control({action:'resume'});
  assert.equal(calls.filter(c=>c[0]==='load').length,before+1);
  assert.equal(music.state,'playing');assert.equal(music.index,0);assert.equal(music.queue.length,1);
});
test('resume with endpoint still missing reports error and keeps the queue',async()=>{
  const {music}=fixture();await music.play({query:'Test'});music.player.socket=null;
  music.player.start=async()=>{throw new Error('选定音频设备不可用');};
  await assert.rejects(music.control({action:'resume'}),/不可用/);
  assert.equal(music.state,'error');assert.equal(music.queue.length,1);
});
test('web play, wake pause, volume change and resume keep the same track and queue',async()=>{
  const {music,calls}=fixture();await music.play({query:'Test'});
  const queue=music.queue,current=music.status().current;
  music.positionSeconds=37.5;
  await music.control({action:'pause',quiet:true});
  music.ducked=true;await music.setVolume();
  await music.control({action:'volume',volume:20});
  music.ducked=false;await music.setVolume();
  await music.control({action:'resume'});
  assert.equal(music.queue,queue);assert.deepEqual(music.status().current,current);
  assert.equal(music.positionSeconds,37.5);assert.equal(music.state,'playing');
  assert.equal(calls.filter(c=>c[0]==='load').length,1,'ordinary resume must not reload the song');
});
test('service restart restores a paused queue and seeks on explicit resume only',async()=>{
  const original=fixture();await original.music.play({kind:'playlist',query:'Liked'});
  await original.music.advance(1);original.music.positionSeconds=46.25;
  const snapshot=JSON.parse(JSON.stringify(original.music.snapshot()));
  const restored=fixture();restored.music.player.socket=null;
  assert.equal(restored.music.restore(snapshot),true);
  assert.equal(restored.calls.length,0,'restore must never autoplay');
  assert.equal(restored.music.state,'paused');assert.equal(restored.music.index,1);
  await restored.music.control({action:'pause',quiet:true});
  assert.equal(restored.calls.length,0,'waking a restored queue must not create an empty player');
  await restored.music.control({action:'volume',volume:25});
  await restored.music.control({action:'resume'});
  assert.equal(restored.music.queue.length,2);assert.equal(restored.music.index,1);
  assert.equal(restored.music.state,'playing');
  const seek=restored.calls.findIndex(c=>c[0]==='seek');
  const load=restored.calls.findIndex(c=>c[0]==='load');
  assert.deepEqual(restored.calls[seek],['seek',46.25,'absolute+exact']);assert.ok(seek>load);
  assert.equal('url' in snapshot.queue[0],false);
});
test('explicit stop clears checkpoint and invalid or foreign-source checkpoints are rejected',async()=>{
  const {music}=fixture();await music.play({query:'Test'});
  const valid=music.snapshot();
  for(const invalid of [{...valid,index:99},{...valid,source:'local'},
    {...valid,seconds:-1},{...valid,queue:[{id:'file://unsafe',name:'X',artist:''}]}])
    assert.equal(fixture().music.restore(invalid),false);
  await music.stop();assert.equal(music.snapshot(),null);
});
