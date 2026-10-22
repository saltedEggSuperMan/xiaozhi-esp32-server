'use strict';
// Testing an endpoint must not clear the user's music queue. Match ordinary TTS
// software ducking, then restore the prior music gain even when a test fails.
async function testWithMusic(music,player,test) {
  const ducked=music.ducked;
  music.ducked=true;
  try {
    if(player.socket)await music.setVolume();
    return await test();
  } finally {
    music.ducked=ducked;
    if(player.socket)await music.setVolume();
  }
}
module.exports={testWithMusic};
