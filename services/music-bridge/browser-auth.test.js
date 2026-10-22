'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {browserAuth}=require('./browser-auth');
const token='a'.repeat(64);
test('authorized launch establishes HttpOnly session cookie without the bridge token',()=>{
  const auth=browserAuth(token);
  assert.equal(auth.authorized({headers:{authorization:'Bearer '+token}}),true);
  assert.match(auth.cookie,/HttpOnly; SameSite=Strict; Path=\/api\//);
  assert.equal(auth.cookie.includes(token),false);
});
test('new tab and server restart accept the same browser session',()=>{
  const cookie=browserAuth(token).cookie.split(';')[0];
  const auth=browserAuth(token);
  assert.equal(auth.authorized({headers:{cookie}}),true);
  assert.equal(auth.authorized({headers:{cookie,authorization:'Bearer stale'}}),true);
});
test('absent, wrong and modified cookies do not authorize APIs',()=>{
  const auth=browserAuth(token),cookie=auth.cookie.split(';')[0];
  for(const headers of [{},{cookie:'xiaozhi_music_session=bad'}, {cookie:cookie+'extra'},
    {authorization:'Bearer stale'},{cookie:browserAuth('b'.repeat(64)).cookie}])
    assert.equal(auth.authorized({headers}),false);
});
