'use strict';
function validSongURL(text) {
  try {
    const u=new URL(text);
    return ['http:','https:'].includes(u.protocol) && !u.username && !u.password &&
      (!u.port || ['80','443'].includes(u.port)) &&
      /(^|\.)(music\.126\.net|music\.163\.com|music\.126\.com)$/.test(u.hostname);
  } catch (_) {return false;}
}
function track(song) {
  const id=String(song.id || song.songId || '');
  if (!/^\d+$/.test(id)) throw new Error('歌曲信息无效');
  return {id,name:String(song.name || song.songName || '未知歌曲').slice(0,200),
    artist:(song.ar || song.artists || []).map(a=>a.name).join(' / ').slice(0,200)};
}
class Music {
  constructor(player,api,settings,loggedIn) {
    Object.assign(this,{player,api,settings,loggedIn});
    this.queue=[]; this.index=-1; this.state='stopped'; this.ducked=false; this.message='';
    this.pausedRequested=false;
    this.positionSeconds=0;
    this.needsReload=false;
  }
  get state(){return this._state;}
  set state(value){this._state=value;if(this.onState)this.onState(value);}
  status(){return {state:this.state,current:this.queue[this.index] || null,position:this.index+1,
    count:this.queue.length,volume:this.settings().volume ?? 35,message:this.message};}
  snapshot(){
    if(!this.queue.length)return null;
    return {version:1,source:this.settings().source,queue:this.queue.map(({id,name,artist})=>({id,name,artist})),
      index:Math.max(0,this.index),seconds:this.positionSeconds};
  }
  restore(saved){
    if(!saved || saved.version!==1 || saved.source!=='netease' || this.settings().source!==saved.source ||
       !Array.isArray(saved.queue) || !saved.queue.length || saved.queue.length>500 ||
       !Number.isInteger(saved.index) || saved.index<0 || saved.index>=saved.queue.length ||
       !Number.isFinite(saved.seconds) || saved.seconds<0 || saved.seconds>86400 ||
       saved.queue.some(s=>!s || !/^\d+$/.test(s.id) || typeof s.name!=='string' || typeof s.artist!=='string'))return false;
    this.queue=saved.queue.map(s=>({id:String(s.id),name:s.name.slice(0,200),artist:s.artist.slice(0,200)}));
    this.index=saved.index;this.positionSeconds=saved.seconds;this.pausedRequested=true;this.needsReload=true;
    this.state='paused';this.message='已恢复播放队列，请说“继续播放音乐”';return true;
  }
  requireAccount(){if(!this.loggedIn()) throw new Error('请运行 music.cmd --open，扫码登录网易云');}
  async userId(){
    this.requireAccount(); const info=await this.api('login_status');
    const id=info.data?.profile?.userId;
    if(!id) throw new Error('网易云登录已失效，请重新扫码'); return id;
  }
  async search(query){
    this.requireAccount();
    if(!query.trim() || query.length>200) throw new Error('请提供歌曲名称或歌手');
    const body=await this.api('cloudsearch',{keywords:query,type:1,limit:20});
    return (body.result?.songs || []).map(track);
  }
  async playlists(){
    const uid=await this.userId(); const body=await this.api('user_playlist',{uid,limit:100});
    return (body.playlist || []).map(p=>({id:String(p.id),name:p.name,specialType:p.specialType,trackCount:p.trackCount}));
  }
  async play(data){
    if(this.settings().source!=='netease') throw new Error('当前是本地音乐模式，请先切换到网易云');
    this.requireAccount();
    this.pausedRequested=false;
    const query=String(data.query || 'random'), kind=data.kind || 'song';
    const selectedSong=kind==='song' && Object.prototype.hasOwnProperty.call(data,'id');
    if(selectedSong && (!['string','number'].includes(typeof data.id) || !/^\d+$/.test(String(data.id))))
      throw new Error('歌曲编号无效');
    let songs=[];
    if(selectedSong) {
      // Search rows send an ID without a query. Resolve that exact selection
      // before the query's default "random" can select the liked-song queue.
      const id=String(data.id);
      songs=((await this.api('song_detail',{ids:id})).songs || []).map(track).filter(song=>song.id===id);
    } else if(kind==='cloud') {
      const body=await this.api('user_cloud',{limit:200});
      songs=(body.data || []).map(s=>track(s.simpleSong || s));
      if(query!=='random') songs=songs.filter(s=>(s.name+' '+s.artist).toLowerCase().includes(query.toLowerCase()));
    } else if(kind==='liked' || (kind==='song' && query==='random')) {
      const uid=await this.userId();
      const ids=(await this.api('likelist',{uid})).ids?.slice(0,500) || [];
      if(ids.length) songs=(await this.api('song_detail',{ids:ids.join(',')})).songs?.map(track) || [];
    } else if(kind==='playlist') {
      const lists=await this.playlists();
      const list=lists.find(p=>p.id===query || p.name===query) || lists.find(p=>p.name.includes(query));
      if(!list) throw new Error('没有找到该个人歌单，请在管理页查看歌单名称');
      const detail=await this.api('playlist_detail',{id:list.id});
      const ids=(detail.playlist?.trackIds || []).slice(0,500).map(s=>s.id);
      if(ids.length) songs=(await this.api('song_detail',{ids:ids.join(',')})).songs?.map(track) || [];
      else songs=(detail.playlist?.tracks || []).map(track);
    } else if(kind==='song') {
      songs=await this.search(query);
      const normalized=query.replace(/\s/g,'').toLowerCase();
      const exact=songs.find(s=>s.name.replace(/\s/g,'').toLowerCase()===normalized);
      songs=exact?[exact]:songs.slice(0,1);
    } else throw new Error('不支持的音乐类型');
    if(!songs.length) throw new Error('没有找到可播放的歌曲');
    if(!selectedSong && query==='random') songs=songs.map(value=>({value,key:Math.random()})).sort((a,b)=>a.key-b.key).map(x=>x.value);
    await this.player.stop(); this.queue=songs; this.index=-1; this.message='';this.state='loading';
    try {return await this.playAt(0,1);}
    catch(error) {await this.stop();this.state='error';throw error;}
  }
  async setVolume(){
    return this.player.command(['set_property','volume',(this.settings().volume ?? 35)*(this.ducked?0.15:1)]);
  }
  async playAt(index,direction,resumeSeconds=0){
    let skipped=0;
    for(let i=index;i>=0 && i<this.queue.length && skipped<10;i+=direction,skipped++) {
      const song=this.queue[i];
      // Package 4.41.1 defaults this module to xeapi, which requires a separately
      // provisioned publicKeyState. Use its supported weapi transport instead;
      // this is the same account/NetEase endpoint and keeps trial restrictions.
      const body=await this.api('song_url_v1',{id:song.id,level:'exhigh',unblock:false,crypto:'weapi'});
      const audio=body.data?.[0];
      if(!audio?.url || audio.freeTrialInfo || !validSongURL(audio.url)) continue;
      await this.player.start(); await this.setVolume();
      const seconds=i===index?resumeSeconds:0;
      await this.player.command(['set_property','pause',this.pausedRequested || seconds>0]);
      await this.player.load(audio.url);
      if(seconds>0)await this.player.command(['seek',seconds,'absolute+exact']);
      this.positionSeconds=seconds;
      await this.player.command(['set_property','pause',this.pausedRequested]);
      this.index=i;this.needsReload=false; this.state=this.pausedRequested?'paused':'playing'; this.message=skipped?'已跳过无完整播放权限的歌曲':'';
      return {message:'正在播放：'+song.name+(song.artist?'，'+song.artist:''),current:song};
    }
    await this.stop(); throw new Error('未找到有完整播放权限的歌曲，可能需要会员、单独购买或歌曲已下架');
  }
  async advance(delta){
    if(!this.queue.length) throw new Error('当前没有音乐队列');
    const next=this.index+delta;
    if(next<0 || next>=this.queue.length) {await this.stop(); return {message:'播放队列已结束'};}
    return this.playAt(next,delta);
  }
  async stop(){await this.player.stop();this.queue=[];this.index=-1;this.positionSeconds=0;this.needsReload=false;this.state='stopped';return {message:'音乐已停止'};}
  async control(data){
    const action=data.action;
    if(action==='stop') return this.stop();
    if(action==='status') return {...this.status(),message:this.queue[this.index]?'当前歌曲：'+this.queue[this.index].name:'当前没有播放音乐'};
    if(action==='volume') {
      if(!Number.isFinite(data.volume) || data.volume<0 || data.volume>100) throw new Error('音乐音量范围是 0 到 100');
      this.settings().volume=data.volume; if(this.player.socket) await this.setVolume();
      return {message:'音乐音量已设置为 '+data.volume};
    }
    if(action==='pause') this.pausedRequested=true;
    if(action==='pause' && !this.queue.length && data.quiet) return {};
    if(!this.queue.length) throw new Error('当前没有音乐队列，请先点歌');
    if(action==='next') {this.pausedRequested=false;return this.advance(1);}
    if(action==='previous') {this.pausedRequested=false;return this.advance(-1);}
    if(action==='pause' || action==='resume') {
      this.pausedRequested=action==='pause';
      if(action==='pause' && (!this.player.socket || this.needsReload)) {
        this.state='paused';return {message:'音乐已暂停'};
      }
      if(action==='resume' && (!this.player.socket || this.needsReload)) {
        // Endpoint loss destroys the old mpv process. Reopen the retained track
        // only on explicit resume; toggling pause on a new idle player is silent.
        try {return await this.playAt(Math.max(this.index,0),1,this.positionSeconds);}
        catch(error) {this.state='error';throw error;}
      }
      await this.player.command(['set_property','pause',action==='pause']); this.state=action==='pause'?'paused':'playing';
      return {message:action==='pause'?'音乐已暂停':'继续播放音乐'};
    }
    throw new Error('不支持的播放控制');
  }
}
module.exports={Music,track,validSongURL};
