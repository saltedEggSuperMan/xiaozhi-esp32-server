'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {testWithMusic}=require('./audio-test');
test('speech test ducks music and restores it without stopping or clearing queue',async()=>{
  const queue=[{id:'test'}],gains=[];
  const music={queue,ducked:false,setVolume:async()=>gains.push(music.ducked)};
  const value=await testWithMusic(music,{socket:true},async()=>{assert.equal(music.ducked,true);return 'sample';});
  assert.equal(value,'sample');assert.equal(music.queue,queue);assert.equal(music.ducked,false);
  assert.deepEqual(gains,[true,false]);
});
test('failed speech test restores existing music ducking state',async()=>{
  const gains=[],music={ducked:true,setVolume:async()=>gains.push(music.ducked)};
  await assert.rejects(testWithMusic(music,{socket:true},async()=>{throw new Error('failed');}),/failed/);
  assert.equal(music.ducked,true);assert.deepEqual(gains,[true,true]);
});
test('idle speech test never starts a music player',async()=>{
  const music={ducked:false,setVolume:async()=>{throw new Error('should not start');}};
  await testWithMusic(music,{socket:null},async()=>{});assert.equal(music.ducked,false);
});
