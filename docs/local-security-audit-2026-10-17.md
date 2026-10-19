# 本地小智部署安全排查（2026-10-17）

本报告基于当前检出的源码、正在运行的管理 API 返回配置、只读数据库查询、Windows TCP 监听与防火墙规则，以及既有设备串口记录。未修改运行配置、账号、网络规则、数据库内容或固件。排查脚本与脱敏快照位于 `.runtime/security-audit/`。

## 核心结论

- 历史设备日志证明，2026-10-16 22:23:23 至 22:48:46 曾多次连接 `api.tenclass.net:443`。这说明曾进行官方 OTA/激活连接，不能单凭这些连接记录认定上传了对话录音。
- 2026-10-16 22:55:21 起的另一份串口记录显示 OTA 改连 `192.168.0.102:8002`。
- 本次查询的实际 WebSocket 配置为 `ws://192.168.0.102:8000/xiaozhi/v1/`；Python 管理接口及聊天记录上报指向 `http://127.0.0.1:8002/xiaozhi`。MQTT 网关和远程 MCP 接入点未配置。
- 在已检查的后端业务代码、实际配置和当前日志中，没有发现向小智官方上传当前对话/录音的路径或证据。但未对开发板做路由器抓包，也未读取当前设备 NVS，不能声称所有设备出站流量均已验证。
- 当前部署仍使用云服务：DeepSeek 接收对话文本，微软 Edge TTS 接收待合成的回复文本。天气、联网搜索、新闻、IP 定位也会产生外部请求。
- 已确认数据库默认弱口令、完整认证头写入日志、本地录音留存、网络放行范围偏宽等需要处理的问题。

## 数据流向

| 功能 | 本次确认的配置/目标 | 数据范围及证据 |
| --- | --- | --- |
| 开发板音频 | 本机 Python WebSocket，8000 | 设备上传音频，本机播放回复；实际服务配置及历史设备记录支持 |
| VAD/语音识别 | Silero / FunASR `fun_local` | 本地模型处理；本次未发现将原始识别音频送往小智官方或云 ASR 的配置 |
| 大模型 | `https://api.deepseek.com`，`deepseek-flash` | 对话消息，包括组装进上下文的系统提示和历史；`core/providers/llm/openai/openai.py:97` |
| 语音合成 | `speech.platform.bing.com` | 回复文本经 edge-tts 发送至微软；`core/providers/tts/edge.py:65` 及已安装 edge-tts 的 constants.py |
| 天气 | 配置的 `*.re.qweatherapi.com` | 城市/位置、语言等查询参数；`plugins_func/functions/get_weather.py:115` |
| 联网搜索 | Tavily | 搜索关键词；当前插件 provider 为 tavily |
| 新闻 | `newsnow.busiyi.world` | 新闻源/类别查询；当前插件配置 |
| IP 定位 | `whois.pconline.com.cn` | 公网客户端 IP 或服务端出口 IP；私网 IP 在代码中置空后查询，不等于完全不发请求；`core/utils/util.py:76` |
| 聊天记录“上报” | 本机 Java API `/agent/chat-history/report` | 文本和音频；“上报”在当前配置下是本机服务间传递，不是上报小智官方 |

配置中存在 `server.fronted_url=http://xiaozhi.server.com`，源码将其用于激活提示文字，不应把这个占位显示地址当成已发生的官方数据上传。它应改为真实管理页面地址，以免误导操作。

## 已确认问题与优先级

### P1：项目数据库使用 root 默认弱口令，口令与 Git HEAD 相同

运行配置在 `main/manager-api/src/main/resources/application-dev.yml:16` 附近。只读检查确认：项目连接 `127.0.0.1:3307`，使用 root，密码命中常见默认值且与 Git HEAD 中配置一致。本报告不包含密码。

MySQL 8.4 的 3307 以及 X 协议 33060 监听所有网卡，Windows 防火墙存在启用的入站 Allow 规则，Profiles 为 Domain/Private/Public，RemoteIP 为 Any。**同时查询确认 root 仅允许 localhost 登录，因此没有证明可直接从远程使用 root 登录。**

建议：为项目建立仅具有项目库所需权限的账号，替换弱口令；真实凭据迁出受 Git 跟踪文件；项目数据库及 X 协议绑定回环，收紧对应防火墙规则。3306 属于另一个实例，本报告没有修改或审查其业务数据。

### P1：日志记录完整 Authorization 头及对话正文

`main/xiaozhi-server/core/connection.py:218` 将整个请求头写入 INFO 日志，其中包括设备 Bearer token。排查时两份当前日志分别发现 30/8 行此类记录；同时存在大量识别文本和回复文本。数字仅代表审计读取时快照，日志可能继续增长。

这不是已证实的外传，但读取或分享日志的人可能获得可用凭据。建议：Authorization、Cookie 等头统一脱敏；默认减少对话正文日志；限制日志访问和保留周期。修复日志后评估轮换已落盘的设备凭据，并确认设备重连。

### P1：OTA 设备身份验证不足（源码确认，未冒充真实设备实测）

`main/manager-api/src/main/java/xiaozhi/modules/security/config/ShiroConfig.java:79` 对 `/ota/**` 匿名放行。`OTAController.java:42` 接受 Device-Id / Client-Id，主要校验 MAC 格式。`DeviceServiceImpl.java:220` 在开启 WebSocket 鉴权时为这些标识签发 token，没有在该调用链中验证设备持有的独立秘密或签名。

因此，开启 WebSocket token 验证不能单独证明请求来自真实开发板。可访问 OTA 的人若掌握标识，存在冒充设备、消耗模型额度和污染会话的风险。未执行冒充真实设备的请求，也没有据此宣称能远程控制真实硬件。

建议：短期将 8000/8002 的来源限制为可信设备/网段；长期加入每设备独立凭据或签名验证、签发限速和吊销机制。不要将 MAC 地址当成秘密。

### P2：局域网音频与设备凭据使用明文 HTTP / WebSocket

当前设备 WebSocket 为 `ws://`，设备 OTA 为 `http://`。网络上能观察或篡改这些链路的一方可能获得音频/标识/token，或干预 OTA 响应。当前绑定设备启用了自动更新；这放大了对 OTA 响应可信性的要求，但本次没有验证设备是否启用了 Secure Boot 等镜像信任保护。

建议：先收紧网络访问范围；需要跨不可信网络时部署 HTTPS/WSS 并正确配置设备的证书信任，核实固件更新签名方案。不能仅把地址前缀改为 HTTPS 就认定已加密成功。

### P2：本地数据库保存聊天文字和录音

当前设备 `chat_history_conf=2`。`core/handle/reportHandle.py:174`、`:241` 在此模式下上报回复和用户音频至本机管理 API。只读数据库计数为：

- `ai_agent_chat_audio`：585 条；
- `ai_agent_chat_history`：785 条；
- `ai_agent_chat_title`：26 条。

这些是数据库行数，不代表独立会话数量。本次只查询计数，没有导出录音或聊天正文。

`delete_audio=true` 控制临时音频文件处理，**不表示 Java 数据库里的历史录音已经删除**。建议明确选择“不保存 / 只保存文本 / 文本与音频”，设定保留周期，备份也遵循同样的数据保留规则。

### P2/P3：不必要的服务暴露、宽泛跨域和接口文档

当前 8000/8002/8003 监听所有网卡，8001 管理网页与 8006 测试页只监听回环。Python 和当前 Java 程序存在 Private 网络下的入站放行规则，RemoteIP 为 Any。是否可被公网直接访问还取决于路由、端口映射等，本次未证明公网可达。

Java 的 `WebMvcConfig.java:43` 使用任意 Origin 模式并允许 credentials。只读请求确认响应反射了审计用的 Origin。未携带 token 的配置读取和设备列表仍被拒绝，因此这不构成已证明的鉴权绕过。

`/v3/api-docs` 未登录可以读取。`/druid/index.html` 当前返回业务 404，没有确认 Druid 控制台正在对外提供服务。

建议：限制允许的 Origin；按需要关闭文档/调试入口；不用视觉接口时减少 8003 的监听范围。8002 的 OTA 被真实开发板使用，不能直接把它整体改成仅回环而不安排替代入口，否则会破坏设备联网。

## 已验证的保护

- 实际 WebSocket 鉴权开关为 true，源码校验 HMAC token。
- 缺少凭据访问管理配置、设备列表时，HTTP 状态虽为 200，但业务错误码为 401；不能只看 HTTP 200 就判断泄露。
- 录音播放链接需先经过所属智能体权限校验生成，存活 300 秒且使用后删除；不能因为播放路由匿名就认定所有录音可直接下载。
- Redis 仅监听 `127.0.0.1:6379`，项目使用独立逻辑库 15。当前未配置 Redis 密码，仍需防止本机不可信进程访问，但未发现其直接暴露局域网。
- 当前 Python 管理 API 客户端启用 TLS 证书校验，且本机调用设置 `trust_env=False`。
- Windows 三类防火墙配置均开启，默认入站阻止、出站允许；上述明确放行规则仍需单独评估。

## 官方服务与固件默认地址

官方 v2.2.4 源码的默认 OTA 是 `https://api.tenclass.net/xiaozhi/ota/`。`Ota::GetCheckVersionUrl()` 优先读取设备保存的 `wifi.ota_url`，为空才用编译默认地址。版本检查通过请求头发送 MAC/Client-Id，并发送设备系统信息。

- 默认地址：[Kconfig.projbuild](https://github.com/78/xiaozhi-esp32/blob/v2.2.4/main/Kconfig.projbuild)。
- 地址选择与版本检查：[ota.cc](https://github.com/78/xiaozhi-esp32/blob/v2.2.4/main/ota.cc)。

所检查路径中没有“本地 OTA 请求失败后自动改用官方 OTA”的逻辑。但是清除 NVS、恢复出厂或更换使用官方默认地址的固件后，可能再次连接官方。若目标是持续隔离官方服务，后续应编译本地默认地址的固件，并通过路由器/网关抓包验证。

## 建议处理顺序

1. 数据库最小权限、强口令和回环监听；收紧 3307/33060 放行。
2. 修复认证头日志泄露，评估凭据轮换及日志保留。
3. 限制设备服务入口来源，再补强 OTA 设备身份验证。
4. 决定聊天文字和录音是否留存，并落实自动清理。
5. 逐项决定保留哪些云服务；若要求完全离线，需同时替换 DeepSeek、Edge TTS，并处理搜索/天气/IP 定位。
6. 根据实际使用范围配置 HTTPS/WSS、跨域和文档入口。

## 验证边界

没有进行完整依赖 CVE 扫描、渗透测试、设备闪存读取、路由器抓包、外网可达性测试或固件镜像反编译。本报告不能作为“项目不存在其他漏洞”或“设备完全不外联”的保证。数据库与网络探测均仅针对本机授权部署；没有向云模型发送测试对话，也没有清理任何现有数据。
