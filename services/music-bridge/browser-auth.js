'use strict';
const {createHmac,timingSafeEqual}=require('node:crypto');
function equal(a,b) {
  const left=Buffer.from(a || ''),right=Buffer.from(b);
  return left.length===right.length && timingSafeEqual(left,right);
}
function browserAuth(token) {
  // Separate browser credential; stable across project restarts. Never expose
  // the command-line bridge token to JavaScript through a readable cookie.
  const credential=createHmac('sha256',token).update('xiaozhi-music-browser-session-v1').digest('hex');
  const name='xiaozhi_music_session';
  return {
    authorized(req) {
      if(equal(req.headers.authorization,'Bearer '+token))return true;
      const cookie=(req.headers.cookie || '').split(';').map(x=>x.trim()).find(x=>x.startsWith(name+'='));
      return Boolean(cookie && equal(cookie.slice(name.length+1),credential));
    },
    cookie: name+'='+credential+'; HttpOnly; SameSite=Strict; Path=/api/',
  };
}
module.exports={browserAuth};
