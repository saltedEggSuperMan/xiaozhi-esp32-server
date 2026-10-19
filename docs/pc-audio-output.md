# 切换开发板 / PC 扬声器

`device` 使用原来的开发板音频模块，默认行为保持不变；`server_pc` 在运行 Python 语音服务的电脑上播放回答。
PC 模式继续用开发板麦克风、唤醒词和 BOOT 键。开发板仍收到文字、TTS 开始/结束状态，但不再收到回答音频包。
短按 BOOT 或固件支持的唤醒打断会停止 PC 播放；这不会给固件新增“说停即打断”的能力。

## Windows 快速使用

双击根目录 `start-all.cmd` 后，输入 `1` 选择 PC 扬声器、输入 `2` 选择开发板原音频模块，回车沿用上次选择。
启动器保存选择并在需要时只重启语音服务，无需先运行下面的手动配置命令。
也可用 `start-all.cmd -AudioOutput server_pc` 或 `start-all.cmd -AudioOutput device` 跳过菜单。
`restart-all.cmd` 重启前也会显示相同菜单，回车沿用上次选择，也支持 `-AudioOutput` 直接指定；`stop-all.cmd` 关闭项目应用服务。

在项目根目录终端执行（使用项目自己的 Python，无需激活环境）：

```powershell
# 首次安装 PC 播放依赖；原音频模块模式不需要安装
.\main\xiaozhi-server\.venv\Scripts\python.exe -m pip install -r main/xiaozhi-server/requirements-pc-audio.txt

# 列出电脑输出设备，只列扬声器/耳机，不访问麦克风
.\audio-output.cmd --list-devices

# 使用 Windows 默认输出，测试成功后保存 PC 模式
.\audio-output.cmd --mode server_pc --device default --volume 0.6 --test

# 或按上方列表指定输出设备名称 / 编号，例如 12
.\audio-output.cmd --mode server_pc --device 12 --test

# 查看当前配置
.\audio-output.cmd

# 切回开发板原音频模块
.\audio-output.cmd --mode device
```

保存后重启 Python 语音服务；本项目也可用 `restart-all.cmd` 重启完整服务。开发板无需重新烧录或绑定。
设备编号可能随插拔变化，固定输出可以用列表中的名称加音频接口名，例如 `扬声器 Realtek WASAPI`；`default` 跟随系统默认输出，默认设备可能是耳机。
`--test` 播放约 2 秒的变调提示音，通过真实 Opus 编解码和同一播放模块完成，测试软件音量最高为 0.6；失败时不保存新选择。

## 配置文件

设置保存在 `main/xiaozhi-server/data/.config.yaml`，不要把整个文件发给他人，其中可能包含密钥。
也可只手动修改以下部分：

```yaml
audio_output:
  mode: server_pc  # device / server_pc
  pc:
    device: null  # 系统默认，或 sounddevice 输出编号 / 名称
    volume: 0.6   # 0~1，额外的 PC 软件音量
```

这是服务端本机配置，单服务与智控台完整部署都生效，智控台刷新业务配置也不会覆盖它。
切换需重启 Python 服务，作用于之后建立的会话。PC 模式下音量由这里与操作系统控制；开发板的音量指令不控制 PC 音量。

## 播放与限制

- PC 模式使用可选依赖 [sounddevice RawOutputStream](https://python-sounddevice.readthedocs.io/en/0.5.3/api/raw-streams.html)，在独立线程里播放，不阻塞网络事件循环。音频在内存中解码、按声卡采样率转换，没有新增录音存储或外部网络请求。
- 队列有大小限制，正常结束会等尾音播完；打断和断线会丢弃尚未播放的音频。切换出口包含回答、唤醒回复、插件音乐及服务器发送的提示音；固件自己生成的启动/配网提示仍在原模块播放。
- PC 音频流由各连接复用，连接关闭时释放；只有正在播放的一轮回答占用全局播放锁，空闲旧连接不会阻塞重连后的回答。同一时刻只播放一个设备会话的回答，播放期间其他会话竞争时明确记录错误并跳过本轮外放。播放失败不会偷偷回退到开发板。
- 电脑需要开机、语音服务需要运行且能访问音频设备。以 Windows 桌面用户运行；容器、远程主机、无桌面服务账号不会自动连接到你笔记本的扬声器。Linux 还需系统 PortAudio 库及声卡访问权限。
- 仍由开发板麦克风收音，PC 扬声器应与麦克风保持距离。更换外放位置后，原固件本地回声消除无法取得 PC 扬声器参考信号，免提/打断效果需实机确认。

本机离线 TTS、静音等待和切回 Edge 的说明见 [Windows 本地语音合成](windows-local-tts.md)。
