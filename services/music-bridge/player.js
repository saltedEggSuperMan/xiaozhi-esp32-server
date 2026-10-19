'use strict';
const {spawn} = require('node:child_process');
const net = require('node:net');
const {randomUUID} = require('node:crypto');
const {EventEmitter} = require('node:events');

class Player extends EventEmitter {
  constructor(executable) {
    super(); this.executable = executable; this.pending = new Map(); this.serial = 0;
  }
  async start() {
    if (this.socket && !this.socket.destroyed) return;
    if (this.starting) return this.starting;
    this.starting = this._start().finally(() => { this.starting = null; });
    return this.starting;
  }
  async _start() {
    const pipe = '\\\\.\\pipe\\xiaozhi-music-' + randomUUID();
    this.process = spawn(this.executable, ['--no-config', '--load-scripts=no', '--ytdl=no',
      '--idle=yes', '--no-video', '--audio-display=no', '--terminal=no', '--input-terminal=no',
      '--keep-open=no', '--volume=35', '--demuxer-max-bytes=16MiB', '--network-timeout=10',
      '--input-ipc-server=' + pipe], {windowsHide:true, stdio:'ignore'});
    let failed = false;
    this.process.once('error', () => { failed = true; });
    this.process.once('exit', () => {
      failed = true;
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
        return;
      } catch (_) { await new Promise(resolve => setTimeout(resolve,80)); }
    }
    this.close(); throw new Error('无法启动 mpv 播放器');
  }
  async command(command) {
    await this.start();
    return new Promise((resolve,reject) => {
      const id = ++this.serial;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('播放器响应超时')); }, 4000);
      this.pending.set(id, {resolve:value => {clearTimeout(timer);resolve(value);}, reject:error => {clearTimeout(timer);reject(error);}});
      this.socket.write(JSON.stringify({command, request_id:id})+'\n');
    });
  }
  async stop() { if (this.socket && !this.socket.destroyed) await this.command(['stop']); }
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
  close() { if (this.socket) this.socket.destroy(); if (this.process) this.process.kill(); }
}
module.exports = {Player};
