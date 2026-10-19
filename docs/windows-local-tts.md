# Windows 本地语音合成与响应优化

本机已经安装 `Microsoft Huihui Desktop` 中文离线声库，使用 Windows SAPI 进行本地合成。Edge TTS 仍是云端服务；本功能增加了可替换它的本地引擎，并保留原配置供切回。

## 本次效果与选择依据

2026-10-18，同一台电脑使用相同的两条测试文字（8 / 32 字符），只生成到内存、不播放：

| 方案 | 首次合成 | 预热后的合成 |
| --- | ---: | ---: |
| Edge TTS，收齐当前片段 | 1256 ms | 1078 ms |
| Windows Huihui | 255 ms | 8～63 ms |

本地方案在合成阶段减少约一秒。用户实机试听确认“明显更快”，但认为声音像机器人、自然度明显不如 Edge，因此日常配置切回 Edge，本地引擎仅保留为可选项。400 ms 静音等待和 PC 声卡复用继续保留。上述速度收益不是完整问答耗时，识别、模型与工具调用仍然存在。

随后用真实本地 TTS 提供器完成 WAV→重采样→Opus 的四次集成测试，总耗时分别为 155、150、45、133 ms，生成 44 / 135 个可解码的 Opus 包。PC 声卡以同一条 60 ms 静音包测试复用，首轮打开、写入、排空共 1102 ms，之后两轮约 190 ms，确认三轮使用同一个输出流。这是静音诊断，不等同于完整问答延迟或第一声的时间。

同时完成：

- 本机 VAD 静音等待设置为 400 ms（原有效配置是 700 ms），降低判断说完前的等待。讲话停顿容易被截断时，可调回 600～700 ms。
- 同一 WebSocket 连接复用 PC 声卡，每轮结束会排空、停止播放；打断会丢弃剩余数据；连接关闭才释放设备。第一轮仍需打开声卡，后续轮次复用。
- 本地语音引擎使用专用线程，复用声库，合成结果作为 WAV 进入原 PCM/Opus 管线，PC 和开发板原模块都能播放。
- 增加不带原文的 ASR、本地 TTS 毫秒级计时，便于下一步用真实对话检查剩余等待。

Edge 路径仍是原来的“先收齐片段再转换”；切到本地后不调用 Edge。本次没有改成 MP3 网络块增量解码。

## 使用与回退

使用项目根目录的配置工具：

```powershell
# 本机离线 TTS，并缩短说完后的等待
.\audio-output.cmd --tts-mode windows_local --vad-silence-ms 400

# 切回智控台/原配置里的 TTS（本机原配置为 Edge）
.\audio-output.cmd --tts-mode configured

# 如讲话停顿被过早截断，恢复原静音等待
.\audio-output.cmd --vad-silence-ms 700
```

修改后重启 Python 语音服务，也可运行 `restart-all.cmd`，继续选择所需外放方式。TTS 合成引擎与播放设备是两个独立配置。

配置保存在 `main/xiaozhi-server/data/.config.yaml`：

```yaml
speech_response:
  vad_silence_ms: 400
  tts_mode: configured       # 当前沿用 Edge；可选 windows_local
  windows_voice: Microsoft Huihui Desktop
  windows_rate: 0           # -10~10
  windows_volume: 90        # 0~100
```

这属于本机覆盖，不写入或替换智控台数据库中的 Edge 配置。公共配置、设备私有配置与管理配置刷新都会应用本机选择。仓库默认仍为 `configured` 与 `vad_silence_ms: null`，不会强迫其他部署使用 Windows。

仅 Windows 本地 TTS 需要 `requirements-windows-local-tts.txt` 中的 pywin32，本机已有。运行服务的 Windows 用户必须能访问已安装的语音库；没有可用声库时明确报错，不会自动发送文字到 Edge。
语音合成在本地完成不代表整个项目离线：当前 DeepSeek 模型、天气等插件仍可能访问外部服务。

实现参考：[Microsoft SAPI SpVoice](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ms723602%28v%3Dvs.85%29)，使用 `AudioOutputStream` 写内存，不直接播放；[Speak 标志](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ms720892%28v%3Dvs.85%29)指定纯文本，避免把用户输入当成 XML 或文件路径。
