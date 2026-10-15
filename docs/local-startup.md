# Windows 本地完整启动

在项目根目录双击 `start-all.cmd`，或在 VS Code 终端运行：

```powershell
.\start-all.cmd
```

脚本使用已经配置好的 JDK 21、Maven、Python 3.10 虚拟环境、Node.js 和音频工具，无需激活虚拟环境或修改系统 PATH。

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

## 日志与运行方式

启动后服务在后台运行，关闭启动终端后仍会继续运行。本脚本不设置开机自启。

日志位于 `.runtime/logs/`，对应 `manager-api`、`manager-web`、`xiaozhi-server`、`digital-test` 的 `.out.log`、`.err.log` 文件。重新启动某个服务前会自动备份其原日志。新拉起的进程 PID 保存在 `.runtime/all-services.json`，已由其他方式启动并被复用的服务不会冒充为脚本新建的进程。

在 VS Code 终端跟随语音日志：

```powershell
Get-Content .runtime\logs\xiaozhi-server.out.log -Tail 80 -Wait
```

测试页启动入口为 `main/digital-human/serve-local.py`，默认不自动开启后台麦克风采集。页面连接服务后，可以发送文字，或点击录音按钮测试语音。

完整就绪检查验证本地服务可连接；DeepSeek、EdgeTTS 等外部服务仍使用智控台保存的密钥及网络连接，启动过程不发送实际模型对话。
