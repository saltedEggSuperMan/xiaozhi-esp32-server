# Windows 本地完整启动

回答音频可以选择开发板原音频模块或本机 PC 扬声器，见 [PC 外放配置](pc-audio-output.md)。

在项目根目录双击 `start-all.cmd`，或在 VS Code 终端运行：

```powershell
.\start-all.cmd
```

脚本使用已经配置好的 JDK 21、Maven、Python 3.10 虚拟环境、Node.js 和音频工具，无需激活虚拟环境或修改系统 PATH。

启动时先选择回答的播放设备：输入 `1` 使用服务端 PC 扬声器，输入 `2` 使用开发板原音频模块。
直接回车沿用上次选择，输入 `Q` 取消。选择会保存；如果正在运行的语音服务使用了不同配置，启动器会只重启语音服务。
旧版启动器没有记录音频配置时，会先重启一次语音服务确保选择生效。其他健康服务继续复用。

音乐组件已安装时，随后选择本地/网易云来源。交互选择网易云后，服务就绪会自动打开已授权的音乐管理页；重启脚本行为相同。使用 `-OpenBrowser` 时也会包含这个授权入口，避免手工输入 `127.0.0.1:8007` 导致页面未授权。已有网易云账号登录继续保留。

自动化启动可跳过菜单，直接指定：

```powershell
.\start-all.cmd -AudioOutput server_pc
.\start-all.cmd -AudioOutput device
```

`-Check` 只检查，不弹出菜单、不修改播放配置。

## 启动内容与顺序

| 服务 | 地址或端口 | 就绪检查 |
| --- | --- | --- |
| MySQL 8 | `127.0.0.1:3307` | 连接及 MySQL 8 握手 |
| Redis | `127.0.0.1:6379`，逻辑库 15 | 认证（如有）、SELECT、PING |
| Java 管理后端 | `http://127.0.0.1:8002/xiaozhi` | 公共配置接口、语音服务配置密钥验证 |
| 智控台 | `http://127.0.0.1:8001` | 页面及 Java API 代理 |
| Python 语音服务 | `ws://127.0.0.1:8000/xiaozhi/v1/` | WebSocket 诊断响应、8003 视觉接口 |
| 浏览器测试页 | `http://127.0.0.1:8006/index.html` | `/health` 和测试页面内容 |

MySQL、Redis 连接参数读取 `main/manager-api/src/main/resources/application-dev.yml`；Java、Python 端口和配置地址读取项目 YAML。智控台及测试页使用当前约定端口 8001、8006。

**服务已经正常响应时显示“已运行，跳过”，不会再次启动。** 端口已监听但尚未就绪时，脚本会等待；如果始终没有通过检查，则报告异常和日志位置。启动脚本使用文件锁防止两个完整启动脚本同时拉起进程。

MySQL 或 Redis 未监听时，会尝试启动 Windows 服务 `MySQL84`、`Redis`。如果系统拒绝操作，请右键以管理员身份运行 `start-all.cmd`。Java 初始化会清空配置的 Redis 逻辑库，所以脚本要求使用专用库，阻止误用数据库 0。

## 电脑 IP 变更

PC 局域网 IP 改变时，需要同步服务端下发的 WS 地址以及设备保存的 OTA/WS 地址。
双击 `start-all.cmd` / `restart-all.cmd`，完成音频及音乐选择后，会提示可选改址：依次填写局域网 IP、设备访问的 WS 端口、OTA 端口和 COM 口。当前服务端口通常为 8000、8002。**任一步直接回车均跳过本次改址，不读取串口、不修改地址，继续正常启动。**

填写完整后，启动器先等本地服务就绪，再校验新地址上的 WS/OTA 服务并备份、同步设备配置，最后自动复位开发板。需 USB 连接并关闭串口监视器；失败会明确报告，已经启动的服务保持运行。所填端口是设备访问端口，不会自动改变服务监听端口；没有额外端口映射时，应与配置的监听端口一致。

`-Check`、关闭服务、显式指定 `-AudioOutput` 或 `-MusicSource` 的非交互启动不出现改址提示。直接运行 PowerShell 入口可用 `-SelectServerAddress` 启用该提示。
也可单独运行 `server-address.cmd --host 新IP --port COM3 --apply`，避免重新进入配网。工具保留 Wi-Fi、绑定和 Otto 资源，详见 [USB 同步服务端地址](server-address.md)。它不是 IP 自动发现；长期使用建议在路由器设置 DHCP 地址保留。

## 可选参数

仅检查环境和当前运行状态，不启动服务：

```powershell
.\start-all.cmd -Check
```

服务就绪后自动打开智控台、测试页：

```powershell
.\start-all.cmd -OpenBrowser
```

首次 Maven 编译或前端编译较慢时，可将每个服务的等待上限从默认 180 秒调高：

```powershell
.\start-all.cmd -TimeoutSeconds 300
```

也可以直接执行 PowerShell 入口：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-all.ps1
```

## 一键重启

修改天气插件等配置后，需要清除语音服务内存缓存时，可以双击项目根目录的 `restart-all.cmd`，或执行：

```powershell
.\restart-all.cmd
```

重启前同样显示音频选择菜单：`1` 为服务端 PC 扬声器，`2` 为开发板原音频模块；回车沿用上次选择，`Q` 取消重启。
自动化运行可用 `restart-all.cmd -AudioOutput server_pc` 或 `restart-all.cmd -AudioOutput device` 跳过菜单。
`restart-all.cmd -Check` 只检查，不显示菜单、不修改配置、不重启服务。

它会先检查环境和数据库连接，核对当前进程属于本项目，再停止 Python 语音服务、测试页、智控台和 Java 后端，确认端口释放后按依赖顺序重新启动，等待全部健康检查通过。MySQL、Redis 保持运行；角色、密钥及数据库中的持久配置保留。数字人当前连接会断开，完成后刷新页面重新连接。

重启前的进程检查依据当前进程命令行和父子关系，不直接信任旧 PID 文件。遇到身份不明的端口占用会中止并提示，避免误关其他项目。重复执行 `restart-all.cmd` 会重新启动一轮；服务已经停止时，会直接进入启动流程。启动和重启共用同一把文件锁，防止并发启动。

仅检查环境及重启目标，不关闭服务：

```powershell
.\restart-all.cmd -Check
```

自动打开页面，或增加每项服务的启动等待时间：

```powershell
.\restart-all.cmd -OpenBrowser -TimeoutSeconds 300
```

PowerShell 等价命令：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-all.ps1 -Restart -SelectAudioOutput
```

## 一键关闭

双击项目根目录的 `stop-all.cmd`，或在终端执行：

```powershell
.\stop-all.cmd
```

脚本会核对进程归属，关闭本项目的 Python 语音服务、浏览器测试页、智控台和 Java 后端，清理对应启动记录。
MySQL、Redis 保留运行，不删除配置和数据。已经关闭时可重复执行。
关闭操作与启动/重启共用锁，不会同时执行；不要求 API 健康，也不做模型、JDK、Node 或密钥检查。
遇到未知端口占用或权限不足会报错；不会仅凭端口号或旧 PID 关闭不明进程。

只预览关闭目标，不实际关闭：

```powershell
.\stop-all.cmd -Check
```

## 日志与运行方式

启动后服务在后台运行，关闭启动终端后仍会继续运行。本脚本不设置开机自启。

日志位于 `.runtime/logs/`，对应 `manager-api`、`manager-web`、`xiaozhi-server`、`digital-test` 的 `.out.log`、`.err.log` 文件。重新启动某个服务前会自动备份其原日志。新拉起的进程 PID 保存在 `.runtime/all-services.json`，已由其他方式启动并被复用的服务不会冒充为脚本新建的进程。

在 VS Code 终端跟随语音日志：

```powershell
Get-Content .runtime\logs\xiaozhi-server.out.log -Tail 80 -Wait
```

测试页启动入口为 `main/digital-human/serve-local.py`，默认不自动开启后台麦克风采集。页面连接服务后，可以发送文字，或点击录音按钮测试语音。

完整就绪检查验证本地服务可连接；DeepSeek、EdgeTTS 等外部服务仍使用智控台保存的密钥及网络连接，启动过程不发送实际模型对话。
## 网易云音乐可选组件

安装后，完整启动脚本会同时管理本机网易云 API/播放器服务。音乐来源可选本地文件或网易云账号，与回答外放设备独立。使用 `music.cmd --open` 扫码及管理播放；使用 `music.cmd --source local` 恢复原本地文件播放。详见 [网易云音乐接入](netease-music.md)。
