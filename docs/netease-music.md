# 本机网易云音乐与本地音乐切换

适用当前 Windows 完整部署。音乐来源与回答的外放设备独立配置：回答仍可选择开发板或 PC；网易云音乐固定由服务端 PC 的系统默认音频设备播放，保留音源立体声。原本的本地文件插件保留原来的目录、匹配规则和语音播放管线。

## 开始使用

组件已安装在项目 `.runtime/` 内，不修改全局 Node 环境。固定网易云 API 4.41.1；mpv 为 2026-10-18 构建。新环境安装：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install_music.ps1
```

登录并使用网易云：

```powershell
.\music.cmd --source netease --open
```

页面打开后点击“扫码登录”，用手机网易云音乐扫描并确认。支持检查登录状态、搜索点歌、我的喜欢、个人歌单、云盘及播放器控制。不会使用桌面网易云客户端的登录文件。登录失效时重新扫码。

音乐服务只绑定 `127.0.0.1:8007`。请通过 `music.cmd --open` 打开页面，直接输入页面地址没有管理凭据；脚本将本机令牌通过 URL fragment 交给网页，网页立即移除地址中的令牌，不写到访问日志。不要分享带令牌的原始链接。

网页的本机访问凭据与网易云账号登录是两回事：新标签页直接输入地址可能提示未授权，此时重新运行 `music.cmd --open` 即可，已有网易云登录会继续沿用。页面未授权、服务超时会显示明确错误，不再停留在“正在读取状态”。只有“检查登录状态”确认网易云登录失效时，才需要重新扫码。

## 切换模式

```powershell
.\music.cmd --source local       # 恢复原本地文件播放，停止网易云音乐
.\music.cmd --source netease     # 启用账号资源，不清除登录
.\music.cmd --status             # 查看来源、登录及播放状态
```

也可以在音乐管理页切换来源。选择保存在 `.runtime/music/settings.json`，每次语音点歌重新读取，不需要重启语音服务。切换只影响下一次点歌；若原本地文件仍在播放，先用 BOOT 打断它。删除全部 `.runtime/` 会丢失选择及项目登录；默认退回本地模式。

`start-all.cmd` / `restart-all.cmd` 交互启动时，在回答外放选择后提供音乐来源选择，回车沿用上次。自动化可明确指定：

```powershell
.\start-all.cmd -AudioOutput server_pc -MusicSource netease
.\restart-all.cmd -AudioOutput server_pc -MusicSource local
.\stop-all.cmd
```

指定 `-AudioOutput` 而未指定音乐来源时，沿用已有音乐来源，不追加交互。`-Check` 不提示选择，不改设置。已安装音乐组件时，完整启停脚本管理 Node 音乐服务及其 mpv 子进程，并保留 MySQL/Redis。也可单独用 `music.cmd --start` 启动音乐服务。

## 语音与命令行

继续使用现有 `play_music` 工具，无需另加同名插件。网易云模式下支持：

- “播放晴天”“播放周杰伦的晴天”：搜索并播放最匹配歌曲。
- “播放我喜欢的音乐”“播放我的某某歌单”“播放我的云盘”。
- “暂停音乐”“继续播放音乐”“下一首”“上一首”“停止音乐”。这些明确控制词可绕过 LLM 直接操作。
- “把音乐音量调到 30”：由工具设置独立音乐音量，范围 0～100。

```powershell
.\music.cmd --play "晴天"
.\music.cmd --play random --kind liked
.\music.cmd --play "我的歌单名" --kind playlist
.\music.cmd --play random --kind cloud
.\music.cmd --action pause
.\music.cmd --action resume
.\music.cmd --volume 35
```

唤醒词、手动开始拾音及 BOOT 打断会暂停网易云音乐；自动拾音状态切换不会自动暂停刚点播的音乐。请先说“你好小智”或按 BOOT 暂停音乐，再说控制命令。需要继续时说“继续播放音乐”。TTS 开始时降低音乐音量，结束排空后恢复音乐音量；不会自动解除用户暂停。该协调不是声学回声消除，音乐较响时仍可能遮盖人声。

初版把音乐期间所有麦克风音频直接丢弃，实际使用发现开发板已在自动拾音时不会重复发送唤醒事件，导致语音无法打断。已改为：播放期间将收到的 PCM 仅送入本机 Sherpa ONNX 中文关键词模型，检测“你好小智”；未命中的音频不送入普通 ASR、对话上报或 LLM，也不保存录音。命中后先暂停音乐，再回复唤醒提示并恢复普通对话。该检测只在网易云实际播放时运行，暂停/停止后丢弃独立流状态，连接关闭时释放。模型或依赖缺失会记录明确警告，仍保留 BOOT 方式。

本地唤醒模型位于 `.runtime/music-wake/`，使用 [Sherpa 官方中文 KWS 模型](https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html#sherpa-onnx-kws-zipformer-wenetspeech-3-3m-2024-01-01-chinese)的 int8 文件；新增环境执行 `scripts/install_music_wake.ps1`，完整音乐安装脚本也会安装该组件。本机已有 sherpa-onnx 1.12.29，不增加云端语音服务。

2026-10-18 验证：本地合成的“你好小智”命中一次，普通句子未触发；回归测试确认非关键词音频不进入普通 ASR，命中后走暂停和唤醒回复。用户随后实机确认音乐能暂停、能回复，并能继续播放歌曲。播放期间持续收到音频时会更新连接活跃时间，避免长歌曲导致连接空闲超时。

本地模式保留按名称/随机播放、下一首随机选曲及停止；原文件队列不提供精确暂停/续播。网易云队列独立于对话，普通设备断线后音乐可继续，关闭项目或显式停止才终止。

## 资源与权限边界

使用第三方 [NeteaseCloudMusicApiEnhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced)，只从账号接口取得资源，不承诺接口长期稳定。首次使用及授权失效需要本人扫码。

- 只播放网易云返回、且有完整播放权限的音源。跳过无 URL 或带试听标记的条目，不尝试绕过会员、单独购买或地区限制，不做自动解灰/第三方音源替换。
- 当前单次加载最多 100 个歌单、所选歌单前 500 首、云盘前 200 首；队列逐首请求播放地址，避免长时间排队导致地址过期。
- API 4.41.1 的播放地址模块默认使用需要额外公钥状态的 xeapi，本机验证会报缺少 publicKeyState；适配器明确使用该模块支持的 weapi 协议，仍请求网易云同一账号音源，并检查试听标记，不使用替换音源。
- 登录 Cookie 由当前 Windows 用户的 DPAPI 加密保存在 `.runtime/music/account.dpapi`；不会送入大模型或设备，不在管理 API 中返回。退出只清除本项目保存的账号，不退出桌面客户端。
- 本机桥接 API 需要独立令牌，限制 Host/Origin、JSON 请求和请求体长度，不暴露第三方 API 的全部接口。日志抑制上游完整错误对象，播放器日志不记录签名播放地址。
- Node/mpv 需要联网访问网易云；本功能不是离线音乐方案。

## 验证与依赖检查

已验证真实二维码生成、未授权访问拒绝、跨站拒绝、DPAPI 加解密，以及 mpv 的 44.1 kHz 双声道静音样本加载与暂停/继续/停止。

本机账号扫码后，已验证重启恢复登录、个人歌单与喜欢列表读取、喜欢列表完整权限歌曲加载、暂停/继续/停止，以及本地/网易云实时切换。最初 12% 音量、约三秒试播用户未听到；随后固定提示音和约十二秒、50% 音乐音量的账号歌曲均由用户确认听到。日常音乐音量已设为 50，可独立调节。音乐唤醒打断修复后，用户也已确认暂停、回复、继续播放的实机操作正常。

自动测试：

```powershell
node --test services/music-bridge/music.test.js
main/xiaozhi-server/.venv/Scripts/python.exe -B main/xiaozhi-server/tests/test_music_service.py -q
main/xiaozhi-server/.venv/Scripts/python.exe -B main/xiaozhi-server/tests/test_wakeup_listening.py -q
main/xiaozhi-server/.venv/Scripts/python.exe -m unittest scripts.tests.test_start_local -q
```

2026-10-19：补齐“继续播放”“恢复播放”“暂停播放”的直接控制；缓存唤醒回应播放结束后立即恢复拾音，清除回应期间积压的音频，避免 ASR 工作线程等待回应结束后才开始两秒保护期、吞掉紧接着说的短指令。

“继续播放”“恢复播放”等恢复指令成功后直接播放音乐，不播报确认语音，也不播放结束提示音；恢复失败时仍会提示原因。音乐工具的 `resume` 操作同样保留执行记录并省略确认播报。

2026-10-18 的 npm audit：已将 basic-ftp 固定到修复版本 6.2.3；仍有 [node-forge 1.4.0 RSA 签名验证漏洞](https://github.com/advisories/GHSA-86w9-cpqp-85rv)及向 API 包传导的两条 high 记录，发布源暂无修复版本。所检查的网易云适配代码只调用它的公钥解析与加密，不调用受报告影响的签名验证；这不代表整个依赖不存在风险。保持仅本机监听，并关注后续上游修复。依赖锁文件保存在 `services/music-bridge/package-lock.json`。

播放器来源与控制：[mpv Windows 安装来源](https://mpv.io/installation/)、[JSON IPC 文档](https://mpv.io/manual/stable/#json-ipc)。安装脚本校验固定 mpv 压缩包和解压器 SHA-256，下载内容变化时停止安装。
