'use strict';
const {spawn} = require('node:child_process');
const net = require('node:net');
const {randomUUID} = require('node:crypto');
const {EventEmitter} = require('node:events');
const {outputArgs,signature,musicVolume} = require('./audio-route');

class Player extends EventEmitter {
  constructor(executable, routeProvider=async()=>({mode:'server_pc',volume:1})) {
    super(); this.executable = executable; this.pending = new Map(); this.serial = 0; this.routeProvider=routeProvider;
  }
  async start() {
    if (this.socket && !this.socket.destroyed) return;
    if (this.starting) return this.starting;
    this.starting = this._start().finally(() => { this.starting = null; });
    return this.starting;
  }
  async _start() {
    this.outputRoute=await this.routeProvider();
    const output=outputArgs(this.outputRoute);
    const pipe = '\\\\.\\pipe\\xiaozhi-music-' + randomUUID();
    this.process = spawn(this.executable, ['--no-config', '--load-scripts=no', '--ytdl=no',
      '--idle=yes', '--no-video', '--audio-display=no', '--terminal=no', '--input-terminal=no',
      '--keep-open=no', '--volume=35', '--demuxer-max-bytes=16MiB', '--network-timeout=10',
      ...output, '--input-ipc-server=' + pipe], {windowsHide:true, stdio:'ignore'});
    const process=this.process;
    let failed = false;
    this.process.once('error', () => { failed = true; });
    this.process.once('exit', () => {
      failed = true;
      if(this.process!==process)return;
      if (this.socket) this.socket.destroy();
      this.socket = null;
      for (const job of this.pending.values()) job.reject(new Error('播放器已退出'));
      this.pending.clear(); this.emit('exit');
    });
    const deadline = Date.now() + 6000;
    while (!failed && Date.now() < deadline) {
      try {
        this.socket = await new Promise((resolve,reject) => {
          const sock = net.createConnection(pipe);
          sock.once('error', reject); sock.once('connect', () => { sock.removeListener('error',reject); resolve(sock); });
        });
        this.socket.setEncoding('utf8');
        let buffer = '';
        this.socket.on('error', () => {});
        this.socket.on('data', part => {
          buffer += part;
          while (buffer.includes('\n')) {
            const i = buffer.indexOf('\n'), line = buffer.slice(0,i); buffer = buffer.slice(i+1);
            try {
              const msg = JSON.parse(line), job = this.pending.get(msg.request_id);
              if (job) { this.pending.delete(msg.request_id); msg.error === 'success' ? job.resolve(msg.data) : job.reject(new Error('播放器命令失败')); }
              else if (msg.event) this.emit(msg.event, msg);
            } catch (_) { /* Ignore non-JSON diagnostics. */ }
          }
        });
        if(this.outputRoute.mode==='PC_DEVICE') {
          await this._command(['observe_property',41,'audio-device-list']);
        }
        await this._command(['observe_property',42,'time-pos']);
        return;
      } catch (_) { await new Promise(resolve => setTimeout(resolve,80)); }
    }
    this.close(); throw new Error('无法启动 mpv 播放器');
  }
  async command(command) {
    const route=await this.routeProvider();
    try {outputArgs(route);}catch(err){this.close();throw err;}
    if(this.socket && this.outputRoute && signature(route)!==signature(this.outputRoute))this.close();
    await this.start();
    if(command[0]==='set_property' && command[1]==='volume' && route.mode==='PC_DEVICE')
      command=[...command.slice(0,2),musicVolume(route,command[2])];
    return this._command(command);
  }
  async _command(command) {
    return new Promise((resolve,reject) => {
      const id = ++this.serial;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('播放器响应超时')); }, 4000);
      this.pending.set(id, {resolve:value => {clearTimeout(timer);resolve(value);}, reject:error => {clearTimeout(timer);reject(error);}});
      this.socket.write(JSON.stringify({command, request_id:id})+'\n');
    });
  }
  async stop() {
    // Stopping must also work after a device disappears or config becomes invalid.
    if (this.socket && !this.socket.destroyed) {
      try {await this._command(['stop']);}catch(_){this.close();}
    }
  }
  async diagnostics() {
    if(!this.socket || this.socket.destroyed)return {running:false};
    const properties={};
    // Deliberately exclude media path/URL and account metadata.
    for(const name of ['audio-device','current-ao','volume','mute','pause','time-pos','duration','audio-params','core-idle']) {
      try {properties[name]=await this._command(['get_property',name]);}
      catch(_){properties[name]=null;}
    }
    return {running:true,properties};
  }
  async load(url) {
    await this.start();
    // A successful loadfile command only means accepted. Wait until the file
    // actually loads, so a broken audio URL is not reported as successful play.
    let cleanup;
    const loaded = new Promise((resolve,reject) => {
      const onLoaded = () => {cleanup();resolve();};
      const onEnd = event => {if(event.reason==='error'){cleanup();reject(new Error('音源无法播放，请切换歌曲'));}};
      const onExit = () => {cleanup();reject(new Error('播放器已退出'));};
      const timer=setTimeout(()=>{cleanup();reject(new Error('音源加载超时，请切换歌曲'));},14000);
      cleanup=()=>{clearTimeout(timer);this.removeListener('file-loaded',onLoaded);this.removeListener('end-file',onEnd);this.removeListener('exit',onExit);};
      this.on('file-loaded',onLoaded);this.on('end-file',onEnd);this.on('exit',onExit);
    });
    // Attach a handler immediately, including when command itself fails first.
    loaded.catch(()=>{});
    try {await this.command(['loadfile',url,'replace']);await loaded;}
    finally {cleanup();}
  }
  close() {
    if (this.socket) this.socket.destroy(); if (this.process) this.process.kill();
    this.socket=null;this.process=null;
    for(const job of this.pending.values())job.reject(new Error('播放器已停止'));
    this.pending.clear();
  }
}
module.exports = {Player};
