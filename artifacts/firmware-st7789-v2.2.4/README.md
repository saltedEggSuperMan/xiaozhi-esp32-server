# ESP32-S3 N16R8 + ST7789 240×240（8针带CS）

小智固件 2.2.4；ESP-IDF 5.5.2；目标 bread-compact-wifi-lcd。
16MB Flash（80MHz），8MB 八线 PSRAM（80MHz），中文界面、你好小智唤醒及默认字体/表情/模型资源。

## 接线

先断电接线。屏幕：GND→GND，VCC→3V3，SCL→GPIO21，SDA→GPIO47，RES→GPIO45，DC→GPIO40，CS→GPIO41，BLK→GPIO42。
INMP441：VDD→3V3，GND/LR→GND，WS→GPIO4，SCK→GPIO5，SD→GPIO6。
服务端 PC 外放继续由服务端配置控制，不需要接回 MAX98357A。

## 烧录方式

### 保留已有配网设置（分段烧录）

关闭占用串口的调试工具，从本目录运行以下命令，COM3 替换成实际串口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\flash-preserve-settings.ps1 -Port COM3
```

脚本使用本项目独立 ESP-IDF Python，先检查 SHA256，再按照 flash-manifest.json 写入分段文件，避开 NVS 的 0x9000–0xCFFF；不执行全片擦除。
分区表布局与原 2.2.4 的 16MB v2 分区一致。已有 Wi-Fi、UUID、OTA 设置仍以 NVS 中保存的值为准。

### 使用原 Flash Download Tool（完整镜像）

选择 ESP32-S3，加载 merged-binary.bin，烧录地址填 0x0，Flash 为 16MB，SPI 为 DIO/80MHz（与 ESP-IDF 生成的烧录参数一致）。
完整镜像会覆盖范围内的 NVS，可能需要重新配网和确认设备绑定；不要把它当作保留配置的升级方式。

固件默认本地 OTA 为 http://192.168.0.102:8002/xiaozhi/ota/ 。如果服务器 IP 改变，在配网页高级选项中更新 OTA 地址。

## 验证范围

已完成编译、ESP32-S3 镜像头检查、16MB 边界检查、合并文件与分段内容核对、关键配置检查。
未写入开发板；屏幕亮屏、方向和颜色需要实际烧录接线后验证。
SHA256SUMS.txt 包含烧录文件与配置的校验值。源代码副本和完整编译日志在项目 .runtime/fw244 中；用户原始源码未改动。
