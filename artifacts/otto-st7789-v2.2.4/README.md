# Otto 动态表情资源包

适用：已烧录本项目 v2.2.4 bread-compact-wifi-lcd 的 ESP32-S3 N16R8，ST7789 240×240 屏幕。

- 21 种 Otto 动态表情，适配为 128×128，保留状态栏和对话文字，黑底白字。
- 保留原资源包中的字体和“你好小智”唤醒模型；仅写入 assets 分区，地址 **0x800000**。
- 不替换应用固件，不写入配网、设备标识所在的 NVS 分区。
- `preview-neutral.gif` 可在浏览器预览待机动画。

在 PowerShell 中执行（关闭占用 COM3 的串口监视器）：

```powershell
.\artifacts\otto-st7789-v2.2.4\flash-otto.ps1 -Port COM3
```

恢复原默认表情：

```powershell
.\artifacts\otto-st7789-v2.2.4\flash-otto.ps1 -Port COM3 -Restore
```

资源来自 https://github.com/txp666/otto-emoji-gif-component ，MIT 许可见 OTTO-LICENSE.txt。
完整校验与来源见 manifest.json。资源文件不能按完整固件的 0x0 地址烧录。
