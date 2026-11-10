# 麦克风唤醒 / 声源定位监视器

把**讯飞六麦圆形阵列**（幻尔 JetMax 同款，CH340 串口）插到任意一台电脑上，喊一声唤醒词，
终端就会回传**声源方位角**、**波束号**和**唤醒词**。零第三方依赖，Windows / Linux / macOS 通用。

```
========================================================================
  麦克风唤醒 / 声源定位监视器  v1.0.1
  串口      : COM16  (115200 8N1)
  后端      : kernel32 原生
  角度换算  : (600-raw)%360  [ROS 一致]
  开始时间  : 2026-11-10 14:48:06
========================================================================
[xf_mic_app]: >>> 检测到唤醒！
[xf_mic_app]:     角度: 278.0 度  (换算 322° / 左前方)
[xf_mic_app]:     波束: 5
[xf_mic_app]:     唤醒词: 小幻小幻  (xiao3 huan4 xiao3 huan4)
[xf_mic_app]:     置信度 1869   能量 736424640
[xf_mic_app]: ------------------------------------------------------------
[mic_serial_node]:Wakeup event published: angle=278.0, beam=5, keyword=xiao3 huan4 xiao3 huan4
```

> 上面这段是实测输出，不是示意。

---

## 特性

- **零依赖**：有 `pyserial` 就用；没有则自动回退到 Windows `kernel32`（ctypes）或 Linux `termios` 原生实现。**不需要 pip 安装任何东西**，只要有 Python 3.7+。
- **自动找串口**：不用手填 `COMx`，程序会扫一遍所有串口，看谁在发 `A5` 帧。
- **离线回放**：内置真实抓包样本，不接硬件也能演示、测试、做培训。
- **可记录**：`--csv` 落盘，`--json` 输出 JSONL，方便接自己的程序。
- **容错**：串口被占用、CH340 卡死、不在 dialout 组等常见错误都翻译成中文提示。
- **与 ROS 一致**：换算角度用 `(600 - raw) % 360`，与 `awake_node.py` 的 `val_map(angle,0,360,360,0)+240` 完全等价，同一个方位在两边是同一个数。

---

## 支持的硬件

| 硬件 | 声源定位（角度） | 说明 |
|---|:---:|---|
| 讯飞六麦圆形阵列（CH340 / `XFMDPV0018`） | ✅ | 唤醒时上报 `angle` 0~360，本工具的主要目标 |
| WonderEcho Pro 等 CH340 语音模块 | ❌ | 只能读到命令词，报文里没有 `angle` |
| 普通 USB 麦克风（无串口） | ❌ | 本工具走串口协议，不解析音频流 |

串口参数固定 **115200 8N1**，DTR / RTS 拉低（与机器人上的用法一致）。

---

## 环境要求

| 平台 | 要求 |
|---|---|
| Windows | Python 3.7+；CH340/CH341 驱动（多数系统自带，设备管理器里能看到 `USB-SERIAL CH340 (COMx)` 即可） |
| Linux | Python 3.7+；当前用户需在 `dialout` 组：`sudo usermod -aG dialout $USER`（重新登录生效） |
| macOS | Python 3.7+；设备一般是 `/dev/cu.usbserial-*` |

可选：`pip install pyserial`（装了会优先用 pyserial，不装也能跑）。

---

## 快速开始

### Windows

```bat
run_windows.bat
```

或直接命令行：

```bat
python mic_wake_monitor.py
```

### Linux / macOS

```bash
chmod +x run_linux.sh
./run_linux.sh
```

### 不接硬件先看效果

```bash
python mic_wake_monitor.py --replay sample_capture.bin
```

样本里是真实抓到的 2 次唤醒 + 240 帧心跳，回放会完整走一遍解析流程。

---

## 命令行参数

| 参数 | 说明 |
|---|---|
| `-p, --port` | 串口名，如 `COM16` / `/dev/ttyUSB0`；不填则自动识别 |
| `-b, --baud` | 波特率，默认 `115200` |
| `--list` | 列出所有串口并逐个探测，告诉你哪个是麦克风 |
| `--replay FILE` | 离线回放抓包文件，不接硬件 |
| `--csv FILE` | 把事件追加记录到 CSV（UTF-8 带 BOM，Excel 可直接打开） |
| `--json` | 每行输出一个 JSON 对象，便于二次开发 |
| `--no-convert` | 只显示原始角度，不做 ROS 换算 |
| `--tag NAME` | 日志标签，默认 `xf_mic_app` |
| `--seconds N` | 运行 N 秒后自动退出（0 = 一直运行） |
| `--heartbeat-interval N` | 每 N 秒打印一次心跳统计 |
| `-v, --version` | 显示版本 |

示例：

```bash
# 看看插了几个串口，哪个是麦克风
python mic_wake_monitor.py --list

# 指定串口，录 60 秒，同时存 CSV
python mic_wake_monitor.py -p COM16 --seconds 60 --csv wake_log.csv

# 输出 JSON 给别的程序消费
python mic_wake_monitor.py -p COM16 --json
```

`--list` 的输出长这样：

```
串口          探测结果
------------------------------------------------
COM1        无数据
COM16       mic (心跳 3 帧 / 36 字节)
COM26       无数据
COM4        无数据
```

---

## 角度与标定

模块上报的 `angle` 是**麦克风阵列自身坐标系**下的方位角。本工具额外给出换算值：

```
换算角度 = (600 - raw) % 360
```

这个公式与 ROS 节点 `awake_node.py` 里的 `val_map(angle, 0, 360, 360, 0) + 240` 完全等价
（`600 - raw` 与 `360 - raw + 240` 相同，再模 360）。

方位文字（正前方 / 右前方 / 右侧 …）按「**0° = 正前方，顺时针增大**」推算，
**这只是一个默认假设**，使用前建议做一次标定：

1. 站在你想定义为「正前方」的位置，喊唤醒词，记下换算角度 `A`；
2. 之后所有角度按 `(角度 - A) % 360` 修正，就是你的坐标系；
3. 如果发现左右颠倒，改为 `(360 - 角度) % 360`。

不想用换算值时，加 `--no-convert` 只看原始角。

实测数据参考（三次唤醒，人站不同位置）：

| 原始角 | 换算角 | 波束 |
|---|---|---|
| 340.0° | 260° | 0 |
| 105.0° | 135° | 2 |
| 278.0° | 322° | 5 |

---

## 串口协议

```
帧头 3 字节 : A5 01 01 = 心跳        A5 01 04 = 唤醒 / 语音事件
长度 4 字节 : 前 2 字节小端 = 负载长度 L，后 2 字节为保留位
负载 L 字节 : UTF-8 JSON
帧尾 1 字节 : 校验

整帧长度 = 3 + 4 + L + 1 = L + 8
```

唤醒帧（`info` 是一层转义 JSON，需要解析两次）：

```json
{
  "content": {
    "arg1": 2,
    "arg2": 0,
    "eventType": 4,
    "info": "{\"ivw\":{\"start_ms\":249860,\"end_ms\":250880,\"beam\":0,\"physical\":0,\"score\":1788.0,\"power\":61585884.0,\"angle\":340.0,\"keyword\":\"xiao3 huan4 xiao3 huan4\"}}",
    "result": "xiao3 huan4 xiao3 huan4"
  },
  "type": "aiui_event"
}
```

字段说明：

| 字段 | 含义 |
|---|---|
| `angle` | 声源方位角 0~360（阵列坐标系） |
| `beam` | 选中的波束号 0~5（六麦环形，每 60° 一个） |
| `physical` | 实际物理通道号 |
| `score` | 唤醒置信度 |
| `power` | 该波束能量，可用于判断远近 / 强弱 |
| `keyword` | 唤醒词（拼音带声调数字） |
| `start_ms` / `end_ms` | 唤醒音频片段的时间戳（毫秒） |

心跳帧每约 300ms 一个（12 字节），内容为递增计数 + 校验，本工具只用于统计，不解析。
校验规则：整帧所有字节相加恒等于 `0x0200`（和校验）。

---

## 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| `WinError 5 拒绝访问` | 串口被别的程序占用：串口助手、上位机、ROS 节点、或另一个本程序 |
| `WinError 31 device not functioning` | **CH340 经典卡死**：把麦克风 USB 线拔插一次，或换 USB 口 |
| 一直只有心跳、没有唤醒 | 唤醒词不匹配（中文固件「小幻小幻」/ 英文固件 `hello hiwonder`）；或麦克风静音、增益太低 |
| 显示「角度: 无」 | 模块不支持声源定位（如 WonderEcho Pro），只有命令词 |
| 串口列表里没有设备 | 驱动没装 / 用的是充电线不是数据线 / 换 USB 口 |
| Linux 打不开 `/dev/ttyUSB0` | 当前用户不在 `dialout` 组 |
| Windows 中文乱码 | 程序已自动把控制台切到 UTF-8（`chcp 65001`）；若仍乱码，在终端执行 `chcp 65001` 后重开 |

---

## 目录结构

```
ring/
├── mic_wake_monitor.py     主程序（零依赖，单文件）
├── run_windows.bat         Windows 双击运行
├── run_linux.sh            Linux / macOS 运行脚本
├── requirements.txt        可选依赖说明（pyserial，非必需）
├── sample_capture.bin      真实抓包样本，供 --replay 使用
├── .gitignore
└── README.md
```

---

## 更新记录

**v1.0.1**
- 修正自动探测只认唤醒事件的问题：空闲时只发心跳的麦克风会被误判成"无数据"，现按心跳识别
- `--list` 探测结果更详细（区分 `mic (心跳 N 帧)` / `无数据` / `未知设备`）
- 新增 `--seconds` 定时退出

**v1.0.0**
- 首个版本：串口解析、角度换算、CSV / JSON 输出、离线回放、自动识别串口

---

## 致谢

串口协议来自讯飞 AIUI / IVW 的语音上报格式，已在幻尔 JetMax 六麦圆形阵列上实测验证。
